# Is this what you would have done?

**One property. The next 90 days. Generated from the leasing calendar and the
unit availability feed, not from a ticket.**

Date generated: 2026-09-08 · Design doc: `docs/FLIGHT_PLAN_WORK_ITEMS.md`

---

## The property

**LYV Broadway** — Carrollton, TX (Dallas market) · lyvbroadway.com
RPM-managed since 2024-07-01

| | |
|---|---|
| Units | 390 |
| Occupancy | 91.79% (≈358 occupied) |
| Target occupancy | 95% — about **12 units short** |
| Available to rent | **44 units** (11.28%) |
| Of those, sitting 90+ days | **10 units** (one at 306 days) |
| Coming available in the next 90 days | **23 units**, 17 of them within 5 weeks |
| Concentrated in | one-bedrooms — A1 ×7, A3 ×6 (13 of 23) |
| SEO package | Standard, $800/mo |
| Paid media | $2,839/mo |
| Total monthly plan | $4,638/mo |

Occupancy and budget are live HubSpot values. Unit-level availability is the
AptIQ daily unit feed, deduped to one current record per unit — its available
count (44) matches HubSpot's availability figure exactly.

---

## The 90-day board

Sorted by **must start by** — the date after which the work can no longer land
on time. Not a due date. The date the clock runs out on starting.

### 1 · Diagnose the stale units — **overdue**

| | |
|---|---|
| **Must start by** | **now** — the threshold was crossed weeks ago |
| Why now | 10 of the 44 available units have sat 90+ days on market. One has been listed **306 days**. |
| Backwards from | nothing — this is already late, which is the point of showing it |
| Who | **Ready for your OK** — the system assembles the unit list, rents and days on market; you decide whether it's price, photos, or the unit |
| Signal behind it | AptIQ unit feed: `Unit Status = Available` and `Days On Market ≥ 90` |

### 2 · Flight paid onto one-bedroom inventory

| | |
|---|---|
| **Must start by** | **Mon 2026-09-14** — 6 days from now |
| Why now | **7 units come available the week of October 5** — the biggest wave in the quarter. Four are A1/A3 one-bedrooms. |
| Backwards from | 10/05 units hit the market → bid and budget change live 09/28 → approved 09/21 → drafted 09/14 |
| Who | **Ready for your OK** — the system drafts the shift; **you commit the money** |
| Signal behind it | AptIQ unit feed: future `Date Available` grouped by week and floorplan |

### 3 · File the renewal-season creative set

| | |
|---|---|
| **Must start by** | **Tue 2026-09-15** — 7 days from now |
| Why now | Renewal season opens **Thu 2026-10-01**. |
| Backwards from | 10/01 season opens → creative in hand 09/24 → ticket filed 09/15 (7 business day specialist SLA) |
| Who | **With the creative team** — files automatically, no approval needed |
| Signal behind it | Fixed annual calendar (renewal push Oct–Feb) |

### 4 · Draft the 2027 marketing budget recommendation

| | |
|---|---|
| **Must start by** | **Fri 2026-09-18** — 10 days from now |
| Why now | Budgets lock end of October. Current plan $4,638/mo against 91.8% occupancy and a 95% target. |
| Backwards from | 10/30 budget lock → client review 10/16 → AM review 10/02 → drafting starts 09/18 |
| Who | **Ready for your OK** — system drafts the comparison, **you set the number** |
| Signal behind it | Fixed annual calendar + the property's current spend |

### 5 · Second availability wave — two-bedrooms

| | |
|---|---|
| **Must start by** | **Mon 2026-10-05** — 4 weeks from now |
| Why now | 4 units come available the week of October 26, two of them B1 two-bedrooms. |
| Backwards from | 10/26 units hit the market → live 10/19 → approved 10/12 → drafted 10/05 |
| Who | **Ready for your OK** |
| Signal behind it | AptIQ unit feed |

### 6 · Kick off the Q4 creative refresh

| | |
|---|---|
| **Must start by** | **Tue 2026-10-06** — 4 weeks from now |
| Why now | Last quarterly creative cycle ended 2026-07-31; next is due **2026-10-29**. |
| Backwards from | 10/29 due → assets due 10/22 → ticket filed 10/06 |
| Who | **With the creative team** |
| Signal behind it | The `quarterly_creative_refresh_end_date` on the property record |

---

## What the board did *not* generate, and why

| Not generated | Why |
|---|---|
| Waves smaller than about 4 units | Otherwise the board fills with two-unit noise. **Is 4 the right floor?** |
| Anything from a renewal rate | The renewal figure on the property record is a flat 30% assumption applied to expirations, not counted renewals. Held back until it's tested. |
| Contract renewal decisions (SEO, social, review response, landing page) | Those service end dates are all stamped in the past — the newest is 2026-07-31. The rule is built and gated; it fires the day the dates are current. |
| Peak-season paid flight prep | Correctly outside the window — it starts 2027-02-02, working backwards from April 1. |
| Units on notice that aren't advertised yet | We see what's listed, not the rent roll. So this is **marketing exposure, not lease exposure**. |

---

## The questions

**1. Are these six the right six? Which one is noise?**
If you'd have done four of them, which two would you drop — and why?

**2. Item 1 says ten units have sat 90+ days, one for 306 days. Did you know?**
If yes, where did you see it? If no, is that the most useful thing on this page?

**3. Are the lead times right?**
Every one is a placeholder — 7 business days for a creative ticket, 15 for an
availability wave, 30 for a budget cycle. If a bid change really takes 3 days,
item 2 could start a week later. If a creative ticket really takes 12 days,
item 3 is already late. **What are the real numbers?**

**4. Item 2 says flight spend onto one-bedrooms because 7 units open Oct 5.**
Is that how you'd actually respond to a wave — or would you do something else
entirely, like adjust pricing or push the ILS?

**5. Does the routing feel right?**
Item 3 files a creative ticket without asking you. Item 6 does too. Everything
else waits for you. Is that the right split?

---

## The one that shows the limit

Run the same generator on **Skye Reserve** — 982 units, 76% occupied, 165 units
available, **108 of them sitting 90+ days**, 63 units coming open in 90 days.

Its biggest wave is **17 units the week of September 21**. On our lead times,
that item needed to start **August 31 — nine days ago**. It arrives already too
late to fully work.

That's real: the availability feed shows what's advertised, and things get
advertised roughly 5–6 weeks out, not 12. So either the chain for these items
has to be shorter than we assumed, or the first two weeks of every curve arrive
as "partly too late."

**Which is it? How much notice do you actually need to move spend onto a
floorplan?** That number sets the whole design.
