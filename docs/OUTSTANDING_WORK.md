# Outstanding Work — Single Source of Truth

**Last updated:** 2026-05-20 (paused mid-Customer-Match rollout)
**This file:** the canonical list of "what's left." When Kyle asks "what do I still need to do?" — this is the answer.

## How to read this

- **🔴 BLOCKER** — Phase 2 success depends on this; do it first
- **🟠 HIGH** — High leverage, low effort
- **🟡 MEDIUM** — Should happen this quarter
- **🟢 LOW** — Nice-to-have / backlog
- **⏸️ BLOCKED** — Waiting on external dependency (Hyly beta, vendor decision, etc.)

---

## 🟠 Customer Match — Active rollout (paused 2026-05-20)

**Status:** Pipeline plumbing proven end-to-end; ~50% through the Google
Ads activation. Audience for paid team is **NOT yet usable** — at least
3 more steps + 24-48h Google processing window.

**Full reference:** `docs/RUNBOOKS/google-ads-data-manager-setup.md`

### What's done

| Step | Status |
|---|---|
| Code shipped (migration 0011, customer_match_export, sync + build endpoints, NinjaCat backfill script, runbook) | ✅ commits `809718b`, `dc0c911`, `3cda2a4`, plus migration 0009 recovery |
| Migration 0011 applied (`hubspot_contacts_for_match` table + dedup view) | ✅ |
| GCS bucket `rpm-ads-audiences` created + lifecycle + IAM grant to `rpm-portal-bq-reader@...` | ✅ |
| Smoke test 50 contacts end-to-end: HubSpot → BQ → CSV → GCS | ✅ All 4 curls green; CSV format verified valid by inspection |
| Data Quality probe: 100% email coverage, 62% phone, 100% names | ✅ Predicted match rate 60-80% |
| Google Ads Data Manager connection at MCC `3158541695` | ✅ Connected to `gs://rpm-ads-audiences/customer_match/18185/latest.csv`, first scheduled run May 21 12:28 PT |

### What's left — in order (~30 min active + 24-48h Google wait)

| # | Priority | Task | Time | Notes |
|---|---|---|---|---|
| CM-1 | 🔴 | **Verify Data Manager settings before audience creation** | 5 min | Click Edit update method → switch from "Add more customers" → **"Replace existing customers"** (so dropped contacts also drop from audience). Click Edit mapping → confirm hashing is set to **"Data is already hashed"** (else Google re-hashes our hashes → 0% match). Both are silent failures if wrong. |
| CM-2 | 🔴 | **DEBUG: full async sync writes nothing** | varies | Async sync endpoint returns 202 "started" but BQ row count stays at 50 (the test sample from May 20). No `loop_events` recorded for `hubspot_list_to_bq` runs. Likely daemon thread silent failure. Next debug: grep Render logs (search `hubspot_list_to_bq`) for traceback. Code path: `webhook-server/server.py:sync_hubspot_list_to_bq → _do_sync`. |
| CM-3 | 🔴 | **Create the Customer Match audience in Google Ads** | 5 min | MCC → Tools → Audience manager → Segments → New segment → Customer list. Name: `RPM Living — Current Residents (Marketable)`. Source: Data Manager connection from Step 4. Membership: 540 days. Status will show "Processing" 6-24h. |
| CM-4 | 🔴 | **Schedule 2 Render Cron Jobs** | 5 min | Cron A `customer-match-sync`: `0 9 * * *` (04:00 CT) → curl sync endpoint. Cron B `customer-match-csv-build`: `30 9 * * *` (04:30 CT) → curl build endpoint. Both write loop_events. See runbook §6 for exact commands. |
| CM-5 | ⏳ | **Wait 24-48h for Google's first match processing** | passive | Audience size populates in Google Ads UI once Google finishes hash-matching. Only THEN can paid team meaningfully use it. |
| CM-6 | 🟠 | **Ping paid media team to attach the audience** | 5 min | When size populates, the audience auto-appears in every child account under MCC `3158541695`. Paid adds it as Targeting / Observation / Similar Audience seed per campaign. |

### Phase 2 (per-property fanout) — queued, code ready

| # | Priority | Task | Notes |
|---|---|---|---|
| CM-7 | 🟡 | Run `scripts/backfill_google_ads_cid.py` against NinjaCat CSV | Populates `google_ads_customer_id` on every HubSpot company (733 properties under MCC). Dry-run first (`--csv ~/Downloads/advertiser_networks-list (11).csv`); `--commit` after spot-checking. Strips dashes from UI-formatted CIDs (e.g., `486-980-3719` → `4869803719`). |
| CM-8 | 🟡 | Extend build endpoint to support per-CID grouping | Once `google_ads_customer_id` is populated, modify `/api/internal/build-customer-match-csv` to optionally group by CID and write one CSV per child account. Then 700 Data Manager connections (or use Google Ads API to push per-account) for per-property audiences. ~1-day code lift. |

### Reference

- Pipeline modules: `webhook-server/customer_match_export.py`, `webhook-server/server.py` (sync + build endpoints)
- Migration: `migrations/0011_hubspot_contacts_for_match.py`
- Phase-2 prep script: `scripts/backfill_google_ads_cid.py`
- Runbook: `docs/RUNBOOKS/google-ads-data-manager-setup.md`
- HubSpot list ID: **18185** (`Customer Match — Marketable Residents`)
- MCC: **3158541695**
- GCS bucket: **rpm-ads-audiences**, path `customer_match/18185/{date}.csv` + `latest.csv`
- BQ project/dataset: `rpm-portal-492523.rpm_portal`
- Service account: `rpm-portal-bq-reader@rpm-portal-492523.iam.gserviceaccount.com`

---

## Phase 2 — Active (4-week target per ADR 0019)

### 🔴 BLOCKER — Configuration only (you, not me)

| # | Task | Time | Notes |
|---|---|---|---|
| 1 | Apply migration 0010 (forecast_accuracy view) | 3 min | `python3 migrations/_runner.py up` after Render auto-deploys commit `120e379` |
| 2 | Set Slack webhook env vars in Render | 5 min | `SLACK_DIGITAL_OPS_WEBHOOK`, `SLACK_AM_TEAM_WEBHOOK`, `SLACK_CLIENT_WINS_WEBHOOK`. Without these, the Slack code runs but doesn't post. |
| 3 | Wire Portal Loop subpage into HubSpot CMS | 30 min | Option B per ADR 0018: add `{% if view_param == 'loop' %}` branch to `hubspot-cms/templates/client-portal.html`. Then `scripts/deploy_template.py`. |
| 4 | Configure HubSpot webhook subscriptions | 15 min | Settings → Private Apps → Webhooks. Subscribe to 4 topics → endpoints at `/api/webhooks/hubspot/{topic}`. Set `HUBSPOT_WEBHOOK_SECRET` in Render. |
| 5 | Schedule auto-pilot cron in Render | 5 min | Hourly. Endpoint: `POST /api/internal/loop-autopilot` with `lookback_hours: 24` |
| 6 | Schedule weekly forecast cron in Render | 5 min | Monday 5am CT. Iterates company_ids and POSTs to `/api/loop/forecast/run`. |

### 🟠 HIGH — Portfolio rollout (one curl, one wait)

| # | Task | Time | Notes |
|---|---|---|---|
| 7 | Bootstrap top-10 properties via `/api/internal/loop-bootstrap` | 30 min (mostly wait) | Pick top revenue. Returns 202; watch `/api/loop/events?stage=ops` for progress. Each property gets AptIQ history + spend rows + initial forecast. |
| 8 | Spot-check forecasts for the 10 properties via `/api/loop/forecast?uuid=X` | 15 min | Sanity-check non-zero numbers + reasonable CI bounds. |
| 9 | Set `loop_mode` HubSpot property on those 10 (auto-pilot or co-pilot) | 10 min | UI edit per property. Co-pilot recommended for most; auto-pilot only after observing for a week. |

### 🟡 MEDIUM — Phase 2 polish

| # | Task | Time | Notes |
|---|---|---|---|
| 10 | Triage the 30 AptIQ `addr_mismatch` rows from May 15 backfill | 1 hour | At least 2 confirmed false negatives (Lakecrest, Highlands at the Lake). Spot-check the rest, add a `--from-overrides` flag, commit. |
| 11 | Verify Slack signal volume after 1 week | 10 min | If > 5/day, recalibrate `NOTIFIABLE_EVENT_TYPES`. |
| 12 | Build `monthly_spend_per_property` daily snapshot cron | 1 hour | Currently spend is only written when sync-spend-to-bq is manually triggered. Should run daily so new month rolls forward automatically. |
| 13 | Build `aptiq_snapshots` monthly auto-refresh cron | 2 hours | Current month only — supplements bootstrap historical pulls with ongoing freshness. Per-property single-month call ~30 sec. |
| 14 | Fluency execution hook for auto-pilot budget shifts | 2 hours | Currently auto-pilot logs intent; needs the actual Fluency sheet write to be wired. |

---

## Phase 1 — Deferred items (from autonomous build)

| # | Task | Priority | Notes |
|---|---|---|---|
| 15 | Unit normalization migration (AptIQ decimals vs CSV percentages) | 🟡 | Bulk_api returns 0.92, CSV returns 92. Decide which scale wins in BQ; migrate existing rows + update writers. Will affect Loop view display logic. |
| 16 | Server.py decomposition completion | 🟡 | Currently ~5,500 lines / 60+ routes. Loop blueprint extracted; remaining `/api/redlight*`, `/api/fluency*`, `/api/internal/*` could go to their own files. |
| 17 | Migrate 6 existing `scripts/create_hubdb_*.py` to migrations pattern | 🟢 | Cleanup; not blocking. Pattern: ADR 0011. |
| 18 | Per-property HubSpot Memberships authorization on `/api/loop/*` | 🟡 | Currently uses X-Portal-Email trust. ADR 0020 (future) covers proper multi-tenant RBAC. |
| 19 | AEO writer real implementation | 🟢 (queued for Standard tier release) | Stub in place with signature locked. Full impl: Claude prompt + HubDB write + fair_housing check. ~1 week. |
| 20 | Marquee real implementation | 🟢 (queued for Premium tier release) | Stub with signature locked. Full impl: provider routing + asset library pull + winning-pattern bias. ~1 week. |

---

## Phase 3 — Future (blocked or scheduled)

| # | Task | Status | Notes |
|---|---|---|---|
| 21 | Hyly real-data integration | ⏸️ | Awaits Hyly beta (June 2026). Stub `loop_convert_v1` view in place; set `BIGQUERY_HYLY_DATASET` env var + re-run migration 0004 when their dataset name lands. |
| 22 | Forecasting v2 (channel-attributed regression from Hyly leads) | ⏸️ | Depends on Hyly being live + 3 months of trailing data. ADR 0009 commits to this. |
| 23 | NinjaCat replacement connectors | 🟡 | Phased through Feb 2026 per ADR 0016. Start: GSC API direct + Clarity API (both $0 cost). |
| 24 | Multi-tenant RBAC for portal Loop view | 🟡 | ADR 0020 (TBD). Current X-Portal-Email model good through Phase 2; tighter authorization for Phase 3. |
| 25 | AptIQ token: ask vendor for 90-day expiry | 🟢 | Email AptIQ support during next rotation cycle (June 2026). |

---

## Cross-cutting cleanups (do when bored)

| # | Task | Why |
|---|---|---|
| 26 | Dedupe existing rows in `aptiq_snapshots` table (not just the view) | Cleanup; view masks the issue. ~50 LOC SQL DELETE script. |
| 27 | Add exponential backoff to `_search_companies` in portfolio.py | HubSpot 429 risk during portfolio scans. ~20 LOC. |
| 28 | Drop the `data_months: 27` inflation in forecasting.get_trailing_data | Read from `aptiq_snapshots_latest` view (already exists). Already partially done; verify. |
| 29 | Render shell: document the recurring SSH/git fetch failure pattern | Each new shell session hits it. One-time runbook update. |
| 30 | Daily AptIQ token expiry monitor cron | Currently runnable but not scheduled. Add Render Cron entry. |

---

## State of the platform as of this writing

```
Loop Event Bus          ✅ live, observable, 14+ events written today
Migrations runner       ✅ 10 migrations applied, schema_migrations tracking
AptIQ bulk_api flow     ✅ verified end-to-end against Ashton
Forecasting v1          ✅ Ashton: 10.8 leases forecast w/ 80% CI 7.6-14.0
Spend ingest            ✅ Ashton: 13 rows, $3,400/mo (paid_search $2,900 + seo $500)
Slack notifier          ✅ committed, awaits webhook env vars
Auto-pilot handler      ✅ committed, awaits 1st auto-pilot property
Forecast accuracy view  ✅ committed, awaits migration 0010 apply
Portal Loop subpage     🟡 ready, awaits HubSpot CMS wiring
HubSpot webhooks        🟡 ready, awaits subscription config in HubSpot UI
Hyly integration        ⏸️ awaits June 2026 beta
NinjaCat sunset         ⏸️ Feb 2026; phased replacement plan in ADR 0016
```

## Quick-reference command palette

```bash
# Apply pending migrations
python3 migrations/_runner.py up

# Check what's applied
python3 migrations/_runner.py status

# Single-property AptIQ history (dry-run first)
curl -X POST -H "X-Internal-Key: $INTERNAL_API_KEY" -H "Content-Type: application/json" \
  -d '{"company_id":"...","months_back":13,"dry_run":true}' \
  https://rpm-portal-server.onrender.com/api/internal/aptiq-backfill-history

# Single-property spend ingest with baseline backfill
curl -X POST -H "X-Internal-Key: $INTERNAL_API_KEY" -H "Content-Type: application/json" \
  -d '{"company_id":"...","backfill_baseline":12}' \
  https://rpm-portal-server.onrender.com/api/internal/sync-spend-to-bq

# Multi-property AptIQ batch (10 properties in parallel chunks)
curl -X POST -H "X-Internal-Key: $INTERNAL_API_KEY" -H "Content-Type: application/json" \
  -d '{"company_ids":["...","...","..."],"months_back":13}' \
  https://rpm-portal-server.onrender.com/api/internal/aptiq-backfill-batch

# Full property bootstrap (rpm_properties + spend + AptIQ batch in one call)
curl -X POST -H "X-Internal-Key: $INTERNAL_API_KEY" -H "Content-Type: application/json" \
  -d '{"company_ids":["...","...","..."],"months_back":13,"backfill_baseline":12}' \
  https://rpm-portal-server.onrender.com/api/internal/loop-bootstrap

# Run forecast for a property
curl -X POST -H "X-Internal-Key: $INTERNAL_API_KEY" -H "Content-Type: application/json" \
  -d '{"company_id":"...","seo_tier":"Standard"}' \
  https://rpm-portal-server.onrender.com/api/loop/forecast/run

# Read latest forecast
curl -H "X-Internal-Key: $INTERNAL_API_KEY" \
  'https://rpm-portal-server.onrender.com/api/loop/forecast?uuid=...'

# Read forecast accuracy (portfolio or single property)
curl -H "X-Internal-Key: $INTERNAL_API_KEY" \
  'https://rpm-portal-server.onrender.com/api/loop/accuracy?uuid=...&months=6'

# Trigger auto-pilot cron (hourly via Render Cron in production)
curl -X POST -H "X-Internal-Key: $INTERNAL_API_KEY" -H "Content-Type: application/json" \
  -d '{"lookback_hours":24}' \
  https://rpm-portal-server.onrender.com/api/internal/loop-autopilot
```

---

## When you ask "what should I do next?"

The answer is **task #1 through #6 above**, in order. Each unlocks the next.

Once those 6 are done (total < 1.5 hours of human time), Phase 2 is operationally live and the Loop is running for 10 properties with real forecasts, Slack alerts, and auto-pilot mode where you want it.
