# Runbook: Loop architecture deploy + verification

**Audience:** dev/ops.
**When to run:** after the Loop architecture commits land (Saturday
2026-05-17 onward).
**Goal:** prove the Loop Event Bus + Forecasting + Portal subpage are
wired correctly end-to-end.

## Pre-flight checklist

1. Render auto-deploy ran for the latest main commit (check the Deploys tab — look for the green `aptiq + loop` commit batch).
2. Render env vars present:
   - `BIGQUERY_PROJECT_ID`, `BIGQUERY_DATASET_PROD`, `BIGQUERY_SERVICE_ACCOUNT_JSON`
   - `HUBSPOT_API_KEY`
   - `ApartmentIQ_Token`
   - `INTERNAL_API_KEY`
3. New env vars to add for Hyly (when their beta lands):
   - `BIGQUERY_HYLY_DATASET` — name of Hyly's BQ dataset
4. New env var for HubSpot webhooks (when subscriptions configured):
   - `HUBSPOT_WEBHOOK_SECRET` — app secret from HubSpot UI

## Step 1: Apply migrations

```bash
# In the Render Shell (Starter tier or higher)
cd ~/project/src
python3 migrations/_runner.py status
# Expect: 6 pending migrations
python3 migrations/_runner.py up
# Expect each migration line to print "OK (..ms, checksum...)"
python3 migrations/_runner.py status
# Expect: all migrations show "applied"
```

If 0006 fails (HubSpot CRM property create), confirm `HUBSPOT_API_KEY` is set
and re-run. The migration is idempotent (409 on existing properties = success).

## Step 2: Verify Loop Event Bus

Write a test event:
```bash
python3 -c "
import sys; sys.path.insert(0, 'webhook-server')
import loop_writer
event_id = loop_writer.record(
    stage='ops', event_type='cron_started',
    property_uuid='10560269171', company_id='10560269171',
    source='manual_verify', trigger='manual',
    payload={'test': True},
)
print(f'Wrote event_id={event_id}')
"
```

Read it back via the API:
```bash
INTERNAL_API_KEY=$(grep ^INTERNAL_API_KEY .env | cut -d= -f2-)
curl -sH "X-Internal-Key: $INTERNAL_API_KEY" \
  'https://rpm-portal-server.onrender.com/api/loop/events?uuid=10560269171&limit=5' \
  | python3 -m json.tool
```

Expect: the event you just wrote in the response.

## Step 3: Verify AptIQ historical pull for Ashton

This was the original 2026-05-15 test target. Run dry-run first (no BQ writes,
just confirms the bulk_api flow works):

```bash
INTERNAL_API_KEY=$(grep ^INTERNAL_API_KEY .env | cut -d= -f2-)
python3 <<PY
import os, json, urllib.request

req = urllib.request.Request(
    "https://rpm-portal-server.onrender.com/api/internal/aptiq-backfill-history",
    method="POST",
    headers={
        "X-Internal-Key": os.environ["INTERNAL_API_KEY"],
        "Content-Type":   "application/json",
    },
    data=json.dumps({
        "company_id":  "10560269171",  # Ashton on West Dallas
        "months_back": 13,
        "dry_run":     True,
    }).encode(),
)
print(json.dumps(json.loads(urllib.request.urlopen(req, timeout=900).read()), indent=2))
PY
```

Expected: response with `months_returned ~13`, plus `sample_latest_month`
showing populated occupancy / leased_percent / etc. If sample shows
mostly None values, the JSONL field names from AptIQ's bulk export
don't match our `_SNAPSHOT_FIELD_ALIASES` map — patch the alias map and
retry.

Confirm the dry-run was logged as a Loop event:
```bash
curl -sH "X-Internal-Key: $INTERNAL_API_KEY" \
  'https://rpm-portal-server.onrender.com/api/loop/events?uuid=10560269171&stage=ops&limit=5' \
  | python3 -m json.tool
```
Look for `event_type=aptiq_history_backfill` with status=completed and
non-null runtime_ms.

When dry-run looks good, flip `dry_run: false` and re-run. That writes
~13 rows to `aptiq_snapshots`.

## Step 4: Run a forecast

```bash
INTERNAL_API_KEY=$(grep ^INTERNAL_API_KEY .env | cut -d= -f2-)
curl -sX POST \
  -H "X-Internal-Key: $INTERNAL_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"company_id":"10560269171","seo_tier":"Standard"}' \
  https://rpm-portal-server.onrender.com/api/loop/forecast/run \
  | python3 -m json.tool
```

Expected: a forecast JSON blob with `forecast_leases`, `ci_low`,
`ci_high`, `recommendations`, `channel_allocation`. With trailing data
present from Step 3, methodology should be `simple_lag_v1`; without, it
falls back to `tier_baseline_v0`.

Confirm forecast row landed in BQ:
```bash
curl -sH "X-Internal-Key: $INTERNAL_API_KEY" \
  'https://rpm-portal-server.onrender.com/api/loop/forecast?uuid=10560269171' \
  | python3 -m json.tool
```

## Step 5: Verify Loop Status panel works

```bash
curl -sH "X-Internal-Key: $INTERNAL_API_KEY" \
  'https://rpm-portal-server.onrender.com/api/loop/status?uuid=10560269171' \
  | python3 -m json.tool
```

Expected: a `stages` map with `attract / engage / convert / optimize`,
each with `health` + `last_event_type` + `last_at`. After steps 2-4,
the `ops` and `optimize` stages should show recent activity.

## Step 6: Verify Portal Loop subpage renders

Open the new Loop view in a browser (with HubSpot Memberships logged in):

`https://digital.rpmliving.com/staging/portal-dashboard-loop?uuid=10560269171`

(Adjust the URL based on how you wired the template in HubSpot — see
the ADR 0018 "Wiring it up" section.)

Expect:
- Header shows "Ashton on West Dallas" + market + units
- Status tab shows 4 stage cards with health dots
- Plan tab shows the forecast number with confidence interval
- Timeline tab lists the recent ops events from steps 2-4
- Execute tab shows action buttons

## Step 7 (when Hyly beta lands)

Once Hyly provides their BQ dataset name:

1. Render → Environment → add `BIGQUERY_HYLY_DATASET=<their_dataset>`
2. Re-run migration 0004 to install the real view replacing the stub:
   ```bash
   python3 migrations/_runner.py down 0003   # rolls back 0004
   python3 migrations/_runner.py up           # re-applies 0004 with real view
   ```
3. Backfill `hyly_property_id` onto each affected HubSpot company record
   (TBD — pattern mirrors aptiq_property_id backfill)
4. Verify the Convert channels table renders on the Loop subpage's
   Status tab

## Smoke tests after every deploy

```bash
# Webhook health
curl -s https://rpm-portal-server.onrender.com/api/webhooks/hubspot/health \
  | python3 -m json.tool

# Loop API liveness (returns empty/no-data, but should not 500)
curl -sH "X-Internal-Key: $INTERNAL_API_KEY" \
  'https://rpm-portal-server.onrender.com/api/loop/status?uuid=nonexistent-uuid'
```

## References

- `migrations/_runner.py` — migration runner
- ADR 0009 — Multifamily Loop architecture
- ADR 0010 — Loop Event Bus
- ADR 0014 — HubSpot Integration Surface
- ADR 0015 — Hyly Integration
- ADR 0018 — Portal Loop subpage
