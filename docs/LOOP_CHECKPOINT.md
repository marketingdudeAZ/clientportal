# 🔖 Loop Project — Checkpoint / Resume Point

**Paused:** 2026-05-17
**Reason:** Context switch back to the Client Brief project.
**Status when paused:** Phase 1 verified end-to-end; Phase 2 infrastructure shipped; awaiting human config steps.

---

## TL;DR — when you come back, read these 3 files in order

1. **`docs/LOOP_CHECKPOINT.md`** ← you are here (the 60-second resume)
2. **`docs/OUTSTANDING_WORK.md`** ← the prioritized task list (start at task #1)
3. **`docs/PHASE_2_KICKOFF.md`** ← the 8-step playbook with exact commands

Architecture context (only if you need the "why"):
- `docs/architecture/decisions/0009-multifamily-loop.md` — the spine
- `docs/architecture/decisions/0019-phase-2-scope.md` — current phase scope
- `docs/SESSION_RETRO_2026-05-17.md` — what the verification proved

---

## Where we left off (one paragraph)

The Multifamily Marketing Loop is **architecturally complete and verified
end-to-end against one property (Ashton on West Dallas, company
10560269171)**. Ashton has 13 months of real AptIQ history in BigQuery,
$3,400/mo of spend ingested and channel-mapped, and a live forecast of
**10.8 leases / 30 days** (80% CI: 7.6–14.0). Every piece of
infrastructure for portfolio scale + the client portal MVP is shipped
and deployed. What remains is **human configuration**, not engineering:
apply one migration, set Slack env vars, wire one HubL include,
configure HubSpot webhook subscriptions, schedule two crons, and run
one bootstrap curl for the top-10 properties.

---

## The exact next action when resuming

Open `docs/OUTSTANDING_WORK.md`, do tasks **#1 → #6** (the 🔴 BLOCKERs).
Total human time: ~1.5 hours. Each unlocks the next. After those six,
Phase 2 is operationally live.

The very first command (everything else depends on it):

```bash
# In a fresh Render shell
cd ~/project/src
# Render auto-deploys main; confirm latest commit is present
git --no-pager log --oneline -3   # expect 5a7e9de at/near top
python3 migrations/_runner.py up   # applies migration 0010 (forecast_accuracy)
```

If Render shell can't `git fetch` (the recurring SSH issue), trigger a
Manual Deploy from the Render dashboard first, then open a fresh shell.

---

## What's DONE (do not redo)

| Area | State |
|---|---|
| ADRs 0009–0019 (11 architecture decisions) | ✅ Committed to main |
| Migration runner + migrations 0001–0010 | ✅ 0001–0009 applied on Render; **0010 pending apply** |
| Loop Event Bus (`loop_writer.py`) | ✅ Live, observable, verified |
| AptIQ bulk_api/jobs flow | ✅ Verified — 13 mo Ashton history in BQ |
| Spend ingest (`sync-spend-to-bq`) | ✅ Verified — Ashton $3,400/mo |
| Forecasting Engine v1 (`simple_lag_v1`) | ✅ Verified — Ashton 10.8 leases |
| Loop API (`/api/loop/*`) | ✅ status/events/forecast/recommendations/approve/reject/accuracy/channels/forecasts-batch |
| Slack notifier | ✅ Code shipped; **awaits env vars** |
| Auto-pilot handler + cron endpoint | ✅ Code shipped; **awaits cron schedule + a property in auto-pilot mode** |
| Forecast accuracy view + endpoint | ✅ Code shipped; **awaits migration 0010** |
| RedLight v2 PDF Loop section | ✅ Auto-active on next report gen |
| Portal Loop subpage (template + 4 partials + JS) | ✅ Built; **awaits HubSpot CMS wiring** |
| Portal dashboard Loop card (partial + JS) | ✅ Built; **awaits 1-line include** |
| HubSpot webhook receivers | ✅ Built; **awaits subscription config in HubSpot UI** |
| Batch endpoints (aptiq-backfill-batch, loop-bootstrap) | ✅ Live; awaits a portfolio run |

## What's PENDING (human config, not code)

The 🔴 BLOCKER list from OUTSTANDING_WORK.md tasks #1–6:

1. Apply migration 0010 (3 min)
2. Set 3 Slack webhook env vars in Render (5 min)
3. Add `{% include "partials/loop-forecast-card.html" %}` + script tag to client-portal.html (5 min)
4. Wire Loop subpage: `{% if view_param == 'loop' %}` branch in client-portal.html (30 min)
5. Configure 4 HubSpot webhook subscriptions + `HUBSPOT_WEBHOOK_SECRET` (15 min)
6. Schedule 2 Render Crons (auto-pilot hourly, forecast weekly) (10 min)

Then 🟠 HIGH: run `loop-bootstrap` for top-10 properties (~30 min mostly wait).

## What's BLOCKED (external dependency, don't touch)

- **Hyly integration** — waits for Hyly beta (June 2026). Stub view in place.
- **Forecasting v2** — depends on Hyly + 3 months trailing data.
- **AEO / Marquee real impls** — stubs locked; their own sprints when scheduled.
- **NinjaCat replacement** — phased through Feb 2026 per ADR 0016.

---

## Known gotchas for whoever resumes

1. **Render shell can't `git fetch`** (SSH auth not carried). Workaround:
   Manual Deploy from dashboard → fresh shell. Or in-place patch via
   Python heredoc. This bites every new shell session.
2. **AptIQ token expires every 30 days.** Current token valid through
   **2026-06-16**. Monitor: `webhook-server/aptiq_token_monitor.py`
   (runnable, not yet scheduled). Runbook:
   `docs/RUNBOOKS/aptiq-token-rotation.md`.
3. **AptIQ percentages are decimals** (0.92 = 92%); CSV fallback returns
   whole numbers (92). Unit normalization is deferred (OUTSTANDING_WORK
   task #15). Consumers must tolerate both scales for now.
4. **HubSpot 429s** during portfolio scans. `sync-properties-to-bq`
   got rate-limited during verification. `loop_attract_v1` was
   reworked (migration 0009) to drive from spend table instead of
   rpm_properties to sidestep this. rpm_properties still empty until a
   successful sync — metadata columns (name/market/seo_tier) are NULL
   in the view until then, but forecasts still compute correctly.
5. **aptiq_snapshots has duplicate rows** (~27 for 13 months on Ashton).
   The `aptiq_snapshots_latest` view dedupes; forecasting reads the
   view. Raw-table cleanup is OUTSTANDING_WORK task #26 (cosmetic).

---

## Commit range for this whole effort

```
9da8e65  docs: cherry-pick foundational architecture
ef56500  docs: ADRs 0009-0018
2b8b6a7  migrations: runner + 6 initial migrations
f795833  loop: writer + Hyly reader + Forecasting + blueprint
679fe7c  portal: Loop subpage
1468570  loop: retrofit async endpoints + stubs + webhooks
a9e7837  docs: runbooks + weekend retro
2b29127  migrations: glob precedence fix
26f0a23  migrations: stub-view SQL fix
67f72f8  aptiq: verified JSONL field map
9a6193a  loop: spend ingest + batch AptIQ backfill
6e2eee4  loop: dedup view + status enrichment + quote→forecast
4af0303  loop: loop-bootstrap orchestrator
7b18d7b  docs: session retro
f99f680  migrations: 0009 real loop_attract_v1 view
fdc40a9  phase 2: Slack + auto-pilot + forecast accuracy
120e379  docs: Phase 2 kickoff
5a7e9de  loop portal: RedLight v2 PDF + dashboard card + batch API
```

All on `main`. All deployed (Render auto-deploy). Nothing stranded on
a branch. Working tree clean except `.claude/worktrees/` (tooling, not ours).

---

## Resume checklist (paste this into the next Loop session)

```
[ ] Read docs/OUTSTANDING_WORK.md — start at task #1
[ ] Confirm Render deployed commit 5a7e9de (or later)
[ ] Apply migration 0010
[ ] Set Slack env vars (optional but high-leverage)
[ ] Wire loop-forecast-card include into client-portal.html
[ ] Wire ?view=loop branch into client-portal.html
[ ] Configure HubSpot webhook subscriptions
[ ] Schedule auto-pilot + forecast crons
[ ] Run loop-bootstrap for top-10 properties
[ ] Verify a non-zero forecast for 3+ of them
```

Loop work is in a clean, safe, fully-documented paused state.
Switch away with confidence — nothing will rot.
