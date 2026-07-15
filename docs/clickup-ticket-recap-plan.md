# Plan — ClickUp ticket complete → HubSpot company note (AI recap, client-facing framing)

**Status:** Draft for review (Kyle + Senior Ops Manager)
**Date:** 2026-07-15
**Becomes:** ADR 0021 once approved.

## The goal in one paragraph

When a ClickUp ticket is marked **Complete**, automatically write a short,
**client-facing** activity note on the matching **HubSpot company record**
recapping what we did. ClickUp becomes a **marketing-services-only** space —
property marketing and clients no longer see into it the way they do today. The
curated HubSpot note becomes the single, client-safe record of work delivered.
An AI recap plus a **positioning layer** makes the note proactive and
professional: problems are surfaced honestly, but framed appropriately — never
internal manager→specialist coaching, never self-blame, never raw config talk.

## Why now — what actually changes

- **Today:** property marketing / clients can see into ClickUp, so they see raw
  internal chatter (a manager coaching a specialist, "the setting was wrong,"
  "we missed this at onboarding").
- **New model:** ClickUp is internal-only. The **company-record note is the
  client-facing artifact.** The team keeps working candidly in ClickUp; clients
  get a clean, curated summary of what was accomplished.

## The flow (plain English)

1. A specialist marks a ticket **Complete** in ClickUp.
2. ClickUp notifies our system (a webhook).
3. We pull the ticket's details and the work thread (comments).
4. We match the ticket to the right **HubSpot company** — by website, and
   **only** for companies that have a `uuid`.
5. AI writes a short recap, run through the **positioning layer** (below).
6. A deterministic safety scrub strips any internal terms that slipped through.
7. **[At launch: a person approves the draft]** → the note posts to the company
   record, tagged by ticket type.

## The positioning & filter layer  ← the part to sanction

Every recap is rewritten to these rules before it can post:

**Principles**
- **Client-facing and proactive** — what we did and the outcome, in plain,
  confident language.
- **Strip the internal layer** — no manager↔specialist coaching, no internal
  role names, no internal tooling or process talk.
- **Don't hide problems — reframe how we speak about them.** Surface the issue
  *and* that we handled it.
- **Missing inputs frame to the property-marketing / onboarding side** when
  that's accurate (per Kyle: our processes are buttoned up), not "our error."
- **Forward-looking close.**

**Before → After (illustrative — please react to these)**

| Internal ClickUp reality | Client-facing note |
|---|---|
| "Specialist set the geo radius too tight, manager caught it, had them redo it." | "We reviewed the campaign's targeting and refined the service radius to focus spend on the highest-intent renters in the area." |
| "Onboarding never sent unit mix, so the ads were generic." | "To sharpen messaging, finalized unit-mix details will let us tailor the creative further — once provided, we'll update the campaigns accordingly." |
| "Client's site was down for a week, killed our conversion tracking." | "We flagged a period of website downtime affecting tracking and have re-validated conversion measurement now that the site is restored." |

**One integrity guardrail I want on the record (needs your ruling).** The layer
removes self-blame and internal coaching, and defaults missing-input framing to
the property-marketing side **where that is factually true**. It should **not
fabricate client blame for a genuine internal error** — a written, client-visible
note that falsely blames the client is a liability. Recommended stance: for a
true internal slip, go **neutral/proactive** ("we identified a pacing adjustment
and corrected it to keep spend on plan"), never a false accusation. This keeps
the spin positive without creating exposure.

**Two-layer safety**
1. The AI positioning prompt (above).
2. A deterministic **redaction backstop** — block-list of internal words
   (specialist, manager, coaching, teammate names, internal tool names) that
   hard-stops or flags a draft before it can post, even if the AI misses.

## Matching a ticket to a company

- **Hard rule:** only write to companies that have a **`uuid`**. No uuid → skip
  and log. (Same rule we use everywhere — never touch a non-addressable record.)
- **Match key:** **website / domain** (your call), via the existing
  `resolve_company_by_domain` (normalize www / https / trailing slash).
- **Stronger option (recommended):** stamp a **HubSpot company-id (or uuid)
  custom field** on ClickUp tickets at creation — most of our tickets are already
  created by automation, so this is easy — giving an **exact** match. Website
  stays the fallback for legacy/manual tickets.
- **No match / ambiguous** (two companies share a domain, or none found) → route
  to a **review queue**; never guess-write.

## Per-ticket-type policy (your 7 lists)

| List | Client note? | Framing |
|---|---|---|
| New Account Build | Yes | "Your campaigns are live" onboarding recap |
| Budget Updates | Yes | "Your budget change is live and pacing" |
| General Tickets | Yes | Generic, content-dependent |
| Dispo / Cancel | **No (default)** | Off-boarding — sensitive; exclude unless you want a neutral closeout |
| Creative & Ad Copy Updates | Yes | "Refreshed your creative / copy" |
| Campaign Performance Review | Yes | Performance recap (decide: numbers vs qualitative) |
| Rebrands | Yes | "Rebrand rollout complete" |

## Guardrails

- **uuid required**; **no-match review queue**; **idempotent** (dedupe on ticket
  id + completion so a re-fired webhook can't double-post).
- **Human-in-the-loop at launch** — the AI drafts, an AM/ops approves, then it
  posts. Graduate to auto per ticket-type once framing is trusted. (Recommended;
  this is client-visible, so start supervised.)
- **Audit trail** — store the raw ticket, the generated draft, the final posted
  note, and who approved, in BigQuery, for traceability.
- **ClickUp access change** — reaffirm ClickUp is internal-only going forward
  (the whole model depends on it).

## What's already built (so this is a modest build)

- ClickUp API + task/custom-field reads (`clickup_client.py`).
- Domain → HubSpot company resolution (`brief_ai_drafter.resolve_company_by_domain`).
- HubSpot note create + company association (`call_notes.save_call_notes`).
- Claude LLM via the existing pattern.

New work = a **ClickUp webhook receiver**, the **positioning prompt + redaction
backstop**, the **review-queue/approval UI**, and the wiring.

## Rollout

1. **Shadow mode** — on ticket complete, generate the draft + match, but **don't
   post.** Collect drafts so you + ops can judge framing quality on real tickets.
2. **Approval mode** — drafts post to the company record after a human approves.
3. **Auto mode (optional)** — trusted ticket types post automatically; sensitive
   ones stay supervised.
4. **Harden** — per-type prompt tuning, add the company-id field to ticket
   creation for exact matching.

## Decisions needed (you + ops manager)

1. **Framing policy** — sign off on the reframing principles and the
   "never fabricate client blame → go neutral instead" integrity line.
2. **Per-type inclusion** — confirm the table, especially **Dispo/Cancel = off**.
3. **Matching** — website-only, or add a company-id field to tickets for exact
   matches?
4. **Launch mode** — shadow → approval → auto (recommended), and **who approves**
   the notes (which role)?
5. **Performance recaps** — include metrics/numbers, or keep qualitative?
6. **ClickUp access** — confirm the cutover to internal-only, and when.
