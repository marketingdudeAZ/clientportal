# Runbook: Google Ads Customer Match via Data Manager (MCC-level)

**Audience:** dev/ops + Kyle (one-time setup, then hands-off).
**Goal:** Wire a daily-refreshed Customer Match audience into Google Ads
across all 700+ child accounts under MCC `3158541695`, sourced from
HubSpot list `18185` (`Customer Match — Marketable Residents`).
**Time:** ~30 min one-time. After that, daily crons keep it fresh.

## Architecture

```
HubSpot list 18185 (marketing-owned audience filter)
        │
        ▼  sync-hubspot-list-to-bq  (Render Cron, daily 04:00 CT)
        │
   BigQuery: hubspot_contacts_for_match (hashed-only, append-only)
        │
        ▼  build-customer-match-csv  (Render Cron, daily 04:30 CT)
        │
   gs://rpm-ads-audiences/customer_match/18185/latest.csv
   gs://rpm-ads-audiences/customer_match/18185/YYYY-MM-DD.csv
        │
        ▼  Google Ads Data Manager  (auto-refresh, MCC scope)
        │
   "RPM Living — Current Residents (Marketable)" audience
        │
        ▼  Shared down via MCC
        │
   Available in all 700+ child accounts under MCC 3158541695
```

The audience is created **once at the MCC**. Google Ads automatically
makes MCC-level audiences available to child accounts when the MCC has
audience-sharing enabled (default). Child account managers add the
audience to campaign targeting like any other.

## Prerequisites

- [ ] Migration 0011 applied (`python3 migrations/_runner.py up` on Render)
- [ ] Code from commit `(latest)` deployed on Render (the sync +
      build endpoints)
- [ ] `HUBSPOT_API_KEY`, `BIGQUERY_PROJECT_ID`,
      `BIGQUERY_DATASET_PROD`, `BIGQUERY_SERVICE_ACCOUNT_JSON`,
      `INTERNAL_API_KEY` all set in Render env
- [ ] HubSpot list `18185` exists and has members
- [ ] You have Admin access to MCC `3158541695` in Google Ads

---

## Step 1 — Create the GCS bucket (~5 min)

```bash
# Use the same GCP project as BIGQUERY_PROJECT_ID
gcloud config set project YOUR-PROJECT-ID

# Create the bucket. US-CENTRAL1 keeps it close to your BQ datasets.
gsutil mb -p YOUR-PROJECT-ID -c STANDARD -l US-CENTRAL1 gs://rpm-ads-audiences/

# Lifecycle policy: auto-delete dated snapshots after 30 days.
# Keeps `latest.csv` forever (since it's overwritten daily).
cat > /tmp/lifecycle.json <<'JSON'
{
  "lifecycle": {
    "rule": [
      {
        "action": {"type": "Delete"},
        "condition": {
          "age": 30,
          "matchesPrefix": ["customer_match/"]
        }
      }
    ]
  }
}
JSON
gsutil lifecycle set /tmp/lifecycle.json gs://rpm-ads-audiences/

# Grant the BQ service account (the one Render uses) Storage Object Admin
# on this bucket so the build endpoint can upload.
SA_EMAIL="<the email from BIGQUERY_SERVICE_ACCOUNT_JSON>"
gsutil iam ch serviceAccount:$SA_EMAIL:objectAdmin gs://rpm-ads-audiences/

# Grant Google Ads Data Manager service agent read access so it can pull.
# (Data Manager presents a specific service-account email when you set
# up the connection; come back here after Step 3 to add that grant.)
```

---

## Step 2 — Smoke-test the sync + build endpoints (~5 min)

```bash
INTERNAL_API_KEY=$(env | grep ^INTERNAL_API_KEY | cut -d= -f2-)

# 2a. Sync HubSpot list → BQ (dry-run first)
curl -sX POST -H "X-Internal-Key: $INTERNAL_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"list_id":"18185","max_contacts":50,"dry_run":true}' \
  https://rpm-portal-server.onrender.com/api/internal/sync-hubspot-list-to-bq \
  | python3 -m json.tool
# Expect: members_seen > 0, contacts_read > 0, rows_written: 0 (dry_run)
# sample_signature populated

# 2b. Real sync — write 50-row sample to BQ
curl -sX POST -H "X-Internal-Key: $INTERNAL_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"list_id":"18185","max_contacts":50}' \
  https://rpm-portal-server.onrender.com/api/internal/sync-hubspot-list-to-bq \
  | python3 -m json.tool
# Expect: rows_written ~50

# 2c. Build the CSV (dry-run, no GCS write)
curl -sX POST -H "X-Internal-Key: $INTERNAL_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"list_id":"18185","dry_run":true}' \
  https://rpm-portal-server.onrender.com/api/internal/build-customer-match-csv \
  | python3 -m json.tool
# Expect: rows ~50, sample_csv showing headers + 2 hashed rows

# 2d. Build CSV for real and upload to GCS
curl -sX POST -H "X-Internal-Key: $INTERNAL_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"list_id":"18185"}' \
  https://rpm-portal-server.onrender.com/api/internal/build-customer-match-csv \
  | python3 -m json.tool
# Expect: gs_uri_latest = gs://rpm-ads-audiences/customer_match/18185/latest.csv

# Inspect the CSV
gsutil cat gs://rpm-ads-audiences/customer_match/18185/latest.csv | head
# Expect: header row + hashed contact rows
```

If any step fails, check Render logs and `/api/loop/events?stage=ops`
for the `cron_started`/`cron_completed` Loop events.

---

## Step 3 — Run the full sync (~10 min, depends on list size)

Remove the `max_contacts` cap and let it pull the full list:

```bash
INTERNAL_API_KEY=$(env | grep ^INTERNAL_API_KEY | cut -d= -f2-)
curl -sX POST -H "X-Internal-Key: $INTERNAL_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"list_id":"18185"}' \
  https://rpm-portal-server.onrender.com/api/internal/sync-hubspot-list-to-bq \
  | python3 -m json.tool

# Then rebuild the CSV with the full member set
curl -sX POST -H "X-Internal-Key: $INTERNAL_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"list_id":"18185"}' \
  https://rpm-portal-server.onrender.com/api/internal/build-customer-match-csv \
  | python3 -m json.tool
```

For 100k contacts expect ~5 min sync + ~30 sec build.

---

## Step 4 — Configure Data Manager in Google Ads (~10 min, ONE TIME)

1. Sign in to Google Ads at the **MCC level** (account `3158541695`),
   NOT a child account.
2. Tools → Audience manager → Your data sources → click **Connect data
   source** → choose **Google Cloud Storage**.
3. Configure:
   - **Source:** `gs://rpm-ads-audiences/customer_match/18185/latest.csv`
   - **Format:** CSV with headers
   - **Schedule:** Daily, ~05:00 CT (after the build cron at 04:30)
   - **Schema mapping:** Google should auto-detect from the headers
     (`Email`, `Phone`, `First Name`, `Last Name`, `Country`, `Zip`).
     Confirm each maps to the corresponding User identifier field.
   - **Hashing:** Tell Data Manager the data is **already hashed**.
     (Otherwise Google will hash again and break matching.)
4. Google will surface a **service-account email** for Data Manager.
   Copy it.
5. Back in your shell — grant that service account read access to the
   bucket:
   ```bash
   DATA_MANAGER_SA="data-manager-XXX@google-ads-data-manager.iam.gserviceaccount.com"
   gsutil iam ch serviceAccount:$DATA_MANAGER_SA:objectViewer gs://rpm-ads-audiences/
   ```
6. Click **Test connection** in Data Manager. Should turn green.
7. Click **Activate**.

---

## Step 5 — Create the Customer Match audience (~5 min)

Still at the MCC:

1. Tools → Audience manager → Segments → click **+** → **Customer list**.
2. Configure:
   - **Name:** `RPM Living — Current Residents (Marketable)`
   - **Description:** *Sourced from HubSpot list 18185 (Customer Match
     — Marketable Residents). Auto-refreshed daily from
     gs://rpm-ads-audiences/customer_match/18185/latest.csv. Members:
     current residents who have never been Denied or Evicted.*
   - **Data source:** the Data Manager connection from Step 4
   - **Membership duration:** Maximum (540 days) — let Google decide
     when to drop stale members
3. Save. Google will process the list (~6-24 hours for first match;
   subsequent refreshes are faster).
4. Match rate appears in the audience detail page once processing
   completes. Typical match rates: 50-80% for well-formed hashed
   email+phone. Below 40% suggests an input quality issue worth
   investigating.

---

## Step 6 — Schedule the daily Render Crons (~5 min)

Render Dashboard → **Cron Jobs** → add two:

### Cron A: HubSpot list sync (daily 04:00 CT)

```
Name: customer-match-sync
Schedule: 0 9 * * *    # 04:00 CT (UTC = CT + 5)
Command:
  curl -sX POST \
    -H "X-Internal-Key: $INTERNAL_API_KEY" \
    -H "Content-Type: application/json" \
    -d '{"list_id":"18185"}' \
    https://rpm-portal-server.onrender.com/api/internal/sync-hubspot-list-to-bq
```

### Cron B: CSV build + GCS upload (daily 04:30 CT)

```
Name: customer-match-csv-build
Schedule: 30 9 * * *   # 04:30 CT
Command:
  curl -sX POST \
    -H "X-Internal-Key: $INTERNAL_API_KEY" \
    -H "Content-Type: application/json" \
    -d '{"list_id":"18185"}' \
    https://rpm-portal-server.onrender.com/api/internal/build-customer-match-csv
```

Both write `loop_events` rows (`cron_started` + `cron_completed` with
runtime) so you can verify they ran via `/api/loop/events?stage=ops`.

---

## Step 7 — Verify in Google Ads (~24h after activation)

1. MCC → Audience manager → Segments → click the audience.
2. **Size**: should be roughly proportional to your HubSpot list size
   minus Google's match losses (50-80% match rate is typical).
3. **Status**: Open / Active.
4. **Available in**: should list "All accounts under MCC" or similar.
5. In a child account's campaign targeting, search for the audience
   name — it should appear.

If size is 0 after 48 hours, common causes:
- Data Manager hashing setting wrong (told Google "raw" instead of
  "already hashed")
- Column mapping wrong (Email column not mapped to Email field)
- Service account on the bucket missing read permission
- CSV has < 1,000 members (Google's minimum for some segment types)

Loop events with errors will surface in `/api/loop/events?status=failed`.

---

## Operating

- **Daily lifecycle:** crons run unattended. Each sync writes a `cron_completed`
  Loop event with row counts; alerting (Slack notifier ADR 0019) will
  surface failures.
- **Backfilling history:** rerun the sync endpoint at any time with
  `dry_run:true` to see counts without disturbing data.
- **Per-property fanout (Phase 2):** once
  `scripts/backfill_google_ads_cid.py` populates
  `google_ads_customer_id` on company records, we can build per-property
  CSVs grouped by that CID and create one audience per child account
  (or upload via the Google Ads API directly using the CID as the
  target).

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| sync returns `members_seen: 0` | HubSpot list filter excludes everyone, or list_id wrong | Open HubSpot UI, confirm the list has members |
| build returns `rows: 0` | sync hasn't run yet, or list_id mismatch between sync and build calls | Check `hubspot_contacts_for_match` for rows with that list_id |
| GCS upload fails with 403 | Render service account doesn't have objectAdmin on bucket | Re-run the `gsutil iam ch` from Step 1 |
| Data Manager says "no data" | Read permission for Data Manager SA on bucket missing | Add `objectViewer` per Step 4.5 |
| Audience size 0 in Google Ads | Data Manager hashing setting wrong | Edit the Data Manager source, switch hashing to "already hashed" |
| Match rate < 40% | Postal_code missing or wrong, phone formats inconsistent | Inspect a few CSV rows; verify your contacts have populated phones |

## References

- Migration: `migrations/0011_hubspot_contacts_for_match.py`
- Hashing: `webhook-server/customer_match_export.py`
- Sync endpoint: `POST /api/internal/sync-hubspot-list-to-bq`
- Build endpoint: `POST /api/internal/build-customer-match-csv`
- Phase-2 CID backfill: `scripts/backfill_google_ads_cid.py`
- Google's Customer Match spec:
  https://support.google.com/google-ads/answer/7659867
