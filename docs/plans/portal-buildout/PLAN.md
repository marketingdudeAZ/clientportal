# Client Portal Buildout — Plan (August 2026)

Owner: Kyle Shipp. Written 2026-08-05 from a full codebase audit on
`claude/client-portal-setup-yv6r9a` (same head as `main`, post-#26).

Four asks, organized into **three parallel workstreams** (asks 1 and 2
are one workstream — same files, same flow; splitting them guarantees
merge conflicts in `client-portal.html`).

| # | Workstream | Branch | Prompt file |
|---|---|---|---|
| 1+2 | Ticket ↔ Community Brief integration + profile-change callout | `feature/ticket-brief-integration` | `terminal-1-ticket-brief.md` |
| 3 | AI recommendations — voice alignment + consolidation | `feature/ai-recommendations-dialin` | `terminal-2-recommendations.md` |
| 4 | Asset naming/sizing → shared Google Drive (ADR 0020) | `feature/asset-drive-pipeline` | `terminal-3-drive-assets.md` |

All three branch off `main`, PR back to `main`.
**Merge order:** Terminal 1 first, then Terminal 2 (both touch
`hubspot-cms/templates/client-portal.html`; T2 rebases on T1).
Terminal 3 is independent — merge any time.

---

## Workstream 1 — Ticket form reads/writes the Community Brief (asks 1 + 2)

### Current state (verified)
- Ticket form: `hubspot-cms/templates/client-portal.html:3065-3098`
  (markup) and `:3686-3932` (JS). Only Request Type + Subject are
  hard-coded; every other input renders dynamically from
  `GET /api/portal-tickets/types` (`webhook-server/routes/portal_tickets.py:48`),
  whose schema comes from ClickUp custom fields
  (`webhook-server/portal_tickets.py`).
- Server-side prefill already pulls HubSpot company props (Property
  URL, Market, Property Code, AM, uuid) via `PORTAL_TICKET_PREFILL_SOURCES`
  (`config.py:187-194`) and hides those fields from the client.
- The community brief is a separate subsystem with a clean contract:
  `webhook-server/community_brief.py` — `SECTIONS` (the canonical
  field list), `resolve_value()` (override > resolved > empty),
  `write_field()` (validates, PATCHes only the `*_override` prop,
  writes the audit log). Company-id-keyed read already exists:
  `GET /api/accounts/property/brief` (`webhook-server/server.py:6667`).
- **There is currently zero cross-reference between the two systems.**

### Design
1. **Mapping** — new config `PORTAL_TICKET_BRIEF_FIELDS`:
   `ticket_type → ordered list of community-brief field keys` relevant
   to that request type (e.g. `creative_ad_copy` → taglines,
   brand_adjectives, differentiators, property_amenities, unit_features,
   must_include, forbidden_phrases; `rebrand` → advertised_name,
   short_name, former_property_name, taglines, brand_adjectives).
   Keys must exist in `community_brief.FIELDS`; `internal=True` fields
   are **never** eligible (client-facing form).
2. **Gap check at form load** — extend the types endpoint (or add
   `GET /api/portal-tickets/brief-gaps?company_id=`) to load brief
   state once and split mapped fields into:
   - **On file** → returned with their resolved value; rendered
     read-only/collapsed ("Already on your property profile") and
     *not* asked again.
   - **Missing** → returned as form inputs (typed from the
     `BriefField` — text/textarea/multiselect with its options),
     flagged `profile_field: true`.
3. **The callout (ask 2)** — the missing-field block renders under a
   visible banner: *"Heads up — your answers here also update your
   Property Profile, so we only have to ask once."* Each on-file value
   shown gets a smaller note that editing it changes the profile.
4. **Write-back on submit** — `POST /api/portal-tickets/create`
   accepts a `profile_updates` map and writes each via
   `community_brief.write_field(company_id, key, value, edited_by=email)`
   (audit trail comes free). Answers are also stamped into the ClickUp
   task description so the fulfillment team sees them inline.
   Partial-failure policy: ticket creation must still succeed if a
   profile write fails; failures are reported in the response and
   appended to the ClickUp description.

### Guardrails
- **R1:** never write `uuid`. `write_field` only touches `*_override`
  props — stay on that path; no raw HubSpot PATCHes.
- Only fields with an `hs_override` are writable (that's the
  `write_field` contract already).
- Respect `FAIR_HOUSING_PROTECTED_TOPICS` (`community_brief.py:371`).
- Tests extend `tests/test_portal_tickets.py` (12 existing tests show
  the mocking pattern).

---

## Workstream 2 — AI recommendations: dial in the voice, connect the surfaces (ask 3)

### Current state (verified)
Five disconnected recommendation surfaces:
1. **Call Prep** (LLM, live) — `server.py:5134-5460`, portal section
   `client-portal.html:2121-2240`, monthly cycle stored on company
   props, approve → ClickUp AM task.
2. **HubDB rec-feed** (LLM, **dormant**) — `red_light_pipeline.py`,
   `partials/rec-feed.html` — not in the deploy manifest
   (`scripts/deploy_to_hubspot.py`), included by no template.
3. **Loop recs** (deterministic) — `forecasting.py:274-320`,
   `routes/loop.py:263-372` — recs have **no stable IDs**.
4. **Self-checkout budget recs** (deterministic) —
   `recommendation_gen.py`, `routes/self_checkout.py` — currently on
   fabricated demo signals; its standalone partial
   (`self-checkout-recommendations.html`) is included nowhere.
5. **Red Light flag cards** — `partials/recommendations.html` (the one
   partial that IS deployed).

Four different approve endpoints, four rec-ID namespaces, and no
shared voice guidance: the strongest client-voice rules live in
`ticket_recap.py:30-101` (voice, product accuracy, never-invent-
numbers, no internal tool names, fair-housing) but nothing else reuses
them. `fair_housing_gate.py` exists but is not applied to rec
generation. Two files hardcode models instead of importing config.

### Design
1. **Shared voice layer** — new `webhook-server/rec_voice.py`:
   `build_voice_context(company_props)` composes a per-property voice
   block from the community brief (voice_tier via `resolve_value`,
   unit_noun, brand_adjectives, taglines, must_include,
   forbidden_phrases) + the canonical client-comms rules (extracted
   from `ticket_recap.py` into a shared constant both import). Inject
   into the Call Prep prompt (`server.py:5136`) and the Red Light
   insight extraction prompt (`red_light_pipeline.py:51`). Run
   client-facing LLM rec copy through `fair_housing_gate`.
2. **One client-facing rec surface** — Call Prep section (main portal)
   + Loop plan pane (Loop subpage) are canonical. Retire the dormant
   rec-feed partial (delete or fold its HubDB feed into Call Prep);
   wire or delete the orphaned `self-checkout-recommendations.html`;
   fix the deploy manifest accordingly.
3. **Normalize the rec model** — shared `rec_id` convention + status
   vocabulary (`pending/approved/dismissed/expired`) documented in one
   module; give Loop recs stable IDs (hash of uuid+action+period);
   keep the four write paths but route them through one small
   `recommendations.py` helper so status/telemetry are consistent.
4. **Hygiene** — hardcoded models (`content_brief_writer.py:28`,
   `kb_writer.py:39`, `url_scraper.py:39`) move to config. This is NOT
   the LLM Gateway build (Phase 0 open question B6) — do not build a
   gateway; just centralize the constants.

---

## Workstream 3 — Assets named, sized, dropped into shared Google Drive (ask 4)

### Current state (verified)
- **ADR 0020** (`docs/architecture/decisions/0020-asset-pipeline-google-drive.md`,
  status Proposed) already designs this: Portal → Google Shared
  Drive → Fluency, with folder layout, a BQ `rpm_portal.assets` index,
  env vars `RPM_ASSETS_SHARED_DRIVE_ID` / `RPM_ASSETS_ROOT_FOLDER_ID`,
  and proposed modules `drive_client.py`, `asset_resizer.py`,
  `asset_index.py` — **none of which exist yet.**
- Everything needed to build it exists elsewhere in the repo:
  - Resize machinery: `blueprint_assets.py:94-141` (letterbox/crop-fit
    variants), `asset_uploader.py:33-96` (compression), variant dims
    in `config.py:311-325` (`FLUENCY_ASSET_VARIANTS`).
  - Drive auth precedent: `kb_writer.py:180-253` — the repo's one real
    Drive call (`supportsAllDrives=True`, full `drive` scope,
    service-account via `GOOGLE_SERVICE_ACCOUNT_JSON`).
  - Upload entry points: `POST /api/asset-upload` (`server.py:1001`)
    → `asset_uploader.process_asset_upload()`, and the blueprint flow
    (`routes/onboarding.py:216` → `blueprint_assets.process_upload()`).

### Design — implement ADR 0020
1. `webhook-server/drive_client.py` — service-account Drive v3 client
   (pattern from `kb_writer.py`), ensure-folder-path + upload +
   shareable-link helpers, all `supportsAllDrives=True`.
2. `webhook-server/asset_resizer.py` — reuse the blueprint resize
   logic against the ADR's variant/size set.
3. **Deterministic naming** — follow the ADR's convention; if the ADR
   leaves it open, use
   `{property_code|uuid}_{category}_{variant}_{WxH}.{ext}` and record
   the choice in the ADR (flip status → Accepted, note deltas).
4. **Hook, don't fork** — after the existing HubSpot Files upload
   succeeds, mirror the named/sized variants into the Shared Drive
   folder tree. Drive failure must never fail the portal upload
   (log + retry queue or flagged row).
5. **Backfill script** — `scripts/backfill_assets_to_drive.py` for
   existing HubSpot-hosted assets (dry-run flag, per-property limit).
6. **Config/docs** — new env vars in both `config.py` copies and
   `.env.example`; runbook for creating the Shared Drive and granting
   the service account access.

Sharing with the team is a one-time Drive action (Shared Drive
membership), not code — see "What Kyle provides" below.

---

## What Kyle needs to provide (blockers, per workstream)

- **T1 (tickets):** none to start. Note `.env.example` is missing the
  nine `CLICKUP_LIST_*` vars — types with unset vars silently vanish
  from the picker; confirm Render has them all set.
- **T2 (recs):** decision baked into the prompt: Call Prep + Loop pane
  are the canonical surfaces; rec-feed retires. Flag if you disagree.
- **T3 (Drive):** create the Google **Shared Drive**, add the service
  account (the `client_email` in `GOOGLE_SERVICE_ACCOUNT_JSON`) as
  Content Manager, then set `RPM_ASSETS_SHARED_DRIVE_ID` (and
  optionally `RPM_ASSETS_ROOT_FOLDER_ID`) on Render. Code lands
  behind these env vars and no-ops until they're set, so this does
  not block the branch.

## Known pre-existing bugs adjacent to this work

Logged here so terminals fix what's in their lane and skip the rest:
- Dashboard ticket counts still come from the legacy HubSpot path and
  its status filter is a no-op (`client-portal.html:5334, 5636-5640`
  vs `ticket_manager.py:220-232`). (T1 lane — fix if cheap.)
- Dead legacy ticket card renderer + cosmetic-only `escalateTicket()`
  (`client-portal.html:3934-3974`). (T1 lane.)
- `loop.js:336` deep-link `#ticket=new` has no hash handler. (T1 lane.)
- `brief_ai_drafter.CB_DRAFTABLE` contains three keys not in
  `community_brief.FIELDS` (`marketed_amenity_names`,
  `amenities_descriptions`, `selling_points`) — extractions silently
  dropped. (T1 lane — add the missing `BriefField`s or drop the keys.)
- Recap pipeline fuzzy-matches companies despite an exact BigQuery
  `portal_tickets` mapping existing (`clickup_recap.py:81-111`).
  (T1 lane — optional.)
