# BigQuery + NinjaCat Setup Runbook

End-to-end steps to take RPM Client Portal from seeded benchmarks to live BigQuery data sourced from NinjaCat.

**Who does what**
- **Kyle / ops**: steps 1–6 (GCP, BQ, service account, Render env, NinjaCat export config)
- **Portal already wired**: the `/api/benchmarks` and `/api/property.current_perf` endpoints will automatically flip from seeded data to BigQuery the moment env vars are present and the table has ≥3 comps per segment

---

## 1. Create the GCP project

1. Go to https://console.cloud.google.com/projectcreate
2. Project name: `rpm-portal` (or whatever). Note the **Project ID** (e.g., `rpm-portal-471234`) — you'll need it.
3. Enable billing on the project (BigQuery is free up to 1 TB/mo queries + 10 GB storage; you'll stay well under).
4. Enable the **BigQuery API**: https://console.cloud.google.com/apis/library/bigquery.googleapis.com

## 2. Create the BigQuery datasets

In the BQ console (https://console.cloud.google.com/bigquery):

```sql
CREATE SCHEMA IF NOT EXISTS `YOUR_PROJECT_ID.rpm_portal`
OPTIONS(location="US");

CREATE SCHEMA IF NOT EXISTS `YOUR_PROJECT_ID.rpm_portal_dev`
OPTIONS(location="US");
```

Replace `YOUR_PROJECT_ID`. Pick a location (US is recommended) and don't change it later.

## 3. Create the four tables

Run this DDL in both `rpm_portal` and `rpm_portal_dev`. The portal code already references these exact names and columns.

**Architecture note:** NinjaCat only emits `property_uuid` (via its External ID custom field). Market + unit_count + property name come from HubSpot through a nightly sync job that writes `rpm_properties`. Benchmark queries JOIN the two tables. This way HubSpot stays the single source of truth — no duplicate data entry in NC.

```sql
-- Table 1: Red Light historical scores (one row per property per month)
CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.rpm_portal.red_light_history` (
  property_uuid     STRING NOT NULL,
  report_month      DATE   NOT NULL,  -- first of month
  overall_score     FLOAT64,
  market_score      FLOAT64,
  marketing_score   FLOAT64,
  funnel_score      FLOAT64,
  experience_score  FLOAT64,
  status            STRING,           -- 'RED' | 'YELLOW' | 'GREEN'
  scored_at         TIMESTAMP
)
PARTITION BY report_month
CLUSTER BY property_uuid;

-- Table 2: AI-extracted insights from Red Light PDFs
CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.rpm_portal.report_insights` (
  property_uuid        STRING NOT NULL,
  ninjacat_system_id   STRING,
  report_month         DATE NOT NULL,
  report_type          STRING,        -- 'red_light' | 'other'
  insight_type         STRING,        -- 'strength' | 'weakness' | 'opportunity' | 'threat'
  finding              STRING,
  recommendation       STRING,
  priority             STRING,        -- 'high' | 'medium' | 'low'
  raw_text             STRING,
  ingested_at          TIMESTAMP
)
PARTITION BY report_month
CLUSTER BY property_uuid;

-- Table 3: NinjaCat daily metrics — daily perf rows, one per property × channel × day
CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.rpm_portal.ninjacat_metrics` (
  date                 DATE   NOT NULL,           -- daily grain
  property_uuid        STRING NOT NULL,           -- RPM UUID (from NC's "External ID" custom field)
  ninjacat_system_id   STRING,                    -- NinjaCat internal property ID (informational)
  property_name        STRING,                    -- informational; market/units live in rpm_properties
  channel              STRING NOT NULL,           -- normalized: 'google_ads' | 'meta' | 'seo_organic' | 'video_creative' | 'other'
  spend                FLOAT64,
  impressions          INT64,
  clicks               INT64,
  leads                INT64,
  conversions          INT64,
  ingested_at          TIMESTAMP
)
PARTITION BY date
CLUSTER BY property_uuid, channel;

-- Table 4: RPM properties dimension — synced nightly from HubSpot
--          (see POST /api/internal/sync-properties-to-bq in the portal server).
--          Benchmark queries JOIN ninjacat_metrics ON property_uuid against this
--          table to get market, unit_count, and occupancy status. This lets
--          NinjaCat stay lean (no market or unit custom fields needed there).
CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.rpm_portal.rpm_properties` (
  property_uuid        STRING NOT NULL,
  hubspot_company_id   STRING,
  ninjacat_system_id   STRING,
  name                 STRING,
  market               STRING,
  unit_count           INT64,
  occupancy_status     STRING,                     -- Lease-Up / Stabilized / In-Transition / Renovation
  plestatus            STRING,                     -- RPM Managed / Onboarding / Dispositioning
  updated_at           TIMESTAMP
)
CLUSTER BY property_uuid;

-- Table 5: SEO rank daily snapshots — written by seo_refresh_cron.refresh_ranks()
-- One row per (property_uuid, keyword, day). Read by /api/seo/dashboard to
-- compute 7/30-day position deltas and the Visibility Trend chart.
CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.rpm_portal.seo_ranks_daily` (
  property_uuid  STRING NOT NULL,
  keyword        STRING NOT NULL,
  position       INT64,                             -- null if not in top 100
  url            STRING,                            -- ranked URL if found
  volume         INT64,
  difficulty     FLOAT64,
  fetched_at     TIMESTAMP NOT NULL
)
PARTITION BY DATE(fetched_at)
CLUSTER BY property_uuid, keyword;
```

Repeat for `rpm_portal_dev` (replace the schema name).

## 4. Create a service account for the portal

1. GCP Console → IAM & Admin → Service Accounts → **Create Service Account**
2. Name: `rpm-portal-bq-reader`. Description: "Client portal read access to BigQuery."
3. Grant roles:
   - **BigQuery Data Viewer** on both `rpm_portal` and `rpm_portal_dev` datasets (can scope via `bq mk` or dataset-level grants rather than project-wide)
   - **BigQuery Job User** on the project (needed to run queries)
4. Create a **JSON key**, download it. **This file is a secret** — don't commit to git.

## 5. Add to Render

In your Render service ("rpm-portal-server") → Environment:

1. Upload the JSON key as a **Secret File**. Render will mount it at `/etc/secrets/rpm-portal-bq.json` (or whatever path you choose).
2. Add three env vars:
   ```
   BIGQUERY_PROJECT_ID=YOUR_PROJECT_ID
   BIGQUERY_SERVICE_ACCOUNT_JSON=/etc/secrets/rpm-portal-bq.json
   BIGQUERY_DATASET_PROD=rpm_portal
   ```
3. Save → Render restarts the service automatically.

**Verify**: after restart, the portal still works exactly as before (seeded data) because `ninjacat_metrics` is empty. You're ready for NinjaCat to start writing.

## 6. Configure NinjaCat → BigQuery scheduled export

NinjaCat's native BQ integration lives in their **Data Warehouse** or **Data Feeds** section (exact location depends on your NC plan — check with your NC account rep).

**Prereq — confirm "External ID" is populated with the RPM UUID** for every property you want in the portal. Kyle already did this. Values look like `1201145` (numeric RPM UUID) on NinjaCat's property records. This is the **only** custom NC field the portal needs.

> Market, unit_count, and property name do **not** need to be NC custom fields — they come from HubSpot via the nightly sync job (§7).

**Export target:** project=`YOUR_PROJECT_ID`, dataset=`rpm_portal`, table=`ninjacat_metrics`.

**Schedule:** Nightly (e.g., 2am CT, after the prior day's data is finalized).

**Column mapping** — map NinjaCat columns to these BQ columns:

| BQ column            | NinjaCat source                                         |
|----------------------|---------------------------------------------------------|
| `date`               | Date (daily)                                            |
| `property_uuid`      | **External ID** (NC custom field — the RPM UUID)        |
| `ninjacat_system_id` | NC's internal property/system ID (informational)        |
| `property_name`      | Property Name                                           |
| `channel`            | Normalized channel — see mapping below                  |
| `spend`              | Cost / Spend                                            |
| `impressions`        | Impressions                                             |
| `clicks`             | Clicks                                                  |
| `leads`              | Leads (or Conversions if no separate lead metric)       |
| `conversions`        | Conversions                                             |
| `ingested_at`        | Export run timestamp (can be `CURRENT_TIMESTAMP()`)     |

**Channel normalization rule** — map NinjaCat's channel/source field to one of these values (everything else → `other`):

| NinjaCat value (examples)                         | Target `channel` |
|---------------------------------------------------|------------------|
| Google Ads, Paid Search, SEM                      | `google_ads`     |
| Meta, Facebook, Instagram, Paid Social            | `meta`           |
| Organic Search, SEO                               | `seo_organic`    |
| YouTube, Video, Creatify                          | `video_creative` |
| (anything else)                                   | `other`          |

If NC's export supports a CASE/regex transform, do the normalization in the export config. Otherwise land the raw value and we'll add a BQ view on top.

## 7. Set up the nightly HubSpot → rpm_properties sync

The portal server exposes an authenticated endpoint that scans all active RPM companies in HubSpot and refreshes the `rpm_properties` BQ table. NinjaCat metrics JOIN against this table for benchmarks, so **this sync must run before benchmarks show real data**.

**Endpoint:** `POST https://rpm-portal-server.onrender.com/api/internal/sync-properties-to-bq`
**Auth header:** `X-Internal-Key: <INTERNAL_API_KEY env var>`
**Response:** `{status, rows_written, companies_scanned, runtime_seconds}`

**Option A — Render Cron Job (recommended):**
1. Render dashboard → New → Cron Job
2. Schedule: `0 7 * * *` (2am CT = 7am UTC — runs after NC export)
3. Command:
   ```
   curl -fsSL -X POST \
     -H "X-Internal-Key: $INTERNAL_API_KEY" \
     https://rpm-portal-server.onrender.com/api/internal/sync-properties-to-bq
   ```
4. Set `INTERNAL_API_KEY` env var on the cron service (same value as on the portal service).

**Option B — manual trigger during setup:**
Run the same curl locally to populate the table for the first time:
```bash
curl -X POST \
  -H "X-Internal-Key: <paste INTERNAL_API_KEY here>" \
  https://rpm-portal-server.onrender.com/api/internal/sync-properties-to-bq
```

**Expected first run:** ~700+ rows, ~10–30 seconds.

## 8. Verify end-to-end

Once NinjaCat's first nightly export runs:

1. **BQ console:** run `SELECT COUNT(*), MIN(date), MAX(date) FROM rpm_portal.ninjacat_metrics;` — should show rows.
2. **Portal API:** hit `https://rpm-portal-server.onrender.com/api/property?company_id=<any_real_company_id>` with header `X-Portal-Email: portal@rpmliving.com`. Response should include a non-null `current_perf` object with `leads`, `cpl`, and per-channel breakdown.
3. **Portal API:** hit `/api/benchmarks?market=Dallas&size_band=mid` (swap in a real market). Response should show `"data_source": "bigquery"` (or `"mixed"` if some segments still fall back to seeded).
4. **Portal UI:** open a property → Performance Forecast → "Current State" panel should show **Last Month Leads** and **Blended CPL** with real numbers instead of `—`.

## 9. Troubleshooting

**Portal still shows seeded data after setup:**
- Check Render logs for `BQ benchmarks lookup failed` or `current_perf lookup failed` — the exception message will point to the problem.
- Verify `ls /etc/secrets/rpm-portal-bq.json` on the Render shell exits 0.
- Run the verify query in step 7 — if BQ says 0 rows, the NinjaCat export isn't writing yet.

**Benchmarks returns `"mixed"` or `"seeded"` when you expected `"bigquery"`:**
- `get_ninjacat_benchmarks` requires **≥3 distinct properties per (market, size_band, channel, month)** segment before surfacing a BQ row. Until you have enough comps, low-sample segments fall back to seeded. This is intentional — small samples make noisy benchmarks.

**`property_uuid` is blank in NC rows:**
- The custom dimension wasn't set on those properties before the export ran. Backfill the dimension, then re-export the historical range (NC supports "re-run from date" in most plans).

## 10. What this unlocks in the portal

After NinjaCat → BQ is flowing:

- **Performance Forecast** → Current State shows real **Last Month Leads** + **Blended CPL** (not `—`).
- **Performance Forecast** → Simulator projections use real market benchmarks instead of seeded averages — projected leads/CPL shift based on your portfolio's actual performance.
- **`/api/forecast-context`** → "Peer Insights" comp stats become real (today they use the spend-tracker sheet; we can extend to BQ later if you want per-channel spend comparisons too).
- **Red Light pipeline** → can stop depending on the broken `NINJACAT_COLUMN_MAP` CSV path and instead query BQ directly for historical trends.

## 11. Related files (portal side, already shipped)

- `webhook-server/bigquery_client.py` — `get_ninjacat_current_perf()`, `get_ninjacat_benchmarks()` (JOINs on rpm_properties), `upsert_rpm_properties()`, `is_bigquery_configured()`
- `webhook-server/server.py`:
  - `/api/property` populates `current_perf` from `ninjacat_metrics`
  - `/api/benchmarks` queries BQ then merges with seeded fallback
  - `/api/internal/sync-properties-to-bq` refreshes the `rpm_properties` dimension table from HubSpot
- `webhook-server/config.py` — `BIGQUERY_PROJECT_ID`, `BIGQUERY_SERVICE_ACCOUNT_JSON`, `BIGQUERY_DATASET_PROD`, `INTERNAL_API_KEY`
