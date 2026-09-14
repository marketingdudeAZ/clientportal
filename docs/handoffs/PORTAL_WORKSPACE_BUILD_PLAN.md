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

## Contract requests (UI)

Nothing below was changed silently: the UI builds against the contract above as written and hides what isn't there. These are additions the Paper screens need to reach full parity. All are optional fields, so none of them break the current shapes.

1. **`gaps[]` element shape.** The contract shows `gaps: []` but never an entry. The UI accepts either a plain string or `{message}` (it also reads `detail` / `reason`). Please settle on one; the fixtures use strings.
2. **Item: the evidence table (artboard B, "The units").** Suggest `table: {columns: [...], rows: [[...]], source, as_of}` or null. Without it, the unit-by-unit table is not shown.
3. **Item: the sparkline on the Work card (artboard A, "units coming available, by week").** Suggest `spark: {values: [..], highlight_index, label, source, as_of}` or null.
4. **Decision: the trigger's record bar (artboard F, "17 of 19 … 89%").** Suggest `record: {approved_unedited, total, threshold_pct, threshold_items}`. Today only the `written_down` sentence is shown.
5. **Decision: per-row caption and action in `in_motion[]`** (artboard F's "no spend, reversible" and the "Review the IO" button). Suggest `note` and `action: {label, href}`.
6. **Undo.** Artboard F has Undo; there is no endpoint, so the button is rendered disabled. If it's wanted: `POST /work/<id>/undo` with a window in the decision response (`undo_until`).
7. **Portfolio: scope and "All".** Artboard G's scope line ("Dallas + Austin") and the "Needs me / All 38" chips need `scope_label` and a `?scope=all` variant (or `quiet_properties`). Without them the UI shows the count only and no chips.
8. **Portfolio: the "Automatic" state.** Suggest `top_item.needs_approval` so rows whose only item runs itself read "Automatic" instead of a date.
9. **Property: action links.** "Open in HubSpot" and "Edit the brief" need `hubspot_url` and `brief_edit_url`; the buttons are omitted until then.
10. **Plan: `channels[].status` values.** The UI treats `pending` as amber, anything matching `ended|expired|paused|stopped` as red, and everything else as plain text. An enum in the contract would stop that guess.
11. **Work: which count the sidebar badge shows.** The UI uses `counts.to_do`. Paper's badge (4) matches neither `summary.open` (6) nor `counts.to_do` (3).
12. **Client view: what `changing[].date` means.** The UI captions it "live" (go-live date), as Paper does.

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
