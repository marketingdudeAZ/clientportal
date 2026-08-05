# Scope — Ticket Brief Gaps (ask only what the profile doesn't already know)

**Status:** Draft for Kyle's review
**Date:** 2026-08-05
**Builds on:** `docs/ticket-page-scope.md` (portal ticket page → ClickUp),
`docs/CLIENT_BRIEF_SYSTEM.md` (community brief / override-wins model)

## The problem

The portal ticket page already pre-fills the *property identity* fields
(Property URL, Market, Property Code, AM, uuid — `PORTAL_TICKET_PREFILL_FIELDS`).
It does not pre-fill the *brief* fields, and those are what the work actually
needs. So a "Photos & New Specials" request arrives with a subject and a
paragraph, and the creative team emails back asking for taglines, brand
adjectives, and what residents love — facts that, for most properties, are
**already on the HubSpot company record** from onboarding.

Two failures, one fix:

1. **We ask for what we have.** The community brief holds it; the ticket form
   doesn't read it.
2. **We don't capture what we're missing.** When the team does chase the
   answer over email, it lands in an inbox, not on the property profile — so
   the next ticket asks again.

## The model

**The ticket form is the highest-intent moment we ever get with property
marketing.** They want something from us; they'll answer questions. Spend that
moment on the *gaps* only — never on what we already know — and write the
answers back to the property profile so the question is retired permanently.

```
pick ticket type
  → portal reads the property's community brief
  → shows what it already has (collapsed, reassuring)
  → asks up to 5 fields that are still empty (inline, optional)
  → submit: answers PATCH the brief overrides, then the ClickUp task is created
            with a provenance line naming what was saved / what failed
```

Three rules that keep this from becoming a second onboarding form:

- **Cap the ask at 5.** A 12-question form on the way to a ticket is a form
  people abandon. Ordered mapping + hard cap; the rest wait for the next ticket.
- **Every ask is optional.** Nothing here can block a ticket. Ever.
- **Never ask twice.** A field with an effective value (override *or* resolved,
  per `community_brief.resolve_value`) is never asked — it's shown as known.

---

## 1. The mapping — `PORTAL_TICKET_BRIEF_FIELDS`

Lives in `webhook-server/config.py` next to `PORTAL_TICKET_PREFILL_FIELDS`.
`{ticket_type_key: [community_brief field keys, in priority order]}`. The gap
engine walks the list **in order** and asks the first ≤5 that are still empty,
so position 1 is the field you'd most regret not having.

```python
# Community-brief fields that make each ticket type actionable, in priority
# order. The portal asks for the first PORTAL_TICKET_BRIEF_MAX_ASK of these
# that are still empty on the property record — everything already known is
# shown back, never re-asked. Keys MUST exist in community_brief.FIELDS and
# be askable (has hs_override, type not in TABLE_TYPES, not readonly).
# Client-audience types must not map internal=True fields (budget, ICP,
# resident friction, PMS/CMS) — those stay on the internal surfaces.
PORTAL_TICKET_BRIEF_FIELDS = {

    # Ad Updates: Photos & New Specials — the creative team's actual inputs.
    "creative_ad_copy": [
        "taglines",             # fluency_taglines
        "brand_adjectives",     # fluency_brand_adjectives
        "differentiators",      # fluency_differentiators
        "residents_love",       # fluency_residents_love
        "property_amenities",   # fluency_property_amenities_override
        "unit_features",        # fluency_unit_features_override
        "must_include",         # fluency_must_include_override
        "forbidden_phrases",    # fluency_forbidden_phrases_override
    ],

    # New Account Onboarding — the brief IS the deliverable here. Long list on
    # purpose; the cap keeps the form short and the next ticket picks up more.
    "new_account_build": [
        "voice_tier",
        "unit_noun",
        "advertised_name",
        "differentiators",
        "property_amenities",
        "unit_features",
        "neighborhood",
        "competitors",
        "must_include",
        "forbidden_phrases",
    ],

    # Rebrands — name equity + the new voice.
    "rebrand": [
        "advertised_name",
        "former_property_name",
        "short_name",
        "taglines",
        "brand_adjectives",
        "differentiators",
        "must_include",
        "forbidden_phrases",
    ],

    # Digital Marketing Review — exactly what the team chases over email today.
    "campaign_review": [
        "goals",
        "initiatives",
        "competitors",
        "neighborhood_highlights",
        "local_partnerships",
        "onsite_events",
    ],

    # Budget Changes — stays light on purpose. marketing_budget is internal=True
    # and deliberately NOT asked here.
    "budget_update": [
        "goals",
        "initiatives",
    ],

    # Deliberately empty — a general ticket must stay a one-field escape hatch,
    # a dispo/cancellation is the wrong moment to ask enrichment questions, and
    # New Business is sales intake for a property we have no brief for.
    "general": [],
    "dispo_cancel": [],
    "new_business": [],
}

# Hard cap on how many gap questions the ticket form may show. Five is the
# ceiling that keeps this a nudge rather than a second onboarding form.
PORTAL_TICKET_BRIEF_MAX_ASK = int(os.getenv("PORTAL_TICKET_BRIEF_MAX_ASK", "5"))

# Off by default; flip per environment. Off = the endpoint returns an empty
# ask and create_ticket ignores brief answers, so the ticket page behaves
# exactly as it does today.
PORTAL_TICKET_BRIEF_GAPS_ENABLED = (
    os.getenv("PORTAL_TICKET_BRIEF_GAPS_ENABLED", "false").lower() == "true"
)
```

**Askability rules** (enforced in code *and* by test 1, so a bad mapping fails
CI rather than the portal):

| Rule | Why |
|---|---|
| key ∈ `community_brief.FIELDS` | typo in the mapping = silent missing question |
| `field.hs_override` is set | no override property = nowhere to write the answer |
| `field.type` ∉ `TABLE_TYPES` (`floorplan_table`, `tracking_table`, `documents`) | a table can't be answered in an inline row — it's surfaced as `deferred`, linking to the brief section |
| `field.type != "readonly"` | Identity/year-built are machine-owned |
| `field.internal` is False for `audience == "client"` types | budget, ICP, resident friction, PMS/CMS never get asked of a client |

Input rendering follows the brief field's own `type`: `text` → text input,
`textarea` → textarea, `dropdown` → select, `multiselect` → multi-select using
`field.options` (`voice_tier`, `unit_noun`, `lifecycle_state` are the only
option-bearing fields in the mappings above).

---

## 2. Endpoint contract — `GET /api/portal-tickets/brief-gaps`

New route in `webhook-server/routes/portal_tickets.py`, backed by
`portal_tickets.brief_gaps()`. Auth is the existing dual scheme (`_is_authorized`:
`X-Portal-Email` **or** `X-Internal-Key`), and `OPTIONS` returns
`preflight_response()` like every other route in the blueprint.

### Request

```
GET /api/portal-tickets/brief-gaps?company_id=12345678&ticket_type=creative_ad_copy&uuid=abc-123
X-Portal-Email: manager@rpmliving.com
```

| Param | Required | Notes |
|---|---|---|
| `company_id` | yes | HubSpot company id — the brief lives on the company record |
| `ticket_type` | yes | a `PORTAL_TICKET_TYPES` key |
| `uuid` | no | passed through for logging/parity with the other routes; not used to resolve the brief |

### Response — 200

```json
{
  "ok": true,
  "enabled": true,
  "ticket_type": "creative_ad_copy",
  "company_id": "12345678",
  "property_name": "Sunset Ridge",
  "ask": [
    {
      "key": "taglines",
      "label": "Taglines",
      "input": "textarea",
      "hint": "Property taglines / slogans. One per line.",
      "options": [],
      "placeholder": "One per line"
    },
    {
      "key": "voice_tier",
      "label": "Voice Tier",
      "input": "multiselect",
      "hint": "How copy should feel for this property's price point.",
      "options": ["value", "standard", "lifestyle", "luxury"],
      "placeholder": ""
    }
  ],
  "known": [
    {
      "key": "property_amenities",
      "label": "Property Amenities",
      "preview": "Resort-style pool · 24hr fitness center · Dog park",
      "source": "override"
    }
  ],
  "deferred": [
    { "key": "must_include", "label": "Must Include / Key Messages", "reason": "over_cap" },
    { "key": "floor_plans",  "label": "Floor Plans",                 "reason": "not_askable" }
  ],
  "counts": { "mapped": 8, "known": 1, "asked": 5, "deferred": 2 }
}
```

Field notes:

- `ask` — **length ≤ `PORTAL_TICKET_BRIEF_MAX_ASK`**, mapping order preserved,
  every entry currently empty per `resolve_value`. All optional.
- `known` — mapping fields that already have an effective value. `preview` is
  the effective value flattened to a single line, whitespace-collapsed, truncated
  to 120 chars with a trailing `…`. `source` is `"override"` when the human
  override won, `"resolved"` when the pipeline value did.
- `deferred` — mapped-but-not-asked, with `reason` ∈ `{"over_cap",
  "not_askable"}`. Purely informational; the UI uses it for the "and N more on
  your profile" line.
- `counts.mapped` = `len(PORTAL_TICKET_BRIEF_FIELDS[type])`; the other three
  sum to it.

### Error / edge behavior — the endpoint never 500s and never blocks the form

| Case | Status | Body |
|---|---|---|
| No `X-Portal-Email` and no valid `X-Internal-Key` | 401 | `{"error": "auth required"}` |
| `company_id` missing/blank | 400 | `{"ok": false, "error": "company_id required"}` |
| `ticket_type` missing/blank | 400 | `{"ok": false, "error": "ticket_type required"}` |
| `ticket_type` not in `PORTAL_TICKET_TYPES` | 400 | `{"ok": false, "error": "Unknown ticket type."}` |
| Known type, **no mapping** (`general`, `new_business`) | 200 | `ask: []`, `known: []`, `deferred: []`, `counts.mapped: 0` |
| Feature flag off | 200 | `{"ok": true, "enabled": false, "ask": [], "known": [], "deferred": [], "counts": {...zeros}}` |
| HubSpot unreachable / `load_company_state` returns `{}` or raises | 200 | `{"ok": true, "degraded": true, "ask": [], "known": [], ...}` — logged at WARNING, **not** an error to the user |
| Any other unexpected exception | 200 | same degraded shape; caught at the route boundary like `ticket_types()` does today |

The degraded case deliberately returns **empty asks rather than all asks**: if
we can't read the profile we don't know what's already known, and asking for
things we have is worse than asking for nothing.

---

## 3. UI — callout copy, DOM, and the JS hooks

### Where it lives

Inside `#create-ticket-form` in `hubspot-cms/templates/client-portal.html`,
**between `#ct-fields` and the button row** — after the ClickUp fields (the
request itself) and before the CTA, so the request is never buried under
profile questions.

### DOM structure

```html
<!-- Brief gaps: fields the property profile is missing for this request type.
     All optional; answers PATCH the community-brief overrides on submit. -->
<div id="ct-profile-block" class="ct-profile" hidden>
  <div class="ct-profile-head">
    <div class="ct-profile-title">Help us skip the back-and-forth</div>
    <div class="ct-profile-sub" id="ct-profile-sub"></div>
  </div>

  <div id="ct-profile-ask"></div>

  <details id="ct-profile-known" class="ct-known" hidden>
    <summary id="ct-profile-known-summary">Already on your property profile</summary>
    <div id="ct-profile-known-list"></div>
  </details>

  <div class="ct-profile-foot">
    Optional — you can submit without these.
    <a href="#" onclick="nav('brief'); return false;">See your full property profile →</a>
  </div>
</div>
```

Each ask row renders as an existing `.form-row` (so it inherits portal styling)
with the control carrying `class="ct-bf"` and `data-bkey="<brief field key>"` —
deliberately a *different* class and data attribute from the ClickUp fields
(`.ct-f` / `data-fid`), so `_collectTicketFields()`'s
`querySelectorAll('#ct-fields .ct-f')` can never pick these up and post a brief
answer as a ClickUp custom field.

Each known row: label, the truncated preview, and an `Edit →` link calling
`nav('brief')`.

### Exact copy

| Slot | String |
|---|---|
| Title | `Help us skip the back-and-forth` |
| Sub, known > 0 | `We already have {known} details on file for {property_name}. Answer up to {asked} more and your team can start without emailing you first — we'll save them to your property profile, so you're only ever asked once.` |
| Sub, known = 0 | `Answering these saves your team a round of emails. They go straight onto your property profile, so you'll only ever be asked once.` |
| Known section summary | `Already on your property profile ({known})` |
| Known row action | `Edit →` |
| Footer | `Optional — you can submit without these.` + `See your full property profile →` |
| Per-field label | the brief field's `label` — **no** required asterisk, ever |
| Per-field helper | the brief field's `hint`, truncated to 120 chars |
| Deferred line (when `deferred` non-empty) | `{n} more details live on your property profile.` |

No copy anywhere claims the answers change *this* ticket's turnaround — they
change the property profile. Don't over-promise.

### JS hooks — the four existing functions to touch

1. **`renderTicketFields(typeKey)`** (line ~3723) — after the existing
   `box.innerHTML = ...`, call `_loadBriefGaps(typeKey)`. On a blank/changed
   type, hide and clear `#ct-profile-block` first. New helper `_loadBriefGaps`
   fetches the endpoint with the same `API_BASE` / `X-Portal-Email` pattern as
   `_loadTicketTypes`, and on **any** failure (network, non-200, malformed)
   simply leaves the block hidden — no alert, no console noise beyond
   `console.warn`. Cache per `typeKey` in a `__BRIEF_GAPS__` map so re-picking
   a type doesn't refetch.

2. **`_collectTicketFields(form)`** (line ~3816) — leave it alone. Add a
   sibling `_collectBriefAnswers(form)` that reads
   `#ct-profile-ask .ct-bf`, keyed by `data-bkey`, skipping empties, joining
   multi-selects with `;` (the format `community_brief.write_field` expects for
   multi-enumeration).

3. **`_doSubmitTicket(form, typeKey, subject)`** (line ~3831) — add
   `brief_answers: _collectBriefAnswers(form)` to the POST body. Nothing else
   changes; the existing error handling already covers it.

4. **Reset on success** (line ~3857) — alongside clearing `#ct-subject`,
   `#ct-fields`, and `#ct-type`, clear `#ct-profile-ask` /
   `#ct-profile-known-list` and re-hide `#ct-profile-block`.

`submitTicket()` (the required-field check and KB deflect) is **not** touched:
brief answers are never required, so they must not appear in the `missing`
array, and the KB deflect panel keeps inserting before the first `.form-row`
as it does today.

---

## 4. Write-back ordering — the profile write can never cost us a ticket

`create_ticket()` gains a `brief_answers: dict | None = None` parameter. The
order is fixed:

```
1. resolve type + list id + ClickUp key      (unchanged — early 400/503 returns)
2. prefill property identity fields          (unchanged)
3. _apply_brief_answers(...)  ← NEW, cannot raise, bounded
4. build description (incl. the brief provenance lines from step 3)
5. clickup_client.create_task(...)
6. _record_mapping(...)                      (unchanged, already best-effort)
```

**Why writes go before task creation:** the ClickUp description is the only
place the internal team will see what was captured, and it's composed in step 4.
Attempting the writes after task creation would mean either a second ClickUp
API call to amend the description, or the team never learning a save failed.

**Why that's safe** — `_apply_brief_answers` is bounded and total:

- **Bounded work.** At most `PORTAL_TICKET_BRIEF_MAX_ASK` (5) writes, only for
  keys in that type's mapping. `community_brief.write_field` already uses
  `timeout=10` per HubSpot call, so the worst case is bounded, not open-ended.
- **Total function.** Each write is individually wrapped; a raise, a `False`
  return, or a network error is recorded and the loop continues. The function
  returns `{"saved": [...], "failed": [...], "skipped": [...]}` and **never
  propagates an exception** — `except Exception` at the per-field level, same
  posture as `_prefill_values` and `_record_mapping` today.
- **The ticket is the product.** If HubSpot is down entirely, all five land in
  `failed`, the description says so, and the ClickUp task is created normally.
  A HubSpot outage degrades profile capture; it cannot degrade ticketing.

**Four gates on what may be written** (defense in depth around R1 — code never
writes `uuid`):

1. Key must be in `PORTAL_TICKET_BRIEF_FIELDS[ticket_type]` — an answer for any
   other key is dropped silently. A crafted POST can't reach an arbitrary field.
2. `write_field` itself rejects unknown keys and fields without `hs_override`,
   and only ever PATCHes `field.hs_override`. `uuid` is not a brief field and
   has no override property, so it is unreachable by construction.
3. **Anti-clobber.** Before writing, re-check the field against a fresh
   `load_company_state(company_id)`; if it now has an effective value (someone
   filled it on the brief page while the ticket form sat open), **skip** the
   write and record it under `skipped`. Human edits on the brief page outrank a
   stale ticket form — that's the override-wins model applied to a race.
4. Blank/whitespace-only answers are dropped before the loop (`resolve_value`
   would treat them as empty anyway — same `_nonblank` rule that fixed the
   portal/Fluency drift bug).

`edited_by` is the requester's `X-Portal-Email`, so every write lands in the
existing `property_brief_audit` log attributed to a real person.

### Description lines

Appended by `_description(...)` after the identity block, only when non-empty:

```
Submitted via the RPM client portal.
Requested by: manager@rpmliving.com
Property: uuid=abc-123 · hubspot_company=12345678

Property profile updated from this request: Taglines, Brand Adjectives
Skipped (filled in on the profile while this form was open): Unit Features
⚠ Could NOT save to the property profile — please update manually: Differentiators, Must Include / Key Messages
   Differentiators: Rooftop lounge with skyline views; only property in the submarket with private garages
   Must Include / Key Messages: Now offering 6 weeks free
```

The failed block echoes the **submitted values** underneath, so nothing the
requester typed is lost even when every HubSpot write failed — the assignee can
copy them onto the profile by hand. Labels come from `community_brief.FIELDS`,
not raw keys.

---

## 5. Tests — 9 cases

New file `tests/test_portal_ticket_brief_gaps.py`, following
`tests/test_portal_tickets.py` conventions (webhook-server on `sys.path` first,
all HubSpot/ClickUp/BigQuery I/O mocked, BigQuery left unconfigured).

| # | Case | Asserts |
|---|---|---|
| 1 | **Mapping integrity** (no mocks — pure config) | Every key in every `PORTAL_TICKET_BRIEF_FIELDS` list exists in `community_brief.FIELDS`; has a non-empty `hs_override`; `type` ∉ `TABLE_TYPES` and != `"readonly"`; no `internal=True` field appears under a type whose `audience == "client"`; every mapping key is a real `PORTAL_TICKET_TYPES` key; no duplicates within a list |
| 2 | **Known vs ask split** | Company props with `fluency_taglines` set and `fluency_brand_adjectives_*` empty → `taglines` in `known` with `source="override"`, `brand_adjectives` in `ask`; a resolved-only value yields `source="resolved"`; `counts` sums to `mapped` |
| 3 | **The cap holds** | `creative_ad_copy` (8 mapped) with an empty property → `len(ask) == 5`, mapping order preserved (`taglines` first), 3 entries in `deferred` with `reason="over_cap"`, `counts == {mapped:8, known:0, asked:5, deferred:3}` |
| 4 | **Whitespace-only override is a gap** | `fluency_taglines = "   "` → `taglines` appears in `ask`, not `known` (routes through `resolve_value`/`_nonblank`, so portal, Fluency, and this form agree) |
| 5 | **Endpoint contract** | 200 shape has exactly `ok/enabled/ticket_type/company_id/property_name/ask/known/deferred/counts`; no `X-Portal-Email` → 401; missing `company_id` → 400; missing `ticket_type` → 400; unknown type → 400 `"Unknown ticket type."`; `general` (empty mapping) → 200 with `mapped: 0`; flag off → 200 `enabled: false` with empty ask |
| 6 | **HubSpot down → degraded, not broken** | `load_company_state` raising, and separately returning `{}` → 200, `degraded: true`, `ask == []`, no exception escapes, WARNING logged |
| 7 | **Write-back happy path** | `create_ticket(..., brief_answers={"taglines": "A\nB", "voice_tier": "luxury;lifestyle"})` → `write_field` called once per key with `edited_by` = submitter; multiselect passed through as the `;`-joined string; description contains `Property profile updated from this request: Taglines, Voice Tier`; task created 201 |
| 8 | **Write failure is isolated** | `write_field` raising for `differentiators` and returning `(False, "HubSpot 500")` for `must_include`, succeeding for `taglines` → still 201; description has the `⚠ Could NOT save` line naming both failures **and** echoing their values; `taglines` still in the updated line; `_record_mapping` still called |
| 9 | **Write gates** | An answer for `marketing_budget` (not in the type's mapping) and one for `uuid` are both dropped — `write_field` never called for them; a whitespace-only answer is dropped; an answer whose field became non-empty since form load is skipped and named in the `Skipped (…)` description line; `write_field` is never called with any key outside the type's mapping |

Run: `pytest tests/test_portal_ticket_brief_gaps.py tests/test_portal_tickets.py tests/test_brief_resolver.py`
— the last two are the regression guard, since this touches `create_ticket` and
leans on the canonical resolver.

---

## 6. Commit sequence — 6 commits

Branch `feat/ticket-brief-gaps` off `main`. Each commit is independently green.

| # | Commit | Contents |
|---|---|---|
| 1 | `feat(ticket-brief): brief-field mapping per ticket type + 5-question cap` | `config.py`: `PORTAL_TICKET_BRIEF_FIELDS`, `PORTAL_TICKET_BRIEF_MAX_ASK`, `PORTAL_TICKET_BRIEF_GAPS_ENABLED`. Test 1. No behavior change — config + guardrail only. |
| 2 | `feat(ticket-brief): gap engine — what we know vs what to ask` | `portal_tickets.py`: `brief_gaps(company_id, ticket_type)` + `_askable(field)` + preview truncation. Tests 2–4. Still unreachable from HTTP. |
| 3 | `feat(ticket-brief): GET /api/portal-tickets/brief-gaps` | `routes/portal_tickets.py`: the route, dual auth, `OPTIONS`, the full error table, degraded path. Tests 5–6. Endpoint live but nothing calls it. |
| 4 | `feat(ticket-brief): write answers back to the property profile on ticket create` | `portal_tickets.py`: `_apply_brief_answers` (4 gates, per-field isolation), `create_ticket(brief_answers=...)`, description lines; route passes `brief_answers` through. Tests 7–9. |
| 5 | `feat(ticket-brief): portal UI — profile callout, ≤5 asks, collapsed known section` | `hubspot-cms/templates/client-portal.html`: `#ct-profile-block` markup + `.ct-profile`/`.ct-known` CSS, `_loadBriefGaps`, `_collectBriefAnswers`, the 4 hook edits. **This is the commit that turns the feature on for users** (behind the flag). |
| 6 | `docs(ticket-brief): scope doc, env vars, runbook` | This doc, `.env.example` (`PORTAL_TICKET_BRIEF_GAPS_ENABLED`, `PORTAL_TICKET_BRIEF_MAX_ASK`), a `docs/RUNBOOK.md` note on flipping the flag and reading the `⚠ Could NOT save` lines. |

Commits 1–4 are backend-only and inert; if commit 5 regresses the ticket form,
reverting one commit restores today's behavior with the API left harmlessly in
place.

## 7. Acceptance criteria

1. Picking a request type with a mapping shows the callout with **at most 5**
   questions, all optional, none duplicating a value already on the profile.
2. Picking `General Ticket` or `New Business` shows **no callout at all** —
   the form is byte-for-byte what it is today.
3. A property with a fully-populated brief shows **zero questions** and only
   the collapsed "Already on your property profile (N)" section.
4. A whitespace-only override is treated as missing and gets asked.
5. Submitting with every gap blank creates the ticket exactly as today — no new
   description lines, no HubSpot writes.
6. Submitting with answers: the ClickUp task is created **and** the answers are
   on the HubSpot company record as `fluency_*` overrides, attributed to the
   requester in the `property_brief_audit` log.
7. Re-opening the same ticket type after a successful submit shows those fields
   under "Already on your property profile", not as questions.
8. **With HubSpot returning 500 for every write, the ticket is still created**
   (201), and its description names every field that failed and echoes the
   submitted values.
9. With the endpoint down or slow, the ticket form renders and submits normally
   — the callout simply never appears.
10. No brief answer is ever posted as a ClickUp custom field, and `write_field`
    is never invoked for a key outside the selected type's mapping.
11. `PORTAL_TICKET_BRIEF_GAPS_ENABLED=false` returns the portal to exactly
    today's behavior on both the API and the UI — going live is a config flip,
    not a deploy.
12. Full suite green: `pytest tests/` — no regression in
    `test_portal_tickets.py` or `test_brief_resolver.py`.

## Decisions (Kyle, 2026-08-05)

1. **`campaign_review`** = goals · initiatives · competitors ·
   neighborhood_highlights · local_partnerships · onsite_events.
   **`budget_update`** stays light: goals · initiatives. These are exactly what
   the team chases over email today.
2. **`dispo_cancel` maps to nothing.** A disposition/cancellation ticket is the
   wrong moment to ask profile-enrichment questions — it behaves like
   `general`: no profile block at all.
3. **`new_account_build` keeps the broad mapping and relies on the cap** — it
   asks the first 5 still-empty like every other type. The ticket form does
   **not** link out to the onboarding intake: the onboarding wizard is the deep
   path, the ticket's job is only to close gaps opportunistically, and a second
   navigation option is a decision point the client doesn't need. (The footer's
   "See your full property profile" link points at `#section-brief`, the
   read/edit view of the brief — not the intake wizard.)
4. **Cap of 5** — env-tunable via `PORTAL_TICKET_BRIEF_MAX_ASK`. Worth watching
   completion rate at 5 vs 3 once it's live on a few properties.
