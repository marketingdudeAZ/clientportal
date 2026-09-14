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
