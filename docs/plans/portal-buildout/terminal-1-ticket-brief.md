# Terminal 1 — Ticket form ↔ Community Brief integration

You are working in `marketingdudeAZ/clientportal`. Create branch
`feature/ticket-brief-integration` off the latest `origin/main`,
develop on it, commit in logical chunks, and push with
`git push -u origin feature/ticket-brief-integration`. Do NOT open a
PR unless asked. Read `CLAUDE.md` and `IMMUTABLE_RULES.md` first.

## Goal

When a client opens the portal ticket form ("Request Work"), the form
must check that property's **Community Brief** (the property profile):

1. Information already on the profile is **not asked again** — it's
   shown read-only as "Already on your property profile."
2. Profile-relevant information that's **missing** IS asked in the
   ticket, and the answers are **written back to the profile** so it
   stays current.
3. A clear callout tells the client: their answers here update their
   Property Profile.

## Read these before writing code

- `webhook-server/portal_tickets.py` — schema building
  (`_INPUT_KIND` :39, prefill filtering :95-127, `_prefill_values`
  :156, `create_ticket` :289, BigQuery mapping :368).
- `webhook-server/routes/portal_tickets.py` — the blueprint
  (`/api/portal-tickets/types` :48, `/create` :65, auth :34-43).
- `webhook-server/community_brief.py` — the profile contract:
  `SECTIONS`/`FIELDS`, `resolve_value()` :443 (override > resolved >
  empty; every read MUST go through it), `write_field()` :626 (the
  ONLY legal write path — validates, PATCHes only the `*_override`
  prop, audit-logs), `load_company_state()` :407,
  `build_render_context()` :552, `FAIR_HOUSING_PROTECTED_TOPICS` :371.
- `webhook-server/server.py:6667-6721` — existing company_id-keyed
  brief read/write endpoints (`/api/accounts/property/brief`,
  `/api/accounts/property/field`) — the internal-side precedent.
- `hubspot-cms/templates/client-portal.html` — form markup
  :3065-3098, form JS :3686-3932 (`_loadTicketTypes`,
  `renderTicketFields`, `_collectTicketFields`, `_doSubmitTicket`).
- `tests/test_portal_tickets.py` — mocking patterns to extend.
- `config.py` AND `webhook-server/config.py` (they are mirrored —
  change both): `PORTAL_TICKET_TYPES` :159, prefill config :177-194.

## Build

### 1. Mapping config
`PORTAL_TICKET_BRIEF_FIELDS: dict[str, list[str]]` in both config
copies — ticket_type → community-brief field keys relevant to that
type. Propose the mapping yourself from `community_brief.SECTIONS`
(e.g. `creative_ad_copy` → taglines, brand_adjectives,
differentiators, property_amenities, unit_features, must_include,
forbidden_phrases; `rebrand` → advertised_name, short_name,
former_property_name, taglines, brand_adjectives, romance;
`new_account_build` → a broad set). Validate at import/test time that
every key exists in `community_brief.FIELDS`, is NOT `internal=True`,
and has an `hs_override` (i.e. is writable). Table types
(`floorplan_table`, `tracking_table`, `documents`) are excluded —
too complex for a ticket form.

### 2. Gap computation endpoint
`GET /api/portal-tickets/brief-gaps?company_id=<id>&ticket_type=<t>`
(same auth as the other portal-ticket routes). Loads brief state once
(`load_company_state`), and for each mapped key returns:
`{key, label, section, type, options, hint, status: "on_file"|"missing",
value}` where `value` uses `resolve_value`. Internal fields must never
appear. Return `{fields: [...], callout: true}`.

### 3. Frontend
In `client-portal.html`'s ticket form JS: after the type is chosen,
fetch brief-gaps and render a "Property Profile check" block below the
ClickUp-driven fields:
- **On-file values**: collapsed/read-only rows ("Already on your
  property profile — we won't ask again"). No inputs.
- **Missing values**: real inputs typed from the field (`text`,
  `textarea`, `multiselect` with its options), visually grouped under
  a banner callout: **"Heads up — your answers here also update your
  Property Profile, so we only have to ask once."** Missing fields are
  optional (don't block ticket submission), but encouraged.
- Match the existing form's CSS patterns (see :387-446).

### 4. Write-back on submit
`POST /api/portal-tickets/create` accepts an optional
`profile_updates: {key: value}` map. Server-side:
- Validate keys against the ticket type's mapped, writable set (reject
  anything else — the client can't write arbitrary profile fields).
- Write each via
  `community_brief.write_field(company_id, key, value, edited_by=<X-Portal-Email>)`.
- Append a "Profile updates captured with this ticket" section to the
  ClickUp task description (`_description`) listing key → value.
- Ticket creation must succeed even if a profile write fails; return
  `profile_results` in the response and note failures in the ClickUp
  description.

### 5. Tests
Extend `tests/test_portal_tickets.py`: mapping validity (every
configured key exists / writable / non-internal), gap split logic
(override set vs resolved set vs empty), create with profile_updates
(write_field called with right args; ticket still created when
write_field returns failure), rejection of unmapped keys.

## Guardrails (non-negotiable)

- **R1:** never write the `uuid` HubSpot property. You never PATCH
  HubSpot directly in this work — all profile writes go through
  `community_brief.write_field`. If you find yourself writing a raw
  company PATCH, stop.
- Never expose `internal=True` brief fields to the client-facing form.
- Don't ask for or store fair-housing-protected info
  (`FAIR_HOUSING_PROTECTED_TOPICS`).
- Keep the ClickUp schema-driven fields working exactly as they do
  today — the profile block is additive.

## In-lane cleanups (do these if cheap, skip if they balloon)

- `brief_ai_drafter.CB_DRAFTABLE` has 3 keys missing from
  `community_brief.FIELDS` (`marketed_amenity_names`,
  `amenities_descriptions`, `selling_points`) — extractions are
  silently dropped. Add the missing `BriefField`s (the HubSpot props
  already exist) or remove the keys.
- The dashboard's legacy ticket status filter is a no-op
  (`client-portal.html:5636-5640` filters `t.status`, but
  `ticket_manager.list_tickets` returns `stage_label`).
- Dead code: `_renderTicketCard` (:3934) has no call site;
  `escalateTicket` (:3966) makes no network call.

## Out of scope

Retiring the HubSpot Service Hub ticket path (open decision, docs/
ticket-page-scope.md), ticket attachments/file upload, the
recommendations system, Google Drive work. Run
`pytest tests/test_portal_tickets.py tests/test_brief_resolver.py tests/test_property_brief.py`
before pushing; commit messages follow the repo's `feat(...)` style.
