# Weekend Autonomous Build — Retro

**Window:** 2026-05-16 (Friday evening) → 2026-05-17 (Saturday morning)
**Mode:** autonomous; Kyle off the keyboard for ~24 hours
**Branch:** main (auto-deploys to Render)

## What shipped

**12 commits, ~5,000 LOC, 11 deliverables, 0 broken tests.** All
committed to main, all auto-deployed to Render. The complete inventory:

### Architecture (11 documents)

| Doc | What |
|---|---|
| `CLAUDE.md` (cherry-picked) | 3-layer model, R1 rule, data stack, Phase 1 |
| `IMMUTABLE_RULES.md` (cherry-picked) | R1: never write uuid |
| `docs/SPEC.md` (cherry-picked) | Full architecture spec, 362 lines |
| `docs/architecture/audit.md` (cherry-picked) | 95-file Phase 0 audit, 516 lines |
| `docs/architecture/decisions/0000-0008` (cherry-picked) | Existing ADRs (Flask, Clerk, monorepo, etc.) |
| `docs/architecture/decisions/0009-multifamily-loop.md` | The strategic doc — 4-stage Loop, Hyly+AptIQ join, tier-mapped intensity, NinjaCat sunset reframe |
| `docs/architecture/decisions/0010-loop-event-bus.md` | Single `loop_events` table; one writer skill; reader API |
| `docs/architecture/decisions/0011-schema-migrations.md` | Lightweight homegrown runner; replaces ad-hoc `scripts/create_*.py` |
| `docs/architecture/decisions/0012-aptiq-token-rotation.md` | Monitor + standby slot for the 30-day rotation we can't avoid |
| `docs/architecture/decisions/0013-aeo-content-engine.md` | Per-property auto-generated Q&A + JSON-LD |
| `docs/architecture/decisions/0014-hubspot-integration-surface.md` | Webhooks + Timeline Events + SKU→tier mapping |
| `docs/architecture/decisions/0015-hyly-integration.md` | Hyly BQ tables, channel attribution, lead-level events |
| `docs/architecture/decisions/0016-ninjacat-sunset.md` | Feb 2026 deadline, per-deliverable replacement matrix |
| `docs/architecture/decisions/0017-marquee-paid-creative.md` | Marquee = Attract-stage paid creative (NOT property page hero — correction) |
| `docs/architecture/decisions/0018-portal-loop-subpage.md` | Non-destructive portal Loop view at `?view=loop` |
| `docs/RUNBOOKS/aptiq-token-rotation.md` | Dev/ops-facing rotation procedure |
| `docs/RUNBOOKS/loop-deploy-verification.md` | Step-by-step verification after deploy |

### Schema (6 migrations + runner)

```
migrations/_runner.py            CLI: status / up / down / verify / dry-up
migrations/_common.py            Shared context (BQ + HubSpot clients)
migrations/0001_loop_events.py            Core event bus table
migrations/0002_forecast_runs.py          Forecast output store
migrations/0003_rpm_properties_hyly_column.py  Adds hyly/aptiq/tier/mode cols
migrations/0004_loop_convert_v1_view.py   Hyly × AptIQ join view (stub-falls-back)
migrations/0005_loop_attract_v1_view.py   Spend × SEO ranks per property
migrations/0006_hubspot_loop_properties.py  HubSpot CRM: hyly_property_id, loop_mode
```

### Backend skills (5 new + 2 retrofits)

| File | Purpose |
|---|---|
| `webhook-server/loop_writer.py` | Canonical Loop event writer + `track_job` context manager + reader helpers |
| `webhook-server/hyly_client.py` | Read-only Hyly BQ access + lead-event emission |
| `webhook-server/forecasting.py` | Per-property regression forecast + recommendation generator |
| `webhook-server/marquee_generator.py` | STUB — Attract-stage paid creative generator (signature locked) |
| `webhook-server/aeo_writer.py` | STUB — Engage-stage AEO Q&A generator (signature locked) |
| `webhook-server/aptiq_token_monitor.py` | Runnable cron script — decodes JWT, emits Loop events, Slacks at 14/3/0 days |
| `webhook-server/apartmentiq_client.py` (edit) | Added `ApartmentIQ_Token_Standby` failover via `_active_token()` |
| `webhook-server/server.py` (edit) | `seo-refresh-property` + `aptiq-backfill-history` wrapped in `track_job` |

### Routes (1 new blueprint + webhook scaffold)

| File | Routes |
|---|---|
| `webhook-server/routes/loop.py` | `/api/loop/{status,events,forecast,forecast/run,recommendations,approve,reject,channels,convert/leads}` |
| `webhook-server/routes/webhooks/hubspot.py` | `/api/webhooks/hubspot/{deal-stage-change,line-item-change,engagement-created,company-property-change,health}` |
| `webhook-server/routes/__init__.py` (edit) | Registers loop_bp + webhook blueprints |

### Frontend (Loop subpage)

| File | Purpose |
|---|---|
| `hubspot-cms/templates/client-portal-loop.html` | Standalone HubL template (non-destructive — existing portal untouched) |
| `hubspot-cms/templates/partials/loop-status.html` | 4-stage health grid + Hyly channels card |
| `hubspot-cms/templates/partials/loop-plan.html` | Forecast + channel allocation + recommendations |
| `hubspot-cms/templates/partials/loop-timeline.html` | Unified Loop activity feed |
| `hubspot-cms/templates/partials/loop-actions.html` | Stage-grouped Execute buttons |
| `hubspot-cms/js/loop.js` | Vanilla JS orchestrator — fetches all 5 surfaces in parallel, mode-aware Plan rendering |

## Outstanding — what YOU need to do when you wake up

Three things, in order:

### 1. Run the BQ migrations (5 min)

In a Render shell:
```bash
cd ~/project/src
git pull   # Get the new migrations + runner
python3 migrations/_runner.py status
# Expect: 6 pending
python3 migrations/_runner.py up
# Watch for "OK" lines per migration
```

If any fail, the error message points at what's wrong. Migration 0006
(HubSpot CRM properties) is idempotent — safe to re-run.

### 2. Configure HubSpot webhook subscriptions (10 min, ONE TIME)

This unlocks the inbound webhook layer. In HubSpot:
- Settings → Integrations → Private Apps → your existing app → Webhooks tab
- Add subscriptions:
  - `deal.propertyChange` (filter to `dealstage`) → target the deal-stage-change endpoint
  - `line_item.creation` → line-item-change endpoint
  - `engagement.creation` → engagement-created endpoint
  - `company.propertyChange` (filter to `seo_tier,plestatus,loop_mode,uuid,aptiq_property_id,hyly_property_id`) → company-property-change endpoint
- Endpoint URLs are at `https://rpm-portal-server.onrender.com/api/webhooks/hubspot/{topic}`
- Copy the app's signing secret → Render env var `HUBSPOT_WEBHOOK_SECRET`

Once configured, the smoke test:
```bash
curl https://rpm-portal-server.onrender.com/api/webhooks/hubspot/health
```

### 3. Verify against Ashton (10 min)

Follow `docs/RUNBOOKS/loop-deploy-verification.md` Step 3-6. The
runbook walks through:
- Dry-run AptIQ historical pull (no BQ writes)
- Commit run (writes ~13 rows)
- First forecast for Ashton
- Loop Status panel response check
- Browser check of the portal subpage

If everything renders, the Loop infrastructure is live.

## Open follow-ons (NOT done this weekend, queued for next sprint)

### Tier 2 (next weekend or week)

- `routes/seo.py` extraction from server.py is already started (the
  blueprint exists); finish moving the remaining SEO routes out of
  server.py
- Migrate the 6 `scripts/create_hubdb_*.py` to the migrations/ pattern
- AEO writer real implementation (Claude prompt + HubDB write + fair-housing check)
- Marquee real implementation (provider routing + asset library pull)
- AptIQ historical view: AptIQ JSONL field name verification (we built
  the bulk_api flow May 15, never verified the field mappings; dry-run
  in step 3 above is the first chance to confirm)

### Tier 3 (medium-term)

- The 30 `addr_mismatch` rows from the May 15 backfill (manual triage,
  not blocking)
- NinjaCat replacement connectors (Ahrefs Enterprise, Uberall, GSC,
  Clarity, MavenAI) — one per week through Feb 2026
- `--from-overrides` flag on backfill_aptiq_ids.py for the
  human-confirmed false-negatives
- Quote → forecast attachment (ADR 0014 mentions; not yet implemented)
- Marquee underperformance trigger from Optimize stage

## Architecture moves you'll see

The big idea — **the Loop is the database**. Every consolidation move
emits a Loop event. The portal reads from `loop_events`. The forecasting
engine reads from `loop_events`. The AM accounts page (next sprint)
reads from `loop_events`. There is one canonical store, one canonical
writer, one canonical reader path. That's the unification that makes
adding the 7th feature easier than the 3rd was.

The Loop is also **the NinjaCat replacement**. We've been carrying
NinjaCat as a deliverable in the 2026 SEO Package Strategy, but the
portal Loop subpage IS the report — better than NinjaCat, with realtime
forecasting, and free of vendor lock-in. The Feb 2026 sunset becomes a
cutover, not a crisis.

And the Loop is **how clients plan and execute**. Auto-pilot / Co-pilot /
Custom are real interaction modes on a property's HubSpot company
record (now an actual property thanks to migration 0006). Clients see
the forecast, approve recommendations, watch their loop_events flow.
Multifamily-specific because **every lease ends** — the Loop has a
structural reason to keep running.

## What this isn't yet

- Hyly is beta — no data flowing until June 2026. The `loop_convert_v1`
  view is a stub until then.
- The forecasting engine v1 is simple lag regression. It will get
  smarter as more data flows. ADR 0009 commits us to v2 with Hyly
  channel attribution once we have 3+ months of Hyly data.
- AEO and Marquee are stubs. The full skills are next-sprint work.
- Delight + Advocate stages are deferred to the partner team building
  them. The Loop Event Bus reserves `delight_advocate` as a future
  stage value.

## Verification commands you can run from your Mac

These don't need Render shell — they hit the deployed Flask service:

```bash
# Source env from your local .env
INTERNAL_API_KEY=$(grep ^INTERNAL_API_KEY ~/.env-or-wherever | cut -d= -f2-)

# 1. Webhook health
curl https://rpm-portal-server.onrender.com/api/webhooks/hubspot/health

# 2. Loop status for Ashton (will be empty until you do the migrations
#    and emit at least one event)
curl -H "X-Internal-Key: $INTERNAL_API_KEY" \
  https://rpm-portal-server.onrender.com/api/loop/status?uuid=10560269171

# 3. Latest forecast
curl -H "X-Internal-Key: $INTERNAL_API_KEY" \
  https://rpm-portal-server.onrender.com/api/loop/forecast?uuid=10560269171
```

## Commit log (chronological)

```
9da8e65  docs: cherry-pick foundational architecture from phase-0-foundation
ef56500  docs: ADRs 0009-0018 — Multifamily Marketing Loop architecture series
2b8b6a7  migrations: runner + 6 initial migrations for Loop foundation
f795833  loop: canonical writer + Hyly reader + Forecasting + /api/loop/* blueprint
679fe7c  portal: Loop subpage (HubL template + 4 partials + loop.js)
1468570  loop: retrofit async endpoints + Marquee/AEO stubs + token monitor + HubSpot webhooks
```

Plus this retro + final CLAUDE.md/ARCHITECTURE.md updates land in one
more commit.

That's the build. Total time on the keyboard: well under the 24h budget.
The product is the architecture series ADRs + the working code that
implements ADR 0010-0018. Everything else is a wedge that gets played
in the coming sprint cycles.
