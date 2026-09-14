# Portal Workspace — build plan

**Owner:** Kyle Shipp · **Started:** 14 Sept 2026 · **Target:** demo-ready the week of 21 Sept
**Branch:** `feature/portal-workspace` (off `main` @ `09941e2`). Sub-branches: `feature/portal-workspace-api`, `feature/portal-workspace-ui`.
**Design source:** Paper file **"RPM Portal — PMM Workspace"** (`01M21V8TTX0599CW4M40PZJ6J0`). Read it with the Paper MCP. Take exact values from `get_computed_styles` / `get_jsx`, never from screenshots. Never edit that file.

## Why

The current portal (`hubspot-cms/templates/client-portal.html`, 13,150 lines) is hard to use and hard to change. The replacement is one simple workspace organized around the loop:

| Lens | The job | Where it shows up |
|---|---|---|
| **Express** | One place for all the property's context | Property (brief, floorplans, people, connections) |
| **Tailor** | That context shaped per channel | Work items carry the channel(s) they touch |
| **Amplify** | Easy approve, and publish everywhere | Item detail: Approve / Not now, then "what actually happens" |
| **Evolve** | Every result connected so we learn faster | Performance, Plan & Spend, Ask, Reports, and "when we'll know" on every approval |

The home screen answers one question: **what needs me, and why**.

## Shape

- **One new page, served by Flask** at `GET /workspace` from `webhook-server/portal_pages/workspace.html`.
  - The page is self-contained: inline CSS and JS, no build step.
  - It uses the same pattern as `routes/portal_ui.py`, so there's no HubSpot CDN cache and no template Trap 4.
  - A HubSpot CMS wrapper for go.rpmliving.com comes later and reuses this file.
- **Behind two flags:**
  1. `WORKSPACE_ENABLED=true` (env). Every page and API route 404s without it.
  2. Feature key `workspace` in `feature_access.FEATURES` (default `beta`: internal staff, plus allowlisted clients).
- **One blueprint:** `webhook-server/routes/workspace.py` (`workspace_bp`), registered in `routes/__init__.py`.
- **Layer 2 skills live in `webhook-server/skills/workspace_*.py`.**
  - `workspace_inbox.py` normalizes every approval source into one item shape.
  - Routes never call HubSpot, HubDB, BigQuery or Claude directly.

## Screens (v1)

| # | Screen | Paper artboard | API |
|---|---|---|---|
| 1 | **Work**: per-property queue grouped into Late / This week / Later, filtered by To do / In motion / Done / Everything | A — Work | `GET /api/workspace/work` |
| 2 | **Item detail**: Why this exists + receipts, If approved (dated steps), Your call (Approve / Not now + one-tap reason), Trail | B — Item detail | `GET /api/workspace/work/<id>`, `POST …/decision` |
| 3 | **Approved**: what's in motion (automatic / queued / waiting on a person), what got written down, when we'll know | F — Approved | response of `POST …/decision` |
| 4 | **Portfolio**: the account manager's first screen; properties ranked by units at risk, then start-by date | G — Portfolio | `GET /api/workspace/portfolio` |
| 5 | **Property**: brief (override wins), floorplans, who's on it, connections | K — Property | `GET /api/workspace/property` |
| 6 | **Performance**: occupied, available now, coming open by week, monthly plan | H — Performance | `GET /api/workspace/performance` |
| 7 | **Plan & Spend**: channel mix bar, channel table, attribution caveat | I — Plan & Spend | `GET /api/workspace/plan` |
| 8 | **Ask**: the preset questions, with a receipts panel | J — Ask | existing `GET /api/ask/questions`, `POST /api/ask/<key>` |
| 9 | **Client view**: committed and done only | C — What the client sees | `GET /api/workspace/client-view` |
| — | **Reports** | D — Reporting | **On hold until Kyle's report feedback.** Nav item present, screen says it's coming. |
| — | **New request** (plain words in, tickets out) | E — New request | Stretch goal, after 1–9 |

## Rules for every number and every action

1. **Every number carries a receipt:** `{value, source, as_of}`. If we don't have it, return `null` and add an entry to `gaps[]`. Never invent a number, and never let an LLM write one.
2. **Decisions require a verified identity.** `POST …/decision` requires `_route_utils.identity_is_verified()` AND `require_company_access(company_id)`, whatever `PORTAL_STRICT_IDENTITY` is set to. Reads require `require_access("workspace")` + `require_company_access`.
3. **No `?email=` identity on `/workspace`** (the existing `/portal` route allows that; it's a known hole on `main` and out of scope here). The page gets identity from Clerk (Bearer JWT), as `client-portal.html` does. For internal demos there's an optional **signed preview link**:
   - an HMAC token carrying email + expiry, minted by `scripts/workspace_link.py`;
   - honored only when `WORKSPACE_SIGNED_LINKS_ENABLED=true`;
   - only for RPM internal emails;
   - at most 7 days old;
   - verified server-side, after which it sets `portal.identity_verified`.
4. **Money never moves on a click.** Any decision that changes spend drafts a deal or IO for a human signature, which is the existing behavior. Nothing added here writes to Google Ads, Fluency or a rent roll.
5. **Fair Housing:** item copy that reaches a client or a channel passes through `fair_housing.py` (see `docs/PAID_MEDIA_COMPLIANCE.md`). Never propose radius tightening, ZIP targeting or audience layering.
6. **Loop events:** decisions write `loop_writer.record(stage, "workspace_decision", …)` with the existing stages (attract / engage / convert / optimize / ops). The E/T/A/E lens goes in the payload as `lens`, not as a stage.
7. **Repo conventions:**
   - `from __future__ import annotations` in every new module;
   - new config symbols go in BOTH `config.py` files;
   - HubSpot calls go through `hubspot_client.py`;
   - Claude calls go through `skills/llm_gateway.py`;
   - R1: never write `uuid`.
8. **The retired product the reference PDFs came from is never named anywhere in this repo.** That covers code, docs, fixtures, commits and branches. `tests/test_no_retired_brand.py` enforces it.

## API contract

Base: `/api/workspace`. Every response is JSON. Errors are `{error, detail?}` with 400 / 401 / 403 / 404.

### `GET /me`
```json
{"email": "dana@rpmliving.com", "role": "internal", "verified": true,
 "companies": [{"company_id": "123", "uuid": "…", "name": "LYV Broadway", "city": "Carrollton", "state": "TX", "units": 390}]}
```

### `GET /portfolio` (internal role only)
```json
{"as_of": "2026-09-14T08:04:00Z", "property_count": 38, "item_count": 23, "starting_this_week": 6,
 "properties": [{"company_id": "123", "name": "Skye Reserve", "city": "Tampa", "state": "FL", "units": 982,
   "occupancy": {"value": 0.76, "source": "aptiq", "as_of": "…"},
   "top_item": {"id": "hubdb_rec:991", "title": "108 of 165 available units have sat 90+ days"},
   "more_items": 2, "start_by": "2026-09-10", "overdue": true,
   "units_at_risk": {"value": 108, "source": "aptiq", "as_of": "…"}}],
 "quiet_count": 34, "gaps": []}
```

### `GET /work?company_id=&status=to_do|in_motion|done|all`
```json
{"summary": {"open": 6, "late": 1, "next_deadline": "2026-09-14"},
 "counts": {"to_do": 3, "in_motion": 3, "done": 11},
 "groups": {"late": [Item], "this_week": [Item], "later": {"count": 3, "titles": ["2027 budget draft", "…"]}},
 "hidden_count": 41, "gaps": []}
```

**Item**
```json
{"id": "hubdb_rec:991", "source": "hubdb_rec", "source_id": "991",
 "title": "Flight paid onto one-bedrooms before the October 5 wave",
 "found": "Seven units open the week of Oct 5 — the largest wave this quarter.",
 "expect": null, "if_skip": null,
 "receipts": [{"label": "7 units, week of Oct 5", "source": "aptiq", "as_of": "…"}],
 "channels": ["paid_search", "pmax", "ils"], "lens": "amplify",
 "start_by": "2026-09-14", "due": null, "status": "to_do",
 "needs_approval": true, "client_visible": false, "internal_only": true,
 "owner": "Dana R.", "comments_count": 3,
 "cost_note": "Draft ready — $600 for 3 weeks",
 "steps": [{"when": "2026-09-14", "label": "Hide #1321 from the syndication feed", "channel": "listing", "kind": "auto", "status": "pending"}],
 "trail": [{"at": "2026-08-24T…", "actor": "availability monitor", "text": "Opened"}],
 "actions": {"approve": true, "not_now": true}}
```
- `source` is one of `hubdb_rec | loop_rec | call_prep | content_brief | video_variant | ticket_profile | onboarding_gap | portal_ticket | service_ticket`.
- `lens` is one of `express | tailor | amplify | evolve`.
- `steps[].kind` is one of `auto | queued | person`.
- `found` / `expect` / `if_skip` / `steps` are `null` or empty when the source doesn't carry them. The UI hides empty sections.

### `GET /work/<id>?company_id=`
Returns one Item.

### `POST /work/<id>/decision`
Body: `{"company_id": "123", "action": "approve" | "not_now", "reason": "wrong_data" | "already_handled" | "not_priority" | "discuss_on_call" | null}`

Response:
```json
{"item": Item, "decided_by": "marcus@…", "decided_at": "…",
 "in_motion": [{"label": "…", "kind": "auto", "status": "done", "detail": "Done at 9:14am"}],
 "written_down": "Approved unedited. Counts toward this trigger's record.",
 "check_back": {"date": "2026-10-08", "text": "We check these ten units again on Oct 8."}}
```
`reason` is required when the action is `not_now`. The dispatch goes to the source's existing handler as a module call, never an HTTP self-call:

| Source | Handler |
|---|---|
| `hubdb_rec` | approve/dismiss (`server.py` `/api/approve`, `/api/dismiss`) |
| `loop_rec` | loop approve/reject |
| `call_prep` | approve/dismiss |
| `content_brief` | approve |
| `video_variant` | approve |
| `ticket_profile` | accept/reject |

### `GET /property?company_id=`
```json
{"name": "LYV Broadway", "address": "2800 Broadway Blvd, Carrollton TX 75007", "domain": "lyvbroadway.com", "managed_since": "2024-07",
 "brief": {"text": "…", "curated": true, "edited_by": "AM", "edited_at": "2026-08-12"},
 "floorplans": [{"code": "A1", "beds": 1, "sqft": 712, "available": 7}],
 "people": [{"name": "Marcus Jennings", "role": "Account manager"}, {"name": "Dana Reyes", "role": "Property marketing manager"}],
 "connections": [{"name": "ApartmentIQ", "status": "connected", "synced_at": "…"}, {"name": "Yardi", "status": "not_connected", "synced_at": null}],
 "gaps": []}
```

### `GET /performance?company_id=&range=30|90|365`
```json
{"occupied": {"value": 0.918, "units": 358, "total": 390, "target": 0.95, "source": "aptiq", "as_of": "…"},
 "available_now": {"value": 44, "stale_90_plus": 10, "source": "aptiq", "as_of": "…"},
 "coming_open_90d": {"value": 23, "one_bed": 13, "source": "aptiq", "as_of": "…"},
 "coming_by_week": [{"week_start": "2026-09-07", "one_bed": 2, "two_bed": 0, "other": 1}],
 "monthly_plan": {"value": 4638, "by_channel": {"paid": 2839, "seo": 800}, "source": "hubspot_line_items", "as_of": "…"},
 "note": "Dense for about six weeks, then thin — we see units when they get advertised, not when notice is given.",
 "gaps": []}
```

### `GET /plan?company_id=`
```json
{"monthly_total": 4638, "channel_count": 6, "pending_changes": 2,
 "channels": [{"channel": "Paid search", "monthly": 1762, "share": 0.38, "cost_per_lease": 391, "pointed_at": "All floorplans", "status": "pending", "status_note": "+$600 pending"}],
 "caveat": "Cost per lease comes from last-touch attribution…", "gaps": []}
```

### `GET /client-view?company_id=`
```json
{"changing": [{"date": "2026-10-01", "title": "Renewal-season creative set", "note": "…", "status": "in_production"}],
 "done_this_quarter": [{"date": "2026-08-14", "title": "Paid search shifted onto two-bedroom inventory", "note": "…", "status": "complete"}],
 "done_count": 11, "hidden_open_count": 4}
```

## Workstreams (run in parallel)

**API (`feature/portal-workspace-api`):**
- the blueprint, the flags and the feature key;
- `skills/workspace_inbox.py` source adapters;
- the endpoints above;
- signed preview links;
- tests (`tests/test_workspace_*.py`, all HTTP mocked).

**UI (`feature/portal-workspace-ui`):**
- `portal_pages/workspace.html` implementing screens 1–9 from the Paper file's exact styles;
- the `GET /workspace` page route;
- fixtures in `tests/fixtures/workspace/*.json` that match this contract;
- `scripts/preview_workspace.py` to run the page locally against fixtures;
- a contract test that checks each fixture against the shapes above.

**Integration (after both land):** merge into `feature/portal-workspace`, run the API contract test against the fixtures, run a live read-only smoke test on 3 properties, then Kyle reviews. No push or deploy without Kyle.

## Known gaps to report, not paper over

- Approvals are split across 7 stores, and loop recommendations have no stable IDs (they regenerate with each forecast).
- Neither ticket system has an "awaiting client" status.
- There's no per-property brief completeness score.
- There's no per-channel ad copy generation; the AEO writer and Marquee are stubs.
- Nothing tells a client what's live or when it last synced to Fluency.
- Occupancy history and cost per lease exist only inside the Red Light v2 PDF; there's no JSON.
- GA4 and the Google Ads API aren't wired on `main`; `/api/benchmarks` is seeded data.
- Only portal tickets and Ask check `require_company_access` today; every other legacy endpoint checks email presence only.
- `scripts/deploy_template.py` and the GitHub workflow only know `client-portal.html`.

## Phase 2 contract

# Workspace — Phase 2 contract (Kyle's decisions, 14 Sept 2026)

Copy this whole section verbatim into `docs/handoffs/PORTAL_WORKSPACE_BUILD_PLAN.md` under a heading "Phase 2 contract". The API and UI branches both carry an identical copy so the merge is clean.

## Decisions
1. **Signals gets its own screen.** Internal role only, reached from the Portfolio sidebar.
2. **New request, Search and Undo get built now.** No disabled or "coming soon" buttons.
3. **Full client transparency.**
   - Clients see ALL work items, including open items still on RPM's side, with status.
   - Internal comments, call notes and internal trail entries stay private.
   - The server filters these by role. Never rely on the page to hide them.
   - `internal_only` on Items goes away. Visibility now lives on individual trail entries and notes.
4. **One portal for clients and staff.**
   - Clients sign in to the same Workspace.
   - They see Work, Property, Performance, Plan & Spend, Ask and Reports. Portfolio and Signals are internal only.
   - "Client view" is no longer a client destination. It becomes an internal "Preview as client" toggle that renders the same screens with client-role filtering.
5. **Brand palette** (Paper file "RPM Templates" tokens):

   | Token | Hex |
   |---|---|
   | Juniper | `#444E4C` |
   | Black | `#282D27` |
   | Gray | `#76797A` |
   | Copper | `#AB784A` |
   | Sage | `#8BA395` |
   | Mint | `#BDDDD9` |
   | Rule | `#D9DEDC` |
   | Ink-on-dark | `#E7EBE9` |

   - Large fills, charts, borders and accents use these exact hexes.
   - Small text and button fills use darker shades of the SAME hues for AA contrast: copper button `#97693F`, copper text `#8A6A3F`, muted grey `#6F7372`.
   - No new hues, except semantic status colors. Derive those so they sit with the palette, and document them.

## Item changes
- Remove `internal_only`.
- Add `trail[].visibility` (`"client" | "internal"`) and `notes[]` (`{at, actor, text, visibility}`).
- For client-role callers, the server omits every `visibility: "internal"` entry. `comments_count` counts only what the caller can see.
- `client_visible` stays, and is `true` for every work item.

## `GET /api/workspace/signals?company_id=` (omit `company_id` for the portfolio)
Internal role only; clients get 403. Signals are what changed across properties that has not yet become work. They're computed by deterministic rules from existing data; no LLM writes a number.

```json
{"as_of": "…", "counts": {"high": 3, "medium": 7, "low": 12},
 "signals": [{"id": "occupancy_drop:123:2026-09-14", "company_id": "123", "property_name": "Skye Reserve",
   "kind": "occupancy_drop", "severity": "high",
   "title": "Occupancy down 3.1 points in 30 days",
   "detail": "76.0% today against 79.1% on Aug 15.",
   "metric": {"value": 0.76, "source": "aptiq", "as_of": "…"},
   "change": {"from": 0.791, "to": 0.76, "window_days": 30},
   "detected_at": "…", "work_item_id": null}],
 "gaps": [{"message": "Lead signals need GA4, which is not connected.", "source": "ga4"}]}
```

- `kind` is one of `occupancy_drop | stale_inventory | lease_wave | lead_drop | spend_pacing | reputation_drop | tracking_break | data_stale`.
- Wire every kind the portal has data for: Red Light scores, `aptiq_snapshots`, forecast / `ninjacat_metrics`, `data_quality.py` freshness. The rest are listed in `gaps`.
- `POST /api/workspace/signals/<id>/start-work {company_id}` creates a work item through the existing portal ticket create path. It requires verified identity and returns `{work_item_id}`.

## New request
**`POST /api/workspace/requests/draft {company_id, text}`**
- Turns plain words into proposed tickets through `skills/llm_gateway.py` with structured output.
- `fair_housing.py` checks the text.
- Draft only; nothing is filed.

Response:
```json
{"tickets": [{"draft_id": "d1", "title": "Reshoot A1 photography — post-renovation", "category": "creative",
   "team": "Creative Marketing Services", "needed_by": "2026-09-28", "needed_by_reason": "7 units open Oct 5",
   "attached_context": ["Property address", "Market", "PM contact", "Brand kit"], "warnings": []}],
 "gaps": []}
```
- `category` is one of `creative | web | paid | seo | listing | reputation | other`.
- `warnings` example: "A wrong amenity is also wrong in the paid ads and ILS feeds. We'll flag those too."

**`POST /api/workspace/requests {company_id, tickets:[…draft tickets, possibly edited…]}`**
- Files each ticket through the existing portal ticket create path (ClickUp).
- Requires verified identity.
- Returns `{created: [{draft_id, work_item_id, clickup_task_id}], failed: [{draft_id, reason}]}`.

**`GET /api/workspace/requests?company_id=`**
```json
{"recent": [{"title": "…", "status": "done" | "in_progress" | "new", "status_date": "…", "work_item_id": "…"}]}
```

## Search
**`GET /api/workspace/search?q=&company_id=`** (`company_id` optional)

Searches across the caller's accessible properties: property names via the property resolver, work item titles, report months, and Ask preset questions. At most 20 results, ranked.

```json
{"results": [{"type": "property" | "work_item" | "report" | "question", "id": "…", "title": "…", "subtitle": "…", "company_id": "…", "href": "#/item/hubdb_rec:991"}]}
```

## Undo
- **`POST /api/workspace/work/<id>/decision`** responses gain `undo: {available, until, reason}`. The window is 10 minutes, and undo is available only while no in-motion step has executed an irreversible action.
- **`POST /api/workspace/work/<id>/undo {company_id}`**
  - Requires verified identity AND that the caller is the person who decided.
  - Reverses through the source's handler where one exists (HubDB recommendation status back to pending; loop event `recommendation_undone`; content brief back to draft).
  - Sources with no safe reverse return 409 `{error: "not_undoable", reason}`.
  - Writes a `workspace_decision_undone` loop event.
  - Returns `{item, undone: true}`.

## UI contract requests — resolutions
1. `gaps[]` entries are `{message, source?}` everywhere.
2. The Item gains `evidence: {columns: [...], rows: [[...]], more_count}` when the source carries tabular evidence (e.g. the stale-units list); otherwise `null`.
3. The Item gains `sparkline: {label, points: [{x, y}], highlight_index}` when real series data exists; otherwise `null`.
4. The decision response gains `record: {label, approved_unedited, total, threshold, pct}` when the trigger has history; otherwise `null`.
5. `in_motion[]` gains `note` and `action: {label, href}`, both nullable.
6. Undo: see above.
7. Portfolio gains `scope_label` and `?view=needs_me|all`.
8. `top_item.needs_approval` (bool) is added; a `false` value renders as "Automatic".
9. Property gains `hubspot_url` and `brief_edit_url`, both nullable.
10. Plan `status` is one of `running | pending | ended | paused`, with `status_note` free text.
11. The Work badge counts `summary.open` items that need the caller's approval.
12. `changing[].date` is the planned go-live date.

## Phase 2 amendments (after the API's live smoke test)
Copy these into the plan doc too, directly under "Phase 2 contract".

1. **One gap shape everywhere:** `{message, field?, source?}`. `message` is the human-readable reason (previously `reason`); `field` names the null field. Both the API and the UI use this shape.
2. **Signed preview links are read-only.**
   - A signed link lets an internal person see the Workspace. It does NOT satisfy the verified-identity check for decisions, requests, start-work or undo; those need a Clerk session.
   - The API only sets `portal.identity_verified` from a link when `WORKSPACE_SIGNED_LINKS_CAN_DECIDE=true` (default false).
   - Links are honored only for `@rpmliving.com` emails, in addition to the internal role.
   - `/me` gains `can_decide` (bool). When it's false, the UI shows the decision panel with its buttons replaced by a quiet "Sign in to approve" link, not broken buttons.
3. **Cold start.**
   - Portfolio-wide reads (spend sheet, AptIQ exports) serve the last good copy immediately and refresh in the background. Every response carries `as_of` so staleness is visible.
   - Add an internal `POST /api/internal/workspace/warm` (internal key) that pre-builds those caches, for use before a demo and from cron.
   - Target: first request under 5 seconds after a warm.
4. **LLM-authored text in existing sources.** Don't blanket-withhold text that contains digits. Show it when every number in it can be matched to the source record's structured fields. Otherwise replace only the unmatched sentence and add a gap. A generic title is the last resort.
5. **Fair Housing filtering.**
   - Use `fair_housing.py`'s own severity/context API if it has one.
   - Hide copy from clients only on a high-severity or blocking result. Lower-severity matches are shown, and logged for internal review with a `fair_housing_review` flag on the item (visible to internal users only).
   - Plain words like "single", "color", "age" or "white" in a normal sentence must not hide anything by themselves.
6. **The brief paragraph.** Property `brief.text` uses the first non-empty value, override wins, from: `fluency_romance`, then a composed paragraph of `what_makes_this_property_unique_` + `property_voice_and_tone` + `additional_selling_points` (verbatim field text only, no LLM), then null with a gap.
7. **Transparency.** Remove `hidden_open_count` from client view. Clients now see all work items.
8. **Money guard.** Decisions on any budget or spend recommendation write `requires_signature: true` in the loop event payload. Add a test asserting that no workspace code path writes to Fluency, Google Ads or the spend sheet.
9. **Portfolio for internal users with no assigned properties** defaults to `view=all` (every property, paged 50 at a time, ranked the same way) instead of an empty list.
10. **Register `workspace_decision`, `workspace_decision_undone` and `workspace_request_filed`** as known event types in `loop_writer.py`.

## v3 rebuild

# Workspace — v3 rebuild (Kyle's direction, 14 Sept 2026, evening)

Copy this file verbatim into `docs/handoffs/PORTAL_WORKSPACE_BUILD_PLAN.md` under a heading "v3 rebuild". It supersedes the "Screens (v1)" table. Phase 2 features (search, New request, Undo, transparency, role-based nav, Signals, the decision flow) carry over into this structure.

## What changed and why
Kyle's verdict on the first build: "a straight-up copy of what we had on that separate portal." The Workspace must bring the **v3 product dashboards** to life, not just the "RPM Portal — PMM Workspace" board.

## Source of truth for layout
Paper file `01KX9VQE54KTEC2JT8BGP9FBVR` (read-only). Pass `fileId` explicitly; take exact structure and spacing from `get_jsx` / `get_computed_styles`.

| Workspace screen | Primary artboard | Supporting artboard |
|---|---|---|
| Dashboard | `CXR-2` v3 Dashboard | `FYF-1` v4 Portfolio (home) for the "Needs you" hero and the "what we did on our own" feed |
| Approvals | `DDE-0` v3 Approvals | `19O-0` Approvals Queue, `1HM-0` Approvals — Empty |
| Approval detail | existing Workspace item detail + Approved screens (keep) | `G4K-1` v4 Approval detail for the Found / Expect / If you skip + Now → Proposed block |
| Properties list | `GOO-0` v5 Properties | — |
| Property detail | `D4I-2` v3 Property Detail | `G73-1` v4 Property (the loop story): the loop timeline, relabeled Express / Tailor / Amplify / Evolve |
| Media Plan | `DM0-1` v3 Media Plan | `GJQ-1` v4 Media plan (flight calendar) |
| AI Visibility | `E06-1` v3 AI Visibility | `1PT-0` AI Visibility Audit, `21U-0` Competitor Deep-Dive |
| Content Engine | `E4U-1` v3 Content | `2AW-0` Content Library |
| Creative Library | `E4S-1` v3 Creative | — |
| Reports | the already-built monthly report page (Bromley format, calm tone), linked from property detail | `E4T-1` v3 Reporting ("Property Story") for the report header and forecast-accuracy block |
| Value | `GC6-1` v4 Value | — |
| Signals (internal) | the already-built Signals screen, restyled to v3 | — |

**Deferred (not for the demo):** Vendor Hub, Operating Card, Connections, Settings, Sites, Site Editor, Lead Scoring, Onboarding. Leave no nav items for them.

**Design system reference:** `7H-1` Design System and `EI-1` App Shell (spacing, radii, type ramp, component anatomy).

## Look: v3 layouts in the RPM Living palette
- **Keep v3's structure:** a light app shell with a left nav, a KPI strip, tables with quiet row lines, a right rail for the agent panel, findings and actions, status pills, and the small "loop running" status card at the bottom of the nav.
- **Keep v3's type scale and spacing tokens** (`--text-*`, `--space-*`, `--radius-*`) and v3's font (Inter), unless the Paper tokens contradict.
- **Replace v3's colors with the RPM Living palette** (Paper "RPM Templates"):

  | v3 token | Brand token | Hex |
  |---|---|---|
  | `--color-brand` | Copper (large fills and accents) | `#AB784A` |
  | Primary button fill | Copper, darker shade | `#97693F` |
  | Links and small accent text | Copper, darker shade | `#8A6A3F` |
  | `--color-brand-soft` | Mint tint | derived from `#BDDDD9` |
  | `--color-fg` | Black | `#282D27` |
  | `--color-fg-muted` | Gray, darker shade for AA | `#6F7372` |
  | `--color-border` | Rule | `#D9DEDC` |
  | Nav active state and dark surfaces | Juniper | `#444E4C` |
  | Ink on dark | Ink-on-dark | `#E7EBE9` |

- **Status colors** (healthy / attention / warning / critical / new) are harmonized with the palette: healthy derives from Sage `#8BA395` (darkened for text), and the others are warm, desaturated tones. Document the final values.
- Every text / background pair must pass WCAG AA.
- **No retired branding anywhere.** Rewrite all copy that names the retired product into first-person RPM voice: "We audited…", "RPM Digital drafted…", "Nothing changes until you approve."

## Navigation
- **Clients:** Dashboard · Approvals · Properties · Visibility · Content · Creative · Reports · Value
- **Internal:** the same, plus Signals.
- Property-scoped screens (Media Plan, Visibility, Content, Creative, Report) also open from Property detail with a "← Property name" back link, as in v3.
- **Role lens toggle on the Dashboard:** "Asset manager | Marketing manager" (v3), which changes KPI emphasis only.

## Data contract for the new screens (API)
Every number is `{value, source, as_of}` or null plus a `gaps[]` entry `{message, field?, source?}`. Nothing is invented; no LLM writes a number. Money never moves on a click. The fixtures carry realistic demo values; the live API returns real data or gaps.

### `GET /api/workspace/dashboard?lens=asset_manager|marketing_manager`
```json
{"greeting_name": "Dana", "as_of": "…",
 "kpis": {"ai_visibility": {"value": 62, "source": "ai_mentions", "as_of": "…"}, "portfolio_occupancy": {…}, "identified_savings": {…}, "waiting_on_you": {"value": 3, "source": "workspace_inbox", "as_of": "…"}},
 "health_tiles": [{"company_id": "…", "name": "Parkline", "score": 82, "band": "healthy|attention|warning|critical|new"}],
 "properties": [{"company_id": "…", "name": "…", "units": 138, "to_lease_90d": {…}, "overspend_per_year": {…}, "health": 82, "band": "healthy"}],
 "activity": [{"at": "…", "text": "Audited Apartments.com package — Parkline", "company_id": "…", "kind": "audit|draft|check|flag|forecast|decision|publish", "visibility": "client|internal"}],
 "waiting": [{"item_id": "…", "title": "Step down Parkline ILS", "subtitle": "Reduce Apartments.com Premium to Standard", "category": "cost|vendor|negotiate|content|creative|compliance"}],
 "loop_status": {"running": true, "property_count": 50, "last_pass": "…"},
 "gaps": []}
```
**Sources:**
- health: Red Light scores on HubSpot
- occupancy: AptIQ
- AI visibility: `ai_mentions` (HubDB), and `geo_*` tables when present
- savings: savings fields on recommendations, otherwise a gap
- activity: `loop_events`, workspace decisions, ticket recaps
- waiting: `workspace_inbox`

### `GET /api/workspace/approvals?category=`
```json
{"waiting": 3, "interrupts_count": 2, "approved_this_month": 12,
 "interrupts": [{"id": "…", "kind": "compliance|pacing|tracking", "title": "Compliance review — Arcadia West", "detail": "…", "company_id": "…", "item_id": "…", "primary_action": {"label": "Review now"}, "secondary_action": {"label": "Dismiss"}}],
 "batch": {"label": "Monthly batch — September 2026",
   "rows": [{"item_id": "…", "company_id": "…", "property": "Parkline", "action": "Step down Apartments.com Premium → Standard", "category": "cost", "savings_per_year": {…} | null, "can_edit": true}]},
 "stats": {"approval_rate": {…}, "edit_rate": {…}, "auto_approve_candidates": ["Visibility audits", "Content briefs"]},
 "gaps": []}
```
- Approve / Reject from a row calls the existing decision endpoint. Reject is `not_now` and requires a reason chip, as in the detail view.
- Edit and a row click open the item detail.
- Pacing interrupts NEVER pause or change a campaign directly. "Pause campaign" creates a work item / Ad Updates ticket for a human (money rule).
- `auto_approve_candidates` is computed from decision history (≥ 20 decisions, ≥ 90% approved unedited).

### `GET /api/workspace/property-overview?company_id=`
```json
{"name": "…", "city": "…", "state": "…", "units": 138, "objective": "Stabilize" | null,
 "health": {"score": 82, "band": "healthy", "source": "redlight", "as_of": "…"},
 "kpis": {"ai_visibility": {…}, "renewal_rate": {…}, "units_to_lease": {…}, "lead_to_lease": {…}},
 "exposure_forecast": {"months": [{"month": "2026-10", "units_to_lease": 42}], "source": "forecasting", "as_of": "…"} | null,
 "vendor_audit": [{"vendor": "Apartments.com", "package": "Premium", "monthly": {…}, "verdict": "over|fair|under|unknown", "basis": "…"}],
 "visibility_by_engine": [{"engine": "ChatGPT", "score": {…}}],
 "findings": [{"text": "…", "receipts": [{"label": "…", "source": "…", "as_of": "…"}]}],
 "recommended_action": {"item_id": "…", "label": "Step down Apartments.com"} | null,
 "draft_email": {"subject": "…", "preview": "…", "item_id": "…"} | null,
 "loop": [{"lens": "express|tailor|amplify|evolve", "status": "done|waiting|upcoming", "at": "…", "text": "…"}],
 "links": {"media_plan": "#/property/<id>/media-plan", "visibility": "…", "content": "…", "creative": "…", "report": "…"},
 "gaps": []}
```
A vendor `verdict` other than `unknown` requires a real market-rate source. There is none today, so return `unknown` with a gap.

### `GET /api/workspace/media-plan?company_id=`
```json
{"fiscal_year": "FY 2026-27", "envelope": {…}, "objective": "…" | null, "generated_at": "…",
 "months": [{"month": "2026-07", "units_to_lease": 47}],
 "channels": [{"channel": "Apartments.com", "monthly": [2800, …12 values], "monthly_avg": 2800, "annual": 33600, "share": 0.40, "cpl_target": 45 | null}],
 "allocated": {…}, "notes": ["Concentrated paid search in months 1–4 where 58% of exposure falls"],
 "gaps": []}
```
**Sources:** `forecasting.py` / AptIQ exposure; budget line items for channels. "Regenerate plan" produces a DRAFT only (a work item); it never changes live budgets.

### `GET /api/workspace/visibility?company_id=`
```json
{"score": {…}, "change": {"value": 4, "window": "month"} | null, "last_audit": "…", "next_audit": "…" | null,
 "engines": [{"engine": "ChatGPT", "score": {…}, "queries_hit": 12, "queries_total": 20}],
 "comp_stack": {"competitors": ["The Reserve", "Oakmont"], "rows": [{"surface": "AI visibility", "values": {"self": 71, "The Reserve": 65}}]} | null,
 "citation_sources": [{"source": "Apartments.com", "share": 0.34}],
 "recommendations": [{"text": "Write 3 FAQ pages targeting citation gaps in Perplexity", "action": {"type": "create_brief"}}],
 "alerts": [{"kind": "exposure|competitor", "text": "…"}],
 "gaps": []}
```
**Sources:**
- engines: `ai_mentions.py` (HubDB) now; the GEO tables `geo_responses`, `geo_brand_mentions` and `geo_sources` once the pilot runs, with the metric definitions from `GEO_PROGRAM_BUILD_HANDOFF.md`
- comp set: AptIQ

"Create brief" creates a content brief through the existing content brief path; it requires verified identity.

### `GET /api/workspace/content?company_id=`
```json
{"counts": {"recommendations": 3, "published": 1, "in_review": 2},
 "rows": [{"id": "…", "priority": "high|med|low|done", "type": "FAQ page|Schema|Guide|Blog", "title": "…", "gap_source": "Perplexity (0 citations, 4 queries)", "status": "draft_ready|in_review|not_started|published", "published_at": "…" | null, "item_id": "…" | null}],
 "impact": [{"text": "Parking FAQ: Perplexity citations 0→3 in 14 days."}],
 "gaps": []}
```
**Sources:** content briefs (HubDB `rpm_content_briefs`), the SEO content planner, GEO plans when present. Impact lines only from measured before/after data.

### `GET /api/workspace/creative?company_id=&type=photos|videos|ad_creative|floor_plans|documents`
```json
{"counts": {"assets": 142, "tracked_in_ads": 12},
 "top": {"name": "…", "ctr": {…}} | null, "lowest": {…} | null,
 "assets": [{"id": "…", "name": "parkline-pool-03", "thumbnail_url": "…" | null, "tags": ["amenity"], "origin": "generated|inherited|uploaded", "impressions": {…} | null, "ctr": {…} | null, "leads": {…} | null, "flag": "underperforming" | null}],
 "gaps": []}
```
**Sources:** the HubDB asset table and HubSpot Files; video variants. Per-asset ad performance is a gap (no Google Ads connector on main). Upload uses the existing asset upload path; requires verified identity.

### `GET /api/workspace/value?range=12m`
```json
{"period": "Sep 2025 – Aug 2026",
 "headline": {"savings_captured": {…}, "savings_identified": {…}, "changes_shipped": {…}},
 "rows": [{"change": "…", "company_id": "…", "property": "…", "annual_value": {…} | null, "decided_by": "you|automatic", "decided_at": "…"}],
 "totals": {"annual_value": {…}, "changes": 214, "automatic_share": {…}},
 "gaps": []}
```
**Source:** `workspace_decision` loop events plus savings recorded on the underlying recommendation. Do not show an "asset value at N× NOI" figure unless Kyle confirms the multiple; leave it out for now.

## Carried over from Phase 2 (still required)
Search, New request, Undo, full client transparency (server-side filtering of internal notes), role-based nav, Signals, read-only signed links with `can_decide`, visible `as_of` staleness, and the decision detail with Found / Expect / If you skip and the one-tap Not-now reasons.

## Contract changes (API)

The Phase 2 amendments above supersede this section where they conflict: gap entries are now `{message, field?, source?}` (item 1 below), and `hidden_open_count` is gone from `/client-view`.

Recorded by the API branch (`feature/portal-workspace-api`). Every change is additive or makes a field nullable; nothing in the shapes above was renamed or removed. `tests/workspace_contract.py` encodes the contract with these changes, so fixtures can be checked against it.

1. **`gaps[]` entries are `{field, reason}`.** The contract showed `gaps: []` without an entry shape. `/me` and `/client-view` also return `gaps`.
2. **`check_back` may be `null`.** No approval source on main carries a re-check date, and inventing one breaks the receipts rule. The decision response always returns `check_back: null` until a source provides one.
3. **`units_at_risk` (portfolio) is AptIQ advertised available units, not units vacant 90+ days.** No feed gives days vacant per unit: the AptIQ floor-plan export reports `Days on Market` per floor plan, not per unit. Ranking by available units is the closest real signal. `available_now.stale_90_plus` stays `null` with a gap for the same reason.
4. **Floor plans carry `days_on_market`, `source` and `as_of`.** Days on market came from the live floor-plan export. `source`/`as_of` exist so no number leaves without a receipt.
5. **AptIQ `as_of` is the export's own `Report Generation Date`.** If a row lacks it, `as_of` falls back to when the server fetched the export. `occupied.value` is AptIQ **advertised** occupancy (the export has no physical-occupancy column).
6. **`occupied` carries `total_source: "hubspot_company"`.** `total` (unit count) comes from the company record, while `value` comes from AptIQ.
7. **`/performance` echoes `range`.** Occupancy history isn't available as data, so `range=90|365` returns current values plus a gap. `coming_open_90d` is `null` and `coming_by_week` is `[]`, each with a gap: AptIQ reports 90-day exposure only as a percentage.
8. **`/plan` adds `source` and `as_of`.**
   - `source: "hubspot_line_items"` for the spend sheet; `as_of` is when the sheet was built.
   - `pending_changes` counts open (unsigned) deals on the company and is `null` if they can't be read.
   - `cost_per_lease` and `pointed_at` are `null` with gaps.
   - Management fee and hosting SKUs appear as a "Management and hosting" channel row, so shares sum to 1 over `monthly_total`.
9. **`/me` adds `portfolio_wide`.**
   - `companies[]` rows carry `source: "hubspot_company"`.
   - For internal users, `companies` are the properties that list the email as marketing manager, director or RVP. Internal users can still open any property.
10. **Portfolio rows carry `units_source: "hubspot_company"`.** Portfolio item counts cover recommendation cards, call prep, video variants and onboarding gaps only (a standing gap says so). Tickets, forecast recommendations and profile proposals appear on each property's Work screen.
11. **Connection `status` adds `"linked"`.** It means an id is on the company record but nothing verifies a sync (Hyly, GA4, Google Ads). `"connected"` is used only when a feed returned data (ApartmentIQ).
12. **People rows add `email`.** Marketing director and RVP rows use the email as `name`, because the company record only stores emails for those roles. The account manager is the HubSpot record owner.
13. **Item fields the sources can't fill:**
    - `start_by` is always `null`: no source carries a start-by date.
    - `comments_count` is always `null`.
    - `cost_note` is always `null`.
    - `due` is set only for call prep: the last day of its cycle month, when the item is replaced.
    - Undated items group under `this_week`.
14. **Decisions on sources with no dismissed state.** `not_now` on a content brief or video variant writes the loop event only. The workspace reads its own `workspace_decision` events back to show the item as done. That overlay needs BigQuery; without it there's a `trail` gap.
15. **Loop recommendation ids are per forecast run.** `loop_rec:<16-hex>` hashes `forecast_id` plus the recommendation, so the same recommendation gets a new id when the next forecast runs.
