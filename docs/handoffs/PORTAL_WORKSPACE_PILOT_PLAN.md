<!-- /autoplan restore point: /Users/kyleshipp/.gstack/projects/marketingdudeAZ-clientportal/feature-portal-workspace-autoplan-restore-20260916-144652.md -->
# Portal Workspace: from local preview to a client testing pilot

**Owner:** Kyle Shipp · **Written:** 16 Sept 2026 · **Branch:** `feature/portal-workspace` (140 commits ahead of `main` @ `09941e2`, 0 behind, fast-forward mergeable, not pushed)
**Build spec:** `docs/handoffs/PORTAL_WORKSPACE_BUILD_PLAN.md` (what was built). This doc is only about getting it in front of testers.

## The question

Are we ready to go to testing with real clients, and what has to happen first?

## Where it stands (verified 16 Sept 2026)

- **Tests:** 905 workspace tests pass (`pytest tests -k workspace`, 14s).
- **Runs locally only.** `scripts/preview_workspace.py` serves fixtures on :5057. A read-only artifact demo exists. Nothing is deployed.
- **Served by Flask on Render** at `GET /workspace` (`routes/portal_ui.py:113`), same origin as the API, so no HubSpot CMS wrapper is needed. `Procfile` → `python start.py` (waitress, 16 threads, ~107s cache warm on a boot thread).
- **Gated by** `WORKSPACE_ENABLED` (every route 404s without it) and the `workspace` feature key (beta: staff plus HubDB-allowlisted clients).
- **The 9/16 review** logged 28 criticals; 8 fixed, the rest skipped. Some were addressed by later perf commits (6060d45, 81b3d5c).

## What a real client needs to get in

1. **A Clerk account on a production instance.** Only a `pk_test_` dev instance exists (`amused-tiger-74.clerk.accounts.dev`). Clerk production needs a domain RPM controls; today the service answers at `rpm-portal-server.onrender.com`.
2. **A HubDB access row:** `email`, `role=client`, `beta_features=workspace[,ask,portal_tickets]`, `companies=<ids>` (`feature_access.py:255-281`; 60s cache).
3. **Render env:** `WORKSPACE_ENABLED=true`, `CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY`, `HUBDB_PORTAL_ACCESS_TABLE_ID`, `INTERNAL_API_KEY`, BigQuery creds. `WORKSPACE_REQUIRE_PROOF` defaults to true.

Signed links are staff-only (`skills/workspace_links.py:87-91`), so they cannot substitute for Clerk for clients.

## Blockers found (pilot cannot start with real clients)

| # | Blocker | Evidence |
|---|---|---|
| B1 | **Report API accepts an asserted email.** `/api/workspace/report` lives on its own blueprint, so `_require_proof` (a `workspace_bp.before_request`) never runs. `X-Portal-Email: anyone@rpmliving.com` resolves to internal and reads any property's report. | `routes/workspace_report.py:63-75`; `routes/workspace.py:105-129`; test passes on header alone at `tests/test_workspace_report_routes.py:74` |
| B2 | **New request is shown to clients but refuses them.** `POST /requests` and `/media-plan/regenerate` go through the ticket gate that defaults to `rpmliving.com`. The draft step spends a model call before the 403. | `routes/workspace.py:537,751`; `routes/portal_tickets.py:101`; `workspace.html:1185` |
| B3 | **No production sign-in.** Clerk dev instance only, no custom domain on the Render service. | `clerk_auth.py:47-58,86,111-147`; `client-portal.html:29` |

## Risks (pilot can run, but these embarrass or harm)

| # | Risk | Evidence |
|---|---|---|
| R1 | **Client approvals do real work.** Budget-change approval creates a HubSpot deal + ClickUp task + HubSpot task; strategy, call prep, content brief and Fair Housing approvals open ClickUp tasks (the Fair Housing path bypasses the ticket domain gate). | `approval_agent.py:215-245`; `workspace_decisions.py:334` |
| R2 | **Context-field edits by clients write straight to HubSpot and flow to the Fluency sheet next sync.** Only the 24 ad-facing fields are held for RPM review; the ad-facing list itself is unconfirmed. | `workspace_profile.py:552`; `fluency_feed.py:60-61,87-89`; `community_brief.py:371,385-392` |
| R3 | **Proposals can be lost.** Pending profile proposals live in memory plus a loop event whose BQ write failure is only logged. A restart drops a proposal the client was told was "Sent to RPM for review". | `workspace_profile.py:269-274` |
| R4 | **Nobody at RPM is notified** when a client proposes, approves or uploads. | no notifier in `skills/workspace_*` |
| R5 | **Internal gap text reaches clients verbatim**, including env var names, "on main", and raw exception strings. | `workspace_dashboard.py:156,187,257`; `workspace_property_overview.py:175`; `workspace.html:1041-1045` |
| R6 | **Data is thin outside Hyly/AptIQ properties.** Cost per lease always empty; leases this month needs `hyly_property_id` (~13 of 840); non-Hyly reports are mostly gaps; Visibility falls back to the AI mentions audit. | `workspace_dashboard.py:272-275`; `workspace_report.py:1460-1515`; `workspace_visibility.py:81,140-142` |
| R7 | **No client-side error reporting, no feedback widget, no login/page-view tracking.** We would not know a pilot client hit an error unless they tell us. | `routes/feedback.py:6-9`; `workspace.html` |
| R8 | **Thread fan-out:** ~80 threads per dashboard request against 16 waitress threads and HubSpot rate limits; per-process item lock. | `workspace_dashboard.py:147`; `workspace_inbox.py:1206`; `workspace_decisions.py:69` |
| R9 | **Monthly Fair Housing review is not scheduled.** Nothing calls `/fair-housing-review/run`, so the client promise ("Your monthly Fair Housing review found…") never fires. | repo-wide search |
| R10 | **Test gaps that let B1 through:** write-route security tests cover 4 routes; no test for `WORKSPACE_REQUIRE_PROOF`; test clients default to verified; the receipt contract test has a vacuous ancestor exemption. | `tests/test_workspace_api.py:219`; `tests/workspace_contract.py:485` |
| R11 | **Accessibility:** `<main aria-live="polite">` re-reads the whole view on every render. | `workspace.html:888` |
| R12 | **Ask link shows for clients** but needs a separate `ask` allowlist entry. | `workspace.html` nav |
| R13 | **`/visibility/create-brief` is client-callable with no ticket gate**; each call starts paid brief generation. | `workspace_visibility.py:325` |

## Proposed rollout (the plan under review)

**Stage 0 — Staff testing on Render (this week).** Fix B1. Push the branch, deploy behind `WORKSPACE_ENABLED=true`, staff sign in with the existing Clerk dev instance. RPM marketing managers test with their real books. Clients cannot get in.

**Stage 1 — Client pilot readiness (next).** Fix B2, B3, R2 (confirm ad-facing list), R3, R4, R5, R7, R9, R10, R13. Pick 2–5 pilot properties that have Hyly + AptIQ + GA4 coverage so R6 doesn't dominate first impressions.

**Stage 2 — Client pilot.** Invite 2–5 client contacts via Clerk production + HubDB rows. Weekly check-in; exit criteria below.

**Exit criteria for Stage 2:** every pilot client logs in at least weekly for 3 weeks; zero auth or data-exposure incidents; at least one approval and one profile edit per property completed end to end; no client-visible internal jargon reported.

## Rules that still apply

- Never write the retired brand name anywhere in this repo (`tests/test_no_retired_brand.py`).
- Housing is a Special Ad Category: never propose radius, ZIP or audience layering.
- Money never moves on a click.
- Clients see all work, never internal notes or internal columns.

## Premises confirmed by Kyle (16 Sept 2026)

1. **Staff first, then clients.** Stage 0 staff on Render, Stage 1 client blockers, Stage 2 with 2–5 invited clients.
2. **Client approvals do real work, and RPM is alerted.** Every client approval, profile edit and upload notifies the account team.
3. **Client address: `digital.rpmliving.com/v2/client-portal/`.** That host is the HubSpot CMS site (portal 19843861) that already serves `/client-portal`, and it is already in `ALLOWED_ORIGINS`. So Stage 1 needs a HubSpot page that hosts the workspace and calls the Render API cross-origin, plus Clerk production on `rpmliving.com`.


# Phase 1 — CEO review (via /autoplan, mode: SELECTIVE EXPANSION)

## System audit
- `main` @ `09941e2` is the merge base; branch is 140 ahead, 0 behind. No stashes relevant. `TODOS.md` does not exist.
- Retrospective: 11 of the last 20 commits are review-driven fixes (auth, cache, perf, client wording). Auth and client-facing copy are recurring problem areas, so they get the hardest look.
- **New, verified in code:** the asserted-email weakness is not workspace-only. `require_company_access` (`_route_utils.py:136-145`) documents that without `PORTAL_STRICT_IDENTITY=true`, anyone sending `X-Portal-Email: <known RPM address>` is internal. The same code is on `origin/main`, where 14 route files read that header. Whether production has the flag on is unverified.

## 0A. Premise challenge
| Premise | Verdict | Why |
|---|---|---|
| Staff first, then clients (Kyle) | Holds | Cheapest path to real-data feedback; B2/B3 only block clients |
| Client approvals do real work + RPM alerts (Kyle) | Holds, incomplete | Alerts are not ownership. Each alert needs a named owner and a due time or it becomes silent backlog (Codex #6) |
| Client URL `digital.rpmliving.com/v2/client-portal/` (Kyle) | Holds, under-costed | The page is currently built to be served same-origin by Flask with a server-injected Clerk key. HubSpot hosting means cross-origin Bearer calls, the ~10h Cloudflare cache, and Trap 4. Clerk production needs DNS records on `rpmliving.com` from whoever runs it |
| "Blockers are B1–B3" (plan) | **Wrong, too narrow** | Platform-wide asserted identity (above) is a pre-existing exposure larger than B1 |
| "Exit = logins + one approval + one edit" (plan) | **Wrong measure** | Both voices: this measures activity, which pilot instructions can manufacture. Needs a value outcome |
| "Pick Hyly+AptIQ+GA4 properties" (plan) | Partial | Shows the best case; says nothing about the ~98% without Hyly |

## 0B. Existing code leverage
| Sub-problem | Existing code | Reuse? |
|---|---|---|
| Proven identity | `clerk_auth.py`, `_require_proof` in `routes/workspace.py:105` | Yes; extend to the report blueprint instead of a new check |
| Client allowlist + property scoping | `feature_access.py` HubDB table | Yes, no new store |
| RPM alerts | `routes/feedback.py` → ClickUp list 901114384845; ClickUp client in `portal_tickets` | Yes; alert = ClickUp task with assignee + due date, not a new channel |
| Proposal durability | `ticket_profile_proposals` migration 0015 (unapplied) | Yes; apply it rather than invent storage |
| Client-safe gap copy | `4cecb14` label mapping in `workspace.html` | Extend server-side (one mapping), not per-screen |
| Usage tracking | `loop_writer.record` event bus | Yes; add `workspace_session` / `workspace_client_error` events |
| HubSpot page host | `scripts/deploy_template.py`, legacy `client-portal.html` Clerk mount | Yes |

## 0C. Dream state
```
CURRENT                         THIS PLAN                           12-MONTH IDEAL
local preview + artifact  --->  staff on Render (Stage 0),     ---> every owner opens one place monthly;
13k-line legacy portal;         2-5 clients on                      each finding carries receipts and a
asserted-email identity         digital.rpmliving.com/v2/,          one-tap action; identity proven
                                proven identity, alerts with        everywhere; legacy portal retired;
                                owners, value-measured pilot        thin-data properties still useful
```

## 0C-bis. Alternatives
```
APPROACH A: Staged portal pilot (this plan, revised)
  Summary: staff on Render now; fix identity + client blockers; 2-5 clients on HubSpot-hosted /v2.
  Effort: M   Risk: Med
  Pros: honest test of the real product; reuses everything built; matches Kyle's premises
  Cons: Stage 1 is broad; HubSpot hosting adds cross-origin work
  Reuses: workspace blueprint, Clerk, HubDB access, deploy_template.py

APPROACH B: Findings-first, no client login (Codex #4, subagent #7)
  Summary: staff use the workspace; clients get a monthly emailed verdict + signed one-tap approval links.
  Effort: S   Risk: Low
  Pros: no Clerk production/DNS; tests whether clients value findings at all
  Cons: signed links are staff-only today (would need client-scoped links); doesn't test the portal Kyle is demoing
  Reuses: workspace_report, workspace_links

APPROACH C: Narrow pilot loop (Codex #5)
  Summary: approach A, but the client pilot exposes one decision loop (monthly report + approvals) and hides New request, Visibility briefs, uploads until later.
  Effort: S-M Risk: Low-Med
  Pros: fewer Stage 1 blockers (B2, R9, R13 drop out); clearer signal
  Cons: clients see less of what was built
```
**RECOMMENDATION:** A with C's narrowing as a taste choice (see gate). A keeps Kyle's confirmed direction; B is logged as an alternative, not adopted, because it contradicts premise 1.

## 0D. Selective expansion scan
- **Complexity check:** Stage 1 touches >8 files (routes, skills, page, tests, HubSpot template). Justified: these are fixes to existing code, not new services. Only one new moving part (HubSpot host page).
- **Minimum for Stage 0:** B1 + platform identity decision + deploy with flag + staff Clerk accounts.
- **Expansion candidates (auto-decided):**
  1. Value exit criterion ("each pilot client acts on one finding they didn't already know"). In blast radius, S → **accepted** (P1, both voices).
  2. One deliberately thin-data property in the pilot. S → **accepted** (P1, both voices).
  3. Client session + client error events on the loop bus. S → **accepted** (P2; closes R7).
  4. Boss demo built around a real finding, not the nav. S → **taste** (surfaced).
  5. Tie pilot timeline to NinjaCat 30-day notice 1/18/2027. S → **accepted** as a dated milestone (P6).
  6. Email/one-tap delivery channel (Approach B). M → **deferred** (contradicts premise 1 for now).

## 0E. Temporal interrogation
- **Hour 1:** which Render service and env holds `PORTAL_STRICT_IDENTITY` today? Who can add DNS for `rpmliving.com`?
- **Hour 2-3:** does the page's Clerk key injection work from a HubSpot template (publishable key in template is fine; API base URL must be absolute)?
- **Hour 4-5:** Cloudflare holds HubSpot pages ~10h; a bad deploy of `/v2` is sticky. Plan a cache-busting query version on JS/CSS.
- **Hour 6+:** who owns alert tasks on weekends? What happens to a client approval of a stale item?

## 0F. Mode
SELECTIVE EXPANSION (autoplan override). Approach A.

## CEO dual voices
**CODEX SAYS (CEO — strategy challenge):** activity ≠ value; cohort hides thin-data weakness; select by decision owner not property; untested no-login alternative; Stage 1 bundles too many hypotheses; alerts ≠ accountability; HubSpot host contradicts "no wrapper" line; no stop rule.
**CLAUDE SUBAGENT (CEO — strategic independence):** platform-wide asserted identity is the critical risk (verified); frame as findings-with-receipts; premise 3 conflicts with architecture and needs a DNS date; Stage 1 undated and 20 skipped criticals un-triaged; client approvals should go through an RPM confirm queue; include thin-data property; alternatives uncompared; competitive/NinjaCat date; owner consent for client edits.

```
CEO DUAL VOICES — CONSENSUS TABLE:
═══════════════════════════════════════════════════════════════
  Dimension                           Claude  Codex  Consensus
  ──────────────────────────────────── ─────── ─────── ─────────
  1. Premises valid?                   Partial Partial CONFIRMED (partial)
  2. Right problem to solve?           Partial Partial CONFIRMED (measure value)
  3. Scope calibration correct?        No      No      CONFIRMED (Stage 1 too broad/undated)
  4. Alternatives sufficiently explored?No     No      CONFIRMED
  5. Competitive/market risks covered? No      —       N/A (Claude only)
  6. 6-month trajectory sound?         Partial No      DISAGREE → taste
═══════════════════════════════════════════════════════════════
Single-voice critical flagged regardless: platform-wide asserted identity (Claude).
```

## Section 1 — Architecture
```
 Client browser
   │  digital.rpmliving.com/v2/client-portal/  (HubSpot CMS page, Cloudflare ~10h)   [NEW host]
   │  staff: rpm-portal-server.onrender.com/workspace (Flask same-origin)
   ▼
 Clerk (prod on rpmliving.com) ──Bearer JWT──┐                                        [NEW instance]
                                            ▼
 Render Flask ── before_request: flag → signed link → _require_proof ──┐
   ├─ workspace_bp  /api/workspace/*        (proof enforced)          │
   ├─ workspace_report_bp /api/workspace/report  (proof NOT enforced) ← B1
   └─ legacy routes (14 files, asserted email unless STRICT)          ← platform gap
        │
        ▼ skills/workspace_*  ──► hubspot_client, HubDB, BigQuery(loop_events, Hyly, AptIQ),
                                  ClickUp (tickets/tasks), approval_agent → HubSpot deals
                                  llm_gateway (drafts, briefs)
                                  [NEW] alert task → ClickUp with owner + due
```
Findings: (1) the proof check is per-blueprint, so any new blueprint silently skips it — move it to one app-level hook scoped by path prefix (auto-decided, P5). (2) HubSpot host introduces cross-origin Bearer calls; CORS already allows the origin. (3) Rollback: `WORKSPACE_ENABLED=false` is instant for API; the HubSpot page must be unpublished separately (Cloudflare lag) — add to runbook. Single points of failure: waitress 16 threads (R8), Clerk.

## Section 2 — Error & Rescue Map
```
 CODEPATH                          | WHAT CAN GO WRONG                 | RESCUED? | USER SEES
 ----------------------------------|-----------------------------------|----------|-------------------------------
 Clerk token verify                | JWKS fetch fails / expired token  | Y        | 401 → sign-in again
 _require_proof                    | report blueprint bypass           | N ← GAP  | nothing; data exposed (B1)
 profile PATCH → write_field       | HubSpot 429/5xx                   | Y        | error message
 profile proposal → loop_writer    | BQ insert fails                   | N ← GAP  | "Sent to RPM" but lost on restart (R3)
 decision → approval_agent         | ClickUp/HubSpot partial failure   | partial  | approved, some side effects missing
 requests draft → llm_gateway      | model error/refusal               | Y        | draft fails message
 requests file → portal_tickets    | domain gate 403 for clients       | N ← GAP  | 403 after paid draft (B2)
 dashboard fan-out                 | AptIQ/Hyly/HubDB exception        | Y (gap)  | raw internal text/exception (R5)
 page JS                           | any uncaught error                | N ← GAP  | blank/broken view, nobody told (R7)
 alert task (new)                  | ClickUp down                      | must add | retry + dead-letter, client unaffected
```
Decisions: every GAP above becomes a Stage 0/1 task (P1). Partial approval side effects: record per-step status in the decision event and show "RPM is finishing this" rather than full success (P1).

## Section 3 — Security & threat model
| Threat | Likelihood | Impact | Mitigated? |
|---|---|---|---|
| Asserted RPM email reads any property (legacy routes, report API) | Med | High | **No** — critical |
| Client A reads client B via company_id | Low | High | Yes (`companies_for`) once identity proven |
| Client edit injects Fair Housing violation into live ads | Med | High | Partly: blocked for high severity; ad-facing list unconfirmed (R2) |
| Upload of malicious/infringing asset goes `status=live` | Low | Med | No review step for client uploads — add RPM review for client uploads (auto, P1) |
| Paid-call abuse via `/requests/draft`, `/visibility/create-brief` | Low | Low-Med | No rate limit — add per-user daily cap (auto, P2) |
| Clerk publishable key in HubSpot template | — | None | Publishable keys are public by design |

## Section 4 — Data flow & interaction edge cases
```
 client edit ─► FH check ─► ad-facing? ─yes─► proposal (memory + BQ event) ─► RPM approve ─► HubSpot ─► Fluency sheet (daily)
                   │               └─no──► HubSpot write ─────────────────────────────────────────► Fluency sheet (daily)
                [blocked]      [BQ fail → lost on restart]                    [stale by the time approved?]
```
| Interaction | Edge case | Handled? |
|---|---|---|
| Approve | double-click | Yes (per-process lock); **No** across Render instances |
| Approve | item changed since page load | Unverified → add stale check on decision (auto, P1) |
| Undo | after restart | Partial (BQ fallback) |
| Profile edit | two people edit same field | Last write wins, history kept |
| Dashboard | 0 properties allowlisted | Needs friendly empty state (design phase) |
| Session | Clerk token expires mid-edit | Unverified → keep draft text, re-auth prompt (design phase) |

## Section 5 — Code quality
Examined the proof check split across blueprints, gap-message producers in 6 skills, and the vacuous contract assertion. Findings: gap strings are authored per skill with no client-safe layer (DRY: one `client_gap_message(code)` map, auto P5); proof enforcement duplicated per blueprint (auto P5).

## Section 6 — Tests
Deferred to the Eng phase diagram (below), which is the authoritative coverage map. CEO-level gaps: no test that every `/api/workspace*` route refuses an asserted email; no test that client-visible payloads contain no internal tokens (env names, "main", ClickUp); no smoke test against a deployed URL.

## Section 7 — Performance
Examined: dashboard 6.5s cold / 1.8s warm for one portfolio; ~80 threads per request vs 16 waitress threads. Pilot load (≤20 staff + 5 clients) is fine warm; the risk is concurrent cold requests after deploy. Decision: keep boot warm; add a per-request thread budget later (deferred, P3 — pilot scale).

## Section 8 — Observability
Gaps: no client error reporting, no login/page-view events, no alert on 5xx rate, no runbook. Accepted: `workspace_session` + `workspace_client_error` loop events, a daily staff digest of client actions, and Render log alert on 5xx (P1).

## Section 9 — Deployment & rollout
```
 1 push branch → PR → merge (fast-forward)        4 staff Clerk (dev) + HubDB internal rows
 2 Render env: WORKSPACE_ENABLED=true,            5 smoke: /workspace loads, asserted email → 401 on ALL workspace APIs
   CLERK_*, WORKSPACE_REQUIRE_PROOF=true          6 Stage 1: Clerk prod DNS, HubSpot /v2 page, client rows
 3 decide PORTAL_STRICT_IDENTITY for legacy       7 Rollback: WORKSPACE_ENABLED=false (instant API), unpublish /v2 page (Cloudflare lag ≤10h)
```
Risks: HubSpot/Cloudflare caching makes a bad `/v2` page sticky; mitigate with versioned asset URLs and a kill switch in the API (flag off → page shows "back soon").

## Section 10 — Long-term trajectory
Reversibility 4/5 (flags + unpublish). Debt: second portal alongside legacy 13k-line template; 20 skipped criticals un-triaged; proposals on loop events instead of migration 0015. Path: retire legacy portal once identity is proven everywhere.

## Section 11 — Design & UX (summary; full pass in Phase 2)
IA is defined (dashboard → approvals → property). Gaps: client empty/error/partial states, session-expiry, thin-data property framing, "what happens after I approve" for real side effects.

## NOT in scope (CEO)
- Approach B (email/one-tap delivery): deferred; contradicts premise 1.
- Scheduler for monthly Fair Housing review: remove the client-facing promise for the pilot instead (taste item C).
- Retiring the legacy portal: after identity is proven everywhere.
- Per-request thread budget: pilot scale doesn't need it.

## What already exists
See 0B table.

## Dream state delta
After Stage 2 we have proven identity on the workspace, a small value-measured client pilot and alerting with owners. Still missing vs ideal: identity proven on legacy routes (if not done in Stage 0), thin-data properties that feel useful, retirement of the legacy portal.

## Failure Modes Registry
```
 CODEPATH              | FAILURE MODE                 | RESCUED? | TEST? | USER SEES?       | LOGGED?
 ----------------------|------------------------------|----------|-------|------------------|--------
 report API auth       | asserted email accepted      | N        | N     | Silent exposure  | N   ← CRITICAL GAP
 legacy route auth     | asserted email accepted      | N        | N     | Silent exposure  | N   ← CRITICAL GAP
 proposal persistence  | BQ write fails, restart      | N        | N     | Silent loss      | Y (log only) ← CRITICAL GAP
 page JS error         | uncaught exception           | N        | N     | Broken screen    | N   ← CRITICAL GAP
 client approval       | side effect partially fails  | partial  | N     | "Approved"       | Y
 alert task            | ClickUp down                 | N (new)  | N     | Nothing          | —
 gap text              | internal string to client    | Y        | N     | Jargon           | N
```

## CEO completion summary
```
  +====================================================================+
  |            MEGA PLAN REVIEW — COMPLETION SUMMARY                   |
  +====================================================================+
  | Mode selected        | SELECTIVE EXPANSION                          |
  | System Audit         | platform-wide asserted identity found        |
  | Step 0               | Approach A; 3 premises confirmed; 5 accepted |
  | Section 1  (Arch)    | 3 issues found                              |
  | Section 2  (Errors)  | 10 error paths mapped, 5 GAPS               |
  | Section 3  (Security)| 5 issues found, 2 High severity             |
  | Section 4  (Data/UX) | 6 edge cases mapped, 3 unhandled            |
  | Section 5  (Quality) | 2 issues found                              |
  | Section 6  (Tests)   | 3 gaps (full diagram in Eng phase)          |
  | Section 7  (Perf)    | 1 issue (deferred P3)                       |
  | Section 8  (Observ)  | 4 gaps found                                |
  | Section 9  (Deploy)  | 2 risks flagged                             |
  | Section 10 (Future)  | Reversibility: 4/5, debt items: 3           |
  | Section 11 (Design)  | 4 issues → Phase 2                          |
  +--------------------------------------------------------------------+
  | NOT in scope         | written (4 items)                           |
  | What already exists  | written                                     |
  | Dream state delta    | written                                     |
  | Error/rescue registry| 10 codepaths, 5 CRITICAL GAPS               |
  | Failure modes        | 7 total, 4 CRITICAL GAPS                    |
  | Scope proposals      | 6 proposed, 4 accepted, 1 deferred, 1 taste |
  | Outside voice        | ran (codex + claude subagent)               |
  | Diagrams produced    | architecture, data flow, deploy, dream state|
  +====================================================================+
```


# Phase 2 — Design review (via /autoplan)

## Step 0 — Design scope
- **Initial design completeness of the pilot plan: 3/10.** It inventories auth and data risks but never says what a client sees on first login, with thin data, or after approving.
- **DESIGN.md:** none. The token source is the `:root` block in `workspace.html:20-39` (RPM palette present). **But the type is Inter (`workspace.html:10,20`), not the RPM brand face Montserrat**, and `--attention:#C39A4B` stands in for Copper on warnings.
- **Existing leverage:** loading spinner + retry component (`:1264-1269`), Review panel for approvals, reduced-motion handling (`:80,230,232`), mobile stacking CSS (`:784-840`), focus styles (`:52`).
- **Mockups:** skipped. The UI is built and running on :5057; reviewing the real page beats generating images of it (P5).

## Design dual voices
**CODEX SAYS (design — UX challenge):** hierarchy serves the feature list (10 nav items, Reports 9th, Ask + New request promoted); value criterion never reached the UI ("Actions we took" still leads); states named not designed; thin data renders as unexplained dashes and "new"; approvals need recorded → implementing → completed with a named owner; mobile hides 10 destinations in a scrolling strip; whole-view live region + no focus management; relative report URLs conflict with HubSpot hosting.
**CLAUDE SUBAGENT (design — independent review):** first login with nothing waiting leads with a KPI row of dashes; single-property owners see "Portfolio health" ranking; server `detail`/`Failed to fetch`/gap text shown verbatim (`:1041,1266,1288`); zero-allowlisted client gets `#/property//profile` (`:1070-1135,2963`); report page expired session has no button (`workspace_report.html:468`); Try again calls `route()` not `boot()` (`:1280`); "Loop running/paused" pulsing card is jargon (`:1158`); New request and Ask refuse clients; `api()` throws on non-same-origin paths (`:963`), so the page cannot run on HubSpot as-is.

```
DESIGN LITMUS SCORECARD — CONSENSUS:
═══════════════════════════════════════════════════════════════════
  Check                                   Claude   Codex   Consensus
  ─────────────────────────────────────── ──────── ─────── ─────────
  1. Brand unmistakable in first screen?  NO       —       NO (Inter, not Montserrat; verified)
  2. One strong visual anchor?            PARTIAL  PARTIAL CONFIRMED (only when something waits)
  3. Scannable by headlines?              PARTIAL  NO      CONFIRMED (jargon: Loop, health, AI visibility)
  4. Each section one job?                MOSTLY   NO      DISAGREE → taste (right rail mixes 3 jobs)
  5. Cards necessary?                     NO       —       N/A single voice
  6. Motion improves hierarchy?           NO       —       N/A single voice (pulsing loop dot)
  7. Premium without decorative shadows?  YES      —       N/A single voice
═══════════════════════════════════════════════════════════════════
Cross-voice agreements: client-safe messages, hosting decision, thin-data states, hide broken client buttons, post-approval status.
```

## Pass 1 — Information architecture: 5/10 → 8/10
Client first screen, specified:
```
 ┌ RPM mark ─ Property (or portfolio) switcher ───────────────── Sign out ┐
 │ Needs you (0–3 items, Review →)          │ This month, calmly:          │
 │   or, when empty: one sentence of what   │  what RPM did · what's next  │
 │   RPM did and what's next + contact name │                               │
 ├──────────────────────────────────────────┴───────────────────────────────┤
 │ Live numbers only (hide nulls): Occupancy · Units to lease · Leases*    │
 │ Latest report (last full month) → open                                   │
 └──────────────────────────────────────────────────────────────────────────┘
 Client nav: Home · Approvals · Reports · Property profile   (* only if Hyly)
```
Decisions: single-property clients land on the property, not a portfolio ranking (P5). Hide null KPIs instead of dashes (P1). "Actions we took for you" stays but below Needs you (Kyle's dashboard rule keeps it; value evidence leads). Client nav trimmed to working destinations → taste item (full nav vs four).

## Pass 2 — Interaction states: 2/10 → 8/10
```
 FEATURE            | LOADING              | EMPTY                                   | ERROR                                  | SUCCESS                         | PARTIAL
 -------------------|----------------------|-----------------------------------------|----------------------------------------|---------------------------------|------------------------------
 Sign-in            | "Signing you in…"    | —                                       | "We couldn't sign you in. Try again" + contact | lands on Home          | —
 No properties yet  | —                    | "Your account is ready. <AM name> is connecting your properties." | —              | —                               | —
 Home KPIs          | skeleton row         | hide row; show report link              | "Numbers are refreshing; check back shortly" (no detail) | values + as-of | show live ones; one line "More numbers as we connect <source>"
 Needs you          | skeleton             | "Nothing needs you. Here's what we did this month." | client-safe message + Try again (reboots) | Review panel | —
 Approve            | button busy, disabled| —                                       | "That didn't go through. Nothing changed." | Recorded → RPM implementing (owner, expected by) → Completed | "Approved. <owner> is finishing <step>."
 Stale item         | —                    | —                                       | "This changed since you opened it" + reload | —                          | —
 Profile edit       | inline saving        | field hint                              | keep text; client-safe reason          | "Saved" or "Sent to RPM for review · usually within 2 business days" | —
 Report             | "Loading report…"    | "Your first report arrives after a full month" | Sign in button / Try again button  | report                          | hide unconnected sections; one note per section
 Session expiry     | —                    | —                                       | sign-in overlay; keep view + unsent text | resumes in place              | —
```
Rule: the page never renders server `detail`, raw exceptions, env names or "Failed to fetch" to a client; internal viewers keep detail (P1, both voices).

## Pass 3 — Journey & emotional arc: 3/10 → 7/10
```
 STEP | CLIENT DOES                 | FEELS (today)          | PLAN NOW SPECIFIES
 1    | Clicks invite, signs in     | unsure what this is    | one-time welcome line + AM name
 2    | Lands on Home               | "data is missing" (dashes) | nulls hidden, calm "this month" sentence
 3    | Sees Loop paused (pulsing)  | alarm                  | loop card removed for clients
 4    | Clicks New request / Ask    | rejected (403)         | hidden for clients until they work
 5    | Reviews an approval         | informed               | why/for-whom panel (exists)
 6    | Approves                    | "did it happen?"       | recorded → implementing (named owner, expected date) → completed
 7    | Opens report                | reassured if full; broken if thin | thin sections folded with one honest note
```
5-second: brand + one thing needing them. 5-minute: can act and see the result. Long-term: trusts numbers because receipts are shown and gaps are honest.

## Pass 4 — AI slop risk: 7/10 → 8/10
App UI classifier. Hard-rule check against the page: no purple gradients, no icon-circle grids, no emoji, no centered-everything. Violations: stacked rail cards repeat the hero's first item (rule 7 "stacked cards instead of layout"), pulsing ornament on the least useful element. Decision: remove rail duplicate of hero item and the pulse for clients (P5).

## Pass 5 — Design system alignment: 5/10 → 7/10
No DESIGN.md. Palette tokens match the RPM Templates board; **typeface does not** (Inter vs Montserrat). `--attention #C39A4B` is off-palette. Decision: font swap is a TASTE item (Kyle approved the current look on 9/15, but the brand face is Montserrat). Write a short `DESIGN.md` from the `:root` tokens after the font call (P3, deferred to after pilot start).

## Pass 6 — Responsive & accessibility: 5/10 → 8/10
- **Phone (≤640px):** client nav is 4 items, so a fixed bottom bar replaces the hidden-scrollbar strip; property name stays visible; evidence renders above Approve.
- **Tablet/desktop:** current layout.
- **200% zoom:** no horizontal scroll on Home, Review panel, Profile.
- **A11y:** replace whole-view `aria-live` (`:888`) with a scoped status region; move focus to the view `h1` on route change (`:1261`); Review panel traps and returns focus; 44px targets; measured contrast for `--attention-text` and copper ink on white (AA 4.5:1).

## Pass 7 — Unresolved design decisions
```
 DECISION NEEDED                              | IF DEFERRED
 ---------------------------------------------|-------------------------------------------
 Hosting: how the page runs on digital.rpmliving.com/v2 | page throws on first API call (api() same-origin guard)
 Client nav: 4 working items vs full 10       | clients hit refusals and internal screens
 Font: Montserrat (brand) vs Inter (approved look) | brand drift from RPM Templates
 Pilot access: only pilot properties per client, or all their properties | thin-data surprise
 Post-approval promise: response time         | "waiting on a person" forever
```
Auto-decided: **hosting = HubSpot template loads the same page and calls Render with an absolute API base + Clerk Bearer**, the pattern the legacy `client-portal.html` already uses (P4). No iframe (third-party cookie breakage), no proxy (HubSpot can't). Pilot access = only pilot properties (P5). Response-time promise = "within 2 business days" placeholder → Kyle to confirm at gate.

## NOT in scope (Design)
- Visual mockup exploration (the UI exists).
- Full DESIGN.md (after the font decision).
- Redesign of internal-only screens (Portfolio, Signals, Spend team view).

## What already exists (Design)
RPM palette tokens, Review panel, Approved screen, spinner/retry component, reduced-motion rules, mobile CSS, logo mark.

## Design completion summary
```
  +====================================================================+
  |         DESIGN PLAN REVIEW — COMPLETION SUMMARY                    |
  +====================================================================+
  | System Audit         | no DESIGN.md; UI scope yes; Inter ≠ brand   |
  | Step 0               | 3/10; focus: client first-run + states      |
  | Pass 1  (Info Arch)  | 5/10 → 8/10                                 |
  | Pass 2  (States)     | 2/10 → 8/10                                 |
  | Pass 3  (Journey)    | 3/10 → 7/10                                 |
  | Pass 4  (AI Slop)    | 7/10 → 8/10                                 |
  | Pass 5  (Design Sys) | 5/10 → 7/10                                 |
  | Pass 6  (Responsive) | 5/10 → 8/10                                 |
  | Pass 7  (Decisions)  | 3 resolved, 2 taste → gate                  |
  +--------------------------------------------------------------------+
  | NOT in scope         | written (3 items)                           |
  | What already exists  | written                                     |
  | Approved Mockups     | 0 generated (UI exists)                     |
  | Decisions made       | 12 added to plan                            |
  | Overall design score | 3/10 → 7.5/10                               |
  +====================================================================+
```


# Phase 3 — Eng review (via /autoplan)

## Step 0 — Scope challenge (read against code)
| Sub-problem | Existing code | Minimum change |
|---|---|---|
| Proven identity everywhere | `server.py:73-98` Clerk hook, `_require_proof` `routes/workspace.py:105` | Move proof to one app-level hook by path prefix; always strip header on bad token; check `azp`/`iss` |
| Cross-origin hosting | legacy `client-portal.html` Clerk + Render pattern | `API_BASE` in both pages (`workspace.html:962,971`; `workspace_report.html:29,463`, `:1128` report href), pk from HubSpot module field, `credentials:'omit'` |
| Real approvals, safely | `workspace_decisions.py:521-560`, `approval_agent.py:215` | Durable idempotency key + persisted external IDs; record intent before side effects |
| Proposal durability | `workspace_profile.py:250-274` (memory + loop event) | **Not migration 0015** (that table is ticket-derived, `migrations/0015_ticket_profile_proposals.py:7`). A workspace proposal store, or a write that fails loudly |
| Pilot-only scope | `feature_access.py:254` unions HubDB + `PORTAL_COMPANY_ACCESS` | Workspace pilot scope intersected with normal access |
| Hidden features | `routes/workspace.py:815` upload, `:491` draft, `workspace_visibility.py:325` | Server-side role gate for pilot clients, not just hidden buttons |

**Complexity check:** Stage 1 touches >8 files. It stays as-is: every item is a fix to code that already exists, and no new service is added (P2 "never reduce" override). Search check: Flask app-level `before_request` is the built-in [Layer 1]; Clerk's documented `azp` check [Layer 1]; idempotency keys for external side effects [Layer 1].

## Eng dual voices
**CODEX SAYS (eng — architecture challenge):** migration 0015 does not fix workspace proposals (10/10); side effects run before the decision is recorded, `loop_writer` returns an ID even when BQ skipped, so crash/retry duplicates or loses (10/10); stale check has no content revision, and profile approval doesn't compare the live value (10/10); "recorded → implementing → completed" has no completion authority (9/10); report page needs the same hosting migration (10/10); hiding features doesn't remove backend risk, upload has no client restriction (10/10); "only pilot properties" is defeated by env-granted access (10/10).
**CLAUDE SUBAGENT (eng — independent review):** B1 confirmed; page can't be hosted as written; no `azp`/`iss` check (`clerk_auth.py:134`); bad token keeps asserted header when `CLERK_SECRET_KEY` unset (`server.py:93`); no preflight `Max-Age`; nested pools ~72 threads/dashboard; HubSpot upload with no timeout + 100MB×20 in memory (`asset_uploader.py:136,215`); in-process approval lock → duplicate deals across instances; security test matrix covers 4 routes; vacuous contract check; `/requests/draft` no write gate or cap; svg uploads public; Clerk prod key switch must be lockstep.

```
ENG DUAL VOICES — CONSENSUS TABLE:
═══════════════════════════════════════════════════════════════
  Dimension                           Claude  Codex  Consensus
  ──────────────────────────────────── ─────── ─────── ─────────
  1. Architecture sound?               Partial No      CONFIRMED (not ready: auth + hosting)
  2. Test coverage sufficient?         No      No      CONFIRMED
  3. Performance risks addressed?      No      —       N/A (Claude only; fan-out, uploads)
  4. Security threats covered?         Partial No      CONFIRMED (partial at best)
  5. Error paths handled?              Partial No      CONFIRMED (durability/retry)
  6. Deployment risk manageable?       Partial Partial CONFIRMED (Stage 0 yes after auth; Stage 1 larger)
═══════════════════════════════════════════════════════════════
DISAGREE resolved by code: migration 0015 (Claude: apply; Codex: wrong table). Verified → Codex.
```

## Section 1 — Architecture
```
                    ┌──────────── digital.rpmliving.com/v2/client-portal/ (HubSpot CMS, Cloudflare) ─┐
 client browser ────┤  workspace shell + API_BASE + Clerk pk (module field)                           │
                    └───────────────┬────────────────────────────────────────────────────────────────┘
 staff browser ── rpm-portal-server.onrender.com/workspace (same origin)
                                    │ Bearer (Clerk prod: clerk.rpmliving.com)
                                    ▼
 ┌──────────────────────── Render Flask (waitress 16) ─────────────────────────────────────────┐
 │ app.before_request:  CORS/preflight(Max-Age) → Clerk verify (iss+azp) → strip header on fail │
 │                      → [NEW] proof gate for /api/workspace* + /workspace*                    │
 │   workspace_bp ─┬─ reads: dashboard/inbox (bounded executor [NEW]) ─ HubSpot, HubDB, BQ      │
 │                 ├─ decision: [NEW] idempotency key → intent event → side effects → result    │
 │                 │      └─ approval_agent → HubSpot deal · ClickUp task · [NEW] alert task    │
 │                 ├─ profile: context→HubSpot │ ad-facing→[NEW] durable proposal store          │
 │                 └─ upload/draft/brief: [NEW] pilot role gate + caps + timeout                │
 │   workspace_report_bp ── now covered by app-level proof gate                                 │
 │   legacy bps (14) ── PORTAL_STRICT_IDENTITY decision                                         │
 └──────────────────────────────────────────────────────────────────────────────────────────────┘
```
Production failure per integration: Clerk prod key mismatch between Render and HubSpot → every call 401 (switch in lockstep, smoke test); HubSpot 429 under fan-out → gaps everywhere (bounded executor + backoff); ClickUp down during approval → intent recorded, retry with same key; BQ down → proposal write must fail loudly.

## Section 2 — Code quality
- Proof enforcement duplicated per blueprint → one hook (DRY, P5).
- Client-facing message strings authored in 6 skills → one map (DRY).
- `_item_locks` never pruned (`workspace_decisions.py:449`) → bounded dict or durable key replaces it.
- `except Exception` around decision handlers (`:536`) logs type only → keep, but persist external IDs created before the failure.

## Section 3 — Test review
```
CODE PATHS                                                   USER FLOWS
[+] app-level proof gate (new)                               [+] Client sign-in on HubSpot host
  ├── [GAP] asserted email → 401 on EVERY workspace rule       ├── [GAP] [→E2E] invite → sign in → Home
  ├── [GAP] report route asserted → 401 (inverts :74)          ├── [GAP] zero-properties screen
  ├── [★★ TESTED] Bearer verified → 200 (test_workspace_api)   └── [GAP] session expiry keeps draft
  └── [GAP] bad token + no CLERK_SECRET_KEY strips header    [+] Approve
[+] clerk_auth.verify                                          ├── [★★ TESTED] approve happy path (r4_approvals)
  └── [GAP] wrong azp / wrong iss → None                       ├── [GAP] [→E2E] double-click / two tabs → one deal
[+] decisions (modified)                                       ├── [GAP] content changed → 409
  ├── [★★ TESTED] handler success + DecisionError              ├── [GAP] crash after ClickUp task → retry no duplicate
  ├── [GAP] idempotency key replay                             └── [GAP] alert task created with owner + due
  ├── [GAP] expected revision mismatch                       [+] Profile
  └── [GAP] per-step external IDs persisted on partial fail    ├── [★★★ TESTED] context save, FH blocked (r5_profile_edit)
[+] profile proposals (modified)                               ├── [GAP] BQ down → no "Sent to RPM"
  ├── [★★ TESTED] propose/approve/reject (r5_profile_approvals) └── [GAP] staff edit after proposal → conflict
  ├── [GAP] durable store survives restart                   [+] Report
  └── [GAP] live value changed → conflict                      ├── [★★ TESTED] internal report (report_routes)
[+] feature_access pilot scope (new)                           └── [GAP] [→E2E] from CMS origin with Bearer
  ├── [GAP] env grant does not widen pilot scope             [+] Thin-data property
  └── [GAP] HubDB revoke removes access                        └── [GAP] no internal tokens in any client payload
[+] upload / draft / create-brief (modified)
  ├── [★★ TESTED] unverified → 401 (v3:435,591; r4_upload:96)
  ├── [GAP] pilot client → refused (role gate)
  ├── [GAP] daily cap exceeded → 429
  ├── [GAP] svg refused for client; magic-byte check
  └── [GAP] Files API timeout → clean error
[+] contract helper
  └── [GAP] fix vacuous ancestor exemption (workspace_contract.py:486)

COVERAGE: 6/31 paths tested (19%)  |  Code: 5/20  |  Flows: 1/11
QUALITY: ★★★:1 ★★:5 ★:0  |  GAPS: 25 (4 E2E)
```
Regression rule: the report route test at `tests/test_workspace_report_routes.py:74` currently asserts the insecure behavior. Inverting it is a **CRITICAL regression test** (added, not asked).
Test plan artifact: `~/.gstack/projects/marketingdudeAZ-clientportal/kyleshipp-feature-portal-workspace-eng-review-test-plan-20260916-145956.md`.
One matrix test replaces piecemeal auth tests: iterate `app.url_map` for every `/api/workspace` rule × {asserted-only → 401, other company → 403, preview → 403 on writes}.

## Section 4 — Performance
- Nested pools (`workspace_dashboard.py:147` × `workspace_inbox.py:1206`) ≈72 threads per dashboard → one process-wide bounded executor + HubSpot 429 backoff (P1 for client pilot; staff Stage 0 tolerable).
- Uploads read whole files into memory with no timeout (`asset_uploader.py:136,215`) → timeout, per-route size cap (P1 before clients can reach upload, or gate upload off).
- Every cross-origin call preflights → `Access-Control-Max-Age: 600` (P3).
- Cold warm ~107s per instance → keep boot warm; don't scale to 2+ instances until approvals have durable idempotency.

## NOT in scope (Eng)
- Retiring the legacy portal and migrating its users to Clerk prod (separate project; only the strict-identity decision is in Stage 0).
- Multi-instance Render scaling (pin to one instance for the pilot).
- Rewriting fan-out architecture beyond a bounded executor.

## What already exists (Eng)
Clerk verify hook, `_require_proof`, `require_company_access`, `feature_access` HubDB scoping, `approval_agent`, `loop_writer` with dead-letter path, `hubspot_client` cache, asset uploader, 905 workspace tests.

## Failure modes (Eng)
```
 CODEPATH                     | FAILURE                          | TEST | HANDLED | USER SEES        | CRITICAL?
 -----------------------------|----------------------------------|------|---------|------------------|----------
 report API                   | asserted email accepted          | N    | N       | silent exposure  | YES
 Clerk verify                 | token from other origin accepted | N    | N       | silent           | YES
 server.py bad-token path     | CLERK_SECRET_KEY unset keeps hdr | N    | N       | silent           | YES
 decision                     | crash after side effect → dup    | N    | N       | two deals/tasks  | YES
 proposal                     | BQ skip → lost                   | N    | N       | "Sent to RPM"    | YES
 profile approve              | overwrites newer staff edit      | N    | N       | silent           | YES
 upload                       | Files API hang                   | N    | N       | spinner forever  | no (visible)
 dashboard                    | HubSpot 429 under load           | N    | Y(gaps) | missing numbers  | no
 pilot scope                  | env grant widens access          | N    | N       | extra properties | YES
```

## Worktree parallelization
| Step | Modules touched | Depends on |
|---|---|---|
| A. Auth hardening (app hook, azp/iss, header strip, report test inversion, url_map matrix) | server.py, clerk_auth, routes/, tests/ | — |
| B. Approvals durability + alerts + stale revision | skills/workspace_decisions, approval_agent, tests/ | — |
| C. Profile proposal store + conflict check | skills/workspace_profile, migrations/, tests/ | — |
| D. Client UI shell, states, hosting API_BASE | portal_pages/, hubspot-cms/ | A (auth contract) |
| E. Pilot scope + role gates + caps + upload timeout | feature_access, routes/workspace, asset_uploader | A |

Lanes: **A** first (small, shared auth). Then **B**, **C**, **E** in parallel worktrees. **D** in parallel with B/C/E once A merges. Conflict flag: E and A both touch `routes/`; run E after A lands.

## Eng completion summary
- Step 0: scope accepted as-is (fixes to existing code)
- Architecture: 6 issues
- Code quality: 4 issues
- Tests: diagram produced, 25 gaps (1 critical regression)
- Performance: 4 issues
- NOT in scope: written · What already exists: written
- Failure modes: 7 critical gaps
- Outside voice: ran (codex + claude subagent)
- Parallelization: 5 lanes, 4 parallel / 1 first
- Lake score: 14/15 recommendations chose the complete option

# Cross-phase themes
- **Identity is enforced in the wrong place** — CEO, Eng (both voices each). High confidence.
- **The HubSpot-hosted page doesn't work as written** — CEO, Design, Eng. Highest-confidence item: it is Stage 1's biggest hidden cost.
- **Real approvals need durability and a visible owner** — CEO, Design, Eng.
- **Thin-data properties must read as honest, not broken** — CEO, Design.
- **Clients see internal text** — CEO, Design.

# Approved rollout (Kyle, 16 Sept 2026 — /autoplan gate, approved as-is)

**Answer to "are we ready?"** Staff testing: after one auth fix and a deploy. Clients: not yet (17 P1 items + Clerk DNS on rpmliving.com).

**Gate choices (defaults accepted):** narrow client pilot (Home, Approvals, Reports, Property profile; other features refused server-side) · real approvals made retry-safe, plus owned ClickUp alerts · Montserrat · "RPM reviews within 2 business days" on ad-facing profile edits · client URL stays `digital.rpmliving.com/v2/client-portal/`.

**Stage 0 — staff testing (this week)**
- [ ] E1 app-level proof gate, Clerk `azp`+`iss`, strip header on bad token, invert report test, url_map auth matrix
- [ ] T2 check `PORTAL_STRICT_IDENTITY` in Render production; decide for legacy routes
- [ ] Push branch, PR, fast-forward merge; Render env `WORKSPACE_ENABLED=true`, `WORKSPACE_REQUIRE_PROOF=true`; one instance
- [ ] Smoke: asserted email → 401 on every `/api/workspace*`; staff sign in; dashboard warm
- [ ] **Ask IT today** for Clerk production DNS on rpmliving.com (longest lead time)

**Stage 1 — client readiness** (lanes from the Eng parallelization table)
- Lane B: E2 approvals durability + T9/D3 status (recorded → implementing → completed) + T3 owned alerts
- Lane C: E3 durable workspace proposal store (supersedes T4) + live-value conflict
- Lane E: E4 pilot scope + server-side gates + LLM caps, E5 bounded executor + upload limits, T8
- Lane D: D4/T7 HubSpot host with `API_BASE` + Clerk prod, D1/T5 client-safe states and copy, D2 client shell, D5 a11y, D6 session, font → Montserrat
- Also: T6 client session/error events + daily digest, E6 contract helper fix, T10 pilot charter

**Stage 2 — pilot:** 2–5 clients, pilot properties only (one thin-data), weekly check-in. Exit = each client acts on a finding they didn't already know; zero auth incidents; approvals complete end to end with an owner. Decide expand / revise / stop before the NinjaCat notice date **1/18/2027**.

<!-- AUTONOMOUS DECISION LOG -->
## Decision Audit Trail

| # | Phase | Decision | Classification | Principle | Rationale | Rejected |
|---|-------|----------|----------------|-----------|-----------|----------|
| 1 | Intake | Wrote a pilot plan doc instead of reviewing the 1,193-line build spec | Mechanical | P5 | The question is go-to-testing; the build spec describes what exists | Reviewing build spec as the plan |
| 2 | Intake | UI scope: yes. DX scope: skipped | Mechanical | P3 | Users are marketing managers and owners; the API is internal to the page, not a developer product | Running DX review |
| 3 | Intake | No /office-hours prerequisite | Mechanical | P6 | Direction settled by 9/14 review rounds | Running office-hours |
| 4 | CEO 0A | Premises gate: staff→clients, real approvals + alerts, digital.rpmliving.com/v2/client-portal/ | USER | — | Kyle answered D1–D3 | Straight-to-clients; record-only; new subdomain |
| 5 | CEO 0D | Add a value exit criterion (client acts on a finding they didn't already know) | Mechanical | P1 | Both voices: activity metrics can be manufactured | Activity-only exit |
| 6 | CEO 0D | Include one deliberately thin-data property in the pilot | Mechanical | P1 | Both voices: best-case cohort hides 98% of portfolio | Hyly-only cohort |
| 7 | CEO 0D | Add client session + client error loop events and a daily staff digest | Mechanical | P2 | Closes R7; reuses loop_writer | New analytics tool |
| 8 | CEO 0D | Date the pilot against NinjaCat notice 1/18/2027 | Mechanical | P6 | Natural forcing date | Undated Stage 1 |
| 9 | CEO 0D | Email/one-tap delivery (Approach B) deferred | Mechanical | P3 | Contradicts confirmed premise 1 | Adopting B now |
| 10 | CEO 0A | Every client alert is a ClickUp task with a named owner + due time | Mechanical | P1 | Codex: alerts are not accountability; reuses ClickUp | Slack/email-only alerts |
| 11 | CEO S1 | Enforce proof in one app-level hook by path prefix, not per blueprint | Mechanical | P5 | B1 happened because a new blueprint skipped it | Patch report blueprint only |
| 12 | CEO S2 | ~~Apply migration 0015~~ → REVERSED in Eng (#43): 0015 is ticket-derived | Mechanical | P4 | Superseded | — |
| 13 | CEO S2 | Partial approval side effects show "RPM is finishing this", per-step status in event | Mechanical | P1 | No false "done" | Show success regardless |
| 14 | CEO S3 | Client uploads land as pending RPM review, not status=live | Mechanical | P1 | Fair Housing + brand risk; small change | Live on upload |
| 15 | CEO S3 | Per-user daily cap on paid model calls (draft, create-brief) | Mechanical | P2 | Cheap abuse guard | No cap |
| 16 | CEO S4 | Stale-item check on decision POST | Mechanical | P1 | Approving changed item is wrong action | Trust page state |
| 17 | CEO S5 | One server-side client-safe gap message map | Mechanical | P5 | DRY over 6 skills | Per-screen label patches |
| 18 | CEO S7 | Per-request thread budget deferred | Mechanical | P3 | Pilot scale fine warm | Build now |
| 19 | CEO S9 | Versioned asset URLs + API-side kill switch for /v2 page | Mechanical | P1 | Cloudflare 10h stickiness | Rely on unpublish |
| 20 | CEO | Narrow client pilot to report + approvals + profile (hide New request, Visibility briefs, uploads) | TASTE | P3 vs P1 | Codex narrow; drops B2/R9/R13 from Stage 1 | Full screen set |
| 21 | CEO | Legacy-portal asserted identity added as Stage 0 security item | USER CHALLENGE (security) | — | Verified in code on main; Claude voice critical | Treat as workspace-only |
| 22 | CEO | Client approvals: RPM-confirm queue during pilot (subagent) vs real + alerts (Kyle, Codex) | TASTE | — | Kyle's D2 stands by default | — |
| 23 | CEO | Keep digital.rpmliving.com/v2 (Kyle) vs Render subdomain (subagent) | TASTE | — | Kyle's D3 stands; cost recorded | — |
| 24 | Design 0 | Skip mockup generation; review the running page | Mechanical | P5 | UI is built | Generate variants |
| 25 | Design P1 | Single-property clients land on the property page | Mechanical | P5 | Avoid ranking a client against itself | Portfolio health for one property |
| 26 | Design P1 | Hide null KPIs; one "more as we connect" note | Mechanical | P1 | Both voices; dashes read as broken | Show dashes |
| 27 | Design P2 | Client never sees server detail/raw errors; client-safe copy on the page too | Mechanical | P1 | Both voices; verified :955-958 | Server map only |
| 28 | Design P2 | Zero-properties client screen naming the AM | Mechanical | P1 | Broken #/property//profile today | — |
| 29 | Design P2 | Try again reboots (me + route); report page gets Sign in / Try again buttons | Mechanical | P1 | Verified dead ends | — |
| 30 | Design P2 | Session expiry overlay keeps view + unsent text | Mechanical | P1 | Lost drafts | Wipe view |
| 31 | Design P3 | Remove Loop card, hide New request and Ask for clients until they work | Mechanical | P5 | Both voices; B2/R12 | Show and refuse |
| 32 | Design P3 | Approval status: recorded → implementing (owner, expected) → completed | Mechanical | P1 | Both voices | Single "approved" |
| 33 | Design P6 | Scoped live region, focus on route change, 4-item bottom nav on phone | Mechanical | P1 | :888, :1261, hidden scroll nav | — |
| 34 | Design P7 | Hosting: HubSpot template + absolute API base + Clerk Bearer (legacy pattern) | Mechanical | P4 | Reuses client-portal.html pattern; iframe/proxy fail | iframe; proxy |
| 35 | Design P7 | Pilot clients see only pilot properties | Mechanical | P5 | Avoid thin-data surprise | All their properties |
| 36 | Design P1 | Client nav: 4 working items vs full nav | TASTE | P5 vs P1 | Both voices lean narrow | — |
| 37 | Design P5 | Montserrat (brand) vs keep Inter (approved 9/15) | TASTE | — | Brand token says Montserrat | — |
| 38 | Design P7 | Profile review response-time promise ("within 2 business days") | TASTE | — | Needs Kyle's team capacity | — |
| 39 | Eng 0 | Scope accepted as-is despite >8 files | Mechanical | P2 | All fixes to existing code | Reduce |
| 40 | Eng S1 | App-level proof gate for /api/workspace* and /workspace* | Mechanical | P5 | Both eng voices; B1 root cause | Per-blueprint patch |
| 41 | Eng S1 | Verify Clerk azp + iss; always strip asserted header on bad token | Mechanical | P1 | Verified clerk_auth.py:134, server.py:93 | — |
| 42 | Eng S1 | Durable idempotency key; record intent before side effects; persist external IDs | Mechanical | P1 | Codex 10/10; verified decisions.py:532-542 | Process lock only |
| 43 | Eng S1 | Workspace proposal store (not migration 0015); fail loudly if write fails | Mechanical | P5 | Verified 0015 is ticket-derived; reverses #12 | Apply 0015 |
| 44 | Eng S1 | Expected content revision on decisions; live-value compare on profile approval | Mechanical | P1 | Codex 10/10 | Actionable-only recheck |
| 45 | Eng S1 | Completion authority = ClickUp task status + deal stage, polled daily | Mechanical | P5 | Explicit, reuses existing IDs | Webhooks now |
| 46 | Eng S1 | Pilot scope intersected with normal access; env grants cannot widen it | Mechanical | P1 | Verified feature_access.py:254 union | Edit HubDB only |
| 47 | Eng S1 | Server-side role gate for features hidden from pilot clients | Mechanical | P1 | Hiding a button isn't security | UI-only hide |
| 48 | Eng S3 | Invert report test :74 to 401 (critical regression test) | Mechanical | Iron rule | Test asserts the hole | — |
| 49 | Eng S3 | url_map-driven auth matrix test for every workspace rule | Mechanical | P1 | Covers future routes automatically | Per-route lists |
| 50 | Eng S3 | Fix vacuous contract exemption | Mechanical | P1 | workspace_contract.py:486 | — |
| 51 | Eng S4 | Bounded process-wide executor + HubSpot 429 backoff before clients | Mechanical | P1 | ~72 threads/dashboard | Defer |
| 52 | Eng S4 | Upload timeout + size cap; svg/magic-byte check for clients | Mechanical | P1 | asset_uploader.py:136,215 | — |
| 53 | Eng S4 | Preflight Max-Age 600 | Mechanical | P3 | Halves cross-origin round trips | — |
| 54 | Eng S4 | Pin Render to one instance for the pilot | Mechanical | P3 | Until idempotency lands | Scale out || 55 | Gate | Approved as-is: narrow pilot, real retry-safe approvals, Montserrat, 2-day promise, keep /v2 URL | USER | — | Kyle approved | — |

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 1 | ISSUES_OPEN (PLAN via /autoplan) | 6 proposals, 4 accepted, 1 deferred; 4 critical gaps |
| Codex Review | `/codex review` | Independent 2nd opinion | 3 | ran per phase | CEO 8 · Design 7 · Eng 7 concerns |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | ISSUES_OPEN (PLAN via /autoplan) | 39 issues, 7 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 1 | ISSUES_OPEN (PLAN via /autoplan) | score: 3/10 → 7/10, 15 decisions |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | skipped | no developer-facing scope |

- **CROSS-MODEL:** Codex and Claude agreed on 11 of 14 scored dimensions. They disagreed on migration 0015, which the code resolved in Codex's favour. Themes both raised in several phases: where auth is enforced, the HubSpot host, approval durability, thin data, and internal text reaching clients.
- **VERDICT:** Plan APPROVED by Kyle. Reviews are complete but issues remain open by design. The Stage 0 auth fix (E1) blocks staff testing, and 17 P1 tasks block the client pilot. Eng review stays open until those land and `/review` runs on the diff.

NO UNRESOLVED DECISIONS
