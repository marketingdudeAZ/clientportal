# Blueprint Redesign — Problem Definition

A diagnosis of the current new-build process, derived from 8 recent
ClickUp builds and their corresponding Google Ads exports. The point
of this doc is to define the problem precisely **before** we touch the
Blueprint design, so the Blueprint solves the right thing.

**Project doc set** (all in `docs/handoffs/`):
- `BLUEPRINT_REDESIGN_HANDOFF.md` — Claude Code handoff and phase plan
- `BLUEPRINT_REDESIGN_PROBLEM_DEFINITION.md` — this doc
- `BLUEPRINT_REDESIGN_SPEC_DRAFT_V0.md` — early Blueprint draft, deprecated

**Sample**: 8 properties, 5 markets, 5 clients, 4 account managers,
2 property statuses (stabilized + lease-up), 6 with structured intakes,
2 with mostly empty intakes.

---

## TL;DR

The earlier framing of this as "AI slop in the briefs" was wrong.
**There is no AI generating ad copy from briefs.** The current build
is a fixed boilerplate template stamped onto every property, with
~5 variables swapped in (property name, city, state, floor plan label,
pet-friendly bool). Everything else in the intake is decorative.

This is actually good news for the Blueprint redesign. We're not
fighting an LLM that's hallucinating "luxury" — we're replacing a
fixed template with a parameterized one. The work is data modeling
and template authoring, not ML.

The real problem isn't the campaigns Tyler builds. He's executing the
template he was given, faithfully, every time. The problem is that
the template only has 5 levers when the briefs have 30+ structured
inputs.

---

## The 8 properties

| # | Property | Market | Client | AM | Status | Brief richness |
|---|---|---|---|---|---|---|
| 1 | Everton at Bellmar | Dallas, TX | Endurance | Logan | Stabilized | Structured bullets |
| 2 | Riverfalls at Bellmar | Dallas, TX | Endurance | Logan | Stabilized | Structured bullets |
| 3 | The Brixton | Dallas, TX | Endurance | Logan | Stabilized | Structured bullets |
| 4 | Waterford at Bellmar | Dallas, TX | Endurance | Logan | Stabilized | Structured bullets |
| 5 | AXIS Crossroads | Cary, NC | Virtus REC | Juliana | Stabilized | Free text |
| 6 | BLVD 2600 | Apopka, FL | RPMI | Dustin | Stabilized | Mostly `n.a` |
| 7 | Territory at Spring Stuebner | Spring, TX | (TBD) | Izabelle | Lease-up | Buried in comments |
| 8 | Ascend at the Parkway | Katy, TX | D.R. Horton | Izabelle | Lease-up | Rich + comments + 5 attachments |

---

## What the system reliably handles today

These are the 5 levers that work consistently across all 8 builds:

1. **Property name** — swapped into Brand AG keywords and headlines
2. **City + state** — swapped into Location AG and "Now Leasing in {city}, {state}" headlines
3. **Final URL** — plumbed end to end
4. **Phone number** — call extension at campaign level
5. **Pet-friendly bool** — when set, pet headlines appear (sometimes)

That's it. Everything else in the intake form is, in practice, ignored
by the build process.

---

## The 9 dimensions where the system fails

### 1. Voice / positioning is hardcoded "luxury"

**Pattern**: Every property gets the same headline pool with "Luxury,"
"Premier," "Elevate," and "Top-Tier" defaults — regardless of brief.

**Evidence**:

| Property | Brief said | Luxury headlines shipped |
|---|---|---|
| Brixton | "Avoid luxury overstatement" | 18 |
| Waterford | "Updated interiors WITHOUT luxury pricing" | 19 |
| Everton | "Clean, dependable, affordable" | 18 |
| Riverfalls | "Lifestyle + value balance, not just cheap" | (similar) |
| AXIS | "Aspirational + polished + upscale" | 22 (appropriate) |
| Territory | "New + luxury apartments" | 8 (appropriate) |
| BLVD 2600 | (empty) | 22 (defaulted) |
| Ascend | "Inviting and elevated, ease + comfort" | 9 + first "Modern" hits |

The system has *one* voice. It happens to match for AXIS, Territory,
Ascend (where the brief wanted upscale anyway). It actively contradicts
the brief at Brixton, Waterford, Everton.

**The Ascend exception**: Ascend was the only property that produced
"Modern Apartments and Casitas" instead of "Luxury Apartments." So
there's *some* voice swap mechanism in there — it's just undocumented
and inconsistent.

### 2. Property-specific differentiators don't survive the template

**Pattern**: Every property gives us specific selling points. None
of them make it into ad copy.

**Evidence — what survived vs. what was lost**:

| Property | Differentiators in brief | In ads |
|---|---|---|
| Brixton | gated, fenced yards, garages, controlled access, North Dallas vs Plano value | 0 / 5 |
| Waterford | renovated interiors, granite, stainless, walkable retail/dining, Preston Hollow, medical/hospital workers | 0 / 7 |
| Riverfalls | Peloton-equipped fitness, fireplaces, courtyard | 0 / 3 |
| AXIS | iApartment smart homes, 9' ceilings, in-unit W/D, granite/quartz, Virtu coffee bar, Luxer package room, walk-in closets, plank flooring | 0 / 8 |
| BLVD 2600 | saltwater pool/beach entry, lanai with fireplace, putting green, sand volleyball, walking trail, two playgrounds | 0 / 6 |
| Territory | pickleball, bocce ball, hot yoga, infrared sauna, cabanas, sun shelf | 0 / 6 |
| Ascend | pickleball, casitas, BTR-style, Katy schools, premier shopping, medical district proximity | 1 / 6 (casita only) |

**Total across 8 properties: 1 differentiator out of ~50+ landed in
ads.** And the one that landed (casita) was a property *type*, not an
amenity — it survived because the system had a pre-existing slot for
floor plan terms.

Pickleball appears in 2 different briefs. It is one of the hottest
amenity terms in current multifamily search. Zero pickleball headlines
shipped.

### 3. Concessions / promos are invisible

**Pattern**: When a brief specifies a promotional offer, the offer
does not appear in the campaign.

**Evidence**:

- **AXIS Crossroads** — Campaign Focus field said *"Waived application
  fee + $100 off administration fee."* Headlines mentioning concession:
  **0**. Descriptions mentioning concession: **0**. The property paid
  to advertise a concession that never appeared in any ad.

The other 7 properties didn't have concessions in their briefs, so
this only had one chance to demonstrate the failure — and it failed
cleanly.

### 4. Property status (lease-up / stabilized / rebrand) ignored

**Pattern**: Lease-up properties need fundamentally different ad copy
than stabilized ones — preleasing, grand opening, "be among the
first," months-free, preferred employer programs are standard. None
of this exists in the template.

**Evidence — both lease-ups in the sample**:

| Lease-up signal | Territory | Ascend |
|---|---|---|
| Brand new / newly built | 5 H (lucky brand-AG hit) | 0 |
| Now leasing | 6 H (generic) | 6 H (generic) |
| Preleasing / pre-lease | 0 | 0 |
| Now open / grand opening | 0 | 0 |
| Be among the first / first residents | 0 | 0 |
| Months free / move-in special | 0 | 0 |
| Preferred employer | 0 (explicit ask) | 0 |

Lease-up campaigns are typically the highest-budget builds in the
portfolio (Territory $4500/$1500, Ascend $4000/$1500/$800 social). They
are *also* the campaigns where the strategy gap costs the most per
month.

### 5. Local landmarks / employers / hotspots ignored

**Pattern**: Every intake form has a "Local Hotspots / Attractions"
field. These never make it into ad copy.

**Evidence — landmarks named in briefs**:

- Everton: NorthPark Center, Preston Hollow dining, Fair Oaks Park, Watercrest Park
- Riverfalls: White Rock Lake, NorthPark Center, SMU area
- Brixton: Addison Circle, Legacy West, Shops at Willow Bend, Arbor Hills, Dallas North Tollway
- Waterford: NorthPark Center, Preston Hollow retail, Royal Lane dining, Park Lane shops
- AXIS: Crossroads Plaza (called out as "co-marketing opportunity"), Paragon Theaters Fenton, NC State, Lake Johnson
- BLVD: Wekiwa Springs, Rock Springs, Lake Apopka, Wekiva Riverwalk, Downtown Apopka
- Territory: (collapsed in PDF)
- Ascend: Typhoon Texas, LaCenterra at Cinco Ranch, Katy Mills Mall, Mary Jo Peckham, Memorial Hermann Katy, Igloo Factory, Texas Children's Hospital West

**Total mentions of any of these landmarks in shipped ads, across all
8 properties: roughly 0** (NC State got 1 hit on AXIS as "Apartments
Near NC State").

### 6. Audience targeting ignored

**Pattern**: Briefs specify target audiences in detail. The campaigns
do not reflect them.

**Evidence — target audiences from briefs**:

| Property | Target audience | In campaign |
|---|---|---|
| Everton | Budget-conscious renters, service industry, healthcare | None |
| Riverfalls | Active young professionals, fitness-oriented, grad students | None |
| Brixton | Young professionals in Plano/Addison, roommates needing 2-3BR, budget-conscious | None |
| Waterford | Medical/hospital employees, retail/service workers, Preston Hollow | None |
| AXIS | Young professionals, tech/medical employer adjacent | None |
| BLVD 2600 | (n.a) | N/A |
| Territory | "Preferred employers" (explicit ask) | None |
| Ascend | Professionals + families wanting suburb + city convenience | None |

### 7. Cross-property relationships unmodeled

**Pattern**: Properties exist in families. Briefs reference each
other. The system has no concept of this.

**Evidence**:

- **Bellmar siblings explicitly reference each other**: Riverfalls
  brief says *"Slightly more lifestyle-oriented than Waterford."*
  Multiple Bellmar properties share competitor lists (Five90, MidTown
  905, Mark at Midtown Park show up across 3+).
- **Territory brief**: *"sister properties in development"*
- **Ascend brief**: *"owned by D.R. Horton"* (parent brand attribution
  required in every description)

The new pipeline needs to model:
- Sibling properties (shared brand voice, shared comp avoidance)
- Parent owner / management company (attribution requirements)
- Sister property network (cross-promotional opportunities)

### 8. Floor plan ad group instantiation is unreliable

**Pattern**: Floor plan ad groups should be deterministically built
from `floor_plans[]`. Today this is operator-judgment, and it's wrong
~50% of the time.

**Evidence**:

| Property | Floor plans available | Floor plan AGs built | Match? |
|---|---|---|---|
| Everton | Studio, 1, 2, 3 | Studio, 1, 2 | ✗ (3BR dropped) |
| Riverfalls | 1, 2 | (parsing pending) | – |
| Brixton | 1, 2, 3 | (parsing pending) | – |
| Waterford | 1, 2 | 1 only | ✗ (2BR dropped) |
| AXIS | 1, 2, 3 | 1, 2, 3 | ✓ |
| BLVD 2600 | 1, 2, 3 | 1, 2, 3 | ✓ |
| Territory | 1, 2, 3 | 1, 2, 3 | ✓ |
| Ascend | 1, 2, 3, Casita | **none** | ✗ (entire campaign skipped) |

Logan's Bellmar builds tend to drop floor plan ad groups. Ascend
skipped the entire Floor Plans campaign. AXIS / BLVD / Territory got
it right. **No deterministic rule ties intake to output**.

### 9. Client-mandated content silently dropped

**Pattern**: Clients sometimes have explicit "must include" content
requirements. The current system has no slot for this and nothing
checks for compliance.

**Evidence**:

- **Ascend at the Parkway** — D.R. Horton is the property owner and
  client. The conversation thread has explicit guidance: *"please
  include 'owned by D.R. Horton' in each description."* Mentions of
  "Horton" anywhere in the 90 headlines + 24 descriptions: **0**.

This is a *contractual* miss. The client paid for advertising that
attributes the property to their parent brand, and didn't get it.

---

## Cross-cutting issue: operator drift

Same intake → different output depending on who's building.

| AM | # Properties | Amenities default | Comps default | Floor plans | Brief style |
|---|---|---|---|---|---|
| Logan | 4 | Always paused | Mostly paused | Often drops one | Structured bullets |
| Juliana | 1 | Active | Active | Complete | Free text |
| Dustin | 1 | Active | Active | Complete | Sparse |
| Izabelle | 2 | Mixed (Paused on Territory, Active on Ascend) | Active | Complete on Territory, **dropped entirely on Ascend** | Mostly comments |

There is no central rule for paused-vs-active or which floor plan
groups to include. Each AM has a house style. Same property, built by
a different AM, would ship differently.

The new pipeline's job here is to **remove the human judgment from
these decisions entirely**. Pause / active should be a deterministic
function of intake fields (e.g., "ship Comps paused if
`len(competitors) < 3`"), not a per-build call.

---

## Cross-cutting issue: field semantics drift

The same intake field is filled with different ontologies depending
on who's filling it.

| Field | Bellmar | AXIS | BLVD | Territory | Ascend |
|---|---|---|---|---|---|
| **Market** | Dallas (city) | Mid-Atlantic (region) | RPMI (client name) | (TBD) | Houston (metro) |
| **Campaign Focus** | Positioning | Concessions | `n.a` | (in comments) | Property narrative |
| **Local/Colloquial Terms** | Hyperlocal | Markets | `n.a` | (TBD) | Submarkets |
| **Top Amenities** | Bullet list | Comma dump | Bullet list | Few bullets | Free paragraph |

The intake form's field labels alone don't enforce semantics. The new
form needs to:

1. Use typed fields (city != region != metro != client)
2. Use structured pickers (no free-text amenity dump)
3. Separate fields that are doing double duty today (Campaign Focus is
   serving as positioning OR concessions OR narrative)

---

## Cross-cutting issue: critical content lives outside structured fields

Where the actual brief is, today:

- **Territory's strategy** = an "Other Info" comment: *"This is a
  new, lease-up community with a few sister properties in development.
  Client wants to prioritize new and luxury apartments — be sure to
  include preferred employers in our overall strategy."* None of this
  is in any structured field.
- **Ascend's D.R. Horton attribution** = a comment from the client
  contact during SEO review. Not in any structured field.
- **Ascend's casita keyword negotiation** = multi-thread back-and-forth
  between SEO, AM, and client. The agreement to track casita keywords
  was made verbally in the thread and never made it into paid search.
- **Ascend has 5 attachments** (PDFs and XLSXes) that contain more
  brief detail. None of this is parsed or extracted.

The new pipeline must capture in structured fields what is currently
captured in:
- Comment threads
- Email exchanges
- Attachment files
- Verbal agreements during client review

If it stays in unstructured channels, it can't drive Blueprint
variables, and we ship the same generic campaign as today.

---

## What this means for the Blueprint design

The Blueprint redesign is not "make the AI better." It's:

1. **Expand the variable surface** from ~5 levers to ~30 levers
2. **Define the input schema** so every brief input has a typed home
3. **Define the rendering logic** for how each input drives output
4. **Make pause/active and floor plan instantiation deterministic
   rules**, not operator calls
5. **Capture client-mandated content** as a structured "must include"
   field with rendering compliance checks

The 9 failure dimensions above each become a Blueprint variable (or
set of variables) and a corresponding template slot. The intake form
must be redesigned in lockstep so the variables are actually filled
with the right typed data.

---

## Success criteria for the Blueprint redesign

The Blueprint can be considered successful when, for each property in
this sample (re-run through the new pipeline), the resulting campaign
satisfies these acceptance tests:

| # | Test | Today's score | Target |
|---|---|---|---|
| 1 | Voice tier matches brief intent (luxury / standard / value / lifestyle / practical) | 3/8 | 8/8 |
| 2 | At least 2 property-specific differentiators appear in headlines | 0/8 | 8/8 |
| 3 | Concession/promo (if specified in brief) appears in ≥ 2 descriptions | 0/1 | 1/1 |
| 4 | Lease-up signals (preleasing, grand opening, etc.) appear when status = lease-up | 0/2 | 2/2 |
| 5 | At least 1 local landmark appears in PMax search themes or audience signal | ~0/8 | 8/8 |
| 6 | Target audience tags appear in audience signal | 0/8 | 8/8 |
| 7 | Floor plan ad groups exactly match `floor_plans[]` array | 4/8 | 8/8 |
| 8 | Pause/active state is deterministic from intake fields | 0/8 | 8/8 |
| 9 | Client-mandated content (if specified) renders in 100% of descriptions | 0/1 | 1/1 |

Hitting these for the existing 8 properties via re-run validates the
Blueprint before we touch a new one.

---

## What to do next

In order:

1. **Lock this problem definition** with Tyler, Sam, and the Account
   Manager group. The framing matters — if everyone agrees the gap is
   "the template only has 5 levers when the brief has 30," the
   Blueprint design conversation gets much easier.
2. **Map the new intake schema** to the 9 failure dimensions. Every
   dimension becomes a typed field.
3. **Then** design the Blueprints. The existing
   `FLUENCY_BLUEPRINT_SPEC.md` was built off Everton alone and missed
   most of these dimensions. It needs a second pass after this
   problem definition is locked.

The Blueprint should not be designed before the new intake schema is
locked. The intake is the contract. The Blueprint is the materializer.
Designing materializer first means the intake gets constrained to
match a template that was made up in advance.

---

## Appendix: source data

8 properties, ClickUp ticket numbers + Google Ads exports analyzed:

- 25DIGITAL-72984 — Everton at Bellmar
- 25DIGITAL-72951 — Riverfalls at Bellmar
- 25DIGITAL-72884 — The Brixton
- 25DIGITAL-72918 — Waterford at Bellmar
- 25DIGITAL-70687 — AXIS Crossroads
- 25DIGITAL-71875 — BLVD 2600
- 25DIGITAL-71912 — Territory at Spring Stuebner
- 25DIGITAL-65712 — Ascend at the Parkway

Reporting period: April 17–28, 2026 (most builds) and March 30–April 28
(Ascend, Territory).
