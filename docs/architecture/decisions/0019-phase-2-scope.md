# ADR 0019 — Phase 2 scope: Client Portal MVP + Portfolio Scale

**Status:** Accepted
**Date:** 2026-05-17
**Authors:** Kyle Shipp, Claude

## Context

Phase 1 shipped the Multifamily Marketing Loop architecture and proved
it end-to-end against Ashton on West Dallas:

- 8 ADRs defining the Loop (0009 multifamily loop) and supporting decisions
- Loop Event Bus on BigQuery + writer skill + reader API
- 8 migrations applied (loop_events, forecast_runs, hyly stub views,
  monthly_spend_per_property, aptiq_snapshots_latest, real loop_attract_v1)
- AptIQ bulk_api/jobs flow integrated (13 months of Ashton history in BQ)
- Spend ingest from HubSpot deals → BQ channel rollup
- Forecasting Engine v1 (simple_lag_v1): forecast 10.8 leases for Ashton
  with $3,400/mo spend, 80% confidence band 7.6 – 14.0
- Quote → forecast attachment (auto-notes on HubSpot deals)
- 5 endpoints: sync-spend-to-bq, aptiq-backfill-batch, aptiq-backfill-
  history, loop-bootstrap, loop/forecast/run
- Portal Loop subpage template + 4 partials + loop.js

This works for 1 property. Phase 2 makes it work for the **portfolio**
and gives the **client** a useful surface to interact with it.

## Decision

Phase 2 has three goals, in priority order:

### Goal 1 — Portfolio Scale (week 1-2)

Get the Loop running for 50+ properties, not just Ashton. Concretely:

- `loop-bootstrap` validated against a 10-property batch
- Top revenue properties have full 13-month AptIQ history + spend snapshots
- A daily cron keeps `monthly_spend_per_property` current (new month rolls
  forward automatically)
- A weekly cron keeps `forecast_runs` fresh (each property forecasted
  every Monday)
- `rpm_properties` sync runs as a separate weekly cron (or daily once
  the HubSpot 429 risk is understood — see below)

### Goal 2 — Client Portal MVP (week 1-3)

Make the Loop visible to clients. Concretely:

- Wire the Portal Loop subpage into HubSpot CMS at
  `/portal-dashboard?uuid=X&view=loop` (Option B per ADR 0018)
- Loop Status panel renders the 4-stage health, real forecast number,
  and channel allocation table for ANY property the user is authorized
  for (existing HubSpot Memberships flow)
- Auto-pilot / Co-pilot / Custom modes wired: approve/reject buttons
  in the Plan tab actually fire the downstream automation per mode
- Loop Timeline shows the unified event feed (already implemented;
  just needs the wiring step)

### Goal 3 — Closed-Loop Optimization (week 2-4)

Connect the Loop to the things it should control. Concretely:

- **Slack notification layer** for high-signal events:
  - Recommendation proposed (Co-pilot users + AM channel)
  - Forecast deviates >25% from prior run (anomaly)
  - AptIQ token < 14 days from expiry (operational)
  - First lease signed for a Convert-tracking property (celebration)
- **Auto-pilot handler** — when `loop_mode='auto-pilot'` on a property,
  approved-by-rule recommendations apply themselves within bounded
  heuristics (shift max 15% of any channel; no single change > $500)
- **Forecast accuracy tracker** — backfill `forecast_runs.observed_leases`
  from the next-month AptIQ snapshot so we can measure CI calibration
  over time and prove the simple_lag_v1 methodology before investing in v2
- **Recommendation execution hooks** — approved budget shifts get
  written to Fluency sheet or queued as AM tasks; approved
  AEO/Marquee/refresh-content actions fire the actual skill

## What's explicitly OUT of Phase 2 scope

- Hyly real-data integration — Hyly's beta lands June 2026; tracked as
  a Phase 3 milestone. Stub view stays in place.
- Forecasting v2 (channel-attributed regression from Hyly lead-level
  data) — depends on Hyly being live; v1 is good enough through Phase 2.
- AEO real implementation — Stub remains; full Claude prompt + HubDB
  writer is a 1-week build that lands when Standard-tier release is
  scheduled.
- Marquee real implementation — Same as AEO: stub + signature locked,
  full impl is its own sprint when paid creative is in priority.
- NinjaCat replacement connectors — Phased plan in ADR 0016; first
  connector (GSC direct) starts in Phase 2 only if time, else Phase 3.
- Server.py decomposition completion — Loop blueprint is extracted;
  the rest is tech-debt cleanup, not user-facing value. Schedule for
  Phase 3.
- Multi-tenant authorization tightening — Phase 2 still uses the
  X-Portal-Email trust model. ADR 0020 (future) addresses RBAC.

## Sequencing (with dependencies)

```
WEEK 1 — Portfolio scale + Slack
  □ Slack notifier module (no deps)
  □ Auto-pilot mode handler (no deps)
  □ Forecast accuracy tracker (depends on more monthly forecast runs accumulating)
  □ Trigger 10-property loop-bootstrap (depends on sync-properties-to-bq
    succeeding without HubSpot 429)
  □ Verify each onboarded property has a non-zero forecast
  □ Wire weekly forecast cron (depends on auto-deploy of Phase 1 code)

WEEK 2 — Portal MVP
  □ HubSpot CMS wiring (Option B: ?view=loop in client-portal.html)
  □ Loop Status panel renders for at least 3 properties live
  □ Loop Timeline shows real events from the past 2 weeks
  □ Plan tab approve/reject buttons fire correctly
  □ Cross-browser smoke test (Chrome, Safari, mobile Safari)

WEEK 3 — HubSpot integration + execution hooks
  □ HubSpot webhook subscriptions configured + HUBSPOT_WEBHOOK_SECRET
  □ Verify deal-stage-change webhook lands in loop_events
  □ Verify line-item-change webhook updates seo_tier on the company
  □ Recommendation execution hooks: budget shifts → Fluency / AM task

WEEK 4 — Polish + observability
  □ Slack alerts wired for the 4 high-signal event types
  □ Forecast accuracy report (any property with ≥ 3 forecasts has
    accuracy ratio computed)
  □ Phase 2 retro + roadmap for Phase 3 (Hyly, AEO, Marquee)
```

## Success criteria (acceptance for Phase 2)

Phase 2 is complete when:

1. **≥ 50 properties** have at least one forecast row in `forecast_runs`
2. **At least 3 clients** have opened the Loop subpage in their portal
   in a 2-week window
3. **At least 1 budget shift recommendation** has been approved and
   executed end-to-end (HubSpot webhook → loop_event → downstream
   action → Fluency write)
4. **Forecast accuracy** measured for ≥ 10 properties with ≥ 3
   forecasts each, ci calibration within ±20% of stated confidence
5. **No P0/P1 incidents** rooted in Loop infrastructure in the prior
   30 days
6. **Slack signal volume**: < 5 alerts/day in `#digital-ops` (anything
   higher = noise, recalibrate thresholds)

## Risks + mitigations

| Risk | Mitigation |
|---|---|
| HubSpot 429 blocks portfolio bootstrap | Bootstrap in waves; add exponential backoff to `_search_companies`; consider HubSpot Custom Properties batch read endpoint as alternative |
| Hyly beta slips past June 2026 | Loop already works without it. Phase 2 success criteria don't require Hyly. v1 forecast methodology stays through Phase 2. |
| Forecast v1 calibration is wrong | The accuracy tracker is in scope precisely to detect this. If CI breaches systematically, recalibrate before widening to portfolio. |
| Slack alert fatigue | Tight signal list (4 event types); volume ceiling in success criteria (< 5/day). |
| Multi-tenant data leakage via the portal | Phase 2 keeps the existing HubSpot Memberships trust; ADR 0020 addresses RBAC in Phase 3. Document the gap explicitly. |

## What this weekend's autonomous build will ship toward Phase 2

While Kyle's offline tonight, the autonomous build adds:

1. Slack notifier module (`webhook-server/slack_notifier.py`) — pure
   code, no deps. Auto-wired into `aptiq_token_monitor`,
   `loop_writer.track_job` failures, and forecast deviation detection.
2. Auto-pilot mode handler (`webhook-server/loop_autopilot.py`) — picks
   up `recommendation_proposed` events for `loop_mode='auto-pilot'`
   properties, applies bounded changes, emits `recommendation_approved`
   events as if the property owner had clicked Approve.
3. Forecast accuracy tracker (migration + skill) — `forecast_accuracy`
   table joins forecast_runs to next-month aptiq_snapshots and computes
   per-property accuracy ratio. Once the data accumulates over months,
   we can prove or disprove the simple_lag_v1 methodology.
4. Loop Event Bus subscriber pattern — generic `subscribe_to_events()`
   helper so future agents (AEO writer, Marquee gen, custom Optimize
   agents) can react to events without modifying the writer.

Each is ~150-300 LOC, narrow scope, no breaking changes. Phase 1
infrastructure stays untouched.

## References

- ADR 0009 — Multifamily Loop architecture (the spine Phase 2 builds on)
- ADR 0010 — Loop Event Bus (Phase 2 adds subscribers)
- ADR 0014 — HubSpot Integration Surface (Phase 2 wires the webhooks)
- ADR 0015 — Hyly Integration (Phase 3, not Phase 2)
- ADR 0018 — Portal Loop subpage (Phase 2 wires it into HubSpot CMS)
- `docs/SESSION_RETRO_2026-05-17.md` — Phase 1 verification playbook
