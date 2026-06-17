# Operating Model Redesign — HubSpot + ClickUp + the Community Brief

**Owner:** Kyle Shipp, Managing Director, Digital Products & Services
**Companion to:** `Digital_Team_Roadmap_2026.md` (slots into Goal 3 "Automate
delivery" and Goal 1 "Productize the agency")
**Status:** Working draft for Gstack office hours
**Date:** 2026-06-17

> **The one-sentence thesis:** Run the team on **two surfaces and one source of
> truth** — HubSpot Service Hub is the **Account Manager's cockpit**, ClickUp is
> the **doer's production queue**, and the **Community Brief is the system of
> record** that feeds everything. Work flows by *state change*, not by *ticket
> hand-off*.

---

## 1. The problem we're solving

We are clunky because **work is coordinated by tickets instead of by state**.
Concretely, three symptoms:

1. **AMs and doers live in the same tool, so doers get tapped for everything.**
   Status updates, coordination, and real production work all arrive as ClickUp
   tasks. A specialist can't tell "the world changed, do something" from "FYI."
2. **The Community Brief is downstream of a ticket, not the source.** Today a
   property's marketing inputs change by *filing a ticket that eventually edits
   the brief*. The brief is treated as an output, when it should be the input
   everything else reads.
3. **Mid-month budget changes are a 4-system relay race.** Portal/config →
   HubSpot deal → AM review task → ClickUp task → manual Fluency sheet edit →
   campaign reconfigure. Every hop is manual; proration is manual; nothing
   propagates on its own. This is the single most-felt pain.

The fix is not "more tickets, better organized." It's **moving the coordination
load off of tickets and onto data + automation**, so a human is only pulled in
when a human is actually required.

---

## 2. The target operating model

### Two surfaces, two audiences, one source of truth

| System | Audience | What it's for | What it is NOT for |
|---|---|---|---|
| **HubSpot Service Hub** | **Account Managers** | Client comms, ticket pipelines, SLAs, recommendations + approvals, viewing/editing the Community Brief. The AM's whole day lives here. | Tasking specialists. Production work. |
| **ClickUp** | **Doers** (SEO, paid, social, reputation, creative) | The production queue. A task appears **only when there is real work to do**, triggered by a state change, with full context attached. | Coordination, status, FYIs, client comms, "please update the brief." |
| **Community Brief** (HubSpot company record) | **System of record** | The canonical marketing identity + budget + channels + guardrails per property. Everything reads from it; updating it *is* the update. | A document you regenerate from a ticket. |

**The principle:** AMs operate on *clients and state*; doers operate on *changes
that need production*. The Community Brief is the contract between them.

### The pattern that makes it work — "property-as-trigger" (we already proved it)

We don't need new infrastructure to do this. We already built the pattern in the
**gap-review workflow** (`docs/handoffs/HUBSPOT_GAP_REVIEW_WORKFLOW.md`):

> The Flask app sets a **property** on the HubSpot company
> (`rpm_gap_review_action`). A **HubSpot Workflow watches the property change**
> and creates the AM task, sends the email, escalates on no-response, and closes
> the loop — all without the app needing the `tasks` scope, and with marketing
> admins owning the templates (no code deploy to reword).

**Generalize this.** State changes write a property on the HubSpot company →
HubSpot Workflows route the right action to the right surface (AM task in Service
Hub, or a routed ClickUp task for doers). This is the spine of the whole redesign,
and it's a pattern we've already shipped once.

---

## 3. Rework A — HubSpot Service Hub as the AM operating system

Make Service Hub the place AMs actually work, so they stop coordinating through
ClickUp.

**Build:**
- **Ticket pipelines that mirror our real workflows** — onboarding, budget
  change, campaign request, support. We already have the stage model and
  **per-stage SLAs defined** (`ONBOARDING_SLA_PER_STAGE_HOURS` in `config.py`:
  intake 48h, brief review 24h, strategy build 72h, etc.). Stand these up as
  Service Hub pipelines with SLA automation that auto-escalates on breach (the
  gap-review escalation workflows are the template).
- **Help desk / shared inbox** — client email + portal tickets land in one AM
  workspace, threaded to the company record (we already write ticket threads via
  the Conversations API today).
- **Recommendations + approvals in the AM view** — the Loop's co-pilot
  recommendations and the configurator submissions surface as Service Hub cards
  the AM approves, not as ClickUp tasks.
- **The Community Brief, editable in place** — the AM opens the company record
  and edits the brief directly (the `/api/accounts/property/field` PATCH path
  already exists, override-wins).

**Result:** An AM's day is "work my Service Hub queue" — clients, SLAs,
approvals, briefs — and they never open ClickUp.

**Decision flag:** This leans on Service Hub seats/tier (likely Service Hub Pro
for SLA + automation + help desk). Cost + seat count is a Gstack/finance question
— see §7.

---

## 4. Rework B — ClickUp as the doer queue, gated by "something changed"

ClickUp stops being a coordination tool and becomes a **clean production queue**.

**The gate:** A ClickUp task is created **only** when a state change requires
human production work that automation can't absorb. Everything else (status,
coordination, brief edits the system can auto-apply) never reaches ClickUp.

**Routing already exists** — `CLICKUP_LISTS` maps channels to lists
(seo / paid_media / social / reputation / onboarding / property_brief). The
property-as-trigger workflow picks the list from the change context and attaches
full context (property, brief link, what changed, the Loop forecast delta).

**Examples of what does / doesn't create a doer task:**

| Event | Auto-absorbed? | Doer task? |
|---|---|---|
| AM edits brief voice-tier → daily Fluency cron rebuilds tags | ✅ yes | ❌ no |
| Budget reallocated within existing channels, within bounds | ✅ yes (see §6) | ❌ no |
| New channel purchased (needs campaign build) | ❌ | ✅ paid_media list |
| Brief change that needs new creative | ❌ | ✅ creative list |
| Property flagged Red Light (needs intervention) | ❌ | ✅ routed by channel |
| Client asks "how's my SEO?" | ✅ (Loop view / reactive agent) | ❌ no |

**Keep the discipline we have:** the 5-project WIP cap (Goal 3) stays; the
brief-attestation gate ("Community Brief is up to date & accurate",
`CLICKUP_BRIEF_ATTEST_FIELD`) stays — but now it confirms the *source of truth*
is current, not that someone re-keyed it into a ticket.

**Result:** Doers open ClickUp and every card is real work with context. No
noise, no "why am I tagged on this."

---

## 5. Rework C — the Community Brief as the central hub ("update the brief, not a ticket")

This is the inversion you asked for. Today the brief is *generated from* a ticket.
We flip it so the brief **is** the input and editing it propagates automatically.

**What changes:**
- **Remove "file a ticket to update the brief."** The brief is already editable
  in place on the HubSpot side (`/api/accounts/property/field`, override-wins,
  `fluency_*_override`). The AM edits the field; that edit *is* the change of
  record. No ticket.
- **Propagation is automatic.** The daily Fluency tag-sync cron already respects
  overrides and rebuilds live ad tags/copy from the merged values. A brief edit
  reaches Fluency on the next run with zero human hops.
- **A doer task is created only if the edit needs production** (creative rework,
  new page, campaign rebuild) — via property-as-trigger, routed to the right
  ClickUp list. Edits the cron can absorb create no task at all.
- **The brief feeds the accounts surface and the Loop.** It already publishes to
  `/accounts/property` on approval; the Loop's Engage stage consumes it. One
  edit, many consumers, no re-keying.

**Net:** The Community Brief becomes the literal hub — every property's truth in
one place, edited by AMs in Service Hub, read by Fluency, the portal, the Loop,
and (only when needed) the doers. We delete an entire class of "update the brief"
tickets.

**Adjacent fix worth bundling:** close the known Fair Housing gap — the Property
Brief LLM prompt lacks the strict FHA block the Community Brief prompts have
(`docs/CLIENT_BRIEF_SYSTEM.md` §5). ~5 lines of prompt; do it as part of making
the brief the system of record.

---

## 6. Hero automation — mid-month budget change (the clunk, solved)

This is the flagship. It exercises all three reworks at once and kills the most-
felt pain.

### Today (4 systems, human at every hop)
```
Client/AM changes budget
   → POST /api/configurator-submit  (creates/updates HubSpot deal + line items + quote)
   → notifier.notify_am             (HubSpot task: "Review Budget Submission")
   → approval_agent (on approval)   (HubSpot Deal + ClickUp paid_media task + AM task)
                                     "All approvals route to a HUMAN. No auto-execution."
   → someone hand-edits Fluency sheet
   → campaigns reconfigure
Proration mid-month: manual.
```

### Proposed (one capture, bounded auto-apply, human only on exception)
```
1. CAPTURE (one place)
   Client self-serve in portal configurator, OR AM edits the deal/brief in HubSpot.

2. SYSTEM OF RECORD
   Write to HubSpot deal line items + the Community Brief budget fields. One write,
   one truth. (configurator-submit already does the deal half.)

3. CLASSIFY (auto vs. human) — reuse the Loop auto-pilot bounds we already built
   Within bounds  → auto-apply:  reallocation within existing channels,
                                  ≤15% of any channel AND ≤$500 absolute swing
                                  (loop_autopilot.py thresholds, already coded).
   Out of bounds  → human:        new channel, large swing, creative implications.

4. PROPAGATE
   Auto-apply path: write the override to the Fluency pipeline sheet → campaigns
   update on the next sync. This is outstanding work item #14 ("Fluency execution
   hook for auto-pilot budget shifts") — the one missing link; autopilot already
   logs the intent, it just doesn't write Fluency yet.

5. TASK ONLY IF NEEDED
   Out-of-bounds path: property-as-trigger fires ONE ClickUp paid_media task with
   full context. AM gets a Service Hub note, not a ticket to babysit.

6. AUDIT + FORECAST
   Emit a Loop event (stage=optimize, budget_shift), refresh the property forecast,
   log to the deal timeline. Proration handled in code at capture time, not by hand.
```

**Why this is credible, not aspirational:** every piece except the Fluency write
hook already exists — the configurator/deal path, the auto-pilot bounds, the Loop
event bus, the override-wins Fluency cron. We're connecting built parts, plus one
~2-hour execution hook (#14).

**Decision flag for Gstack:** what's the right auto-apply boundary for *client-
initiated* mid-month changes? The Loop's 15%/$500 was set for AI-proposed shifts;
a client moving their own money may warrant a different (wider?) bound — and a
Fair Housing re-check on any audience-affecting change.

---

## 7. How this slots into the existing roadmap

This redesign doesn't add a new goal — it sharpens two existing ones:

- **Goal 3 (Automate delivery, "capacity reclaimed not cost cut")** — this is the
  concrete mechanism. Every brief-update ticket and budget-change relay we delete
  is AM + specialist hours returned. Frame the win as **hours reclaimed**, same
  as the GTM/GA4 onboarding automation (~30 hrs/wk).
- **Goal 1 (Productize the agency)** — the Community Brief as system of record +
  the portal budget configurator is exactly the "named, priced, measured,
  delivered with minimal manual lift" product surface. The operating model is
  what makes the product deliverable at 700 properties.

**Suggested new roadmap rows (Goal 3):**

| Initiative | Phase | Owner | Dependency |
|---|---|---|---|
| HubSpot Service Hub as AM cockpit (pipelines + SLA automation + help desk) | Now → Next | Kyle / Sam | Service Hub tier decision; reuse gap-review SLA workflows |
| ClickUp-as-doer-queue (change-gated task creation only) | Now | Kyle / Ben / Tara | Property-as-trigger workflows live |
| Community Brief = system of record (remove "update-the-brief" tickets) | Now | Kyle / Claude Code | Brief in-place edit shipped; close FHA prompt gap |
| Mid-month budget-change automation (bounded auto-apply → Fluency) | Next | Kyle / Tara / Claude Code | Fluency execution hook (outstanding #14); auto-apply bound decision |

---

## 8. Phased rollout

- **Now (Q3 2026):** Stand up the property-as-trigger workflows generally. Make
  the Community Brief the system of record and stop opening tickets to edit it.
  Split ClickUp so doers only get change-gated tasks. Close the FHA prompt gap.
- **Next (Q4 2026):** HubSpot Service Hub as the full AM cockpit (pipelines, SLAs,
  help desk). Ship the mid-month budget-change automation end-to-end (the Fluency
  execution hook is the keystone). Pilot bounded auto-apply on ~10 properties.
- **Later (2027):** Widen auto-apply as forecast-accuracy and Fair Housing checks
  prove out. Fold in the Loop's auto/co/custom modes so clients self-steer budget
  within guardrails, with doers pulled in only on exceptions.

**Sequencing honesty (matches your roadmap's §"Sequencing reality"):** Q3 is
already overloaded (Summer Sweepstakes, CMP, portal). The cheapest, highest-
leverage Now item is **Community Brief = system of record + ClickUp change-gating**
— it's mostly workflow config on patterns we've shipped, and it immediately stops
the ticket churn. The Service Hub cockpit and budget automation are the heavier
Next-quarter lifts.

---

## 9. Decisions to bring to Gstack

1. **Service Hub tier + seats.** AM-cockpit needs SLA automation + help desk —
   likely Service Hub Pro. What's the seat count and cost, and is that the right
   spend vs. building more in the Flask portal?
2. **Do AMs fully leave ClickUp?** Clean split is the goal, but some AM↔doer
   coordination may still want a shared view. Hard wall, or a read-only window?
3. **Auto-apply boundary for client-initiated budget changes.** Reuse 15%/$500,
   or set a wider client-self-serve bound? What triggers a mandatory human +
   Fair Housing re-check?
4. **Brief as single source of truth — governance.** If editing the brief
   instantly propagates to live ad spend/copy, what's the approval gate? Today
   it's override-wins + attestation; is that enough once it's load-bearing?
5. **Sequencing under Q3 overload.** Which of the three reworks goes first given
   the team is down an Analytics Manager and Summer Sweepstakes eats Q3 capacity?
6. **Where does this break at 700 properties?** HubSpot Workflow limits, ClickUp
   API quota (already noted hitting Customer Match quota in your roadmap), Fluency
   sheet write volume — which ceiling do we hit first?

---

## 10. Appendix — what we're leveraging (this is not greenfield)

| Capability we reuse | Where it lives today |
|---|---|
| Property-as-trigger → HubSpot Workflow creates tasks (no tasks scope needed) | `docs/handoffs/HUBSPOT_GAP_REVIEW_WORKFLOW.md` |
| Per-stage SLA model | `ONBOARDING_SLA_PER_STAGE_HOURS` in `config.py` |
| Community Brief, editable in place, override-wins | `community_brief.py`, `/api/accounts/property/field` |
| Brief → live ad tags/copy propagation | daily Fluency tag-sync cron (respects overrides) |
| Channel→ClickUp routing | `CLICKUP_LISTS` in `config.py` |
| Brief attestation gate | `CLICKUP_BRIEF_ATTEST_FIELD` |
| Budget capture → deal + line items + quote | `/api/configurator-submit`, `deal_creator.py` |
| Bounded auto-apply thresholds (15% / $500) | `loop_autopilot.py` |
| Budget-shift audit + forecast | Loop Event Bus, `forecasting.py` |
| The one missing link | Fluency execution hook = outstanding work item #14 (~2 hrs) |
