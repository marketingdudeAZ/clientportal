# Runbook — Turn ON the ticket-recap automation (ClickUp Done → HubSpot note + PDF)

**What it does:** when a ClickUp ticket is marked Complete, the server matches
the ticket to its HubSpot company (uuid required, exact match only), generates
a client-facing recap through the positioning layer, and posts it as a note on
the company record — authored by the company owner, with the detailed recap
PDF attached (permanent HubSpot Files URL) — plus an AM close-out task to
review the note after posting. Dispo/Cancel tickets are always skipped.

**Where it lives:** `webhook-server/clickup_recap.py` (orchestrator),
`ticket_recap.py` (AI draft + redaction backstop), `ticket_recap_pdf.py`,
`ticket_recap_writer.py` (HubSpot writes), endpoint
`POST /api/webhooks/clickup/ticket-complete` in `server.py`.
Plan/policy: `docs/clickup-ticket-recap-plan.md`.

The code ships with main and is already live on
`https://rpm-portal-server.onrender.com` — the automation is OFF until a
ClickUp webhook (or Automation) actually calls the endpoint. That
registration is the on switch.

## Go-live steps

### 1. Register the ClickUp webhook(s)

From repo root on a machine with `.env` (needs `CLICKUP_API_KEY`):

```bash
# See what's already registered:
python3 scripts/register_ticket_recap_webhook.py status

# Register per ticket list (recommended — repeat for each of the ticket
# lists: New Account Build, Budget Updates, General, Creative & Ad Copy,
# Campaign Performance Review, Rebrands. SKIP Dispo/Cancel — the server
# excludes it anyway, but there's no reason to even send it the events):
python3 scripts/register_ticket_recap_webhook.py register --list-id <LIST_ID>
```

Each registration prints a **signing secret** — ClickUp shows it only once;
copy it now.

Alternative (no script): a ClickUp Automation per list — "when status changes
to Complete → Call webhook" pointed at
`https://rpm-portal-server.onrender.com/api/webhooks/clickup/ticket-complete?token=<SECRET>`
where `<SECRET>` is any one entry of `CLICKUP_WEBHOOK_SECRET`.

### 2. Configure the secret on Render

On the Render service (`rpm-portal-server`), **append** each new secret to the
`CLICKUP_WEBHOOK_SECRET` env var, comma-separated. Do NOT replace the existing
value — the property-brief webhooks verify against the same variable. Save and
let the service restart.

The receiver rejects all requests (including `?token=`) until a secret is
configured — there is no unauthenticated mode on this endpoint.

### 3. Verify with a dry run against production

Pick a recently completed real ticket and ask for the draft without posting:

```bash
curl -s -X POST \
  "https://rpm-portal-server.onrender.com/api/webhooks/clickup/ticket-complete?token=<SECRET>&dry_run=1" \
  -H "Content-Type: application/json" \
  -d '{"task_id": "<CLICKUP_TASK_ID>"}' | python3 -m json.tool
```

Check: the right company matched (`match` method + `company` name), the note
reads client-safe, `needs_review`/`flags` look sane. A
`{"skipped": "no match …"}` here is the system working as designed — it never
guess-writes.

### 4. First live post

Mark one real ticket Complete (or re-fire it through the webhook without
`dry_run`). Then confirm on the HubSpot company record:

- the **note** is there, authored by the company owner, with the **PDF
  attached**;
- the **AM close-out task** exists and is assigned to the owner;
- the ClickUp task now carries the **`recap-posted`** tag (this is the
  idempotency guard — a re-fired webhook won't double-post).

Render logs show `recap posted: company=… note=… task=…` on success and
`clickup_recap: …` lines for every skip with the reason.

## Ongoing safety model

Per Kyle (2026-07-15): no pre-approval queue. The AM close-out task **is** the
human check — owners review/correct each posted note. Notes the AI or the
deterministic redaction backstop flag land with a HIGH-priority task marked
"⚠ flagged by AI — check framing carefully".

## Turning it OFF

```bash
python3 scripts/register_ticket_recap_webhook.py status   # find the webhook id(s)
python3 scripts/register_ticket_recap_webhook.py delete <WEBHOOK_ID>
```

(or disable the ClickUp Automation). Removing the secret from Render also
hard-stops the endpoint, but prefer deleting the webhook so ClickUp doesn't
keep retrying failed deliveries.
