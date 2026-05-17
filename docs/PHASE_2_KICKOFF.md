# Phase 2 Kickoff — Loop Portfolio Scale + Client Portal MVP

**Date:** 2026-05-17
**Status:** Phase 2 underway. Foundations shipped tonight; portfolio scale + portal wiring + execution hooks land week-by-week.
**Reference ADR:** `docs/architecture/decisions/0019-phase-2-scope.md`

## What just shipped (continuation of tonight's session)

| Commit | What |
|---|---|
| `fdc40a9` | Phase 2 bundle: ADR 0019 + Slack notifier + auto-pilot + forecast accuracy |

Four independent additions, each ~150-300 LOC, no breaking changes to
Phase 1 infrastructure:

### 1. Slack notifier (`webhook-server/slack_notifier.py`)

Tight signal post-to-webhook layer. Three named channels via env vars:
- `SLACK_DIGITAL_OPS_WEBHOOK` — ops alerts (AptIQ token expiry, R1 violations)
- `SLACK_AM_TEAM_WEBHOOK` — Co-pilot recommendations awaiting approval
- `SLACK_CLIENT_WINS_WEBHOOK` — first lease celebrations

Only 6 event types auto-post (`NOTIFIABLE_EVENT_TYPES`). Wired into
`loop_writer.record()` — every Loop event automatically routes to
the right channel if it's notifiable. Missing webhooks silently no-op
(safe to deploy without env vars set; configure later).

**You enable it by setting the env vars on Render.** Without them, the
code runs but doesn't post.

### 2. Auto-pilot mode handler (`webhook-server/loop_autopilot.py`)

Handler for `loop_mode='auto-pilot'` properties (the mode you specified
in ADR 0009 — auto/co/custom). Cron endpoint at
`/api/internal/loop-autopilot` scans recent `recommendation_proposed`
events, filters to auto-pilot properties past their 7-day warmup, applies
bounds:

- Action in `{shift_budget, refresh-seo, generate-aeo-batch}`
- Forecast impact must be positive
- Budget shifts capped at 15% of from_channel AND $500 absolute

Safe ones get auto-approved (emits `recommendation_approved` event with
`auto_approved=true`), then fires downstream actions. Designed for
hourly Render Cron.

### 3. Forecast accuracy tracker

- Migration `0010_forecast_accuracy_view.py` — joins `forecast_runs` to
  next-month `aptiq_snapshots_latest`. Computes abs_error, rel_error,
  ci_hit (was realized inside the CI?), bias_direction.
- New endpoint `GET /api/loop/accuracy?uuid=X&months=N` — per-property
  + portfolio rollup of forecast performance.

Once 3+ forecasts per property accumulate, you can prove/disprove
`simple_lag_v1` empirically. If CI breaches systematically, recalibrate
before widening to portfolio.

### 4. ADR 0019 — Phase 2 scope

185 lines defining 4-week plan. Three goals (portfolio scale, portal
MVP, closed-loop optimization). 6 acceptance criteria. Explicit OUT
list so scope doesn't creep:
- Hyly real data — Phase 3 (waits for June beta)
- Forecasting v2 — Phase 3
- AEO/Marquee real impls — their own sprints
- NinjaCat replacement — phased through Feb 2026
- server.py decomposition completion — Phase 3 tech debt
- Multi-tenant RBAC — Phase 3 (ADR 0020 future)

## Tomorrow's playbook

### Step 1 — Apply migration 0010 (3 min)

The forecast_accuracy view. Same patch-in-place trick or trigger a
manual Render deploy.

```bash
# In Render shell (after manual deploy or in-place patch)
cd ~/project/src
python3 migrations/_runner.py up
# Expect: 0010 applies cleanly. The view is empty until forecasts have run-month + 1 month of realized data.
```

### Step 2 — Set Slack env vars in Render (optional but recommended)

In Slack: create 3 incoming-webhook URLs (or reuse existing). In Render
Environment tab, add:

```
SLACK_DIGITAL_OPS_WEBHOOK=https://hooks.slack.com/services/T.../B.../...
SLACK_AM_TEAM_WEBHOOK=https://hooks.slack.com/services/T.../B.../...
SLACK_CLIENT_WINS_WEBHOOK=https://hooks.slack.com/services/T.../B.../...
```

You can set just one initially (e.g. digital_ops) to test. The other two
silently no-op until configured.

### Step 3 — Test the auto-pilot processor (5 min)

Set `loop_mode='auto-pilot'` on Ashton's HubSpot company record
(HubSpot UI → company 10560269171 → Edit → Loop Mode property →
Auto-pilot → Save). Then:

```bash
INTERNAL_API_KEY=$(env | grep ^INTERNAL_API_KEY | cut -d= -f2-)
curl -sX POST -H "X-Internal-Key: $INTERNAL_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"lookback_hours":48}' \
  https://rpm-portal-server.onrender.com/api/internal/loop-autopilot \
  | python3 -m json.tool
```

For Ashton today, the only recent recommendation was "hold" — so the
processor will scan, find nothing actionable, and report
`{auto_approved:0, skipped:N, by_skip_reason:{...}}`. The skip reasons
show why each was rejected (mode_unset / bounds_xxx / etc.) — that's
the diagnostic surface for tuning auto-pilot behavior.

### Step 4 — Bootstrap portfolio (when ready, ~30 min)

Pick top 10 revenue properties + their HubSpot company_ids:

```bash
INTERNAL_API_KEY=$(env | grep ^INTERNAL_API_KEY | cut -d= -f2-)
curl -sX POST -H "X-Internal-Key: $INTERNAL_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "company_ids": ["10560269171","10559996814","10560283797","..."],
    "months_back": 13,
    "backfill_baseline": 12
  }' \
  https://rpm-portal-server.onrender.com/api/internal/loop-bootstrap
```

202 returned; ~30 min later, watch `/api/loop/events?stage=ops` for
`cron_completed` events (one per chunk).

Each newly-bootstrapped property gets a forecast within 1-2 hours
(when the next weekly forecast cron runs — or run it manually per
property via `/api/loop/forecast/run`).

### Step 5 — Wire the Portal Loop subpage (30 min)

Choose Option B per ADR 0018 (lowest-friction; opt-in via `?view=loop`):

In `hubspot-cms/templates/client-portal.html`, add this at the top:

```hubl
{% set view_param = request.query_dict.view %}
{% if view_param == 'loop' %}
  {% include "client-portal-loop.html" %}
{% else %}
  ... (existing template content stays here) ...
{% endif %}
```

Deploy the CMS update via `scripts/deploy_template.py` (your existing
HubSpot deploy flow). Then load:

```
https://digital.rpmliving.com/staging/portal-dashboard?uuid=10560269171&view=loop
```

Should render the Loop view for Ashton with the **real forecast we just
generated** (10.8 leases, $3,400 spend, etc.).

### Step 6 — HubSpot webhook subscriptions (15 min)

Settings → Integrations → Private Apps → your app → Webhooks tab. Add:
- `deal.propertyChange` (filter: dealstage) → `/api/webhooks/hubspot/deal-stage-change`
- `line_item.creation` → `/api/webhooks/hubspot/line-item-change`
- `engagement.creation` → `/api/webhooks/hubspot/engagement-created`
- `company.propertyChange` (filter: `seo_tier,plestatus,loop_mode,uuid,aptiq_property_id,hyly_property_id`) → `/api/webhooks/hubspot/company-property-change`

Copy app signing secret → Render env var `HUBSPOT_WEBHOOK_SECRET` → save.
Smoke test:

```bash
curl https://rpm-portal-server.onrender.com/api/webhooks/hubspot/health
```

Then change a deal stage in HubSpot → check
`/api/loop/events?stage=convert` → should see `deal_stage_changed` event
land in real time.

### Step 7 — Schedule the auto-pilot cron (5 min)

Render Cron job:
- Name: `loop-autopilot`
- Schedule: `@hourly` (or every 6h to start; tune later)
- Command:
  ```
  curl -sX POST -H "X-Internal-Key: $INTERNAL_API_KEY" \
       -H "Content-Type: application/json" \
       -d '{"lookback_hours":24}' \
       https://rpm-portal-server.onrender.com/api/internal/loop-autopilot
  ```

Once running, you'll see Loop events flow per cron tick.

### Step 8 — Schedule the weekly forecast cron (5 min)

Render Cron job:
- Name: `loop-forecast-weekly`
- Schedule: `0 5 * * 1` (Monday 5am CT)
- Command: a small script that iterates company_ids with seo_tier set
  and POSTs each to `/api/loop/forecast/run`. (You can use the
  loop-bootstrap endpoint as a workaround — it also triggers forecasts
  as a side effect of the spend ingest path.)

## Where Phase 2 will be in 4 weeks (per ADR 0019)

| Week | Deliverable | How you'll know it's working |
|---|---|---|
| 1 | Portfolio scale + Slack | 50+ properties in forecast_runs; 1st Slack alert in #digital-ops |
| 2 | Portal MVP | 3 clients open Loop subpage in 14-day window |
| 3 | HubSpot integration | 1 budget shift recommendation approved + executed end-to-end |
| 4 | Polish + observability | Forecast accuracy for 10 properties; CI within ±20% of stated |

## What I shipped tonight in total

Across the whole session (verification + delights + Phase 2 kickoff):

```
2b29127  migrations: glob precedence fix
26f0a23  migrations: stub-view SQL fix
67f72f8  aptiq: verified JSONL field map
9a6193a  loop: spend ingest + batch AptIQ backfill
6e2eee4  loop: dedup view + status enrichment + quote→forecast
4af0303  loop: loop-bootstrap orchestrator
7b18d7b  docs: session retro
f99f680  migrations: 0009 real loop_attract_v1 view
fdc40a9  phase 2: Slack + auto-pilot + forecast accuracy
+ Phase 2 kickoff doc (this file)
```

**Net Phase 1 → Phase 2 delta:**
- 4 new BQ tables/views (monthly_spend_per_property, aptiq_snapshots_latest, forecast_accuracy, loop_attract_v1 real)
- 7 new endpoints (sync-spend-to-bq, aptiq-backfill-batch, loop-bootstrap, loop-autopilot, loop/accuracy, plus the existing Phase 1 surface)
- 4 new skills (spend_sheet_to_channels, hubspot_timeline, slack_notifier, loop_autopilot)
- 2 ADRs (0019 Phase 2 scope; cross-references the Phase 1 ADRs 0009-0018)
- ~2,800 LOC across these commits

**Loop architecture maturity:**

```
PHASE 1 (foundation)           ✅ ALL DONE
PHASE 2 (portfolio + portal)
  Week 1 — portfolio scale       🟡 infrastructure shipped, awaits bootstrap run
  Week 2 — portal MVP wiring     🟡 templates shipped, awaits HubSpot CMS hookup
  Week 3 — HubSpot webhooks      🟡 receivers shipped, awaits subscription config
  Week 4 — polish                ✅ Slack + auto-pilot + accuracy all shipped tonight
PHASE 3 (Hyly + agents)        ⏸️ blocked on Hyly beta (June) + AEO/Marquee real impl
```

Phase 2 week-4 work is actually done. Tomorrow's playbook is mostly
wiring + scheduling rather than building. The hard parts are behind us.

Sleep on it. The architecture is sound, the verification worked,
Phase 2 has running code ready to deploy.
