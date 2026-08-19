# The Atwood at Rivulon — pre-lead funnel analysis

**Requested by:** Dustin Lovingood (SVP Property Marketing), 2026-08-18
**Thread:** "Atwood One Bedroom Inventory/Pricing Further Analysis"
**Scope:** January 2026 → current, monthly, one property
**Status:** design + data-availability position. **No numbers have been pulled yet** —
see [Blocker](#blocker-read-this-first).

---

## The question underneath the thread

Two people are describing the same low lead volume and reaching opposite
conclusions, and both readings fit the evidence each has seen.

**Trisha (Operations):** the team is converting leads at a high rate. Loss
leaders and deeper concessions went out and traffic did not move. Therefore
pricing is not the constraint — traffic volume is. Cutting rate further buys
occupancy we are already earning.

**Dustin (Marketing):** pricing does not only affect whether a lead closes. It
affects whether a visitor ever *becomes* a lead. If we are $50–100 out of line,
that gap shows up as people leaving the site before they inquire — invisible in
every report Operations reads, because it happens upstream of the first lead
record.

Both are consistent with "leads are low". That is the entire problem: **the
report Operations is reading starts at the lead, and the disagreement is about
what happens before the lead.** Nothing either party has quoted so far can
separate the two.

One measurement separates them, and it is the reason widget 9 was added to the
requested eight:

> Split every website session into two groups — those that reached a floorplan
> or pricing page, and those that did not — and compare the lead rate of each.

- If sessions that **saw a price convert far worse** than those that did not,
  price is visibly filtering people out at the moment of disclosure. Dustin is
  right, and the size of the gap is the size of the prize.
- If sessions that saw a price convert **the same or better**, and few sessions
  reach pricing at all, then price is not the filter — reach and on-site
  pathing are. Trisha is right.
- If **most leads never saw a price at all**, both are half right in a way that
  changes the client conversation: our lead pool is systematically less
  price-qualified than Operations assumes. A high close rate on leads that never
  saw rent is not evidence that rent is competitive.

That third outcome is the one worth being ready for, because it explains the
whole thread. It would mean Operations' strong close rate and Marketing's weak
lead volume are the same fact viewed from two ends.

---

## Blocker: read this first

**We cannot currently pull session-level data for The Atwood at Rivulon, and it
is not certain we ever could without new work.** Establish this before promising
Dustin a date.

RPM has **no first-party GA4 connector**. `docs/SPEC.md` lists one as Phase 0
work; nothing in the repo implements it. The only GA4-shaped data any code here
can read is Hyly's `ga4_analytics_events` export copy, and that carries three
constraints:

| Constraint | Consequence for this request |
|---|---|
| Covers the **15-property Hyly beta only** | If Atwood is not in the beta, **there is no session data for it in any source this codebase can reach.** Check `hyly_property_id` on the HubSpot company first — this is a five-minute check that determines whether the rest is possible. |
| A **frozen snapshot** taken 2026-08-11, never refreshed (ADR 0022) | Data ends 2026-08-10. "Through current" is not available; the honest end date is 10 August. |
| **Not ingested** — 10.4 GB, no rollup exists | Widgets 1, 2, 4, 5, 9 need an ingest job written before they can run. This is real work, not a query. |

Two further items are blocked independently of Hyly:

- **Google Ads is not connected.** `webhook-server/google_ads_islost.py` has the
  GAQL and parsing built and unit-tested, but `_run_gaql` raises
  `GoogleAdsNotConfigured` — the `google-ads` library is not installed and no
  OAuth credentials exist. Widgets 7 and 8 stop here.
- **Average position no longer exists.** Google retired the metric on
  2019-09-30. It is not gated behind credentials; there is nothing to retrieve.
  `search_top_impression_share` and `search_absolute_top_impression_share` are
  the modern equivalents, offered as an explicit substitution to accept or
  decline rather than swapped in quietly.

---

## Widget-by-widget availability

Ordered as requested. "Conditional" means the query is written and will run once
the ingest lands **and** Atwood proves to be in the Hyly beta.

| # | Widget | Status | What stands in the way |
|---|---|---|---|
| 1 | Sessions by month, trended, prior year | **Conditional** | Needs GA4 ingest. Prior-year (2025) depends on export history — likely absent; will render as `unavailable`, never as zero. |
| 2 | Session → lead conversion rate | **Conditional** | Same. CRM lead count shown *beside* the GA4 figure, never divided into it — see note below. |
| 3 | Phoenix lease-up cohort comparison | **Unavailable** | Session data exists only for the 15 beta properties. A Phoenix-metro lease-up cohort must be drawn from that intersection; n is likely 0–3. The code reports n and warns explicitly when n < 5 rather than presenting a two-property average as a benchmark. |
| 4 | Sessions + conversion by GA4 default channel group | **Conditional** | `default_channel` column exists. Unassigned / (not set) volume is flagged rather than folded into Other. |
| 5 | 1-bedroom floorplan + availability pageviews | **Conditional** | Also needs the property's floorplan/availability URL patterns. These are site-specific and are **not** guessed — the widget reports unavailable without them. |
| 6 | Pricing interaction, form start, form abandon | **Expected unavailable** | Most multifamily templates fire `page_view` and one `generate_lead` and nothing else. The probe enumerates the events the site actually fires. **Form abandonment is not derivable under any current configuration** — it needs a `form_start` paired with a submit. Bounce rate and exit rate are *not* substituted for it. |
| 7 | Impression share + average position, 1-BR terms | **Unavailable (two separate causes)** | Impression share: connection only. Average position: metric retired 2019. Also: "submarket" is not a Google Ads dimension — it has to be expressed as the campaign's geo targets, which is itself the measurement the radius lever needs. |
| 8 | Paid search cost per lead | **Unavailable** | Needs Google Ads cost *and* a paid-search-scoped lead count. Neither side is connected. NinjaCat carried this historically and sunsets February 2026 (ADR 0016), so a backfill from it is time-boxed. Fluency holds spend and could supply the cost side sooner, at channel rather than keyword grain. |
| 9 | **Price exposure segmentation** (added) | **Conditional** | Needs only GA4 + URL patterns — no CRM join, no Google Ads. **This is the first thing that should ship**, because it is the only widget that settles the disagreement. |

### On the two lead numbers

GA4 leads and Hyly/CRM leads are counted side by side and never reconciled into
one figure. If they disagree materially, that gap is a finding, not an error: it
means inquiry paths exist that the website does not tag — call tracking, chat,
ILS handoff — and every GA4 conversion rate is an undercount by that amount.
Picking one silently would hide it.

---

## Articulating the levers

This is the part of the meeting that needs language, not just data. The useful
move is to stop treating "more traffic" as one dial.

**There are two ceilings, and they fail differently.**

1. **The demand ceiling** — how many people are searching for what we sell,
   inside the radius and term set we currently buy. This is *finite and
   measurable*. Once we hold 100% impression share on our terms in our radius,
   another dollar buys nothing. That is the honest answer to "where do we level
   out": not a forecast, an arithmetic limit.

2. **The capture rate** — of the demand we already reach, what fraction raises
   its hand. Price, offer, and the website decide this one. It has no ceiling in
   the same sense; it is a rate we either earn or lose.

"We spent more and got nothing" is not one story. It is exactly one of three,
and they are distinguishable from data we can hold:

| Shape | Reading | Who owns it |
|---|---|---|
| Spend ↑, sessions ↑, leads flat | We bought the traffic. It did not convert. **Capture problem** — price, offer, or site. | Marketing surfaces it; pricing decision is joint |
| Spend ↑, sessions flat | We hit the demand ceiling. Incremental dollars re-bought impressions we already had. **Reach problem** — radius, terms, channels. | Marketing, and it argues *for* widening |
| Spend ↑, sessions ↓ | Media execution regressed. | Marketing owns it outright |

Dominique's instinct in the call — "did website traffic actually increase when
we spent more?" — is precisely this test, and it is worth running as a single
three-line chart (spend, sessions, leads by month) before any of the eight
widgets. It reframes the recurring argument from opinion to shape-reading.

### The levers, each with its measurement

| Lever | What it changes | How we measure the headroom | How we measure the result |
|---|---|---|---|
| **Budget at current targeting** | Buys more of demand we already reach | `search_budget_lost_impression_share` — the literal count of impressions we forfeited to budget | Sessions per incremental dollar; watch for the flattening |
| **Bid / quality** | Wins auctions we currently lose on rank | `search_rank_lost_impression_share` — cannot be fixed by budget | Top / absolute-top impression share |
| **Geographic radius** | Enlarges the demand pool itself | Search volume outside current geo targets vs. inside | Sessions and lead rate **by distance band** — this is how we price the trade-off honestly |
| **Term set** | Captures intent we do not bid on | Search-term report coverage: are we on 1-BR modifiers, price modifiers, comp conquesting? | Sessions and CPL by term group |
| **Channel mix** | Moves money to better-converting sources | Widget 4 — conversion rate by channel | Blended CPL |
| **Price / concession display** | Changes capture, not reach | Widget 9 — conversion of price-exposed vs. not | Same segment's conversion rate after a price change |
| **Site conversion path** | Changes capture | Widgets 5 + 6 — how many reach pricing, how many stall | Step-through rate once instrumented |

**The radius lever needs a companion number, or it will be sold wrong.** Casting
a wider net is not free: prospects from further out typically convert to lease at
a lower rate. Before recommending it, pull historical lead-to-lease by distance
band from the CRM. Then the recommendation becomes "widening the radius adds ~X
leads at ~Y% lower close rate" instead of "let's cast a wider net" — which is the
version Operations can actually act on.

### The concession point, sharpened

Trisha's strongest evidence is that concessions increased and traffic did not
respond. That is a real signal and should not be waved away — but it only holds
if the concessions were *visible to a searcher who never clicked*. Before
accepting it as evidence, check three things with dates attached:

1. Were the concession changes reflected in **paid ad copy** — and when?
2. Were they pushed to **ILS listings** (Apartments.com, Zillow) — and when?
3. Were they above the fold on the **website hero and the 1-BR floorplan page**?

If a concession lived only in the pricing matrix, it could not have moved
click-through, and "we discounted and traffic didn't move" tests nothing. If it
*was* visible on all three surfaces on a known date and traffic still did not
move, that is strong evidence for the demand-ceiling reading and Marketing
should say so plainly. Either way the answer is worth more than the argument.

---

## Recommended sequence

Ordered by what each step unblocks, not by widget number.

1. **Confirm Atwood has a `hyly_property_id`.** Five minutes. If it does not,
   stop and say so — the honest answer to Dustin becomes "we have no
   session-level visibility for this asset, here is what it costs to get it,"
   which is a better answer than a delayed one.
2. **Run the spend / sessions / leads three-line chart.** Answers Dominique's
   question and picks which of the three shapes we are in. Spend is available
   from Fluency today.
3. **Ship widget 9 (price exposure).** Needs only the GA4 ingest and the URL
   patterns. It is the only widget that settles the disagreement.
4. **Then widgets 1, 2, 4, 5** off the same ingest.
5. **Instrument the missing events** — `form_start`, floorplan pricing
   interaction, `form_submit`. Roughly a sprint of tagging, and it is the
   highest-leverage gap in the stack: without it we can see that people reached
   pricing and can never see what they did next.
6. **Connect Google Ads** for widgets 7 and 8 and for the impression-share
   headroom number the whole lever conversation depends on.

### What this analysis will not settle

Worth saying out loud before the call, because it will be asked:

Seeing a price and not inquiring is **not proof of price rejection**. A session
that reached the floorplan page and left cannot be distinguished from one that
was interrupted. Widget 9 bounds the question — it tells us how large the
price-exposed drop-off could possibly be — but only a **deliberate price or
offer test**, watching the same segment's conversion rate before and after,
settles it. If the finding is directionally strong, propose that test rather
than claiming the observational data proved the point.

---

## Draft reply to Dustin

> Dustin — good question, and it is the right place to look. Before I put
> numbers in front of you I want to be straight about what we can and cannot
> see today.
>
> **What I can answer.** There is a specific test that separates your read from
> Trisha's, and neither of the reports we have been circulating can do it. We
> split every website session into two groups — the people who reached a
> floorplan or pricing page, and the people who never did — and compare how
> often each group inquires. If the people who saw a price convert materially
> worse, price is filtering people out before they ever become a lead, exactly
> as you suspect, and we can size it. If they convert the same or better, the
> constraint is reach, not price. And if it turns out most of our leads never
> saw a price at all, that reframes the whole conversation: a strong close rate
> on leads that never saw rent is not evidence that rent is competitive.
>
> **What is in the way.** We do not have our own analytics connection for this
> asset yet — the only session-level data our platform can reach today comes
> through a 15-property vendor beta, and it is a snapshot that ends 10 August
> rather than a live feed. I am confirming this week whether Atwood is inside
> that set. Paid search impression share and cost per lead need a Google Ads
> connection we have built but not yet credentialed. And I will flag one thing
> now: form starts and abandons are not tracked on the site at all, so I will
> report that as unavailable rather than substituting bounce rate for it — a
> wrong number here is worse than a missing one.
>
> **On the levers**, the framing I would bring to the client is that there are
> two different ceilings. There is a hard limit on how many people are searching
> in the radius and the terms we buy — that one is finite and we can measure
> exactly how much of it we already own. And there is the rate at which the
> demand we do reach raises its hand, which is where price, offer and the
> website live. "We spent more and got nothing" resolves into one of three
> shapes: we bought traffic and it did not convert, we bought traffic we already
> had, or the media regressed. Those are distinguishable, and I would rather
> show Trisha which one we are in than argue about it.
>
> One thing that would sharpen this a lot: can Operations confirm the dates the
> deeper concessions went live in **ad copy and on the ILS listings**, not just
> in the pricing matrix? If they only ever existed in the matrix, they could not
> have moved click-through, and the "we discounted and traffic didn't respond"
> data point does not test what we think it tests. If they *were* live on all
> those surfaces and traffic still did not move, that is strong support for the
> reach argument and I will say so.
>
> Happy to workshop on a call once I have the availability answer.

---

## Implementation

`skills/pre_lead_funnel/` — property-agnostic, runnable once credentials exist.

```bash
python3 -m skills.pre_lead_funnel \
  --property "The Atwood at Rivulon" \
  --start 2026-01-01 --end 2026-08-10 \
  --page-pattern "%/floorplans%" \
  --page-pattern "%/availability%" \
  --page-pattern "%1-bedroom%"
```

Design rules enforced in code rather than left to the reader:

- Every rate carries its raw numerator and denominator (`stats.Rate`).
- Nothing is smoothed, modelled or interpolated. There is no fill logic anywhere.
- A zero denominator renders as **undefined**, never as 0% — a month with no
  sessions did not fail to convert.
- Unstable months are flagged inline: fewer than 30 conversions, or a 95% Wilson
  interval wider than ±35% of the estimate. The flag constrains how the number
  may be read; it never adjusts the number.
- Unavailable metrics render as the word "unavailable" plus the named blocker.
  No proxy is ever substituted, and adjacent metrics that *could* stand in are
  offered separately as an explicit choice.
- The CLI **refuses to render** when the warehouse is unreachable. This is
  deliberate: ADR 0022 documents a live incident where swallowed errors made a
  missing vendor table read as "this property has no leads". The same failure
  inside a client-facing pricing analysis is not acceptable.

Tests: `tests/test_pre_lead_funnel.py` (15 cases, no BigQuery required).
