# Ticket → Property Profile loop

**Date:** 2026-08-06
**Specs this closes:** `docs/ticket-page-scope.md`, `docs/clickup-ticket-recap-plan.md`

## Part 1 — the flow as it exists today

### A. Intake — portal is the front door

```
client-portal.html  §Tickets  toggleCreateTicket()
   └─ _loadTicketTypes()      GET  /api/portal-tickets/types
        └─ routes/portal_tickets.ticket_types
             └─ portal_tickets.types_with_schema()
                  ├─ configured_types()   config.PORTAL_TICKET_TYPES × env CLICKUP_LIST_*
                  └─ form_schema(list_id) clickup_client.get_list_fields()
                                          minus PORTAL_TICKET_PREFILL_FIELDS
   └─ renderTicketFields()    inputs keyed by ClickUp custom-field id
   └─ submitTicket()          GET /api/kb-search  ← KB deflection, kept
        └─ _doSubmitTicket()  POST /api/portal-tickets/create
             └─ portal_tickets.create_ticket()
                  ├─ _prefill_values()      hubspot_client.get_company()
                  ├─ _build_custom_fields() _coerce() per ClickUp field type
                  ├─ _description()         identity stamp (uuid + company id)
                  ├─ clickup_client.create_task(list_id, tags=["portal"])
                  └─ _record_mapping()      BQ portal_tickets  (migration 0012)
                                            task_id ↔ company_id ↔ property_uuid
```

### B. Tracking — portal is the status window

```
nav('tickets') → loadTickets()
   GET /api/portal-tickets?company_id&uuid
     └─ portal_tickets.list_tickets()
          ├─ _read_mappings()          BQ portal_tickets — EXACT task_id lookup
          ├─ clickup_client.get_task() per mapped task
          └─ _shape_task() → client_status()  config.PORTAL_TICKET_STATUS_MAP
```

### C. Completion — ClickUp Done → client-facing recap on the company

```
ClickUp webhook/automation
   POST /api/webhooks/clickup/ticket-complete        server.py:6179
     ├─ clickup_recap.verify_webhook_auth()   HMAC X-Signature | ?token=
     │                                        no secret configured → REJECT
     └─ 202 + background thread
          └─ clickup_recap.process_completed_task()
               ├─ status must be done/closed
               ├─ ticket_recap.infer_ticket_type()   ← ClickUp LIST NAME, not id
               ├─ skip ticket_recap.EXCLUDED_TYPES   ← dispo_cancel
               ├─ skip if task already tagged "recap-posted"
               ├─ match_company_for_ticket()
               │     1. Property URL → normalize_domain → domain EQ
               │     2. …           → website CONTAINS_TOKEN
               │     3. Property Code → property_code | yardi_id
               │     4. task name
               │     every step: EXACTLY ONE uuid-bearing company, else skip
               ├─ clickup_client.get_comments()
               ├─ ticket_recap.generate_recap()   LLM + positioning + _REDACT backstop
               ├─ ticket_recap_pdf.build_recap_pdf()
               ├─ ticket_recap_writer.post_recap_to_company()
               │     note authored by hubspot_owner_id + AM close-out task
               └─ clickup_client.add_tag("recap-posted")
```

### D. The property profile — where the loop did NOT reach

```
nav('brief') → loadClientBrief()
   GET /api/accounts/property/brief                  server.py:6667
     └─ community_brief.load_company_state()  one batched HubSpot read
     └─ community_brief.build_render_context()
          rows carry badge_kind ∈ {override, pipeline, pending}
   PATCH /api/accounts/property/field                server.py:6695
     └─ community_brief.write_field()
          ├─ validates options / TABLE_TYPES JSON
          ├─ PATCH the field's fluency_*_override property
          └─ property_brief_audit.log_edit()

community_brief.resolve_value()   community_brief.py:443
   THE single precedence rule: override > resolved > empty
   consumers: _effective, build_render_context, fluency_feed._resolve
```

**The gap.** C and D never met. A completed Rebrand ticket carrying the new
advertised name did not reach the profile, and the profile could not say which
tickets had shaped it. The client-facing recap note was the only artifact a
completed ticket produced.

## Part 2 — what this change adds

```
      C. ticket completes
            │
            ▼
   ticket_profile_sync.propose_from_task()          ← Layer 2 skill
            │  extractors: field-map (always) · AI (TICKET_PROFILE_AI_EXTRACT)
            │  guardrails: per-type allowlist · editable fields only
            │              dispo_cancel excluded · no-op proposals dropped
            │              current value read via community_brief.resolve_value()
            ▼
   BQ ticket_profile_proposals  (migration 0013, append-only state log)
            │  proposal_id = sha1(task_id, field_key)  → idempotent re-fire
            ▼
   D. Property profile tab
        "Updates proposed from your completed requests"
            │
            ├─ Accept → community_brief.write_field()  ← the ONE write path
            │             → append status=accepted
            └─ Dismiss → append status=rejected
        "Requests that shaped this profile"
            └─ portal_tickets mapping (exact) × accepted proposals per task_id
```

### Why proposals, not writes

`CLAUDE.md` rule 4 (override-wins) is load-bearing: a human edit on a profile
field beats every machine-resolved value. A ticket-driven write would silently
stomp that. So a completed ticket **proposes**; a human accepts. A proposal
whose target field already carries a human override is stored with
`conflicts_with_override = true` and rendered with an explicit warning, so
accepting one is a deliberate act.

Accepting routes through `community_brief.write_field()` — the same path the
portal's inline editor uses — so the value lands on the `fluency_*_override`
property, is audit-logged with the accepting user's email, and reaches Fluency
on the next daily sync. No second write path, no second copy of the resolution
rule.

### Matching stays exact-or-skip

Nothing here loosens matching. Proposals are only generated for a task that
`clickup_recap.match_company_for_ticket()` already resolved to exactly one
uuid-bearing company. The ticket-history panel reads the stored
`task_id ↔ company_id` mapping — it never fuzzy-matches.

### Flags

| Flag | Default | Effect |
|---|---|---|
| `TICKET_PROFILE_LOOP_ENABLED` | off | every `/api/ticket-profile/*` route 404s; no proposals are generated on ticket completion |
| `TICKET_PROFILE_AI_EXTRACT` | off | when on, an LLM reads the ticket thread and proposes values for the type's allowlisted fields, marked `extractor=ai` |
| `TICKET_PROFILE_FIELD_MAP_JSON` | built-in map | override/extend the ClickUp-field-name → profile-field map without a deploy |

With the flag off, `process_completed_task` behaves exactly as before.

## Part 3 — activating it

1. **Run migration 0013** so `ticket_profile_proposals` exists. Without it,
   proposals are generated, logged, and dropped — the portal shows nothing.
2. **Dry-run a real completed ticket** before flipping anything:
   `POST /api/webhooks/clickup/ticket-complete?dry_run=1&token=…` now returns a
   `profile_proposals` array alongside the recap draft. Nothing is stored.
   This is the cheapest way to see whether the field map matches your ClickUp
   lists' real field names.
3. **Fill the field map.** The built-in map guesses at names
   (`New Property Name`, `New Budget`, …). Whatever the dry run shows is
   missing goes into `TICKET_PROFILE_FIELD_MAP_JSON` — no deploy needed.
4. **Set `TICKET_PROFILE_LOOP_ENABLED=true`** on the Flask service. Proposals
   start accumulating and the two panels appear on the Property profile tab.
5. **Optionally set `TICKET_PROFILE_AI_EXTRACT=true`** once the deterministic
   path is trusted. It only widens what gets *proposed*; the human accept step
   is unchanged.

### What this does NOT change

* The HubSpot Service Hub ticket flow (`/api/ticket`) is untouched and still
  runs alongside — retiring it is decision #1 in `docs/ticket-page-scope.md`
  and is Kyle's call.
* Company matching is unchanged. Proposals ride on the match
  `clickup_recap` already made; nothing here loosens exact-or-skip.
* R1 holds: nothing in this path writes the company `uuid`. Accepting a
  proposal writes exactly one `fluency_*_override` property.
