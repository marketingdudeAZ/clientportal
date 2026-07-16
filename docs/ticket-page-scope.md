# Scope — Portal Ticket Page (per-type forms → ClickUp, tracked in the portal)

**Status:** Draft for Kyle's review
**Date:** 2026-07-15

## The problem

Ticket intake happens **outside the portal**. Each of the 7 ClickUp lists has its
own ClickUp form that people fill in directly, so:
- Requesters re-type property info the portal already knows (Property URL,
  Market, Property Code, Account Manager).
- There's **no way to see what's open for a property** without ClickUp access —
  and ClickUp is becoming marketing-services-only.
- The portal's own ticket form creates a **HubSpot Service Hub ticket** instead
  (`/api/ticket` → `ticket_manager.create_ticket`, with a thread/reply UI) — a
  second, parallel system that doesn't reach the teams doing the work.

## The model

**Portal = the front door + the status window. ClickUp = where the work happens
(internal).** Property marketing never logs into ClickUp; they open tickets in
the portal and watch status there. On Done, the client-facing AI recap posts to
the company record (already built — see `clickup-ticket-recap-plan.md`).

```
Portal → pick ticket type → per-type form (property fields PRE-FILLED)
      → creates a ClickUp task in that type's list
      → portal shows this property's open tickets + status
      → on Done: AI recap note on the HubSpot company record
```

## 1. Ticket types = the 7 ClickUp lists

New Account Build · Budget Update · General Ticket · Dispo/Cancel ·
Creative & Ad Copy Updates · Campaign Performance Review · Rebrands

## 2. Per-type forms — generate them from ClickUp, don't hand-code

**Recommendation:** read each list's custom fields from the ClickUp API
(`GET /list/{list_id}/field`) and **render the form dynamically** from that
definition. Why: the forms stay in sync when your team edits a ClickUp field —
no portal redeploy, no drift. Hand-coding 7 forms guarantees they rot.

**The big UX win:** the portal already knows the property, so we **pre-fill**
what the ClickUp form makes people type today — Property URL, Market, Property
Code (Yardi), Account Manager, and the uuid. The requester only fills the fields
that are actually about the request.

## 3. Creating the ticket

`POST /api/tickets/create` → `{company_id, ticket_type, fields{}}`
→ `clickup_client.create_task(list_id, name, description, custom_fields)`
→ stamp property identity onto the task (Property URL + Property Code + uuid) so
  the recap automation can match it back with confidence.

**Record the mapping.** Because *we* create the task, store `task_id ↔ company_id`
(BigQuery, or a HubSpot company property holding a JSON list). This makes
tracking exact — no fuzzy matching later.

## 4. Tracking — "what's open for this property"

`GET /api/tickets?company_id=` returns the property's ClickUp tasks:
- Preferred: read the stored `task_id ↔ company_id` mapping, then batch-fetch
  status from ClickUp.
- Fallback for tickets created outside the portal: ClickUp search filtered by the
  Property URL / Property Code custom field.

**Status mapping (decision needed).** ClickUp statuses are internal ("pending pm
approval", "in progress", "complete"). Map them to a clean, client-safe set:

| ClickUp | Portal shows |
|---|---|
| to do / open | **Open** |
| in progress | **In progress** |
| pending pm approval | **In progress** (or "Awaiting review"?) |
| complete / closed | **Done** |

Show: type, subject, status, age, submitted-by. **Do not** show internal ClickUp
comments/chatter — that's the whole point of the recap layer.

## 5. Keep the deflection step

The current form does a KB search before submit (Emily's ask: "did I need a
ticket, or just education?"). Keep it — show help articles first, then let them
submit. This is our best lever against ticket volume.

## Decisions needed from Kyle

1. **What happens to the HubSpot Service Hub ticket flow?** Today `/api/ticket`
   creates HubSpot tickets with a thread/reply conversation. Options:
   (a) **Retire it** — portal tickets go to ClickUp only (simplest, one system);
   (b) keep HubSpot tickets for client↔AM conversation and ClickUp for the work
   (two systems, needs sync);
   (c) ClickUp for work + the AM emails from HubSpot as today.
   *My rec: (a) — one system. The recap note already covers the client-facing record.*
2. **Status labels** — confirm the mapping above (esp. "pending pm approval").
3. **Who can submit which type?** e.g. should property marketing be able to open
   a Dispo/Cancel? Or is that internal-only?
4. **List IDs** — I need the 7 ClickUp **list IDs** (the URLs you sent are view
   IDs). I can pull them via the API with your workspace id `9011805260` if you'd
   rather I find them.
5. **Reply/comment**: can a requester add a comment to an open ticket from the
   portal (→ posts a ClickUp comment), or is it submit-and-watch only?

## Build stages

1. Pull list IDs + field definitions; `GET /api/tickets/types` returns the form schema.
2. Portal: type picker → dynamic form (pre-filled property fields) → submit.
3. `POST /api/tickets/create` → ClickUp task + store the mapping.
4. `GET /api/tickets` → property's tickets + mapped status; render the list.
5. Optional: comment-from-portal; retire the HubSpot ticket path.
