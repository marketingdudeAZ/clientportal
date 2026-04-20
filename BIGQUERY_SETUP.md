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

## 3. Create the three tables

Run this DDL in both `rpm_portal` and `rpm_portal_dev`. The portal code already references these exact names and columns.

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

-- Table 3: NinjaCat daily metrics — THE KEY TABLE for Forecast + benchmarks
CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.rpm_portal.ninjacat_metrics` (
  date                 DATE   NOT NULL,           -- daily grain
  property_uuid        STRING NOT NULL,           -- RPM UUID (MUST be pushed as custom NC dimension)
  ninjacat_system_id   STRING,                    -- NinjaCat internal property ID
  property_name        STRING,
  market               STRING,                    -- RPM market name (custom NC dimension)
  unit_count           INT64,                     -- (custom NC dimension) — used for size_band segmentation
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

**Critical: add two custom dimensions in NinjaCat before enabling the export.** The portal needs these keyed on every row:
- `property_uuid` — set to each property's RPM UUID (same value as HubSpot `uuid` company property)
- `market` — set to the property's RPM market (same value as HubSpot `rpmmarket`)
- *(optional)* `unit_count` — integer unit count

These are typically set as **property-level custom fields** in NinjaCat. If NC won't let you push custom dimensions into the BQ export, fallback plan is a nightly Cloud Function that writes a `properties` reference table; ping me if so.

**Export target:** project=`YOUR_PROJECT_ID`, dataset=`rpm_portal`, table=`ninjacat_metrics`.

**Schedule:** Nightly (e.g., 2am CT, after the prior day's data is finalized).

**Column mapping** — map NinjaCat columns to these table columns:

| BQ column            | NinjaCat source                                         |
|----------------------|---------------------------------------------------------|
| `date`               | Date (daily)                                            |
| `property_uuid`      | Custom dimension `property_uuid`                        |
| `ninjacat_system_id` | NC's internal property/system ID                        |
| `property_name`      | Property Name                                           |
| `market`             | Custom dimension `market`                               |
| `unit_count`         | Custom dimension `unit_count` (optional)                |
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

## 7. Verify end-to-end

Once NinjaCat's first nightly export runs:

1. **BQ console:** run `SELECT COUNT(*), MIN(date), MAX(date) FROM rpm_portal.ninjacat_metrics;` — should show rows.
2. **Portal API:** hit `https://rpm-portal-server.onrender.com/api/property?company_id=<any_real_company_id>` with header `X-Portal-Email: portal@rpmliving.com`. Response should include a non-null `current_perf` object with `leads`, `cpl`, and per-channel breakdown.
3. **Portal API:** hit `/api/benchmarks?market=Dallas&size_band=mid` (swap in a real market). Response should show `"data_source": "bigquery"` (or `"mixed"` if some segments still fall back to seeded).
4. **Portal UI:** open a property → Performance Forecast → "Current State" panel should show **Last Month Leads** and **Blended CPL** with real numbers instead of `—`.

## 8. Troubleshooting

**Portal still shows seeded data after setup:**
- Check Render logs for `BQ benchmarks lookup failed` or `current_perf lookup failed` — the exception message will point to the problem.
- Verify `ls /etc/secrets/rpm-portal-bq.json` on the Render shell exits 0.
- Run the verify query in step 7 — if BQ says 0 rows, the NinjaCat export isn't writing yet.

**Benchmarks returns `"mixed"` or `"seeded"` when you expected `"bigquery"`:**
- `get_ninjacat_benchmarks` requires **≥3 distinct properties per (market, size_band, channel, month)** segment before surfacing a BQ row. Until you have enough comps, low-sample segments fall back to seeded. This is intentional — small samples make noisy benchmarks.

**`property_uuid` is blank in NC rows:**
- The custom dimension wasn't set on those properties before the export ran. Backfill the dimension, then re-export the historical range (NC supports "re-run from date" in most plans).

## 9. What this unlocks in the portal

After NinjaCat → BQ is flowing:

- **Performance Forecast** → Current State shows real **Last Month Leads** + **Blended CPL** (not `—`).
- **Performance Forecast** → Simulator projections use real market benchmarks instead of seeded averages — projected leads/CPL shift based on your portfolio's actual performance.
- **`/api/forecast-context`** → "Peer Insights" comp stats become real (today they use the spend-tracker sheet; we can extend to BQ later if you want per-channel spend comparisons too).
- **Red Light pipeline** → can stop depending on the broken `NINJACAT_COLUMN_MAP` CSV path and instead query BQ directly for historical trends.

## 10. Related files (portal side, already shipped)

- `webhook-server/bigquery_client.py` — `get_ninjacat_current_perf()`, `get_ninjacat_benchmarks()`, `is_bigquery_configured()`
- `webhook-server/server.py` — `/api/property` populates `current_perf`; `/api/benchmarks` queries BQ then merges with seeded fallback
- `webhook-server/config.py` — `BIGQUERY_PROJECT_ID`, `BIGQUERY_SERVICE_ACCOUNT_JSON`, `BIGQUERY_DATASET_PROD`
