# Three Surfaces, One Spine — how the portal should split

**Status:** Proposal / thinking document
**Date:** 2026-09-08
**Author:** Kyle Shipp (with Claude)
**Companion doc:** `docs/PROPERTY_FLIGHT_PLAN.md` — the work generator that feeds surface 1

Interactive version: https://claude.ai/code/artifact/09518e07-6280-4854-88c2-77d1ca3e1a46

---

## The finding

The portal feels busy because it is **three products wearing one interface**, not
because it is built wrong. An account manager at 9am, a client on a quarterly call,
and leadership asking whether the business works all need different things and
currently get the same screen — so that screen has to carry everything.

The recommendation is to split it by audience, not rebuild it. The data spine does
not change.

## We already built most of this

| What we want | What exists | Status |
|---|---|---|
| Which properties need work | `attention.py` — one queue across ClickUp, HubSpot tickets, onboarding, triage | Built |
| What needs to move | `red_light_history`, `/api/internal/red-light-v2-batch` | Built |
| See improvements | `/api/internal/forecast-batch` — leases/30d with 80% CI | Built |
| All the context in one place | Property Brief + Community Brief on the HubSpot record | Built |
| What conversations to have | Brief covers positioning; nothing generates talking points | Partial |
| What is working / not working | `loop_events` — the event spine exists | Built |
| What people use and do not | No usage events; `loop_events` tracks marketing, not the product | Missing |
| Where GTM fails, SKU performance | Spend + entitlement data exists; nothing rolls it up by SKU | Missing |
| Visibility into what the team is doing | Nothing — the real hole | Missing |

Six of nine already exist. That is why this is a split, not a rebuild.

## The design principle is already in our code

From the docstring at the top of `webhook-server/attention.py`:

> "Health-score triage rows are deliberately NOT here. Properties and the Portfolio
> Dashboard already show them; repeating them turns an action inbox into a second
> dashboard, which is what the triage list replaced."

**An action inbox, not a second dashboard.** That instinct is correct. It was applied
to one module; it should be applied to the product.

## The three surfaces

### 1. The Queue — account managers, daily

A ranked list of properties that need something, each with the reason and the next
action attached. Five items, not seven hundred. `attention.py` is most of this
already; what is missing is ranking by consequence and a recommended action per row.
Fed by the work generator in `PROPERTY_FLIGHT_PLAN.md`.

**Answers:** what do I do today?

### 2. The Property Room — AM with the client, weekly/monthly

One property, everything about it, oriented around a conversation rather than a
report: what changed since last time, what is improving, what is not, and the
questions worth asking. The brief, the red-light score, and the forecast already
exist; nobody has assembled them into talking points.

**Answers:** what do we talk about, and what do I ask?

### 3. Signals — leadership, weekly

The PostHog-shaped surface. Adoption, what is used and ignored, which SKUs
underperform, where go-to-market is leaking. Mostly does not exist yet and is the
only surface needing genuinely new instrumentation.

**Answers:** is this working, and where is it not?

## The analytics gap is smaller than it sounds

`loop_events` is already an append-only event log with a writer module and a tracking
context manager — the same primitive product analytics tools are built on.

What it records today is **marketing** events: syncs, batches, cron runs. What it does
not record is **product** events: who opened a property, who ignored a flagged one,
which report nobody clicks, which SKU never gets pitched.

Same table, same writer, new event types. "What are we doing" is instrumented. "Is
anyone using it, and is it working" is not.

## The guardrail: this does not become a third CRM

We have Salesforce and HubSpot. The line that keeps this honest:

**HubSpot owns identity. The platform owns judgment and activity.**

This is already an immutable rule — R1 says every property is addressed by a `uuid` on
the HubSpot company record and code never writes it. Extend the same discipline: if a
field describes *who someone is*, it lives in HubSpot or Salesforce. If it describes
*what we noticed, decided, or did*, it lives here. Anything starting to look like a
contact record means we have drifted.

## Where agents fit

The portal today can tell you a property's score dropped. It cannot tell you why,
what to do, or whether last month's fix worked. That gap is judgment.

- **Researcher** — assembles the Property Room before a client call
- **SEO / Paid agents** — take Queue items, diagnose, propose the fix
- **Analytics agent** — writes the Signals rollup weekly

A shared task board (Hermes ships one) showing work claimed by named agents and
humans on the same board is the missing visibility layer. That does not exist in our
stack today.

## Order

1. **Finish the Loop** — six blockers, ~90 min of human config, documented in
   `OUTSTANDING_WORK.md`. Everything downstream reads from it. Paused since 2026-05-17.
2. **Build the Queue as its own view** — re-rank `attention` by consequence, attach a
   recommended action. Do not add data; subtract until five things are visible.
3. **Point the Researcher at the Property Room** — output to Slack first, no portal
   changes, learn whether AMs use it before building UI for it.
4. **Add product events to `loop_events`** — opens, ignores, SKU pitches.
5. **Then design Signals**, with real usage data behind it.

Nothing here throws away work. The heaviest lift is editorial — deciding what each
audience does *not* see.

---

*Caveat: this is grounded in a read of the repo at commit `09941e2`, not in
observation of the running UI. The "busy" diagnosis is Kyle's, not measured.*
