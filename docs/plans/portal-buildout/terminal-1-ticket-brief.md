# Terminal 1 — Ticket form ↔ Community Brief integration

## Setup

Repo: `marketingdudeAZ/clientportal`. Branch:

```
git fetch origin main
git checkout -B feature/ticket-brief-integration origin/main
```

Develop on that branch only. Commit in the sequence given at the end,
`feat(...)`-style messages matching the repo log. Push with
`git push -u origin feature/ticket-brief-integration`. Do NOT open a
PR unless asked. Read `CLAUDE.md` and `IMMUTABLE_RULES.md` before any
code.

## Goal

When a client opens the portal ticket form ("Request Work"):

1. The form checks that property's **Community Brief** (property
   profile). Anything already on the profile is shown read-only —
   never asked again.
2. Profile-relevant info that's **missing** is asked as optional
   fields inside the ticket, and answers are **written back to the
   profile** (override props, audit-logged).
3. A visible callout says the answers update their Property Profile.

## Required reading (exact places)

- `webhook-server/portal_tickets.py` — `_INPUT_KIND` :39-51, schema
  shaping + prefill filtering :95-127, `_prefill_values` :156-183,
  `_coerce` :188-226, `_description` :264-284, `create_ticket`
  :289-324, BigQuery mapping `_record_mapping` :368-384.
- `webhook-server/routes/portal_tickets.py` — blueprint + auth
  helper :34-43, `GET /api/portal-tickets/types` :48,
  `POST /api/portal-tickets/create` :65, `GET /api/portal-tickets`
  :100.
- `webhook-server/community_brief.py` — `BriefField` :72-90,
  `SECTIONS` :97-359, `FIELDS` :362, `FAIR_HOUSING_PROTECTED_TOPICS`
  :371-376, `_all_property_names` :393, `load_company_state` :407,
  `resolve_value` :443-456 (override > resolved > empty — EVERY read
  goes through this), `build_render_context` :552-620, `write_field`
  :626-723 (the ONLY legal write path — validates key, rejects
  non-writable, PATCHes only the `*_override` prop, audit-logs),
  `TABLE_TYPES` :48.
- `webhook-server/server.py:6667-6721` — `/api/accounts/property/brief`
  and `/field`: the internal-side precedent for company_id-keyed
  brief read/write.
- `hubspot-cms/templates/client-portal.html` — ticket markup
  :3065-3098, ticket CSS :387-446 and :982-993, ticket JS :3686-3932
  (`toggleCreateTicket`, `_loadTicketTypes`, `renderTicketFields`,
  `_collectTicketFields`, `_doSubmitTicket` — submit payload at
  :3847: `{company_id, uuid, ticket_type, subject, fields}` +
  `X-Portal-Email` header).
- `tests/test_portal_tickets.py` — all 12 tests; reuse their
  monkeypatch style.
- `config.py` AND `webhook-server/config.py` — MIRRORED; every config
  change lands in both. Ticket types :159-172, prefill :177-194.

## Step 1 — Mapping config

Add to BOTH config files:

```python
# Community-brief fields the ticket form checks per ticket type.
# Keys must exist in community_brief.FIELDS, be writable
# (hs_override set), not internal, and not a TABLE_TYPES type.
# Order = ask priority when fields are missing.
PORTAL_TICKET_BRIEF_FIELDS = {
    "creative_ad_copy": [
        "taglines", "brand_adjectives", "differentiators",
        "residents_love", "property_amenities", "unit_features",
        "must_include", "forbidden_phrases",
    ],
    "rebrand": [
        "advertised_name", "short_name", "former_property_name",
        "taglines", "brand_adjectives", "romance", "differentiators",
    ],
    "new_account_build": [
        "voice_tier", "unit_noun", "advertised_name", "taglines",
        "brand_adjectives", "differentiators", "neighborhood",
        "landmarks", "competitors", "property_amenities",
        "unit_features", "goals", "must_include", "forbidden_phrases",
    ],
    "campaign_review": [
        "goals", "initiatives", "competitors",
        "neighborhood_highlights", "local_partnerships",
        "onsite_events",
    ],
    "budget_update": ["goals", "initiatives"],
    "general": [],
}

# Cap on missing-field asks per ticket (avoid form fatigue).
# On-file display is uncapped; asks take the first N missing in
# mapping order.
PORTAL_TICKET_BRIEF_MAX_ASKS = 5
```

Sanity-check each key against `community_brief.SECTIONS` while
implementing (e.g. `residents_love` exists; `residents_dislike`,
`target_resident`, `challenges`, `priorities` are `internal=True` —
NEVER map those). Adjust the lists if any key fails the validity
rules, and enforce those rules in a test (Step 5), not just by eye.

## Step 2 — Gap module + endpoint

New module `webhook-server/ticket_brief_gaps.py`:

```python
def compute_gaps(company_id: str, ticket_type: str) -> dict:
    """One HubSpot GET via community_brief.load_company_state, then
    split the mapped keys. Returns the response dict below."""
```

- For each mapped key: `field = community_brief.FIELDS[key]`;
  effective value = `resolve_value(props, field.hs_resolved,
  field.hs_override)`.
- Skip (defense in depth, even though the config test enforces it):
  unknown keys, `internal=True`, `type in TABLE_TYPES`, no
  `hs_override`.
- Non-blank value → `on_file`; blank → `missing` (first
  `PORTAL_TICKET_BRIEF_MAX_ASKS` only; count the overflow).

Route in `webhook-server/routes/portal_tickets.py` (same `_authed`
guard as siblings):

```
GET /api/portal-tickets/brief-gaps?company_id=<id>&ticket_type=<t>
```

Response:

```json
{
  "ticket_type": "creative_ad_copy",
  "company_id": "1234",
  "callout": "Heads up — your answers here also update your Property Profile, so we only have to ask once.",
  "on_file": [
    {"key": "taglines", "label": "Taglines", "section": "Brand & Story",
     "type": "textarea", "value": "Live the lake life"}
  ],
  "missing": [
    {"key": "brand_adjectives", "label": "Brand adjectives",
     "section": "Brand & Story", "type": "text",
     "options": [], "hint": "<BriefField.hint verbatim>"}
  ],
  "missing_overflow": 0
}
```

Errors: 400 missing/unknown params; unknown or unmapped ticket_type →
200 with empty arrays (the form just shows no profile block); HubSpot
failure → 503 `{"error": "..."}` matching sibling routes' style.

## Step 3 — Frontend (client-portal.html)

Work ONLY inside the ticket-form region (markup :3065-3098, JS
:3686-3932) plus the CSS blocks. Another parallel branch owns the
Call Prep section — don't touch it.

1. Markup: add `<div id="ct-profile-block" style="display:none">`
   after `#ct-fields` inside the form.
2. JS: in the type-change path that calls `renderTicketFields`, also
   fire `_loadBriefGaps(type)` →
   `GET /api/portal-tickets/brief-gaps?...` with the same
   `X-Portal-Email` header pattern the other calls use. Render:
   - **Callout banner** (top of block): amber/info style consistent
     with existing portal callouts; text = the `callout` string from
     the API. Add a one-line sub-note: *"Fields marked 'on file'
     come from your Property Profile."*
   - **On-file group** — collapsed `<details>` titled
     `Already on your property profile (N)`; each row:
     label + value, read-only, no inputs.
   - **Missing group** — heading *"Help us complete your profile
     (optional)"*; inputs typed from `type`:
     `text` → `<input type="text">`, `textarea` → `<textarea>`,
     `multiselect`/`dropdown` → checkboxes / `<select>` built from
     `options` (mirror the rendering conventions in
     `renderTicketFields` :3723-3750). Inputs carry
     `data-profile-key="<key>"` and show `hint` as placeholder/help
     text. All optional — never block submit.
3. `_collectProfileUpdates()`: gather non-empty
   `[data-profile-key]` values; multiselect values join with `";"`
   (that is what `write_field` splits on).
4. In `_doSubmitTicket` (:3847 payload): add
   `profile_updates: _collectProfileUpdates()` when non-empty.
5. After successful submit, if the response's `profile_results` has
   failures, keep the existing success toast but append
   *"(some profile updates didn't save — your team was notified)"*.
6. Reset the block on form close/type change.

## Step 4 — Write-back in create_ticket

`POST /api/portal-tickets/create` accepts optional
`profile_updates: {key: value}`. In
`portal_tickets.create_ticket` (or a helper it calls):

1. Validate every key ∈ the ticket type's mapped set AND passes the
   writability rules. Invalid key → include
   `{"key": k, "ok": false, "error": "not allowed"}` in results; do
   NOT fail the request.
2. Valid keys →
   `community_brief.write_field(company_id, key, value,
   edited_by=<X-Portal-Email value>)`. Capture `(ok, message)`.
3. ClickUp description (`_description`): append

   ```
   --- Profile updates captured with this ticket ---
   Taglines: Live the lake life
   Brand adjectives: modern; welcoming   [FAILED TO SAVE: <msg>]
   ```

   using field labels, flagging failures inline.
4. Order of operations: create the ClickUp task FIRST (ticket
   creation must never be blocked by profile writes)… except the
   description needs the results — so: run the profile writes first
   but wrap the whole batch so ANY exception degrades to
   `{"ok": false}` results and ticket creation proceeds. A HubSpot
   outage must still yield a created ticket.
5. Response gains `"profile_results": [{"key","ok","error"}...]`
   (empty list when no updates sent).

## Step 5 — Tests (tests/test_portal_tickets.py, new class or file)

1. `test_brief_mapping_keys_valid` — every configured key exists in
   `FIELDS`, non-internal, has `hs_override`, type not in
   `TABLE_TYPES`. This is the config police; it must import the real
   mapping, not a copy.
2. `test_gaps_split_override_wins` — prop has override → on_file even
   if resolved empty; resolved only → on_file; both blank/whitespace
   → missing (whitespace-only counts as missing — `_nonblank`
   semantics).
3. `test_gaps_max_asks_cap` — >5 missing → 5 returned,
   `missing_overflow` correct, order = mapping order.
4. `test_gaps_unmapped_type_empty` — `general` and unknown types →
   empty arrays, 200.
5. `test_gaps_never_leaks_internal` — poison the mapping via
   monkeypatch with an internal key → it is skipped.
6. `test_create_with_profile_updates` — `write_field` called once per
   key with `edited_by` = portal email; description contains the
   capture section; response `profile_results` all ok.
7. `test_create_profile_write_failure_still_creates_ticket` —
   `write_field` raises/returns failure → ClickUp task still created,
   result marked not-ok, description flags it.
8. `test_create_rejects_unmapped_profile_key` — sneaky key →
   not-ok result, `write_field` NOT called for it.
9. Existing 12 tests still pass unmodified.

Run before pushing:
`pytest tests/test_portal_tickets.py tests/test_brief_resolver.py tests/test_property_brief.py`

## Guardrails (non-negotiable)

- **R1:** never write the `uuid` HubSpot property. You never PATCH
  HubSpot directly here — everything goes through
  `community_brief.write_field`. A raw company PATCH anywhere in this
  branch is a defect.
- Never expose `internal=True` fields client-side.
- Nothing in the mapping may touch fair-housing-protected topics
  (`FAIR_HOUSING_PROTECTED_TOPICS`) — no demographics, ever.
- The ClickUp schema-driven fields keep working exactly as today; the
  profile block is purely additive.
- Don't touch `client-portal.html` outside the ticket region + its
  CSS; another branch is editing the Call Prep section in parallel.

## In-lane cleanups (separate commits; skip any that balloon)

- `brief_ai_drafter.CB_DRAFTABLE` (:525-581) has 3 keys missing from
  `community_brief.FIELDS` — `marketed_amenity_names`,
  `amenities_descriptions`, `selling_points` — so those LLM
  extractions are silently dropped (`write_field` returns unknown-
  field). The HubSpot props exist (see migrations + `server.py:6565,
  6594`). Fix: add the three `BriefField`s to the right sections
  (override-only, non-internal for amenities ones — use judgment),
  and add them to `fluency_feed`'s exclusion check if they shouldn't
  ship to Fluency.
- Legacy dashboard ticket filter is a no-op:
  `client-portal.html:5455, 5636-5640` filter `t.status` but
  `ticket_manager.list_tickets` (:220-232) returns `stage_label`.
  Fix the JS to use `stage_label`.
- Dead code: `_renderTicketCard` (:3934-3964, no call site) and
  cosmetic `escalateTicket` (:3966-3974). Delete both plus their
  now-unreachable helpers IF `loadTicketThread`/reply paths are truly
  unreachable — verify first.

## Commit sequence

1. `feat(tickets): brief-field mapping config + validity rules`
2. `feat(tickets): brief-gaps computation + /api/portal-tickets/brief-gaps`
3. `feat(tickets): profile check block in ticket form (callout, on-file, asks)`
4. `feat(tickets): write profile_updates back via community_brief on create`
5. `test(tickets): brief-gap + write-back coverage`
6. cleanups (one commit each)

## Acceptance criteria

- [ ] Choosing `creative_ad_copy` on a property with taglines set but
      no brand adjectives shows taglines under "Already on your
      property profile" and asks for brand adjectives under the
      callout.
- [ ] Submitting with answers creates the ClickUp task, writes the
      override props, logs audit rows, and the task description shows
      the capture section.
- [ ] Kill HubSpot writes (mock failure) → ticket still created,
      failure surfaced in response + description.
- [ ] `general` tickets look exactly like today.
- [ ] Full listed pytest suite green.
