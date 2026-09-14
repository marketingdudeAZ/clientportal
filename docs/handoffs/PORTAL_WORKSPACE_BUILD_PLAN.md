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

## Report contract

The monthly property marketing report (Paper artboard **D — Reporting** for layout, the sidebar from **A — Work**). Its content follows the June 2026 Bromley report designs; its tone follows Kyle's feedback below. Blueprint `workspace_report_bp` in `routes/workspace_report.py`; assembly in `skills/workspace_report.py`.

### Routes

| Route | Gate | Serves |
|---|---|---|
| `GET /api/workspace/report?company_id=&month=YYYY-MM` | 404 unless `WORKSPACE_ENABLED`; then `require_access("workspace")` and `require_company_access(company_id)` | the report JSON below |
| `GET /workspace/report?company_id=&month=` | 404 unless `WORKSPACE_ENABLED` | `portal_pages/workspace_report.html`; identity from Clerk Bearer or the signed preview link, never `?email=` |

- `month` defaults to the last complete month. A malformed month is `400`; a month outside `available_months` is `404`.
- The page sends the signed preview link (`?t=`, moved to sessionStorage and stripped from the URL) as header `X-Workspace-Link` on every API call. Verifying that header belongs to the Workspace auth layer (API workstream).
- A connector that is configured but fails is `502 {error, detail}`, never an empty report.

### Shape

Every number is a **receipt**, `{value, source, as_of}`, or `null` with an entry in `gaps[]`. Rates and shares are fractions (`0.9431`), recomputed from their components, never averaged. Every section carries a one-line `takeaway` for its collapsed header.

```json
{"property": {"company_id": "123", "name": "The Bromley at Brighton Crossing", "city": "Brighton", "state": "CO", "units": R},
 "month": "2026-06", "month_label": "June 2026", "available_months": ["2026-06"], "as_of": "2026-06-30",
 "summary": {"lead": {"value": R, "label": "leases", "sub": "$1,964 average cost per lease"},
             "text": "8 leases in June at $1,964 average cost per lease. 94.3% leased, with 16 signed residents moving in. …",
             "polished": false},
 "key_numbers": [{"key": "pct_leased", "label": "Leased", "value": R, "sub": "299 units"}],
 "occupancy": {"takeaway": "…", "leased_rate": R, "occupied_rate": R, "exposure_rate": R, "average_occupancy_rate": R,
               "total_units": R, "occupied": R, "available": R, "future_leases": R, "move_ins": R, "move_outs": R, "net_move_ins": R,
               "vacant": R, "vacant_rented": R, "vacant_unrented": R, "delayed_move_ins": R, "note": "…"},
 "funnel": {"takeaway": "…", "stages": [{"key": "created", "name": "Leads created", "count": R, "rate": R|null, "days_to_next": R|null}],
            "lead_to_lease_days": R|null, "net_applied": R, "note": "…"},
 "spend": {"takeaway": "…", "total": R|null,
           "vendors": [{"name": "Google Ads", "spend": R, "share": R, "leads": R, "cost_per_lead": R, "leases": R, "label": "Reviewing"}],
           "note": "…"},
 "attribution": {"takeaway": "…", "prospects": {"created": R, "scheduled": R, "toured": R, "applied": R, "leased": R},
                 "sources": [{"name": "Zillow", "color": "#1E8E77", "created": R, "influenced": R, "toured": R, "leased": R,
                              "stages": {"created": R, "scheduled": R, "toured": R, "applied": R, "leased": R}, "shape": "present_throughout"}],
                 "by_medium": {"created": [{"name": "Organic", "count": R, "share": R}], "leased": []},
                 "note": "…"},
 "website": {"takeaway": "…", "sessions": R, "users": R, "engaged_sessions": R, "engagement_rate": R, "avg_engagement_seconds": R,
             "conversions": R, "floorplan_page_views": R,
             "sources": [{"name": "Zillow", "sessions": R, "engaged_sessions": R, "bounce_rate": R, "conversions": R, "label": "Low engagement"}],
             "floorplans": [{"code": "B2", "views_per_user": R}], "note": "…"},
 "paid_search": {"takeaway": "…", "impressions": R, "clicks": R, "ctr": R, "cpc": R, "platform_spend": R, "ad_conversions": R,
                 "cost_per_conversion": R, "leads": R, "leases": R,
                 "benchmark": {"ctr_low": R, "ctr_high": R, "cost_per_conversion": R}, "note": "…"},
 "reputation": {"takeaway": "…", "target": R, "platforms": [{"name": "Google", "score": R|null, "reviews": R|null, "target": R, "status": "At target|Below target|Not connected"}]},
 "listings": {"takeaway": "…", "placements": [{"name": "Apartments.com", "tier": null, "impressions": R, "leads": R, "media_views": R, "cost": null}] | null, "note": "…"},
 "actions": [{"rank": 1, "title": "…", "detail": "…", "stake_label": "$12,140 monthly budget", "lens": "evolve", "work_item_id": null}],
 "sources_note": "…",
 "discrepancies": [{"key": "google_ads_spend", "text": "Google Ads spend differs between the spend manager ($12,140) and the ad platform ($5,269); we're reconciling before renewal.", "values": [R, R]}],
 "gaps": [{"section": "reputation", "metric": "yelp", "reason": "Not connected"}]}
```
`R` is a receipt, `{"value": 0.9431, "source": "derived:(total_units-available)/total_units", "as_of": "2026-06-30"}`. A receipt may add `"approx": true` when the source itself rounded (the export reports impressions as "21.5K").

### Sources and definitions

Definitions come from the Halo metric library (Hyly). The live assembler reads **only** the library's allowlisted objects, through the portal's BigQuery client, read-only:

| Section | Live source | Status |
|---|---|---|
| Occupancy | `t_oc_agg_occupancy_property` (month-end reading), `t_ot_agg_resident_activity_property`, `t_oc_agg_occupancy_operational`, `t_occupancy_rate` | live |
| Funnel counts | `t_contact_activity` milestones, `pai_journey_*` `h_ms_lease`, `prospect_journey` (net applied) | live |
| Days per step | Hyly velocity cards; not defined in the library | gap live, fixture only |
| First touch by source / medium | `t_contact_activity.mta_first_source_name` / `mta_first_medium_name` | live |
| Multi-touch influence | not defined in the library | gap live, fixture only |
| Spend by vendor | `vendor_spend_ledger` is outside the library allowlist; `/api/budget` is the contracted plan, not spend | gap live, fixture only |
| Website | GA4 has no connector on `main` | gap live, fixture only |
| Paid search | Google Ads API has no connector on `main` | gap live, fixture only |
| Reputation | no connector | gap live, fixture only |
| Listings | `apartmentscom_ils_resolved_v1` (ADR 0021), by `uuid` | live when configured |

Aggregation rules that the assembler enforces (and tests assert): shares and rates are recomputed from counts (for example, bounce rate = `(sessions − engaged) ÷ sessions`); a share's denominator is its parent metric, not the breakdown sum; stocks read the month-end snapshot; flows sum over the month; June 2026 figures carry the notice-data caveat (no notice fields before 2026-07-27) and the June 1–5 backfill caveat.

### Narrative and tone

Kyle's rule: the report goes to clients and owners, so it is calm and constructive, and it never hides or softens a number.

- **Headings** are plain section labels: Occupancy, Leasing funnel, Spend and leases, Where leads came from, Website, Paid search, Reputation, Listings, Next month.
- **The summary** leads with results and progress, then the main thing we're acting on, stated as the step RPM is taking.
- **Badges** are neutral: "Reviewing", "Unpaid", "Low engagement".
- **Banned in generated text** (a test enforces it): "leak", "none of them", "out the door", "flatters", "cannot all be right", "problem", "failing", "wasted", "burned".
- **Discrepancies stay**, phrased neutrally.

Narrative v1 is deterministic templates. The optional LLM polish (`WORKSPACE_REPORT_LLM_POLISH`, off by default, through `skills/llm_gateway.py` only) is accepted only when every number in its text appears in the input data and it passes the banned-phrase check; otherwise the template stands. Actions pass `fair_housing.py` and never propose radius tightening, ZIP targeting or audience layering.

### Page behaviour

The default view shows the summary, one row of key numbers, the funnel and Next month. Every other section starts collapsed, showing its heading and takeaway. The attribution section has a **Cards / Flow** toggle (Cards by default; Flow is an inline SVG). A month selector reloads the report. **PDF** is the browser's print, and the print stylesheet expands every section.

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

### Preview as client (UI mechanism)
- An internal user can turn on "Preview as client" in the sidebar. The page then sends the request header `X-Workspace-Preview-Role: client` on every **read** (`GET`) under `/api/workspace/*` and `/api/ask/*`. Writes (decisions, requests, start-work, undo) never carry it; a write is always made as the real caller.
- The server honors the header only when the verified caller is internal. From anyone else it is ignored.
- When honored, a read returns exactly what a client-role caller with access to that `company_id` would get:
  - no `visibility: "internal"` trail entries or notes;
  - `comments_count` counted the client's way;
  - no `fair_housing_review`;
  - 403 on `/portfolio` and `/signals`.
- `GET /me` ignores the header and always describes the real caller, so the page can keep showing the toggle and the preview banner.
- The toggle lives in `sessionStorage` for the tab only. It never goes into the URL, and a shared link never opens in preview.

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

### Contract requests (UI) — v3 rebuild
The v3 screens are built against the "v3 rebuild" contract above. These are the places the UI had to work around a missing piece; nothing in the contract was changed.

1. **Properties list.** The Properties screen (v5 artboard) needs a list endpoint with per-property spend/month, cost per lead and leads per week. None exists, so the UI builds the list from `/me` companies plus `/dashboard` `properties` and `health_tiles`. Those columns show units, to-lease, overspend, health and status instead.
2. **Create brief.** Visibility recommendations carry `action: {type: "create_brief"}` but no endpoint. The UI routes "Create brief" into New request, prefilled with the recommendation text, so it arrives as a ticket through the existing request path. A `POST /api/workspace/visibility/recommendations/<id>/brief` would make this one tap.
3. **Regenerate media plan.** "Regenerate plan" is draft-only, but no endpoint is named. The UI files it as a New request ("Draft only — no live budget changes").
4. **Interrupt actions.**
   - Pacing "Open an Ad Updates ticket" goes through New request, prefilled.
   - "Dismiss" only hides the interrupt for the browser session, because there is no dismiss endpoint. Please add `POST /api/workspace/approvals/interrupts/<id>/dismiss` if dismissals should persist.
5. **Creative upload.** The contract notes an existing asset upload path but gives no Workspace endpoint. The dropzone links to New request for now.
6. **Item `company_id`.** Approval rows span properties. The UI sets the company from the row before opening or deciding. Adding `company_id` to the Item shape would remove that coupling.
7. **Health score bands.** The UI uses the band the API sends. It falls back to ≥70 healthy, 50–69 attention, 35–49 warning, below 35 critical only when `band` is missing. Engine scores use ≥70 / 60–69 / below 60, as in the v3 audit screen. Please confirm or send bands.

## Round 4 — Kyle's review

# Workspace — Round 4: Kyle's review of the v3 preview (14 Sept 2026)

Copy this file verbatim into `docs/handoffs/PORTAL_WORKSPACE_BUILD_PLAN.md` under a heading "Round 4 — Kyle's review". It amends "v3 rebuild" wherever the two conflict.

## Bugs (fix first)

1. **Wrong draft opens on Property detail.**
   - On LYV Broadway, "Review" and "View full draft" in the recommended-action card open Remi West Dallas's Apartments.com rate email.
   - Only the "Step down Apartments.com" button opens the right one.
   - Every link on a property screen must resolve to that property's own items.
2. **Wrong property on Content.**
   - On LYV Broadway, opening "Pet policy and breed restrictions" shows The Bromley at Brighton Crossing, and Approve names The Bromley too.
   - Cause: the preview serves one property's detail data for every property with only the name swapped, so item IDs, drafts and text still point at the original property.
   - Fix: build per-property fixture data that's internally consistent (IDs, names, drafts, reports), and add a test that every item, draft and report reached from property X names property X.
3. **Media plan contradicts its own recommendation.**
   - The plan notes say "Keep Apartments.com running all year" while Apartments.com has a pending step-down.
   - Plan notes must never contradict a pending recommendation for the same property. Enforce this with a generator rule and a test.

## Dashboard

- **Remove the lens toggle.** No Asset manager / Marketing manager switch; drop the `lens` param and `kpi_order` behavior.
- **Sample book is 35 properties.** That's the minimum book a marketing manager has. The preview portfolio has 35 realistic RPM-style properties, and every total and count on screen agrees with them.
- **KPI strip, in this order:**
  1. Occupancy
  2. Units to lease (90 days)
  3. Leases this month
  4. Cost per lease
  5. AI visibility
  6. Actions we took for you
  7. Waiting on you

  Seven stats. Lay them out cleanly: two rows, or a primary row of four and a secondary row of three, following v3 spacing.
- **Remove Identified savings** from the dashboard.
- **KPI contract** (each value is `{value, source, as_of}` or null plus a gap):
  - `occupancy`
  - `units_to_lease_90d`
  - `leases_this_month`: month to date
  - `cost_per_lease`: last full month, total marketing spend ÷ leases
  - `ai_visibility`
  - `actions_taken`: changes shipped without needing approval in the last 30 days. Count automatic `workspace_decision` / loop events and completed automatic steps; say in the source what was counted.
  - `waiting_on_you`
- **Properties table:** replace "Overspend / yr" with **Occupancy**.

## Approvals

- **Group by what's being approved.** Category chips are All · Cost · Vendors · Content · Creative · Compliance. `negotiate` is no longer its own category; it maps to `vendor`.
- **No pacing for clients.**
  - Remove pacing interrupts from Approvals entirely. Clients don't manage pacing and must never see a pause-campaign control.
  - Pacing remains an internal Signal (`spend_pacing`) for RPM staff only.
- **One button per row: "Review".** It opens a review panel (a side drawer on desktop, full screen on mobile) containing:
  - **Why** we're proposing this: the finding, with receipts.
  - **Who it's for:** the audience or renter question it serves. For content this is the renter question and the prompts or fan-out queries it answers; for vendors, the property's leasing goal.
  - **What approving does:** the concrete next steps and who does them. Nothing vague.
  - **What happens if you skip.**
  - **Approve / Reject**, with Reject using the one-tap reasons.
- **Item contract additions** (nullable when a source has no data):
  - `why: {text, receipts[]}`
  - `for_whom: {text, questions: [str]}`
  - `approving_does: [{label, owner: "RPM Digital" | "vendor" | "you", when}]`
- **Creative recommendations never ask for a photo shoot.**
  - Recommend building new creative from existing assets that highlights X: new crops, video cuts, ad variants, AI-assisted edits within Fair Housing and disclosure rules.
  - A new photo shoot is only proposed when there are no usable assets for a required subject, and at most once per property per 12 months.
  - Add a rule and a test to the recommendation generator.
  - Replace the sample "Refresh amenity photos — The Maddux at Shadowood" with a build-new-creative item.
- **Keep the Fair Housing review tag.**

## Monthly Fair Housing review (new, per property)

- **Cadence:** monthly per property. The run date is shown, and the next run date is shown.
- **Scope:**
  - (a) website pages and listing copy, including content added by people outside the digital team (property marketing edits), checked with `fair_housing.py`;
  - (b) images in the property's asset library and on its site that are AI-generated or AI-edited, checked for the disclosure a New York state law on AI-generated imagery in ads may require.
  - **Do not encode that law's specifics until Kyle confirms the requirement.** Build the check as a pluggable rule with a TODO and a flag, and list it under open questions.
- **Surfacing:** a Compliance approval item titled "Your monthly Fair Housing review for <Property> found <n> item(s)". Its Review panel lists each finding: page or asset, quoted text or image, why it was flagged, and the suggested fix.
  - Approving applies suggested copy fixes as drafts for the web team; nothing publishes automatically.
  - When nothing is found, it appears in "Actions we took for you" as "Monthly Fair Housing review — no issues", not as an approval.
- **Contract:** new item source `fair_housing_review`. The review record is `{property, run_at, next_run, pages_checked, assets_checked, findings: [{kind: "copy" | "image", location, excerpt, reason, severity, suggested_fix}]}`.
- **Schedule:** add an internal `POST /api/internal/workspace/fair-housing-review/run` (internal key) that runs one property or all, for a monthly cron. It must be read-only against the website; findings are stored and approvals created.

## Properties list

- No "Overspend / yr" column. Show Units · Occupancy · To lease (90d) · Leases (month) · Health · Status.

## Property detail

- Fix bug 1.
- Keep findings in the right rail; Kyle likes them.

## Media plan

- **Always-on vs flighted mix** (Kyle's definition):
  - **Always-on**, running all year: Google Business Profile, SEO / content, website, and base ILS listings.
  - **Flighted with exposure:** paid search, PMax, Meta, and ILS upgrades or premium tiers.
- Show the plan in those two groups. Always-on rows are flat across 12 months; flighted rows follow the exposure forecast.
- Plan notes are generated from that mix and from pending recommendations, never contradicting them (bug 3).
- **Contract:** each channel gains `mode: "always_on" | "flighted"`.

## Visibility

- **Show the audit itself on the main page, Searchable-style:**
  - the tracked questions (prompts), grouped by topic and intent;
  - for each engine, whether the property is named or cited;
  - the follow-up searches the engines ran (query fan-out);
  - the sources they used.
- **"What we're writing" moves into the main column.** For each gap or fan-out query cluster, show the planned or drafted content piece that answers it and its status (drafted, in review, published), linking to the Content row and its Review panel. No content recommendations hidden in the right rail.
- **Sources:** `geo_prompts`, `geo_responses`, `geo_brand_mentions`, `geo_sources`, `geo_fanout` and `geo_plans` when present (definitions are in `GEO_PROGRAM_BUILD_HANDOFF.md` on `feature/geo-portal`); `ai_mentions` otherwise; gaps when neither has rows.
- **Contract additions:**
  - `prompts: [{id, text, topic, intent, engines: {<engine>: {named, cited}}}]`
  - `fanout: [{query, engine, count, content: {item_id, title, status} | null}]`
  - `writing: [{title, answers: [query], status, item_id}]`

## Content

- **Everything is draft-ready.** A content row exists only once a draft exists. Statuses are `draft_ready | in_review | published`; remove `not_started`.
- Each row's Review panel shows `why`, `for_whom` (the renter question plus the fan-out queries it answers), `approving_does` (for example: "RPM Digital publishes the FAQ to LYV Broadway's site within 5 business days; we re-check the AI answer in 30 days"), and the draft itself.
- Fix bug 2.

## Creative

- Remove the line "142 assets · 12 tracked in ads · top creative … · lowest …".
- **Drag-and-drop upload.** The drop zone and "Add videos/photos" accept files directly, with a per-file progress state.
  - The API gains `POST /api/workspace/creative/upload` (multipart; verified identity required), reusing the existing asset upload path.
  - No ticket form for uploads.

## Reports

- **Default to the last full month (August 2026)**, with the month picker.
- **Every property has a report.** The preview includes a complete August 2026 report fixture for LYV Broadway, plus at least 3 other properties, in the Bromley format and calm tone, with internally consistent numbers.
- On live data, properties without Hyly data show the sections that can be filled and name the gaps.

## Unchanged rules

- No retired product name.
- Money never moves on a click.
- Receipts on every number; nulls plus gaps, never invented values.
- Clients see all work; internal notes stay private.
- Fair Housing on all client- and channel-facing copy.

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

### Phase 2 and v3 (API)

16. **Gap entries are `{message, field?, source?}`** everywhere (amendment 1). This supersedes item 1. Gaps about internal sources are removed for client-role callers.
17. **Preview as client** uses the request header `X-Workspace-Preview-Role: client`. It is honored only for internal callers. Every read is rendered with client-role filtering; every POST returns 403 `preview_read_only`; Portfolio and Signals return 403. `/me` returns `role: "client"`, `can_decide: false` and `preview_as: "client"`. The header is in the CORS allow-list.
18. **Signed links are read-only by default.** `/me` adds `can_decide` and `signed_link`. `WORKSPACE_SIGNED_LINKS_CAN_DECIDE=true` makes a link a verified identity. Links are honored only for `@rpmliving.com` emails that also resolve to the internal role.
19. **Items.**
    - `internal_only` is removed.
    - `trail[]` and `notes[]` entries carry `visibility`.
    - `comments_count` counts visible notes: it is `0`, never `null`.
    - `fair_housing_review` (`{severity, terms}`) is added for internal callers only. A high-severity match replaces the title with a neutral one and nulls `found` / `expect` / `if_skip` for clients.
    - `evidence` is populated only for onboarding checks.
    - `sparkline` is always `null`: no per-item series exists.
20. **Work** adds `summary.needs_approval`, the badge count. **Client view** drops `hidden_open_count`. `changing[].date` is always `null` with a gap: no source records a planned go-live date. Rows add `item_id`.
21. **Decision response** adds:
    - `record`: null unless the property has earlier decisions on the same source; `threshold` is always null.
    - `undo {available, until, reason}`.
    - `in_motion[].note` and `in_motion[].action {label, href}`. Links exist for a created HubSpot deal (when `HUBSPOT_PORTAL_ID` is set) and a ClickUp task.

    The `workspace_decision` event payload adds `requires_signature` and `title`.
22. **Undo** returns `{item, undone: true}`.
    - Undoable: not-now on Red Light cards, call prep, content briefs, video variants and profile proposals; both actions on forecast recommendations.
    - Approvals that created a deal, a ClickUp task, an asset-library row or a profile write return 409 `{error: "not_undoable", reason}`. So does anything past 10 minutes or with no recorded decision.
    - A caller who is not the original decider gets 403.
23. **Portfolio** adds `view`, `scope_label`, `page`, `page_size`, `next_page` and `top_item.needs_approval`. With `view=all`, `top_item` is `null` for quiet properties.
24. **Property** adds `hubspot_url` (internal callers only, needs `HUBSPOT_PORTAL_ID`), and `brief.source` (`fluency_romance` or `composed`). `brief_edit_url` is always `null`: the editor opens by one-time token.
25. **Plan** channel `status` is `running`, or `ended` when every line item in the channel is at $0. `pending` and `paused` aren't tracked per channel.
26. **Signals** add `change.source`. The `data_stale` signal for the Red Light run date carries a `:red_light` suffix on its id, so it stays distinct from the AptIQ one. Start-work returns 201 `{work_item_id, clickup_task_id}`.
27. **Requests.**
    - Draft tickets add `team` (the label of the matching portal ticket type) and file under a fixed category-to-type map (creative/listing → Ad Updates; paid/seo → Digital Marketing Review; web/reputation/other → General Ticket).
    - A high-severity Fair Housing match in the request text returns 400 `fair_housing`, and the model is never called.
    - Filing goes through the existing portal ticket gate, which by default admits RPM staff only.
28. **Search** results are ranked in Python. Internal callers search every managed property name, and property-resolver lookups apply only to numeric ids. Work items and reports need `company_id` for internal callers.
29. **v3 screens.**
    - `health_tiles[]` and `properties[]` add `source` / `health_source` / `units_source`. The dashboard adds `lens`, `kpi_order` and `scope_label`. `loop_status.running` is `null` when loop events can't be read.
    - Approvals: `approved_this_month` may be `null`; pacing interrupts add `signal_id`; `primary_action` adds `creates`, `href` and `note`; `can_edit` is `false`.
    - Property overview adds `objective_reason`, `units_source`, `findings[].item_id`, `loop[].item_id`, and `exposure_forecast.basis` (the months come from AptIQ exposure windows, not `forecasting.py`).
    - Visibility: `engines[].queries_hit` / `queries_total` are `null` on the ai_mentions fallback; `citation_sources[]` adds `share_source`; `change` adds `source`.
    - Creative `assets[]` add `type` and `file_url`.
    - Media plan channels add `source`, and months add `source` when set.
    - Value rows' `decided_by` adds `"team"` for a colleague's decision.
    - `POST /visibility/create-brief` (202) and `POST /media-plan/regenerate` (201) are added.

### Round 4 (API)

30. **Media plan notes** never contradict a pending recommendation. A note that says to keep, continue or increase a channel is dropped while a recommendation naming that channel is waiting on a decision. The plan says instead: "<channel>: “<title>” is waiting on a decision, so the plan holds <channel> as contracted until it is decided."
31. **Dashboard KPIs** are exactly `occupancy`, `units_to_lease_90d`, `leases_this_month`, `cost_per_lease`, `ai_visibility`, `actions_taken`, `waiting_on_you` (order in `workspace_dashboard.KPI_ORDER`). `lens`, `kpi_order` and `identified_savings` are removed, and `?lens=` is ignored.
    - `leases_this_month` and `cost_per_lease` come from the Hyly lake (`pai_journey` lease events) for Hyly beta properties only. The source string names the coverage. Cost per lease is contracted spend from HubSpot line items ÷ leases.
    - `actions_taken` counts, over 30 days, autopilot approvals plus clean monthly Fair Housing reviews, and its source says so.
32. **Approvals** categories are `cost`, `vendor`, `content`, `creative`, `compliance`. `negotiate` folds into `vendor`. Pacing interrupts are removed, so pacing stays an internal Signal. `interrupts[].kind` is `compliance` only.
33. **Items** add `why {text, receipts[]}`, `for_whom {text, questions[]}` and `approving_does [{label, owner, when}]`. All three come from fields the item already carries; no model is involved.
    - A creative item that proposes a photo shoot becomes "Build new creative that highlights <subject> from existing assets" when usable assets exist.
    - A shoot is proposed only when no usable asset exists, and at most once per property per 12 months. Unknown shoot history never proposes one.
34. **Monthly Fair Housing review.** New source `fair_housing_review` (category `compliance`) and `POST /api/internal/workspace/fair-housing-review/run`.
    - Called with `{company_id}` it runs synchronously and returns 200 with the review record. Called with `{all: true, limit?}` it returns 202 and runs in the background. Anything else is 400. The internal key is required.
    - The review record is `{company_id, run_at, next_run, pages_checked, assets_checked, image_check, findings[{kind, location, excerpt, severity, reason, suggested_fix}], findings_count}`.
    - Findings become one approval. A clean run is an actions-taken event, not an approval.
    - Approve files a ticket with draft fixes and publishes nothing.
    - The AI-image disclosure check is a pluggable rule behind `WORKSPACE_FH_AI_IMAGE_CHECK` and is off by default.
35. **Properties and dashboard rows** replace `overspend` with `occupancy` (metric), `leases_month` (metric) and `status`.
36. **Media plan** `channels[]` add `mode`: `always_on` or `flighted`. Always-on months are flat. Flighted months follow the exposure forecast, and months beyond it are `null`.
37. **Visibility** adds:
    - `prompts[{id, text, topic, intent, engines{<engine>: {named, cited}}}]`
    - `fanout[{query, engine, count, content{item_id, title, status}?}]`
    - `writing[{title, answers[], status: drafted|in_review|published, item_id}]`

    These come from the GEO tables when the property has rows there, and from the ai_mentions audit otherwise. On the ai_mentions path, `named`, `topic` and `intent` are `null` and `fanout` is empty, each with a gap.
38. **Content** rows' `status` is `draft_ready`, `in_review` or `published`. `not_started` is removed, and a row exists only once a draft exists. Rows add `keyword`, `why`, `for_whom` and `approving_does` (empty unless `draft_ready`).
39. **Creative upload.** `POST /api/workspace/creative/upload` takes multipart `company_id`, `files[]`, and optional `metadata` (JSON list) and `category`.
    - It returns 201 `{uploaded[{filename, file_url, thumbnail_url, asset_name, category, subcategory}], skipped[{filename, reason}]}`.
    - It returns 400 for no files or nothing stored, 401 for an unverified identity and 403 for preview-as-client.
40. **Reports** default to the last full month for every property. A property outside the Hyly beta returns 200 with property identity, units and listings, plus a gap naming each Hyly-only section. It no longer returns 404.

## Round 4 — Kyle's review

# Workspace — Round 4: Kyle's review of the v3 preview (14 Sept 2026)

Copy this file verbatim into `docs/handoffs/PORTAL_WORKSPACE_BUILD_PLAN.md` under a heading "Round 4 — Kyle's review". It amends "v3 rebuild" wherever the two conflict.

## Bugs (fix first)

1. **Wrong draft opens on Property detail.**
   - On LYV Broadway, "Review" and "View full draft" in the recommended-action card open Remi West Dallas's Apartments.com rate email.
   - Only the "Step down Apartments.com" button opens the right one.
   - Every link on a property screen must resolve to that property's own items.
2. **Wrong property on Content.**
   - On LYV Broadway, opening "Pet policy and breed restrictions" shows The Bromley at Brighton Crossing, and Approve names The Bromley too.
   - Cause: the preview serves one property's detail data for every property with only the name swapped, so item IDs, drafts and text still point at the original property.
   - Fix: build per-property fixture data that's internally consistent (IDs, names, drafts, reports), and add a test that every item, draft and report reached from property X names property X.
3. **Media plan contradicts its own recommendation.**
   - The plan notes say "Keep Apartments.com running all year" while Apartments.com has a pending step-down.
   - Plan notes must never contradict a pending recommendation for the same property. Enforce this with a generator rule and a test.

## Dashboard

- **Remove the lens toggle.** No Asset manager / Marketing manager switch; drop the `lens` param and `kpi_order` behavior.
- **Sample book is 35 properties.** That's the minimum book a marketing manager has. The preview portfolio has 35 realistic RPM-style properties, and every total and count on screen agrees with them.
- **KPI strip, in this order:**
  1. Occupancy
  2. Units to lease (90 days)
  3. Leases this month
  4. Cost per lease
  5. AI visibility
  6. Actions we took for you
  7. Waiting on you

  Seven stats. Lay them out cleanly: two rows, or a primary row of four and a secondary row of three, following v3 spacing.
- **Remove Identified savings** from the dashboard.
- **KPI contract** (each value is `{value, source, as_of}` or null plus a gap):
  - `occupancy`
  - `units_to_lease_90d`
  - `leases_this_month`: month to date
  - `cost_per_lease`: last full month, total marketing spend ÷ leases
  - `ai_visibility`
  - `actions_taken`: changes shipped without needing approval in the last 30 days. Count automatic `workspace_decision` / loop events and completed automatic steps; say in the source what was counted.
  - `waiting_on_you`
- **Properties table:** replace "Overspend / yr" with **Occupancy**.

## Approvals

- **Group by what's being approved.** Category chips are All · Cost · Vendors · Content · Creative · Compliance. `negotiate` is no longer its own category; it maps to `vendor`.
- **No pacing for clients.**
  - Remove pacing interrupts from Approvals entirely. Clients don't manage pacing and must never see a pause-campaign control.
  - Pacing remains an internal Signal (`spend_pacing`) for RPM staff only.
- **One button per row: "Review".** It opens a review panel (a side drawer on desktop, full screen on mobile) containing:
  - **Why** we're proposing this: the finding, with receipts.
  - **Who it's for:** the audience or renter question it serves. For content this is the renter question and the prompts or fan-out queries it answers; for vendors, the property's leasing goal.
  - **What approving does:** the concrete next steps and who does them. Nothing vague.
  - **What happens if you skip.**
  - **Approve / Reject**, with Reject using the one-tap reasons.
- **Item contract additions** (nullable when a source has no data):
  - `why: {text, receipts[]}`
  - `for_whom: {text, questions: [str]}`
  - `approving_does: [{label, owner: "RPM Digital" | "vendor" | "you", when}]`
- **Creative recommendations never ask for a photo shoot.**
  - Recommend building new creative from existing assets that highlights X: new crops, video cuts, ad variants, AI-assisted edits within Fair Housing and disclosure rules.
  - A new photo shoot is only proposed when there are no usable assets for a required subject, and at most once per property per 12 months.
  - Add a rule and a test to the recommendation generator.
  - Replace the sample "Refresh amenity photos — The Maddux at Shadowood" with a build-new-creative item.
- **Keep the Fair Housing review tag.**

## Monthly Fair Housing review (new, per property)

- **Cadence:** monthly per property. The run date is shown, and the next run date is shown.
- **Scope:**
  - (a) website pages and listing copy, including content added by people outside the digital team (property marketing edits), checked with `fair_housing.py`;
  - (b) images in the property's asset library and on its site that are AI-generated or AI-edited, checked for the disclosure a New York state law on AI-generated imagery in ads may require.
  - **Do not encode that law's specifics until Kyle confirms the requirement.** Build the check as a pluggable rule with a TODO and a flag, and list it under open questions.
- **Surfacing:** a Compliance approval item titled "Your monthly Fair Housing review for <Property> found <n> item(s)". Its Review panel lists each finding: page or asset, quoted text or image, why it was flagged, and the suggested fix.
  - Approving applies suggested copy fixes as drafts for the web team; nothing publishes automatically.
  - When nothing is found, it appears in "Actions we took for you" as "Monthly Fair Housing review — no issues", not as an approval.
- **Contract:** new item source `fair_housing_review`. The review record is `{property, run_at, next_run, pages_checked, assets_checked, findings: [{kind: "copy" | "image", location, excerpt, reason, severity, suggested_fix}]}`.
- **Schedule:** add an internal `POST /api/internal/workspace/fair-housing-review/run` (internal key) that runs one property or all, for a monthly cron. It must be read-only against the website; findings are stored and approvals created.

## Properties list

- No "Overspend / yr" column. Show Units · Occupancy · To lease (90d) · Leases (month) · Health · Status.

## Property detail

- Fix bug 1.
- Keep findings in the right rail; Kyle likes them.

## Media plan

- **Always-on vs flighted mix** (Kyle's definition):
  - **Always-on**, running all year: Google Business Profile, SEO / content, website, and base ILS listings.
  - **Flighted with exposure:** paid search, PMax, Meta, and ILS upgrades or premium tiers.
- Show the plan in those two groups. Always-on rows are flat across 12 months; flighted rows follow the exposure forecast.
- Plan notes are generated from that mix and from pending recommendations, never contradicting them (bug 3).
- **Contract:** each channel gains `mode: "always_on" | "flighted"`.

## Visibility

- **Show the audit itself on the main page, Searchable-style:**
  - the tracked questions (prompts), grouped by topic and intent;
  - for each engine, whether the property is named or cited;
  - the follow-up searches the engines ran (query fan-out);
  - the sources they used.
- **"What we're writing" moves into the main column.** For each gap or fan-out query cluster, show the planned or drafted content piece that answers it and its status (drafted, in review, published), linking to the Content row and its Review panel. No content recommendations hidden in the right rail.
- **Sources:** `geo_prompts`, `geo_responses`, `geo_brand_mentions`, `geo_sources`, `geo_fanout` and `geo_plans` when present (definitions are in `GEO_PROGRAM_BUILD_HANDOFF.md` on `feature/geo-portal`); `ai_mentions` otherwise; gaps when neither has rows.
- **Contract additions:**
  - `prompts: [{id, text, topic, intent, engines: {<engine>: {named, cited}}}]`
  - `fanout: [{query, engine, count, content: {item_id, title, status} | null}]`
  - `writing: [{title, answers: [query], status, item_id}]`

## Content

- **Everything is draft-ready.** A content row exists only once a draft exists. Statuses are `draft_ready | in_review | published`; remove `not_started`.
- Each row's Review panel shows `why`, `for_whom` (the renter question plus the fan-out queries it answers), `approving_does` (for example: "RPM Digital publishes the FAQ to LYV Broadway's site within 5 business days; we re-check the AI answer in 30 days"), and the draft itself.
- Fix bug 2.

## Creative

- Remove the line "142 assets · 12 tracked in ads · top creative … · lowest …".
- **Drag-and-drop upload.** The drop zone and "Add videos/photos" accept files directly, with a per-file progress state.
  - The API gains `POST /api/workspace/creative/upload` (multipart; verified identity required), reusing the existing asset upload path.
  - No ticket form for uploads.

## Reports

- **Default to the last full month (August 2026)**, with the month picker.
- **Every property has a report.** The preview includes a complete August 2026 report fixture for LYV Broadway, plus at least 3 other properties, in the Bromley format and calm tone, with internally consistent numbers.
- On live data, properties without Hyly data show the sections that can be filled and name the gaps.

## Unchanged rules

- No retired product name.
- Money never moves on a click.
- Receipts on every number; nulls plus gaps, never invented values.
- Clients see all work; internal notes stay private.
- Fair Housing on all client- and channel-facing copy.
