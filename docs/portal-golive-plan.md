# Portal go-live: ticketing through the portal + community brief field audit

**Status:** workstream A0 landed; A1 resolved and verified, **not yet pasted
into Render**. Workstream B untouched.
**Date:** 2026-08-10 · updated 2026-08-17
**Branch:** `pilot/portal-tickets` (3 ahead / 9 behind main)
**Goal:** get real property-marketing users into the portal filing tickets.

**Landed on this branch** (commits `0ee7325`, `23f267b`, `524fc42`): A0.1
(identity, as asserted email + pilot roster — Clerk was not workable for these
users), A0.2 (feature gate = the pilot mechanism), A0.2b (audience enforced on
write), A0.2c (BigQuery JSON-in-env + `/health`), A0.2d (retry/backoff, field
cache, bounded list fan-out), A1.5 (degraded states), A1.7 (description bug),
A2 (Account Manager via `hubspot_owner_id`), A5 (status labels), A6 (recap
matches on the mapping table).

**Still open before a real user files:** paste the env block below into Render,
A0.3 (ClickUp Form shutdown sign-off — not code), pick the pilot property and
the named human, then the A1 acceptance run.

## Why now

Two things block go-live, and only one of them is code.

**1. Ticket intake still happens outside the portal.** Requesters fill ClickUp
Forms directly. They re-type property info the portal already knows, and nobody
without ClickUp access can see what's open for a property. ClickUp is becoming
marketing-services-only, so "just give them ClickUp" stops being an answer.

**2. The community brief asks for too much.** 55 fields across 13 sections. A
Community Manager opening it sees a wall of textareas and bounces. The brief is
the load-bearing input to Fluency and to every client-facing generator, so a
brief nobody completes is a portal nobody gets value from.

Neither is a greenfield build. Most of workstream A already exists and is dark.

---

## Ground truth (read the code, 2026-08-10)

### Ticketing is built end to end and currently inert

| Piece | Where | State |
|---|---|---|
| Ticket module | `webhook-server/portal_tickets.py` (452 lines) | Built |
| API blueprint | `webhook-server/routes/portal_tickets.py` (132 lines) | Built, **registered** (`routes/__init__.py:22`) |
| Frontend | `hubspot-cms/templates/client-portal.html:3705, 3841, 3881` | Built (types / create / list) |
| Mapping table | `migrations/0012_portal_tickets_table.py` | Built |
| Type registry | `config.py:164` `PORTAL_TICKET_TYPES` — 8 types | Built |
| Status map | `config.py:204` `PORTAL_TICKET_STATUS_MAP` | Built |
| List-ID discovery | `GET /api/portal-tickets/admin/discover` | Built |
| **ClickUp list IDs** | env `CLICKUP_LIST_*` × 8 | **UNSET** |

`configured_types()` (`portal_tickets.py:63`) skips any type whose list-ID env
var is empty. All 8 are empty, so `GET /api/portal-tickets/types` returns
`{"types": []}` and the picker renders blank. **The single blocker on the
existing feature is config, not code.**

### The brief field census

**55** `BriefField` entries across 13 sections (counted from source, not by hand):

| Bucket | Count | Reaches Fluency? |
|---|---|---|
| Readonly (identity + `year_built`) | 7 | No — identity columns |
| `internal=True` | 17 | **No** — filtered at `fluency_feed.py:89` |
| Structurally excluded (`documents`, `tracking`) | 2 | No — `_EXCLUDE_KEYS` |
| Machine-resolved (`hs_resolved` set) | 20 | Yes, where non-internal |
| **Editable fields a CM actually faces** | **48** | — |
| **…minus every internal field** | **31** | the real floor |
| → `data:` columns written to the Fluency sheet | ~30 | Yes |
| …of those, **textareas** | 28 total in schema | Yes |

**The arithmetic that governs workstream B:** deleting *every* `internal=True`
field takes the CM from 48 editable fields to **31**, still overwhelmingly
free-text. The burden is not the internal fields — it is the Fluency-facing
override-only set (`taglines`, `brand_adjectives`, `differentiators`, `romance`,
`residents_love`, `goals`, `initiatives`, `onsite_developments`,
`local_partnerships`, `onsite_events`, `motivations_considerations`…), none of
which any machine fills today. **Deletion alone cannot make this brief
completable.** See B2 for what this does to the cut list.

---

# Workstream A — ticketing live

## A0. Gates that must close before any list ID is set

Added after review. Setting the `CLICKUP_LIST_*` env vars is a **portfolio-wide
launch**, not a soft start. These three gates are what make it survivable.

### A0.1 — Authorization and multi-tenant scoping (blocking)

`routes/portal_tickets.py:34-38` treats **any non-empty `X-Portal-Email`** as
authorized. `server.py:66-88` overwrites that header only when a Bearer token
is present; with no Bearer token, "legacy header behavior is preserved" — a
caller-supplied header is trusted as sent. `company_id` then flows straight
from the request body (`create`) and query string (`list`) into ClickUp and
BigQuery **with no check that the caller is associated with that property**.

Consequences today: `GET /api/portal-tickets?company_id=<any>` enumerates
another property's ticket subjects, and `POST /create` files tickets on their
behalf. CORS constrains browsers, not `curl`. This violates CLAUDE.md rule 3
("all multi-tenant data is `uuid`-scoped — no cross-client data leakage").

**Fix:** require a verified Clerk identity on these routes (reject when
`CLERK_SECRET_KEY` is set and no Bearer arrives), and scope `company_id`
against the caller's portfolio in a shared `require_company_access()` helper.
This lands **before** A1. It is the reason A1 is not a five-minute config task.

### A0.2 — Feature gate = the pilot mechanism (blocking)

The repo already has a rollout system — `feature_access.FEATURES`
(`feature_access.py:94-112`), HubDB stage table, beta allowlist, and
`require_access()` (`_route_utils.py:68`), used today by `redlight`,
`community_brief`, and `clickup_loop`. **`portal_tickets` is not registered,
and the blueprint calls `require_access` zero times** (verified).

So "pilot property" currently has no enforcement path: setting the list IDs
turns ticketing on for every portal user simultaneously. The plan's "lights up
type-by-type" is about *types*, not *audience*.

**Fix:** register `Feature("portal_tickets", "Portal Ticketing",
default_stage=STAGE_BETA)`, call `require_access("portal_tickets")` in the
blueprint, and put the pilot user's email in the HubDB allowlist. ~20 lines,
and it is what converts A1 from a launch into a pilot.

### A0.2b — `create_ticket` enforces `audience` on read but NOT on write

`configured_types()` filters internal types (`portal_tickets.py:75`).
`create_ticket` does **not** — `_type_by_key` (`:86`) returns any registry
entry, and `create_ticket` (`:299-304`) checks only that the type exists and
has a list ID. So a portal user who POSTs `{"ticket_type": "new_business"}`
files straight into the internal sales-intake list. Same for `dispo_cancel`,
the list that governs whether a property gets **cancelled**.

This is inert today only because the list IDs are unset — and
`discover_list_ids` emits env lines for **all 8 types, including both internal
ones** (`:425-442`). **A1's "paste the block into Render env" is the step that
converts a latent authorization bug into a live one.**

**Fix (~10 lines, blocks A1):** thread `internal: bool` from `_is_internal(request)`
into `create_ticket` and reject non-client types for non-internal callers.
Paste only the 6 client-facing env lines.

### A0.2c — Verify BigQuery is actually configured in prod

`bigquery_client._get_client()` requires a service-account **file on disk**
(`bigquery_client.py:49-56`: `os.path.exists(sa_path)`), and
`is_bigquery_configured()` returns False without it. Render supplies env vars,
not files — and note `fluency_feed._gc()` (`:235`) explicitly handles *both*
raw-JSON-in-env and a path, while `bigquery_client` handles only a path.

If the SA is supplied as JSON on Render: creates succeed, `_record_mapping`
returns early without writing, `_read_mappings` returns `[]`, and the portal
shows **"No open requests" forever** — the identical symptom to unset list IDs,
with no error anywhere. Same class: **has `migrations/0012` actually been run
against the prod dataset?** The plan listed it as "Built," which is a claim
about the file, not about BigQuery.

**This replaces `/types` as A1's acceptance gate:** file one ticket end to end,
reload, confirm it appears in "what's open"; independently confirm
`SELECT COUNT(*) FROM portal_tickets ≥ 1` and expose `is_bigquery_configured()`
on `/health`.

### A0.2d — The server has 4 threads total

`start.py:27`: `serve(app, ..., threads=4)` — for the entire platform.
Against `_TIMEOUT = 10` and zero caching in `clickup_client`:

| Path | ClickUp calls | Worst case | Threads held |
|---|---|---|---|
| `GET /types` (6 types) | 6 × `get_list_fields` | 60s | 1 |
| `GET /api/portal-tickets` (limit 50) | up to 50 × `get_task` | **500s** | 1 |
| Bulk file, 30 properties | 60 | **600s** | 1 |

Four colleagues opening the tickets tab makes the whole portal unreachable.
Waitress has no per-request timeout, so an abandoned request keeps burning its
thread. A1's original acceptance (`/types` returns 6 types) is *exactly* the
call that takes 60s.

**Fix, before the pilot:** TTL-cache `get_list_fields` (~15 lines); replace the
per-task loop in `list_tickets` with a batched read (**the single
highest-leverage change on this surface**); add 429/`Retry-After` + capped
backoff, copying `hubspot_client.py:154`; raise `threads` in `start.py:27`.

### A0.3 — The ClickUp Form shutdown decision (blocking, not code)

The 7 public ClickUp Forms are bookmarked, pasted into Teams, and linked from
onboarding email. If they stay open, portal volume is whatever leaks — **and
the portal's "what's open for this property" is structurally incomplete**,
because `_read_mappings` only ever returns rows the portal itself wrote. A
status window that silently omits externally-filed tickets teaches users the
portal lies.

This is Open Question #1 promoted to a gate. It needs a dated, per-type
shutdown commitment owned by the marketing-services lead. **If that sign-off
cannot be obtained, A1–A5 are premature** and the justified work this cycle is
A0.1, A0.2, A2, A5 and the recap fix (A6) only.

## A1. Discover and configure the 8 ClickUp list IDs

`GET /api/portal-tickets/admin/discover` (internal key) walks workspace
`9011805260`, matches lists to `PORTAL_TICKET_TYPES` by label + alias, and
returns a paste-ready `env_block`.

- Run it against production.
- Any type in `unmatched_types` gets its ID resolved by hand.
- Paste the block into Render env. The picker lights up type-by-type as IDs
  land — no all-or-nothing cutover.

**Acceptance:** `GET /api/portal-tickets/types` returns 6 client-facing types
with non-empty `fields[]`. `dispo_cancel` and `new_business` stay internal
unless `CLICKUP_DISPO_AUDIENCE` / `CLICKUP_NEW_BUSINESS_AUDIENCE` flip.

### A1 execution record — 2026-08-17

Discovery run against the live workspace (487 lists). **Name matching alone was
wrong, and the plan's "confirm `list_name` per type" is what caught it.**

`creative_ad_copy` — label "Ad Updates: Photos & New Specials", alias
"Creative" — loose-matched a list literally named **"Creative"**
(`901112821771`) sitting in the *Paid Media @ RPM* space under
"Process, Best Practice & Documentation/Guidance". Its real intake list is
**"Creative + Ad Copy Updates"** (`901111120522`). Pasting the original block
would have filed every ad-update ticket in the pilot into a documentation
folder nobody triages — and the portal would have reported success.

`campaign_review`'s registered `form_slug` (`8cjaf2c-2771`) pointed at
**"[OLD] - Campaign Performance Review"**, which is **archived**. The form was
rebuilt on the `[NEW]` list as `8cjaf2c-68111`. An archived list still accepts
tasks; nobody opens it.

**Fix:** `discover_list_ids()` now resolves each type through its form view's
`parent` — the id in a public ClickUp form URL is a view id, and the view names
the list it files into, so this is authoritative where a name can only guess.
Name/alias matching is demoted to a second opinion; disagreements come back in
`conflicts`, archived lists are refused with a `warnings` entry rather than
emitted, and unresolvable types carry a `reason`. `clickup_client.get_view()`
added; four tests pin the behaviour.

Verified resolution — every type via its form, no conflicts, none archived:

| type | list id | list name | audience |
|---|---|---|---|
| `new_account_build` | 901111890057 | [NEW] - New Account Build | client |
| `budget_update` | 901111926317 | [NEW] - Budget Update | client |
| `general` | 901111999695 | [NEW] - General Ticket | client |
| `creative_ad_copy` | 901111120522 | Creative + Ad Copy Updates | client |
| `campaign_review` | 901114166834 | [NEW] - Campaign Performance Review | client |
| `rebrand` | 901111120555 | Rebrands | client |
| `dispo_cancel` | 901111965002 | [NEW] - Dispo/Cancel | **internal** |
| `new_business` | 901111120539 | New Business Requests | **internal** |

All 8 live in the *Digital Marketing Services* space.

**Paste into Render — client-facing only:**

```
CLICKUP_LIST_NEW_ACCOUNT_BUILD=901111890057
CLICKUP_LIST_BUDGET_UPDATE=901111926317
CLICKUP_LIST_GENERAL=901111999695
CLICKUP_LIST_CREATIVE_AD_COPY=901111120522
CLICKUP_LIST_CAMPAIGN_REVIEW=901114166834
CLICKUP_LIST_REBRAND=901111120555
```

Do **not** paste `CLICKUP_LIST_DISPO_CANCEL=901111965002` or
`CLICKUP_LIST_NEW_BUSINESS=901111120539`. Those govern property cancellation
and sales intake; A0.2b rejects them on write, but there is no reason to arm
them for a ticketing pilot.

Setting these arms the surface for whoever the feature gate admits, so
`PORTAL_TICKETS_PILOT_EMAILS` and the HubDB allowlist go in **first**.

### A1 side finding — two `config.py` files, and they had drifted

`config.py` (repo root) is a hand-maintained duplicate of
`webhook-server/config.py`. Which one an import gets is decided by `sys.path`:
`start.py` inserts `webhook-server/` first, so **production reads the
webhook-server copy**, while `python3 scripts/<name>.py` from the repo root —
and a full `pytest` run — read the root one.

The root copy still held the pre-fix `form_slug`s, the bare `"Creative"` alias,
`account_manager` (a HubSpot property that does not exist), and
`"pending pm approval" → "In progress"`. A repo-root script running discovery
would have emitted the wrong list ids and looked correct doing it. The three
ticket blocks are re-synced and the header now says which file wins where.

**Not fixed, flagged:** `webhook-server/apartmentscom_ingestion.ingest_date()`
does `from config import BIGQUERY_APARTMENTSCOM_DAILY_TABLE`, and that name
exists **only** in the root copy — so that import fails under the production
path. Out of scope here; it is the same duplication, pointing the other way.

## A1.5 Degraded states — must ship WITH A1, not after

The plan originally specified no states at all. Each of these is live today and
fires on day 1.

**A type with no list ID renders a dropdown containing a sentence.**
`configured_types()` silently omits unconfigured types (`portal_tickets.py:73`);
the UI then renders `<option value="">No request types available</option>`
(`client-portal.html:3712`). The "lights up type-by-type" rollout this plan
endorsed is the *worse* case: with 3 of 6 visible, a requester cannot tell "my
type isn't on yet" from "I'm misreading these labels," so they pick the nearest
wrong one. **A1 as originally written ships the exact silent misroute A3 exists
to prevent.**
→ `/types` returns every registry type with `available:false` + reason.
Unavailable types render disabled: "not online yet — use the form," linking
`form_slug`, which is already on every type in `config.py:164` so the fallback
URL costs nothing. Zero available → don't open a form; show the form links and
the AM's email.

**A rate-limited ClickUp produces a fieldless form, not an error.**
`get_list_fields` returns `[]` on *any* failure, so `types_with_schema` still
offers the type with `fields: []`. The requester fills in Subject, submits
successfully, and files a task missing every required field. Marketing cannot
tell a lazy requester from a degraded fetch.
→ `clickup_client` must distinguish failure (`None`) from genuinely-empty
(`[]`); treat `None` as type-unavailable. Add a 15-minute TTL cache on
`get_list_fields` keyed by list id, stale-on-error. Schemas change monthly;
this alone removes most rate-limit exposure.

**The ticket list silently drops tickets.** `list_tickets` issues one
`get_task` per mapping row — up to 50 sequential calls per page load
(`portal_tickets.py:330-337`) — and does `if not task: continue`. Under
throttling a requester's 6 open requests render as 2, with no indication.
Partial rendered as complete is the worst failure available on a status
surface.
→ Placeholder row per unresolvable task plus a list-level banner and count.

**Day-1 empty list.** Every property's list is empty at go-live even though
real work is open in ClickUp, because the mapping table only holds
portal-created tasks. Ship the copy "requests filed outside the portal aren't
shown here" in A1 — it is true on day 1 regardless of how A0.3 is decided.

## A1.6 Journey fixes on the ticket form (cheap, and they decide adoption)

- **Deflection currently runs after the work.** The KB panel appears on
  "Next" (`client-portal.html:3778`), keyed only off the subject line, inserted
  above the first form row with no `scrollIntoView` — so on a long form the
  button appears to do nothing, and when the panel does land it reads as "you
  wasted your time." Deflection value is inversely proportional to sunk cost,
  and this is the maximum-sunk-cost moment. → Move deflection to the top, live
  against the description, before any field work. This also serves A3.
- **"Next" should read "Submit request."** Labeling a
  hard-to-reverse action with a continuation verb is a real bug.
- **Post-submit shows no reference number, no expected response time, no echo
  of what was submitted.** Its only action is **"Open in ClickUp"**
  (`:3916`) — a link into the system this plan's own premise says these users
  will not have. That is day 1's first escalation. → Inline confirmation:
  request number, type, what you told us echoed back, who it went to, expected
  first response. Gate the ClickUp link on `audience != "client"`.
- **"In progress" and "Done" render with the same CSS class** (`:3911`). The
  two states a requester most needs to distinguish look identical. Ties to A5.
- **Every error on both surfaces is a browser `alert()`** — 11 call sites
  (ticket: `:3757, 3758, 3770, 3866, 3869`; brief: `property_brief.py:585, 603,
  615, 630, 665`). No inline marking, no focus, no scroll to first bad field.

## A1.7 A live bug the "it's just config" framing would have shipped

`portal_tickets.py:279-283`:

```python
for k, v in (extra or {}).items():
    if k in ("subject", "name") or not v: continue
    if k not in applied:
        lines.append(f"{k}: {v}")
```

`extra` is the raw `fields` dict, whose keys are ClickUp **field IDs**
(`client-portal.html:3819-3826` does `out[fid] = val`). `applied` is keyed by
field **name** (`portal_tickets.py:253`). The two key spaces never intersect,
so the `not in` test always passes: **every value the requester types is
duplicated into the task description prefixed with a raw ClickUp field UUID** —
`9a3c…: Please update the pool photos`. The comment says "surface free-text
that isn't a mapped custom field"; the code surfaces everything that *was*
mapped.

Nothing asserts on the description in `tests/test_portal_tickets.py`, and A1's
original acceptance checked custom fields, not the description. This ships.

**Fix:** record ids alongside names (`applied_ids.add(d.get("id"))` at `:253`)
and filter `_description` on ids. Add the assertion to the existing create test.

## A2. Verify the prefill sources actually exist on HubSpot

`PORTAL_TICKET_PREFILL_SOURCES` (`config.py:192`) maps ClickUp field names to
HubSpot company properties:

**VERIFIED 2026-08-10** against the live HubSpot schema (838 company
properties) and a sample of 8 `plestatus = "RPM Managed"` companies:

| ClickUp field | HubSpot property | Exists? | Populated (n=8) |
|---|---|---|---|
| Property URL | `website` | ✅ | 8/8 |
| Property Code | `property_code` | ✅ | 8/8 |
| Market | `market` | ✅ | 8/8 (`rpmmarket` also 8/8, identical values) |
| **Account Manager** | **`account_manager`** | ❌ **DOES NOT EXIST** | — |
| uuid / UUID | `uuid` | ✅ | 8/8 — R1: read only, never written |

**4 of 5 are fine.** The one real defect is `Account Manager`. There is no
`account_manager` company property, so `_prefill_values` resolves it empty on
every single ticket, forever.

**Why that is worse than it sounds — the structural bug behind it.**
`form_schema()` (`portal_tickets.py:120-127`) strips every field whose name
matches `PORTAL_TICKET_PREFILL_FIELDS` *unconditionally*, before knowing
whether prefill will resolve anything. `_prefill_values` then swallows failures
by design (`:174`). So an unresolvable prefill field is **neither on the form
nor on the task** — it is not "the requester re-types it," it is a permanently
blank field on every ticket reaching the services team. That makes portal
tickets strictly worse than the ClickUp Form they replace. This failure mode is
generic: it fires for any prefill mapping that breaks later, silently.

**Do:**
1. Resolve Account Manager from `hubspot_owner_id` (populated 8/8) via an
   owners lookup. `ticket_manager._get_company_owner` (line 62) and
   `_get_owner_names` (line 451) already implement exactly this — reuse them
   before deleting that module in A4.
2. **Render a prefill field on the form when its value resolves empty**, rather
   than hiding it. Requires passing the resolved prefill map into schema
   generation. This is the fix that makes the class of bug impossible, not just
   this instance.
3. Add a startup assertion that every `PORTAL_TICKET_PREFILL_SOURCES` value is
   a real HubSpot property, failing loudly at boot rather than silently per
   ticket.

**Acceptance:** a create call against a known company stamps all five identity
values onto the ClickUp task; deliberately breaking one mapping makes that
field appear on the form instead of vanishing.

## A3. AI triage — describe it, we classify

The requester types what they need in plain language. We suggest a ticket type,
pre-fill what we can infer, and they confirm or override. The 8-type picker
stays as the fallback and as the override surface.

```
"our pool is closed for reno through August"
  → suggests: Ad Updates: Photos & New Specials  (confidence 0.82)
  → pre-fills: start/end dates, flags the amenity change
  → requester confirms → POST /api/portal-tickets/create
```

**New module:** `webhook-server/ticket_triage.py` (Layer 2 skill).

```python
def classify(text: str, *, company_id: str = "") -> dict:
    """{suggested_type, confidence, reason, field_hints{}, alternatives[]}"""
```

Design constraints:
- **Suggest, never auto-file.** Confirmation is mandatory. A misroute costs a
  requester more than the picker ever did.
- Below a confidence floor (start at 0.6), return no suggestion and show the
  plain picker. Silence beats a confident wrong answer.
- Classify against **live** `configured_types()`, not a hardcoded list, so a
  type with no list ID is never suggested.
- Route the LLM call through the LLM Gateway per CLAUDE.md rule 2. Reuse
  `rec_voice` / `CLIENT_VOICE_RULES` for any prose we render back.
- The existing KB-deflection step runs **first** — help articles before a
  ticket. That is the volume lever; triage must not bypass it.
- Log every (text → suggestion → what the requester actually picked) to
  `loop_events` so the confusion matrix is measurable and the floor is tunable
  from data rather than taste.

**Acceptance:** a golden set of ~40 real request phrasings per type; top-1
accuracy reported; override rate visible in `loop_analytics`.

## A4. Retire the HubSpot Service Hub ticket path

Decision: **ClickUp is the only ticket path.** One system, no drift.

This is bigger than `ticket-page-scope.md` §Decisions #1 implied. The Service
Hub surface is **7 routes**, not one:

| Route | `server.py` | ClickUp equivalent today |
|---|---|---|
| `POST /api/ticket` | 2844 | `POST /api/portal-tickets/create` ✅ |
| `GET /api/tickets` | 2895 | `GET /api/portal-tickets` ✅ |
| `GET /api/tickets/mine` | 2925 | **none** ❌ |
| `POST /api/tickets/bulk` | 2950 | **none** ❌ |
| `POST /api/ticket/<id>/stage` | 3058 | n/a — ClickUp owns status |
| `GET /api/ticket/<id>/thread` | 3149 | **none** ❌ (intentional) |
| `POST /api/ticket/<id>/reply` | 3212 | **none** ❌ (intentional) |

### Three capability gaps that must be closed or consciously dropped

**1. "My Tickets" — submitter-scoped view.** `ticket_manager.list_my_tickets`
(line 264) returns a person's own tickets across properties, rendered at
`client-portal.html:10287`. `portal_tickets.list_tickets` is **company-scoped
only**. A regional manager covering 12 properties loses their single queue.
→ ~~Add `submitted_by` filtering to `_read_mappings`. Small change.~~
**Re-estimated after review: this is not small.** The column exists
(migration 0012), but the rendered card (`client-portal.html:10310-10328`)
reads `stage_id`, `stage_label`, `owner_name`, `company_name`, `company_uuid` —
and `_shape_task` (`portal_tickets.py:349-363`) provides **none** of them.
`_read_mappings` doesn't even SELECT a company name (`:396`). Matching the
existing capability needs: N × `get_task` (see A0.2d — a regional with 12
properties is dozens of sequential 10s calls), a company-name join, a caching
layer (`ticket_manager` has a 60s one at `:280`), an owner concept that ClickUp
assignees don't map to, and an entitlement check so `submitted_by` can't be
spoofed via A0.1.

**Also:** `submitted_by` is polluted at the source. The frontend sends
`window.__PORTAL_EMAIL__ || 'portal@rpmliving.com'` (`client-portal.html:3706`,
`:3845`, ~15 sites). Every unresolved user becomes the same sentinel, so a
`submitted_by` filter returns a shared pile rather than "mine" — and this
**cannot be retrofitted** once the mapping table fills with that address.
Fail closed (401) before A1, not after.

**2. Bulk filing.** `POST /api/tickets/bulk` (`client-portal.html:10397`) files
one subject against many selected properties. `portal_tickets.create_ticket`
takes a single `company_id`. Killing this without a replacement is a visible
downgrade for anyone managing a portfolio.
→ Loop `create_ticket` over selected companies, return per-property success or
failure, and make partial failure legible rather than silent.

**3. KB draft generation — DOWNGRADED from blocker after review.** The original
framing (port KB drafting to ClickUp before retirement) overstated the work.
`kb_writer.create_kb_draft` **already takes** `source: str = "HubSpot"  #
"HubSpot" or "ClickUp"` and `ticket_url` (`kb_writer.py:58-59`) — the port is
done. What's missing is a *trigger*, not a module.

And the decay may already have happened: the only trigger today is
`POST /api/ticket/<id>/stage` when `stage == "closed"` (`server.py:3081-3086`)
— **portal-only**. Tickets an AM closes inside HubSpot, which is where AMs
work, never generate a draft. `create_kb_draft` also produces a Google Doc plus
a Sheet row, i.e. a draft needing a human to publish before `/api/kb-search`
can find it.
→ Small task: a ClickUp status webhook (`routes/webhooks/` exists,
`clickup_recap.py` already consumes ClickUp task events) calling
`create_kb_draft(source="ClickUp", ...)`. **First, measure how many drafts the
HubSpot path produced in the last 90 days** — if the answer is zero, this stops
being a risk at all and A4's sequencing gets simpler.

**4. Thread/reply — dropped on purpose.** Internal ClickUp chatter never
reaches the client; that is the entire point of the recap layer. The AI recap
note on the company record is the client-facing written record.

### Sequence

1. Close gaps 1–3.
2. Flag the Service Hub routes off (`SERVICE_HUB_TICKETS`, default false) —
   dark, reversible, same pattern as `SELF_CHECKOUT_ENABLED`.
3. Run dark for one full ticket cycle.
4. Delete routes, `ticket_manager.py`, and the thread/reply UI.
5. Update `RPM_Client_Portal_Technical_Overview.html` (7 stale references).

`triage.py:329` only mentions `ticket_manager.STAGES` in a comment — no runtime
dependency. Confirm before deletion.

## A6. Make the recap match on the mapping table (added after review)

The entire value proposition of filing in the portal is the recap note on the
company record when the work completes. But `clickup_recap.match_company_for_ticket`
(`clickup_recap.py:81-101`) re-derives the company by website domain →
`property_code`/`yardi_id` → name, and returns `(None, reason)` with only a
`logger.info` when the match isn't unique (`:134-137`). **It never reads the
`portal_tickets` mapping table**, which holds `task_id → company_id` exactly
(`portal_tickets.py:368-385`), nor the `hubspot_company=` stamp the portal
writes into the task description (`:275`).

So for any property with a stale or missing `website` property, the requester
files in the portal, waits, and gets **no recap at all** — silently, with no
error surfaced anywhere.

**Fix:** in `match_company_for_ticket`, look up the mapping table by `task_id`
first and fall back to the existing search. Small change; it is what makes A1's
promise actually land. Add an alert (not a `logger.info`) when a portal-created
task fails to match, since by construction that should be impossible.

## A5. Confirm the status map

`PORTAL_TICKET_STATUS_MAP` currently collapses `pending pm approval` →
"In progress". The scope doc flagged this as undecided. If a ticket is parked
waiting on the PM, "In progress" is a lie the requester acts on — they wait
instead of unblocking it. Recommend a distinct **"Needs your approval"** label,
since it is the one status where the requester can do something.

---

# Workstream B — community brief field audit

## B0. Rebase first — main has moved onto this exact territory

This branch is **9 commits behind `origin/main`**, and those commits land
squarely on workstream B's files. Planning against the current working tree
plans against a stale schema.

What main added that this plan did not account for:

| Change | Why it matters to B |
|---|---|
| `webhook-server/brief_hooks.py` (new, 79 lines) | **A second path from the brief to Fluency.** `community_brief.write_field` now fans every real change out via `brief_hooks.on_field_written` — Bridge 2 pushes a per-property Fluency upsert immediately instead of waiting for the nightly batch; Bridge 3 notifies ClickUp. Flag-gated on `FLUENCY_REALTIME_SYNC`, off-thread, best-effort. |
| `fluency_feed.py` +129 lines | The batch path B3 reasons about has changed underneath. Re-read before writing any cut procedure. |
| `community_brief.py` +16 lines | The hook dispatch inside `write_field`. It reads `field.label`, so **`brief_hooks` is a consumer of field metadata** and belongs in the B1 audit. |
| `routes/clickup.py`, `clickup_notes.py` (new) | Brief changes now notify the fulfillment team in ClickUp — this intersects workstream A, not just B. |
| `tests/test_brief_realtime_fluency.py` | The contract to keep green. |

**Consequence for B3.** The plan's cut procedure said "remove from `SECTIONS` →
the column disappears on the next sync." With Bridge 2 live there are **two**
propagation paths with different timing, and a per-property upsert may carry a
different column set than the batch. Any field removal must be traced through
**both**.

**Do:** rebase onto `origin/main` and re-read `fluency_feed.py`,
`brief_hooks.py`, and `community_brief.write_field` before B1 starts. Nothing
in workstream B is safe to execute against the pre-rebase tree.

## B0.5 — WHICH brief? There are two, and this plan never said

**Verified.** Two live brief surfaces with different schemas and different
endpoints:

| | Token brief | In-portal brief |
|---|---|---|
| Rendered by | `routes/property_brief.py:675-708` (`render_template_string`) | `client-portal.html:2674-2890` (card grid) |
| Schema | **`community_brief.SECTIONS` — all 55 fields** | different, older: `voice_and_tone`, `website_cms`, `bot_on_website`… |
| Write endpoint | `POST /api/community-brief/<token>/field` | `PATCH /api/client-brief` |
| Reached by | **emailed token link only** | logged-in portal nav |
| Fair-housing gate | **NONE** (verified: zero matches in the file) | ✅ `server.py:1166`, `:3443` |
| `edited_by` attribution | **not passed** to `write_field` | yes |

Everything in this plan's workstream B — the 55 fields, the census, the cut
list — describes the **token brief**. But a CM who logs into the portal sees
the *other* one. Completion work aimed at the wrong surface is entirely wasted,
and this plan gave no way to tell them apart.

**Decide first, before B1:** which surface is the real community brief going
forward, and does the other get retired or repointed? Every downstream B task
depends on the answer.

### B0.5a — Fair-housing gate gap (compliance, blocking)

`fair_housing_gate.check_fair_housing` runs on `/api/client-brief`
(`server.py:1166`, `:3443`). It does **not** run on the token brief's field
write (`routes/property_brief.py:724-738`) — and the token brief is the surface
that owns `target_resident` ("age, income, lifestyle… protected-class
attributes NEVER reach ad platforms") and `motivations_considerations`
("Fair Housing safe: NOT demographics"). The two fields whose own hint text
warns about protected classes are written through the **ungated** path, with no
`edited_by`, so there is no attribution either.

For a company advertising 700+ multifamily properties, an ungated write path
into ad-copy inputs is not a code-quality issue. **Fix before any other B work:**
run the gate on the token path and pass `edited_by`. Small change, and it is
the highest-severity finding in workstream B.

### B0.5b — The drafter that would fix completion already exists, dark

`brief_ai_drafter.py` plus `/api/client-brief/draft` and a **working
diff-review UI** (`client-portal.html:4100-4194`) already draft brief content
from the website / pitch deck / RFP with per-field confidence. The 55-field
token brief uses **none of it**.

This plan proposed cutting fields while a drafter that fills them sits unused
on the adjacent surface. Confirm-not-author turns a 31-field composition task
into a 31-tap review task. Repointing the drafter at the token brief's schema
(see H3 — the two schemas have drifted apart and must be reconciled) will move
completion far more than the entire cut list. **This, not deletion, is
workstream B's main lever.**

### B0.5c — The empty-state copy is telling users not to fill the brief

`routes/property_brief.py:422` renders **"Not yet computed"** for *every* empty
value — including the 16 override-only fields (`taglines`, `differentiators`,
`romance`, `goals`, `residents_love`…) that no machine will ever compute. The
page tells the CM, in its own words, that the blank fields are the system's
job. Paired with the "Not set" badge, the brief actively instructs users not to
complete it.

**Fix:** editable-with-no-pipeline → "Your answer goes here," input rendered
inline. Badges "Not set"/"Edited" → "Needs you"/"Saved." One template
conditional; plausibly the highest completion-per-hour change available.

## B1. Trace every field to a consumer

For each of the 55 fields, answer one question: **who reads this?**

1. Is it a `data:` column in the Fluency sheet? (`fluency_feed._brief_fields`)
2. Does an agent prompt read it? (`brief_ai_drafter`, `rec_voice`,
   `recommendation_gen`, `ticket_recap`, `reputation/prompts/*`)
3. Does a report, `/accounts`, or the portal read it?
4. Does `services/fluency_ingestion/*` derive or consume it?
5. **Nobody → cut candidate.**

Output: `docs/brief-field-audit.md` — one row per field, its consumers, and a
keep/cut/merge call with the evidence attached.

Build it as a **test**, not a spreadsheet: a checked-in audit that fails CI when
a field exists with no declared consumer. Otherwise the schema re-bloats within
two quarters and we redo this exercise.

## B2. Prime cut candidates

Named up front so the audit either confirms or refutes them:

- **Operations & Tech (10 fields).** ~~All `internal=True`, so nothing reads
  them.~~ **REFUTED before the audit started.** All 10 are rendered on the
  internal `/accounts` property detail —
  `hubspot-cms/templates/accounts-detail.html:518-530`, section
  "Operations & Tech — Internal", banner *"Operational reference. Not used in
  ad copy."* — and they are provisioned HubSpot props fetched at
  `server.py:6653`. They have a real consumer. The honest move is
  **re-audience, not cut**: read-only on `/accounts`, removed from the
  CM-facing brief. Net effect: **−10 of 48 CM fields, −0 Fluency columns.**
  Worth doing, but it does not solve completion.
- **`website_last_updated`, `marketing_budget`** — budget already lives in the
  spend sheet (`spend_sheet.get_company_monthly_spend`). Duplicating it in the
  brief invites two numbers that disagree.
- **`unit_level_details`** — overlaps the structured `floor_plans` table.
  Free-text restating structured data drifts from it.
- **`priorities` vs `initiatives` vs `goals`** — three free-text strategy
  fields with adjacent meanings. Consolidate unless consumers differ.

## B3. Cut safely — REWRITTEN. The original procedure was wrong and dangerous.

**What this plan originally said:** *"Remove from `SECTIONS` → `fluency_feed`
auto-tracks the schema, so its `data:` column disappears on the next sync."*

**What the code actually does** (`fluency_feed.py:279-283`, verified):

```python
header_changed = header != columns
if header_changed:
    ws.clear()                  # ← the ENTIRE tab
    ws.update("A1", [columns])
```

Removing one field from `SECTIONS` changes the column set, which **clears the
whole live Fluency sheet and rewrites it from scratch.** Three consequences the
original plan did not account for:

1. **Row-set truncation, not a column drop.** The rewrite only contains
   companies with `plestatus IN ('RPM Managed','Onboarding','Dispositioning')`
   (`:57`, `:169`) and a non-empty `uuid` (`:201`). Every other row in that tab
   — dispositioned properties Fluency still references, manually added rows,
   anything whose `plestatus` drifted — is **permanently destroyed**.
2. **`clear()` succeeds, `append_rows` fails → an empty sheet with a header.**
   The append is one call whose exception is caught into `errors[]` and
   *returned*, not raised (`:323`). `sync()` exits 200 with
   `{"errors":[...], "written":0}` while the Fluency data source for 700+
   properties is empty. Nothing pages anyone.
3. **`sync(sample=N)` after a schema change is a data-loss button.**
   `header_changed=True` → clear → write N rows. The natural "let me test this
   on five properties first" instinct wipes the sheet down to five rows.

### B3 prerequisites — land these BEFORE removing any field

- **Guard the sample path.** `if header_changed and sample: raise RuntimeError(...)`
  at `fluency_feed.py:279`. One line; removes the worst foot-gun in the repo.
- **Auto-snapshot inside `sync()`.** `ws.get_all_values()` to GCS or BigQuery
  immediately before `clear()` fires — automatically, not as a human checklist
  item. The original step 4 ("snapshot field values from HubSpot") does **not**
  restore the sheet.
- **Make post-`clear()` errors fatal.** If `updates`/`appends` fail after a
  clear, that must page someone, not return 200.
- **Apply `_LEGACY_COLUMNS` (`fluency_feed.py:71`) BEFORE removal**, not after.
  Keeping a Fluency column alive after its source moves is the entire point of
  that pattern; the original plan listed it last.

### Then, and only then

1. Confirm with the Fluency config which `data:` columns are referenced
   Fluency-side. **This is step 1, not step 2.**
2. Prefer `display_only=True` on `BriefField`, filtered in the *form renderer*,
   over deletion from `SECTIONS` (see B2). Zero blast radius on
   `_brief_fields`, `_all_property_names`, or `rec_voice`. For all 10
   Operations & Tech fields this is strictly better than cutting.
3. Leave the HubSpot property in place. Mark deprecated, stop reading.

### Acceptance — the original criterion was unfalsifiable

The original said "zero unexplained changes in the next feed sync's `hash`
beyond the cut columns." **That cannot be evaluated**: the hash is computed over
`columns[:-2]` (`:292`, `:298`), so a header change makes *every* row's hash
change by construction.

**Measurable replacement:** capture `build_records()` output as JSON before and
after the schema change and diff per-property, per-surviving-column; assert the
only deltas are the removed keys. Pure `dry_run`, touches no live sheet.
Field-count target, checkable in CI: **48 CM-facing → ≤20**, asserted as
`len([f for f in FIELDS.values() if f.hs_override and not f.internal and not f.display_only])`.

---

## Sequencing

**Revised after review.** The original sequence (`A1 → A2 → LIVE`) ships an
open auth boundary, an internal-list write path, a description bug, and a
500-second page load to real users.

```
A0  BLOCKS EVERYTHING
    ├─ A0.2b audience check in create_ticket      ~10 lines  ← blocks A1's env paste
    ├─ A0.1  entitlement check on company_id      ~20 lines + reusable helper
    ├─ A0.2c verify BQ configured + 0012 applied  verification only, ~30 min
    ├─ A0.2d TTL cache on get_list_fields         ~15 lines
    └─ A0.2  feature gate = the pilot mechanism   ~20 lines
    └─ A0.3  ClickUp Form shutdown sign-off       not code — if refused, STOP

A1  discover + paste (6 client-facing lines ONLY; confirm list_name per type)
A2  prefill: Account Manager via hubspot_owner_id + render-when-empty
A5  status labels                                 ← genuinely config-only
A1.7 description bug fix
A1.5 degraded states (unavailable types, fieldless form, dropped rows)
        └──────────────► LIVE for ONE pilot property, one named human

A0.2d.2  batched list read + 429 backoff + threads>4   (before pilot #2)
A6  recap matches on the mapping table
A1.6 journey fixes (deflection first, submit copy, confirmation, ClickUp link)
A3  triage        ← GATED: the LLM Gateway rule 2 requires does not exist
A4  retirement    ← gap 1 re-estimated up, gap 3 downgraded, gap 2 needs idempotency

B0    rebase onto main (brief_hooks / realtime Fluency changed the ground)
B0.5  decide WHICH brief; fair-housing gate on the token path  ← compliance
B0.5c "Not yet computed" copy fix                              ← hours, high leverage
B0.5b repoint brief_ai_drafter at the token schema             ← the real lever
B3-pre sync() sample-guard + auto-snapshot + fatal errors      ← before any cut
B1    audit, as a BIDIRECTIONAL test
B2    prefer display_only=True over deletion
B3    staged removal
```

**The three things I would insist on before a single real user files a ticket:**
A0.2b (~10 lines), A0.1 (~20 lines), A0.2c (verification only). Everything else
can follow the pilot.

**Workstream B's order is now inverted from the original.** The compliance gap
(B0.5), the copy fix (B0.5c), and repointing the drafter (B0.5b) all outrank
the field audit. B1 is a governance win, not a completion win — the plan
originally implied otherwise.

## Risks

| Risk | Blast radius | Mitigation |
|---|---|---|
| Prefill maps to a nonexistent HubSpot prop | Silent — requester re-types | A2 verification + health check |
| Triage misroutes | Ticket lands in wrong list | Confirmation gate, confidence floor, log overrides |
| KB drafting dies with Service Hub | Deflection decays silently over months | Port before retirement (A4 gap 3) |
| Bulk filing removed | Portfolio managers file one at a time | A4 gap 2 |
| Brief cut breaks a Fluency template | **Live ad copy** | Confirm columns Fluency-side before drop |
| ClickUp API rate limits | Every form render calls `get_list_fields` | **`clickup_client.py` has NO retry, NO 429 handling, NO backoff, NO cache** — verified, zero matches. One-shot `requests` with `_TIMEOUT=10`, every failure degrades to `[]`/`None`, so the user-visible symptom is "No open requests" with no error. Must be built, not assumed. |

## Not in scope

- `server.py` carve-up (`STURDY_REFACTOR_PLAN.md` step 4).
- Migrating the 38 remaining legacy `api.hubapi.com` call sites to
  `hubspot_client` — strangler pattern; new code only.
- Comment-from-portal on an open ticket (`ticket-page-scope.md` §5).
- Any change to `uuid` writing. R1 is immutable.

## Open questions

1. Do the 7 external ClickUp Forms get disabled at cutover, or run in parallel?
   If parallel, the portal's "what's open" view is incomplete and we should say
   so in the UI rather than imply completeness.
2. Who is the pilot property, and who is the first named human filing a ticket?
3. Does Fluency currently reference any `data:` column sourced from a field on
   the B2 cut list?
