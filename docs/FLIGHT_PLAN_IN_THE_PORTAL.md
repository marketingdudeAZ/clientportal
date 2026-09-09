# The flight plan in the portal — states, surfaces, and what gets automated

**Status:** Design / proposal
**Date:** 2026-09-08
**Branch:** `docs/flight-plan-proposal`
**Reads from:** `docs/FLIGHT_PLAN_WORK_ITEMS.md` (the work-item spec),
`docs/THREE_SURFACES.md` (the audience split)

Answering six questions: does this become a kanban board; what automates and
what needs a name on it; how do we cut the noise; how do we surface **what to
talk about, what we're changing and why, and which channels we're turning on**;
where the standardized property-marketing report fits; and how to add chat with
data without losing what makes Ask trustworthy.

*Caveat: this is a read of `hubspot-cms/templates/client-portal.html` and the
partials, not of the running UI.*

---

## 1. Yes to your four states. No to columns.

Your four buckets — needs work soon, needs work now, in motion, done — are
exactly right. They should not be kanban columns. Four reasons:

1. **The AM can't move most of these cards.** Specialists execute in ClickUp;
   agents execute themselves. A board whose cards you mostly can't drag is a
   dashboard wearing a board's clothes — which is precisely what
   `webhook-server/attention.py:21` was written to stop: *"repeating them turns
   an action inbox into a second dashboard."*
2. **Columns erase the date, and the date is the whole product.** In a "Needs
   work now" column, an item that must start in two days and one that must start
   in nine look identical. `must_start_by` is the only thing the flight plan
   adds over a to-do list; a layout that hides it throws away the mechanism.
3. **Kanban assumes bounded WIP.** 631 properties with 5+ units coming open in
   90 days, most with several waves. There is no column width that survives it.
4. **We already have the kanban, and it's ClickUp.** Specialist work has real
   statuses there. Rebuilding those columns in the portal creates the fourth
   place to look, which is the exact problem `attention.py` exists to solve.

### What instead: an agenda with a state filter

One list. Sorted by `must_start_by`. Grouped by time. States are a **filter row**,
not columns — the pattern the portal already uses at
`client-portal.html:2213` (All / Pending / Approved / Dismissed).

```
  NOW                                       [ All ][ Mine ][ In motion ][ Done ]

  ── LATE ─────────────────────────────────────────────────────────────────
  ● LYV Broadway        Diagnose 10 stale units — one listed 306 days
    Carrollton, TX      10 of 44 available units sitting 90+ days
                        ⟶ paid search · listing         Ready for your OK   ›

  ── THIS WEEK ────────────────────────────────────────────────────────────
  ● LYV Broadway        Flight paid onto 1-bed inventory      start by Mon 9/14
    Carrollton, TX      7 units open the week of 10/05 — 4 are A1/A3
                        ⟶ paid search · pmax · ILS      Ready for your OK   ›

  ○ LYV Broadway        Renewal-season creative set           start by Tue 9/15
    Carrollton, TX      Renewal season opens 10/01
                        ⟶ creative · email               Filing automatically

  ── NEXT WEEK ────────────────────────────────────────────────────────────
  ○ LYV Broadway        2027 budget recommendation            start by Fri 9/18
                        Budgets lock 10/30 · $4,638/mo today
                        ⟶ all channels                   Ready for your OK   ›

  ── LATER (4) ───────────────────────────────────────── show ▾ ────────────
```

Four things every row carries, and nothing else: **what**, **why with a
number**, **which channels**, **who decides**. That is your three questions plus
the owner.

State maps onto this without columns:

| Your bucket | The rule |
|---|---|
| Needs work **now** | `must_start_by` within 5 business days, or already past (renders as LATE) |
| Needs work **soon** | `surfaces_on` reached, `must_start_by` more than 5 business days out |
| **In motion** | status `accepted` / `filed` / `in_progress` — a filter, and rows show their ClickUp status inline |
| **Done** | status `done`, default to the current quarter |

"In motion" and "Done" are deliberately behind a filter rather than always
visible. Work that is already moving needs no daily attention, and a Done column
that grows forever is the thing that makes boards unreadable. Done belongs in
the client conversation (§3), not the daily queue.

---

## 2. What automates, what needs a name on it

The line that decides it, and it is one line:

> **Reversible and invisible to the client → automate. Everything else needs a
> human name attached before it happens.**

Note this is a different axis from *who does the work*. That's the `decision` ×
`executor` split in the work-item spec: a ClickUp ticket filed with no human in
the loop is fully automatic on the decision axis even though a person does the
work. `creative_transition.py:107` already runs exactly that way in production.

### Automate now (no approval, but always logged)

| Action | Why it's safe | Existing code |
|---|---|---|
| Refresh the availability curve daily and re-date every open item | Read-only, and stale dates are worse than none | new — AptIQ unit ingest |
| File the contracted specialist ticket (renewal creative, quarterly refresh) | Contracted, already paid for, reversible; filing decides nothing | `creative_transition.py` — in prod, with dedup stamp + flood guard |
| Assemble the stale-unit diagnosis pack (units, rents, DOM, photos) | Produces a document, changes nothing | new, reads the unit feed |
| Close an item when its ClickUp task closes | Mirroring a fact | `clickup_client` + `loop_ticket_events.py` |
| Expire an item whose anchor passed without action | Housekeeping; the slip is recorded either way | new |
| Post the weekly digest to Slack | Read-only | `slack_notifier.py`, `digest.py` |

### Never automate, at any approval rate

- Any **spend change**. Money moves on a signature. The existing design already
  enforces this — `launch_policy.py` sets a launch date and the *existing* 10pm
  HubSpot automation moves budget; new code never does. Keep that.
- Any **client-visible publish** — a page, a post, a GBP update, an ad.
- Any **HubSpot property write** that a human would consider a decision.
- Anything a **resident** sees.

### Needs approval (the default for everything else)

Agent drafts, human commits: availability-wave budget shifts, budget
recommendations, service renew/change/stop, concession changes, anything with a
dollar sign or a client's name on it.

**How a thing earns automation.** Not by argument — by the numbers already in
the schema: ≥20 completed items on that trigger, ≥90% approved with
`was_edited = false`, zero reversals in 30 days, and the write is reversible.
Any reversal demotes it immediately. `loop_autopilot.py` already implements this
shape (bounded actions, 7-day warm-up); note `loop_mode` is populated on **0 of
887** properties today, so the Auto lane is currently off by accident. Leave it
off on purpose until the numbers exist.

---

## 3. Surfacing your three things

This is the strongest part of the question, and it maps to one surface: the
**Property Room** from `THREE_SURFACES.md` — the screen an AM opens *with* a
client. Three sections, in this order.

### "Talk about" — what needs a decision

Only items whose `decision` is `approved` and whose `must_start_by` falls inside
the next 30 days. Each one framed as the question, not the task:

> **Seven one-bedrooms open the week of Oct 5.** That's the biggest wave this
> quarter and A1/A3 are already our slowest-moving plans. Do we shift $600 of
> paid search onto one-beds for three weeks, or hold and let pricing carry it?

Nothing else belongs here. Not the health score, not the ranking chart — the
dashboard already shows those, and repeating them is the failure mode.

### "Changing" — what we're doing, when, and why

Committed items with dates. This is the change log and it is the artifact that
defends the price:

| What | When | Why | Status |
|---|---|---|---|
| Renewal creative set | Live 10/01 | Renewal season opens; 19 leases in the 120-day window | With creative, filed 9/15 |
| 1-bed paid flight | Live 9/28 | 7 units open 10/05, 4 are A1/A3 | Awaiting your OK |
| Q4 creative refresh | Live 10/29 | Quarterly cycle | Files 10/06 |

Every row has a `why` with a number in it. A quarter that ends with fourteen of
these is the renewal conversation.

### "Activating" — which channels, against which wave

This is the piece with no home today, and it needs **one change to the
work-item schema**: a `channels` array on every item.

```
work_items.channels   ARRAY<STRING>   -- paid_search | paid_social | pmax |
                                      -- meta | display | ctv | retargeting |
                                      -- seo | social_content | reputation |
                                      -- email | ils | creative
```

Vocabulary comes straight from `plan_stages.py:39-52`, which already models
stages and their channels. That one field turns the board into a media plan: a
work item without a channel is neither actionable nor billable.

The section renders as the availability curve with channel activation drawn
against it — what is on, what is being turned up, and against which units:

```
  UNITS COMING AVAILABLE — next 12 weeks                    LYV Broadway

   7 ┤                    ██                                 ██ = 1-bed (A1/A3)
   5 ┤                    ██                                 ▓▓ = 2-bed (B1)
   3 ┤  ██   ██   ██  ██  ██        ██                       ░░ = other
   1 ┤  ██   ░░   ██  ░░  ██  ░░ ░░ ▓▓        ░░
     └──W37──W38──W39─W40─W41─W42─W43─W44──────W49────────────
        ╰──────── paid search +$600, 1-bed focus ────────╯
                              ╰─ renewal creative live ─╯
```

That single picture answers all three of your questions at once: what we're
talking about (the W41 spike), what we're changing and when (the bar underneath
it), and which channel we're activating (its label).

---

## 4. The noise — where it actually comes from

Measured, not impressionistic:

- **28 nav items** in the property sidebar (`grep -c "onclick=\"nav("`).
- **13,150 lines** in one template.
- Two nav items whose own tooltips admit they are duplicates:
  `title="Approvals + AM questions (now also in Plan & Spend)"` and
  `title="Budget tiers (now part of Plan & Spend)"`.
- "Spend Tracker" renders in four places; "Paid Social" in five.

The portal is not busy because it shows too much data. It is busy because **the
same data has several front doors**, which is the `THREE_SURFACES.md` diagnosis
confirmed in the markup.

### Cut the nav from 28 to 6

| Keep | Absorbs |
|---|---|
| **Now** | recommendations, budget approvals, the tickets badge — the agenda from §1 |
| **Property** | overview, brief, assets, activity — the record |
| **Performance** | performance, ask, forecast, SEO insights — the numbers |
| **Plan & Spend** | services, budget, paid, recommendations — the money (it already absorbed two of these; finish the job) |
| **Work** | tickets, onboarding — mirrored ClickUp status, read-only |
| **Reports** | reports |

The two tooltips are the cheapest possible starting point: those nav items are
already redundant *by their own admission*. Deleting them is a day's work and
costs nothing.

### Six data-layer rules that matter more than the nav

Noise is mostly a generator problem. Fix it before the UI.

1. **The `surfaces_on` gate.** Nothing appears before `must_start_by − 14 days`.
   This alone hides most of the backlog.
2. **One item per property per week**, ranked by units in the wave. A property
   with four waves gets one card carrying the curve.
3. **A wave floor** — ignore waves under 4 units or 1% of the property.
   Otherwise the board fills with two-unit noise. *This number is a guess and is
   question 1 for the AM.*
4. **Sticky dismissals.** Dismiss a trigger for a property and it stays gone for
   the cycle. Dismissal reasons are the trigger-tuning dataset.
5. **Never repeat the dashboard.** `attention.py`'s rule, applied to the product
   rather than one module.
6. **Default scope is "my properties."** Not 700.

---

## 5. The standardized report is the board's export, not a separate thing

The standardization work already exists: **Red Light Report v2**
(`webhook-server/redlight_v2.py`, `redlight_v2_narrative.py`,
`redlight_v2_pdf.py`, `redlight_v2_run.py`). Five sections:

1. Where we are — AptIQ snapshot + cost per lease
2. Where you were last month
3. Where you were last year
4. **Where you are going** — Claude trajectory narrative
5. **How you got here** — Claude causation narrative

Its comparison metrics (`redlight_v2.py:34-42`) are occupancy, ATR,
leases_last_30, leased %, **exposure**, monthly service cost, **cost per lease**
— the same AptIQ spine the flight plan runs on. These are not two systems. They
are one object at two time horizons.

**Sections 4 and 5 are the report's weakest, and the board fixes both.** Today
they are a language model writing prose about the future and the past. Replace
them with facts:

| Section | Today | Should be |
|---|---|---|
| 4 · Where you are going | Claude trajectory narrative | **The next 90 days of work items** — dated, with the unit waves behind them and the channels being activated |
| 5 · How you got here | Claude causation narrative | **Last period's completion record** — what we changed, when, why, and what moved after |

That turns the report into the price defence the proposal argues for
(`PROPERTY_FLIGHT_PLAN.md:107-116`): a quarter ends with fourteen completed
items with dates and outcomes, not three PDFs of prose. And it removes two
model-written sections, which is a reliability gain, not just a content one.

**Practically:** the report generator reads `work_items` for the property —
`status = done` in the trailing period for section 5, open items ordered by
`must_start_by` for section 4 — and renders them into the existing PDF
(`redlight_v2_pdf.py` already has the section and table styles). No new
document, no new template. One report, one board, one set of numbers.

It also settles which surface owns what. The report is the **client-facing,
periodic, backward-looking** artifact. The board is the **internal, live,
forward-looking** one. `PROPERTY_FLIGHT_PLAN.md:130-131` already made that call:
keep the working board internal, show clients committed and completed items.
The report *is* that client view.

---

## 6. Chat with data — yes, but keep the thing that makes Ask trustworthy

The Ask surface exists (`webhook-server/skills/ask_engine.py`,
`question_registry.py`, `ask_context.py`) with 5 preset questions over 10 data
pulls. Free text was excluded **on purpose** —
`question_registry.py:4-7`:

> *"The Ask surface is preset-question only. There is no free-text chat in v1: a
> client picks from a fixed list and gets one defensible narrative with receipts.
> That was a product decision, and it is enforced structurally."*

You're asking to change that decision, which is fine — but the reason it was
made is the thing worth preserving. From `ask_engine.py:16-20`:

> *"EVIDENCE IS COMPUTED, NOT WRITTEN. Every number in the answer is formatted
> in `ask_context` before the model sees it, and a finding that cites no evidence
> index is DROPPED. The model chooses which receipts to quote; it never produces
> one. A claim with no numerator and denominator cannot survive."*

**That property is why anyone can trust an Ask answer in front of a client. Free
text must not cost it.**

### The design that gets both: the model routes, it never calculates

Put a router in front of the existing engine rather than replacing it.

```
  free text  →  ROUTER (LLM)  →  {pulls: [...], filters: {...}, focus: ...}
                                          ↓
                              existing ask_context PULLS  ← the numbers happen here
                                          ↓
                              existing narrate + _coerce_findings
                                          ↓
                              answer with receipts, or "we can't answer that"
```

The model's only job is choosing **which pulls to run and how to filter them** —
a constrained output, validated against `PULLS` exactly the way
`question_registry.validate()` already validates preset questions at import. It
never sees a raw number it didn't get from a pull, and `_coerce_findings` still
drops any claim without an evidence index. A question that maps to no pull gets
an honest *"we don't have data for that"* — which is a better answer than a
confident wrong one and is already the house style (`data_quality.py`,
`skills/ask_context` naming the dark input).

Three things this needs:

1. **Widen `PULLS` to the unit feed.** `availability_curve` (units by week by
   floorplan), `stale_inventory` (DOM ≥ 90), `floorplan_mix`, `concessions`, and
   `work_items` (what's on the board and what we completed). Right now Ask can't
   answer "which floorplan is hurting us" because the data isn't in its context —
   that is the biggest single upgrade available to it and it's the same feed §3
   already requires.
2. **A refusal path with teeth.** "What should I charge for a 2-bed?" has no
   pull. Say so.
3. **Scope and logging.** Every answer already scopes through the Property
   Resolver (R1-safe, `ask_engine.py:26-28`); free text must too, and every
   question asked should land in `loop_events` as a product event — which is the
   `THREE_SURFACES.md:82-87` gap. *What people ask is the roadmap.*

### Preset questions do not go away

They become the starting rail: the five buttons stay, free text sits underneath,
and a free-text question that gets asked repeatedly and answers well is promoted
into `QUESTIONS` — the same graduation pattern as the work-item lanes. Measure,
then promote.

### One thing I need from you

There is **no inbound Slack handler in this repo** — `slack_notifier.py` is
outbound only (it posts loop events, `loop_writer.py:303-308`). So the Slack bot
you're describing lives somewhere else. **Where?** If it has a question-routing
or intent layer worth reusing, that is likely most of the router above, and I'd
rather port it than rebuild it.

---

## 7. What I'd change, in order

1. **Delete the two self-admitted duplicate nav items.** A day. Zero risk. It
   also proves the consolidation is real rather than another doc.
2. **Widen `PULLS` to the AptIQ unit feed.** One ingest, and it unlocks three
   things at once: the availability triggers (§3), the report's real content
   (§5), and the questions Ask currently can't answer (§6). Everything else here
   waits on it, so it goes first among the builds.
3. **Build "Now" as the agenda in §1** — one list, time-grouped, state as a
   filter. Do not add data to it; the editorial work is deciding what it does
   *not* show.
4. **Add `channels` to the work item** and render the "Activating" view. This is
   the piece with no home today and the one that makes the board legible as a
   media plan rather than a task list.
5. **Point Red Light v2 sections 4 and 5 at `work_items`.** Small change,
   removes two model-written sections, and turns the report into the quarter's
   receipts.
6. **Ship "Talk about" / "Changing" / "Activating" as the Property Room**, and
   output it to Slack before building any UI — `THREE_SURFACES.md:120` already
   argues this. Learn whether AMs use it before spending template work on it.
7. **Add the free-text router in front of Ask**, logging every question to
   `loop_events`.
8. **Then collapse the nav to six.**

Nothing here is a rebuild. Steps 1, 5, 7 and 8 are edits to code that already
exists; step 2 is the only genuinely new plumbing. The heaviest lift is
editorial: deciding what each audience does *not* see.

---

## The one thing I'd want tested first

The agenda's whole premise is that `must_start_by` is worth sorting on. That
holds only if the lead times are real, and every one of them is currently a
placeholder. If a bid change takes 3 days rather than 15 business days, half the
availability items surface a fortnight too early and the queue is noise again —
just better-organised noise.

Ask the AM the closing question on `FLIGHT_PLAN_AM_TEST.md`: **how much notice
do you actually need to move spend onto a floorplan?** Everything in this
document is sized off that number.
