# Flight Plan — work-item schema, trigger catalog, routing

**Status:** Design / findings. No production code changed.
**Date:** 2026-09-08
**Branch:** `docs/flight-plan-proposal`
**Parent doc:** `docs/PROPERTY_FLIGHT_PLAN.md` (the proposal this responds to)
**Companion:** `docs/FLIGHT_PLAN_AM_TEST.md` — the one-page artifact to hand an account manager

Grounded in a read of the code on this branch plus a **live read of HubSpot**
(887 `plestatus = "RPM Managed"` companies, read-only, 2026-09-08). Every fill
rate below is measured, not estimated.

---

## 0. What the code already does, and where the proposal is wrong about it

The proposal's "How much of this already exists" table (`PROPERTY_FLIGHT_PLAN.md`
lines 83-93) understates the codebase in one place and overstates the data in
another. Both matter for what gets built.

**The three lanes already exist. The work item doesn't.**

| Proposal says | Actually |
|---|---|
| "The router and the board — Missing" | All three lanes exist as three unrelated modules. What's missing is a shared noun they all read and write. |

- **Auto** — `webhook-server/loop_autopilot.py`. Auto-approves recommendations
  inside guardrails, with a gatekeeper (`is_safe_to_auto_apply`), a
  `AUTO_PILOT_SAFE_ACTIONS` allowlist (`loop_autopilot.py:50`) and a 7-day
  warm-up before a property's recommendations auto-apply
  (`loop_autopilot.py:13`).
- **Assisted** — `webhook-server/approval_agent.py`. Its docstring states the
  discipline outright: *"All approvals route to a HUMAN for execution. No
  auto-execution in v1."* (`approval_agent.py:8`).
- **Specialist** — `webhook-server/creative_transition.py` (in prod) and
  `webhook-server/fulfillment_task.py`, which explicitly copies its shape.

None of the three shares a record. `loop_autopilot` acts on
`recommendation_proposed` Loop events, `approval_agent` acts on HubDB
recommendation rows, `creative_transition` acts on a HubSpot property change and
stamps a ClickUp id back onto the company. Three routers, three vocabularies,
no common item. **Build the noun, not the router.**

**Backwards business-day scheduling already exists and should not be rewritten.**
`webhook-server/launch_policy.py` is a pure, tested module that does exactly the
arithmetic the flight plan needs: `_add_business_days` (`launch_policy.py:38`)
and `BUILD_BUFFER_BUSINESS_DAYS = 5` (`launch_policy.py:27`) — a build window
enforced backwards from a launch date, weekends skipped. Its docstring already
flags the gap the flight plan will hit: *"A holiday calendar is out of scope
here."* `webhook-server/launch_rearm.py` is the companion sweep for items whose
date passed before the work started — the flight plan needs that on day one.

**Goal-backwards funnel math already exists.**
`webhook-server/funnel_forecast.py` works backwards from units-to-lease →
conversions → sessions → impressions. That is the "how much spend, aimed at
what" half of a flighted plan, already written and pure/testable.

**The exposure math has a real source we hadn't wired.** AptIQ ships a forward
90-day exposure percentage — column alias `"Exposure % (Next 90d)"`
(`webhook-server/services/fluency_ingestion/apt_iq_reader.py:87`), surfaced as
`exposure` on the snapshot (`webhook-server/apartmentiq_client.py:252`) and
already used to derive lifecycle state
(`services/fluency_ingestion/lifecycle_rules.py`). 705/887 RPM-Managed
properties carry an `aptiq_property_id`.

**And the dated anchor the whole proposal needs already exists, one report
over.** AptIQ's `report_type=unit` feed carries **`Date Available`,
`Date Leased`, `Unit Status`, `Floorplan Name`, `Rent`, `NER`, `Concessions`
and `Days On Market` per unit, refreshed daily.** That is exactly "which units
come open, when, on which floorplan." See §2 Tier A and §5.1 — this is the
single most important finding in the document and it replaces most of what the
first draft of this doc said was missing.

---

## 1. Work-item schema

### Shape: append-only state log, not a mutable row

Copy `migrations/0015_ticket_profile_proposals.py` exactly. Its docstring
(lines 7-15) already argues the case for this problem:

> *"Append-only state log, not a mutable row: one row per state transition
> (`proposed` → `accepted` | `rejected`), latest `created_at` per `proposal_id`
> wins on read. Same shape as loop_events, and it avoids BigQuery's streaming
> buffer, which blocks UPDATE on freshly-inserted rows."*
>
> *"`proposal_id` is deterministic — sha1(task_id, field_key) — so a re-fired
> ticket webhook re-proposes the same id instead of duplicating the card."*

That second property is what makes a **daily regenerating** work generator safe.
The generator can run every morning over 887 properties and re-emit the same
`item_id` for the same underlying fact; the row is a no-op unless the state
changed. Without it, a nightly cron produces 887 duplicate items a day.

`loop_events` cannot be the work-item table — it's immutable and has no notion
of a future date or an owner. It should carry the **transitions** (new event
types), which `loop_writer.record()` already supports via its `status`,
`parent_event_id` and `payload` params (`webhook-server/loop_writer.py:227-244`).

### `work_items` — proposed field list

| Field | Type | Where the value comes from |
|---|---|---|
| **Identity** | | |
| `item_id` | STRING NOT NULL | `sha1(property_uuid + trigger_key + anchor_date)[:16]` — computed. Deterministic, so the daily run is idempotent. |
| `property_uuid` | STRING | HubSpot company `uuid`. **Read only** — R1. The board's join key. |
| `company_id` | STRING | HubSpot `hs_object_id`. Carried separately because they are not always equal (§5.7) and every ClickUp/HubSpot deep link needs the id. |
| **What and why** | | |
| `trigger_key` | STRING NOT NULL | Code constant from the trigger catalog (§2), e.g. `sku.quarterly_creative_refresh_due`. Stable slug — it is the key for routing, for graduation stats, and for suppression. |
| `action` | STRING NOT NULL | Trigger's template. Imperative, one line, what a person would type in ClickUp. |
| `why` | STRING NOT NULL | Trigger's template, interpolated with the actual numbers. The AM must be able to disagree with this sentence. |
| `signal` | STRING (JSON) | The raw values the rule read, each with its own as-of date. This is what makes a wrong item diagnosable instead of merely wrong. |
| `signal_asof` | DATE | Oldest input's freshness (HubSpot `hs_lastmodifieddate`, AptIQ snapshot date). An item is only as trustworthy as its stalest input. |
| `confidence` | STRING NOT NULL | `known` \| `derived` \| `modeled`. Trigger catalog constant. **This field enforces the discipline in the proposal's "Three things to get right" #1** — v1 emits `known` only, and the enum makes any later drift visible in a `GROUP BY`. |
| **Dates** | | |
| `anchor_date` | DATE NOT NULL | The fixed market date. Trigger-supplied. |
| `anchor_kind` | STRING | `season_fixed` \| `contract_end` \| `service_cycle` \| `takeover_ramp` \| `market_date`. Says *why* the date is fixed, which is what an AM argues with. |
| `lead_time_bdays` | INT64 | Trigger catalog constant. Business days; resolved through `launch_policy._add_business_days`. |
| `must_start_by` | DATE NOT NULL | `anchor_date` minus `lead_time_bdays`. **The only date the board sorts on.** |
| `surfaces_on` | DATE | `must_start_by` minus a visibility buffer (default 14 calendar days). Keeps the queue at five items instead of seven hundred. |
| `milestones` | STRING (JSON) | `[{label, date}]` — the intermediate dates from the chain (creative in hand, ticket filed). Stored so the board can show slippage, not just lateness. |
| **Routing** | | |
| `decision` | STRING NOT NULL | `auto` \| `approved`. Who decides. See §3 for why this is split from `executor`. |
| `executor` | STRING NOT NULL | `agent` \| `am` \| `specialist_team`. Where the work happens. Determines whether a ClickUp ticket is filed, on which list, and which SLA sets `lead_time_bdays`. |
| `routing_reason` | STRING | Routing table constant. Why this cell, in one sentence. |
| `owner` | STRING | Resolved at file time: HubSpot `marketing_manager_email` → ClickUp member (`creative_transition._member_id_by_email`), or an agent id. **Nullable, and unassigned must render as unassigned** — `attention.py:334` (`_matches_scope`) already establishes the rule that "we don't know whose this is" must never resolve to "probably yours". |
| `external_ref` | STRING | ClickUp task id / HubSpot task id, once filed. Same role as `creative_transition_task_id`. |
| **State** | | |
| `status` | STRING NOT NULL | `proposed` \| `accepted` \| `filed` \| `in_progress` \| `done` \| `dismissed` \| `expired` \| `superseded`. |
| `actor` | STRING | Email or agent id that caused this transition. |
| `note` | STRING | Free text on the transition. The dismissal reasons are the trigger-tuning dataset. |
| `created_at` | TIMESTAMP NOT NULL | Row write time. Latest per `item_id` wins on read. |
| **Completion record** (written on the `done` transition, identical from every lane) | | |
| `completed_at` | TIMESTAMP | |
| `completed_by` | STRING | Person or agent. |
| `completed_by_executor` | STRING | Which executor actually finished it. May differ from where it started — a specialist item an agent finished is a graduation candidate; the reverse is a demotion signal. |
| `output_ref` | STRING | Link to the artifact: ClickUp task, published page, Fluency budget row, HubSpot note. **Proposal's "Three things to get right" #3 — an Auto action with no `output_ref` is a bug, not a completion.** |
| `was_edited` | BOOL | Assisted only: was the agent's draft approved unedited? **This single field is the entire graduation dataset.** Everything in §3's promotion rule is a query over it. |
| `landed_on_time` | BOOL | `completed_at <= anchor_date - downstream_lead`. Derived on write. |

### Two deliberate omissions

- **No stored `priority` / `score`.** Priority is a read-time sort
  (`must_start_by`, then units at risk), not a stored number. A stored priority
  is stale the day after it's written, and `attention.py` already learned this
  lesson — the triage list it replaced was a stored-score dashboard.
- **No `property_name`.** Resolve at read time from the Property Resolver, the
  way `attention.py` does (bounded at `_MAX_IDENTITY_LOOKUPS`). Copying the name
  into the row is the first step toward the third CRM that `THREE_SURFACES.md`
  warns about.

---

## 2. Trigger catalog

Format: what it reads → what it produces → what it schedules backwards from.
Fill rates are measured against the 887 RPM-Managed companies on 2026-09-08.

### The AptIQ unit feed — the anchor everything else was missing

Before the individual triggers, the source they rest on.

`https://data.apartmentiq.io/.../data_sync/data.csv?key=…&report_type=unit`
— same account and key as `APT_IQ_DAILY_SHEET_URL`, which the repo already
polls daily; `report_type` is just a query param. `apartmentiq_client.py:338`
also exposes it through the bulk job API (`report_type="units"`), which accepts
a `property_ids` filter.

41 columns, one row per listing observation. The ones that matter:
`Property ID`, `Unit`, `Unit Status` (Available / Leased / Applied),
**`Date Available`**, `Date Leased`, `Floorplan Name`, `Beds`, `Baths`,
`Sq Ft`, `Rent`, `NER`, `Concessions`, `Concession Details`,
**`Days On Market`**, `Last Change Date`.

**Measured on 2026-09-08 against the 700 RPM properties carrying an
`aptiq_property_id`:**

| | |
|---|---|
| Rows matching an RPM property | 147,102 across **694 properties** |
| Rows with a **future** `Date Available` | 53,581 (36.4%) |
| Properties with ≥5 units coming open in 90 days | **631 of 694** |
| Whole-file size | 1.1 GB / 1.64M rows (the full market universe, comps included) |

**It is a change log, not a snapshot — dedup is mandatory.** 75.8% of
`(Property ID, Unit)` keys appear more than once: the same unit re-listed at a
new price shows up again. Raw counts are nonsense (Solis Hapeville: "984 units
coming open" against 305 total units).

**Deduped on the newest `Last Change Date` per `(Property ID, Unit)`, it
reconciles exactly.** Across 667 properties, deduped `Unit Status = Available`
against HubSpot's `atr__ × totalunits` gives a **median ratio of 1.00**
(p25 0.93, p75 1.00). LYV Broadway: 44 available, 44 expected. Skye Reserve:
165 available, 172 expected. Two independent AptIQ surfaces agreeing to the
unit is about as good a validation as this stack offers.

Three limits to build against:

1. **It is scraped listing data, not the rent roll.** A unit on notice that
   hasn't been advertised yet does not appear. So the forward curve is dense
   for roughly **5–6 weeks and thin after** — LYV Broadway has 17 of its 23
   next-90-day units inside the first five weeks; Skye Reserve, 55 of 63. That
   comfortably supports a 7-day SLA plus a 14-day creative chain (~21 days). It
   does **not** reliably support a 21-day paid ramp stacked on top (~42 days).
   Size lead times to the horizon the data actually has.
2. **1.15% of future dates are garbage** — 2050, 2051, 2099, 2100. Cap at
   `today + 18 months` on ingest.
3. **Volume.** Don't pull 1.1 GB daily. Use the bulk job API with
   `property_ids`, or filter on ingest and store only RPM rows.

### Tier A — build these. Data present, fresh, and `known`.

#### A0 · `availability.floorplan_wave` — **the trigger the proposal was reaching for**
- **Reads:** the deduped AptIQ unit feed. Group future `Date Available` by
  ISO week × `Floorplan Name` per property.
- **Produces:** *"7 units come available the week of Oct 5, 5 of them 1-bed
  (A1, A3). Flight paid spend onto 1-bed inventory."*
- **Anchor:** the Monday of the availability week. **Not a calendar constant —
  a real market date, per property, per floorplan.** This is the fixed point
  `PROPERTY_FLIGHT_PLAN.md` line 29 says everything else is computed from.
- **Lead time:** 15 business days (7 specialist SLA + 5 creative + 3 flight
  ramp), sized to the ~5–6 week horizon the data actually has. A 42-day chain
  would outrun the signal.
- **Confidence:** `known` — observed listings, no renewal rate anywhere in it.
- **This is also the exposure math, done better.** Counting future
  `Date Available` per week per floorplan *is* the observed exposure curve. It
  needs no `expirations × (1 − renewal rate)` and therefore imports none of
  that formula's sensitivity. H1/H2 stay held back not because we lack a
  substitute but because we now have a better one.
- Fires for **631 of 694** covered properties. Rate-limit hard (§5.2) — one
  item per property per wave, biggest wave first.

#### A0b · `availability.stale_inventory`
- **Reads:** deduped unit rows where `Unit Status = Available` and
  `Days On Market >= 90`.
- **Produces:** *"108 of 165 available units have sat 90+ days. Diagnose
  pricing, listing quality and photos on B5 and A4 first."*
- **Anchor:** the day a unit crosses the DOM threshold — dated, per unit.
- **Lead time:** 5 business days.
- **Confidence:** `known`.
- Skye Reserve is the case in point: **108 of 165** available units are stale.
  Nothing in the portal surfaces that today, and it is a much louder signal
  than the occupancy number the AM already knows.

#### A0c · `availability.concession_drift`
- **Reads:** `Concessions`, `Concession Details`, `NER` vs `Rent` per unit,
  compared across units of the same floorplan.
- **Produces:** a flag where advertised concessions diverge inside a floorplan,
  or where NER has moved without a rent change.
- **Confidence:** `known` (advertised values), but the *interpretation* is
  `derived` — file it Assisted and let the AM judge.
- Lower priority than A0/A0b; listed because the columns are already in hand.

#### A1 · `seasonal.budget_lock`
- **Reads:** calendar constant (budgets lock end of October). For content only:
  `brf___total_budget_monthly` (671/887), `brf___paid_media` (671/887),
  `seo_budget` (655/887), `occupancy__` / `target_occupancy` (832 / 842).
- **Produces:** *"Draft the 2027 marketing budget recommendation. Current plan
  $X/mo ($Y paid media, $Z SEO). Occupancy A% against a B% target."*
- **Anchor:** last business day of October (2026-10-30).
- **Lead time:** 30 business days — 10 for the draft, 10 for AM review, 10 for
  client review. **Placeholder. Replace with measured cycle time from last
  year's BRF submissions.**
- **Confidence:** `known` for the date and the current spend. The *recommended
  number* is `derived` and must be AM-set in v1 — see H3/H4.
- **Fires today.** This is the highest-value trigger in the current window and
  it fires for ~671 properties at once (§5.3).

#### A2 · `seasonal.renewal_push_open`
- **Reads:** calendar constant (renewal push opens 10/1, runs through February).
  `trending_120_days_lease_expiration` is attached as **magnitude only** and
  does not move the date (see B2).
- **Produces:** *"File the renewal-season creative set (resident renewal comms +
  refreshed on-site collateral)."*
- **Anchor:** 2026-10-01.
- **Lead time:** 12 business days — 7 specialist SLA + 5 build window
  (`BUILD_BUFFER_BUSINESS_DAYS`, `launch_policy.py:27`).
- **Confidence:** `known`.

#### A3 · `seasonal.peak_leasing_ramp`
- **Reads:** calendar constant (peak leasing April–August).
- **Produces:** *"Paid flight live for peak season"* with the creative and
  ticket milestones behind it — exactly the chain in `PROPERTY_FLIGHT_PLAN.md`
  lines 31-35.
- **Anchor:** 2027-04-01. **Lead time:** 42 business days (21d flight ramp +
  14d creative + 7 specialist SLA, converted).
- **`must_start_by` 2027-02-02 — outside the 90-day window.** Worth building
  anyway: a board that is *quiet* when nothing is due is a design requirement,
  not a failure. If the first version shows an April item in September, the
  visibility buffer is wrong.

#### A4 · `sku.service_window_expiring`
- **Reads:** the ~40 dated SKU fields on the company record —
  `seo_standard_end_date`, `landing_page_end_date`,
  `social_posting_standard_end_date`,
  `review_response___review_removal_end_date`, `paid_meta_ads_end_date`,
  `geofence_end_date`, `email_drip_campaign_end_date`, and the rest.
  Populated on 36–81% of the portfolio depending on the SKU.
- **Produces:** *"Social Posting Standard ends 2026-11-14 — renew, change tier,
  or stop. Current $X/mo."*
- **Anchor:** the end date. **Lead time:** 30 business days (renewal decision →
  IO → Fluency budget change).
- **Confidence:** `known`.
- **Gate it on `end_date >= today`.** Under that gate it currently produces
  **near zero items portfolio-wide**, because the dates are all in the past
  (§5.4). Ship it anyway: a rule that correctly emits nothing makes the data
  debt visible, where today it is invisible. **Nothing in the codebase reads any
  of these fields** — the only `*_end_date` read anywhere is
  `disposition___end_all_services_end_date` (`webhook-server/disposition.py:85`).

#### A5 · `sku.quarterly_creative_refresh_due`
- **Reads:** `quarterly_creative_refresh_end_date` (719/887 populated).
- **Produces:** *"Kick off the Q4 creative refresh (paid + social asset set)."*
- **Anchor:** `end_date + 90 days` — the SKU is explicitly quarterly.
- **Lead time:** 17 business days (7 specialist SLA + 10 production).
- **Confidence:** `known`, **with one open question**: this reads `end_date` as
  "the last cycle ended here." Someone who knows how that field was set has to
  confirm that in one sentence. If it means "the contract ends here," the whole
  trigger is A4 instead.
- **This is the cleanest end-to-end demo of the entire flight plan**: a real
  populated field, a real date inside the window, and a ticket path
  (`creative_transition.py`) that already runs in production.

#### A6 · `lifecycle.takeover_ramp`
- **Reads:** `managementstart` (883/887 — the best-populated date on the record)
  and `takeover` (883/887).
- **Produces:** day-30 / day-60 / day-90 onboarding-completeness checks.
- **Anchor:** `managementstart + {30,60,90}`. **Lead time:** 5 business days.
- **Confidence:** `known`.
- **Must extend, not duplicate, `onboarding.list_onboarding()`**, which
  `attention.py` already aggregates. Two queues answering the same question is
  the exact failure mode that module's docstring exists to prevent
  (`attention.py:21`).

### Tier B — one human confirmation away from Tier A

#### B1 · `exposure.aptiq_90d`
- **Reads:** AptIQ `Exposure % (Next 90d)`
  (`services/fluency_ingestion/apt_iq_reader.py:87`), available for the 705/887
  properties with an `aptiq_property_id`, refreshed daily.
- **Largely superseded by A0.** The unit feed gives the same forward window at
  unit and floorplan grain, dated, so the rolled-up percentage is a summary
  rather than a source. Keep it as a cheap portfolio-level sort; do not schedule
  from it.
- Still worth one question to AptIQ: does their `Exposure %` apply a renewal
  assumption? If it does, the rollup is `modeled` even though the unit rows
  underneath it are not — and the two must not be mixed in the same item.

#### B2 · `expiration.window_120d`
- **Reads:** `trending_120_days_lease_expiration` (832/887, refreshed daily).
- **Blocked on two problems.** It doesn't reconcile: the median value is **4.0%
  of total units over a 120-day window** (p10 0.0%, p90 6.8%), against a median
  ATR of ~10–11% *today*. A stabilized property turning 35–45% a year should
  show 12–15% expiring per 120 days. Either the field is a subset, a monthly
  figure, or a delta — nobody in this repo writes it, so its definition lives in
  whatever system feeds HubSpot. And even once defined, it is a **rolling count
  with no month distribution**, so it can size an item but can never date one.
- Use it as `magnitude` on A2 with `confidence: derived`. Never as an anchor.

### Explicitly held back

Per `PROPERTY_FLIGHT_PLAN.md` line 124-128 — generate from what we know first.

| Held back | Why |
|---|---|
| **H1 · anything reading `brf___renewal_leases_120_trend`** | **It is not observed renewals. It is a hardcoded 30%.** Of the 579 properties carrying both fields, `renewal_120 / expiration_120` is **exactly 0.30 on 367 of them** (63%); median 0.30, p10 0.27, p90 0.51. This is the modeled renewal rate — the single most sensitive input our own note says to stress-test first — already sitting on the HubSpot company record wearing a fact's costume. Anyone who builds an "exposure" trigger from it will believe they used known data. |
| **H2 · 12-month exposure via `expirations × (1 − renewal rate)`** | Same input as H1, plus there is no per-month expiration distribution to spread the result over. A 12-month exposure number with no month attached cannot produce a date, which is the only thing this system exists to produce. |
| **H3 · `forecasting.py` `shift_budget` recommendations** | The channel CPLs they rest on come from pseudo-attribution — *"channel's share of total spend × total leases"* (`forecasting.py:166`). That assumes spend and leases are proportional, which is the proposition the recommendation is supposed to test. Fine as a card an AM reads. Not fine as a dated work item with an SLA behind it. |
| **H4 · `funnel_forecast.py` goal-backwards plans** | `DEFAULT_LEADS_PER_LEASE = 32.0` (`funnel_forecast.py:27`) is a portfolio constant. Per-property it is the second most sensitive input after renewal rate. Hold until it's measured per property from Hyly — which is 47/887 properties today. |
| **H5 · anything anchored on a NEW-CONSTRUCTION delivery date** | Still held back — no field holds a construction delivery date (§5.1). Note this is now a narrow gap affecting lease-ups only: **turnover availability is fully dated** via A0, and for a stabilized property that is the date that actually matters. |
| **H6 · `loop_autopilot` auto-approvals** | `loop_mode` is populated on **0 of 887** properties, so the module cannot fire today. Leave it that way until §3's graduation rule has data. The Auto lane being empty is currently enforced by accident; make it enforced on purpose. |

---

## 3. Routing rules

### Where I disagree with the proposal: three lanes is the right UI, wrong storage

The proposal's lanes are Auto / Assisted / Specialist. Two of those describe
**who decides**; the third describes **who does the work**. Those are different
axes, and collapsing them costs something concrete:

- A Specialist item has no ladder. There is no Auto version of "a human designs
  an asset," so that lane can never graduate — yet it sits on the same scale as
  two lanes whose whole purpose is graduating.
- It hides what we've already automated. `creative_transition.py` files a
  ClickUp task off a HubSpot state change with **no human in between**
  (`creative_transition.py:107`). On the decision axis that is fully automatic.
  Under three lanes it reads as "Specialist," which makes the one thing we have
  already automated invisible.

**Store two fields; render three chips.**

| | `executor: agent` | `executor: am` | `executor: specialist_team` |
|---|---|---|---|
| **`decision: auto`** | Slack digest, gap list | — (not a real cell) | **File the ticket without asking** — what `creative_transition.py` already does |
| **`decision: approved`** | Agent drafts, AM approves, agent publishes | Agent drafts, AM executes | Agent drafts the brief, ticket carries it, specialist executes |

The payoff is not tidiness. It's that the two concerns stop fighting over one
field: `lead_time_bdays` is set by **executor** (a specialist team has a 5–7
business day SLA; an agent has none), while graduation is measured on
**decision**. Under three lanes those are tangled together, and you can't move
one without moving the other.

AMs never see the matrix. Render exactly the proposal's three chips:
**"We'll do it"** (`auto`), **"Ready for your OK"** (`approved` + `agent`/`am`),
**"With the creative team"** (`executor: specialist_team`).

### Defaults

**Everything starts `approved`.** Two exceptions, both narrow:

1. **`auto` + `specialist_team`** — filing a ticket. Reversible, no spend, no
   client-visible write, and the code is already in production with a durable
   dedup stamp (`creative_transition.py:141`) and a flood guard
   (`FLOOD_THRESHOLD = 25`, `creative_transition.py:188`). Start here; it is the
   lowest-risk automation in the whole design.
2. **`auto` + `agent`** — only where the output is a **read**: assembling a gap
   list, drafting a recap, posting a digest. Never a spend change, never a
   client-visible publish, never a HubSpot property write.

### Per-trigger routing

| Trigger | decision | executor | Why |
|---|---|---|---|
| `availability.floorplan_wave` | approved | am | It moves paid budget onto specific floorplans. Highest-value trigger on the board and therefore the last one to automate. Agent drafts the wave and the proposed shift; the AM commits the money. |
| `availability.stale_inventory` | approved | agent | Agent assembles the stale list and the pricing/listing diagnosis; AM approves before anything is changed. Graduates to `auto` fast if the diagnosis holds up — it publishes nothing. |
| `availability.concession_drift` | approved | am | Advertised values are known; what they mean is a judgment call. |
| `seasonal.budget_lock` | approved | am | The number is the AM's judgment and the client's money. Agent drafts the comparison; the AM owns the figure. Never auto. |
| `seasonal.renewal_push_open` | **auto** | specialist_team | Filing the ticket is the whole action, it happens every year regardless, and the ticket path is proven. Nothing is decided by filing it. |
| `seasonal.peak_leasing_ramp` | approved | specialist_team | Same mechanics as above, but it carries a paid flight and a budget behind it, so a human confirms before the chain starts. |
| `sku.service_window_expiring` | approved | am | Renew / change tier / stop is a revenue decision. |
| `sku.quarterly_creative_refresh_due` | **auto** | specialist_team | Contracted, quarterly, already paid for. Filing it is not a decision. |
| `lifecycle.takeover_ramp` (checks) | **auto** | agent | Output is a read — the gap list. Publishes nothing. |
| `lifecycle.takeover_ramp` (gaps found) | approved | specialist_team | Each gap becomes its own item; a human confirms before the tickets go out, so a data glitch can't file forty. |
| B1 / B2 / anything `modeled` | approved | am | Held back from the board entirely in v1; when they land, they land here. |

### Graduation — a rule, not a vibe

A `trigger_key` moves `approved` → `auto` when **all** hold:

1. ≥ 20 completed items on that `trigger_key`
2. ≥ 90% with `was_edited = false`
3. Zero reversals in the trailing 30 days
4. The write is reversible, and `output_ref` is always populated

Reviewed quarterly. **Any reversal demotes immediately**, no discussion. All
four are queries over the completion record in §1, which is the reason
`was_edited` is in the schema at all.

---

## 4. Worked example

See `docs/FLIGHT_PLAN_AM_TEST.md` for the version to hand an account manager.
The short version, and why it's the finding:

**LYV Broadway** (Carrollton TX, Dallas market, uuid `21598594106`, 390 units,
occupancy 91.79% vs 95% target, ATR 11.28% = 44 units available, SEO Standard
$800/mo, paid media $2,839.46/mo, total $4,638/mo, RPM-managed since 2024-07-01)
generates **six items** in the 90 days from 2026-09-08 — three from the
calendar and three from its own availability curve:

| must_start_by | Action | Anchor | Chip |
|---|---|---|---|
| 2026-09-14 | Flight paid onto 1-bed inventory — 7 units open the week of 10/05, 4 of them A1/A3 | 2026-10-05 availability wave | Ready for your OK |
| 2026-09-15 | File the renewal-season creative set | 2026-10-01 renewal push opens | With the creative team |
| 2026-09-08 (now) | Diagnose 10 stale units (Available, 90+ days on market; one at 306) | DOM threshold, crossed long ago | Ready for your OK |
| 2026-09-18 | Draft the 2027 budget recommendation | 2026-10-30 budget lock | Ready for your OK |
| 2026-10-05 | Second wave — 4 units open the week of 10/26, 2 of them B1 (2-bed) | 2026-10-26 availability wave | Ready for your OK |
| 2026-10-06 | Kick off the Q4 creative refresh | 2026-10-29 (last cycle 07-31 + 90d) | With the creative team |

Its full 90-day curve: **23 units** coming available, 17 of them in the first
five weeks, concentrated in one-bedrooms (A1 ×7, A3 ×6 = 13 of 23).

**Now the two properties diverge, which is the point.** **Skye Reserve** —
982 units, occupancy 76.07%, ATR 17.52% (165 units available against 172
expected), $16,730/mo — generates the same three calendar items *and*:

- **63 units** coming available in 90 days, 55 of them inside five weeks,
  peaking at 17 units the week of 09/21 — concentrated in B5 and A4.
- **108 of its 165 available units have sat 90+ days on market.** Two-thirds of
  its live inventory is stale. That is the single loudest fact about this
  property and nothing in the portal says it today.

An earlier draft of this document concluded that both properties would generate
identical boards, because magnitude had no dated trigger. **That was wrong** —
it was based on HubSpot company fields alone. With the unit feed, a 982-unit
property at 76% occupancy generates roughly triple the work of a 390-unit
property at 92%, dated by week and pointed at named floorplans, which is what
the flight plan was supposed to do all along.

**And Skye Reserve immediately shows the horizon limit biting.** Its largest
wave — 17 units the week of 2026-09-21 — has a `must_start_by` of
**2026-08-31, eight days in the past.** On a 15-business-day chain that wave is
already un-actionable. That is not a flaw in the arithmetic; it is the
arithmetic doing its job, and it is the first thing worth showing an AM. Two
honest responses, and the AM should pick: shorten the chain for availability
items (they are mostly a bid and budget change, not a creative build), or accept
that the first ~15 business days of the curve arrive as "too late to fully
work" and rank them as partial.

---

## 5. What breaks

### 5.1 The anchor exists — but not where the first draft looked

**Correction.** An earlier version of this section concluded that the
proposal's central mechanic had no data behind it. That was based on searching
the 848 HubSpot company properties and finding nothing, which was the wrong
place to look. **AptIQ's `report_type=unit` feed carries dated per-unit
availability for 694 of the 700 covered RPM properties, refreshed daily.** See
§2. The rest of this section is what remains true after that correction.

**What genuinely does not exist is a NEW-CONSTRUCTION delivery date.**
Measured against 887 RPM-Managed companies:

| Field | Populated |
|---|---|
| `building_status__in_construction__pre_leasing__open__new_management__etc__` | **0 / 887** |
| `lease_up_start_date` | **0 / 887** |
| `desired_campaign_launch_date` | **0 / 887** |
| `lease_up_ramp_months` | **0 / 887** |
| `managementstart` | 883 / 887 |

Nothing holds a construction delivery date. AptIQ tracks what is advertised, so
a building that has not started listing is invisible to it; Hyly gives
lead-to-lease funnel milestones, not inventory.

**This is now a narrow gap, not a foundational one.** It affects lease-ups and
new phases only. For a stabilized property — which is nearly the whole
portfolio, since `occupancy_status` is populated on 29/887 and everything else
scores as stabilized — the date that matters is when a unit turns, and that is
fully dated via A0. If lease-up scheduling is wanted, the fix is a field to hold
a delivery date plus someone to maintain it, which is a data-operations task,
not a build.

Related: `lease_up_start_date` is 0% populated but is the *preferred* takeover
source in `portfolio.py:554` and `server.py:418`, which silently fall back to
`managementstart`. And `occupancy_status` is populated on **29/887 (3.3%)**, so
`leasing_ramp.normalize_occupancy_status` (`leasing_ramp.py:28`) returns `''` —
"unknown → treat as stabilized" — for 96.7% of the portfolio. **Lease-up
detection is effectively off across the fleet.** Any trigger keyed on lease-up
status will fire almost nowhere and nobody will notice.

### 5.2 It generates work nobody should do

- **The renewal-rate trap (H1).** The most likely way this design goes wrong is
  someone reading `brf___renewal_leases_120_trend` as observed renewals. It is
  0.30 × expirations on 63% of properties. It will look like known data, and it
  will pass review.
- **Uniform items across a non-uniform portfolio.** Every calendar trigger fires
  for every property on the same day. 671 budget items on 2026-09-18 is not a
  board, it is a mail merge. Rate-limit at the trigger, not the UI: cap items
  per property per week, and stagger a portfolio-wide anchor across its lead
  window rather than stacking it on one date.
- **Duplicating `attention.py`.** Six of the nine things in
  `THREE_SURFACES.md`'s table already exist. A takeover-ramp item that repeats
  what `onboarding.list_onboarding()` already says turns the action inbox back
  into a second dashboard — the exact thing `attention.py:21` exists to prevent.

### 5.3 It produces dates that are wrong

- **Every lead time in this document is a guess.** 21d flight ramp, 14d creative,
  7 business day SLA, 30 business day budget cycle — all placeholders. The
  proposal says the same (line 43). Until they come from measured delivery data,
  every `must_start_by` is precise and unfounded, and precision is exactly what
  makes a wrong date persuasive.
- **No holiday calendar.** `launch_policy.py`'s docstring already flags this.
  Budget lock lands 2026-10-30 and renewal season opens 2026-10-01 — but a
  peak-leasing chain backwards from April crosses Thanksgiving, Christmas and
  New Year, and weekends-only math will be 3–5 days optimistic exactly where the
  stakes are highest.
- **Anchors that land on weekends.** Budget lock "end of October" is Saturday
  2026-10-31; the example normalizes to Friday 10-30. Every `season_fixed`
  anchor needs that normalization or the dates drift by trigger.
- **Stale inputs, confident output.** `occupancy__` / `atr__` /
  `trending_120_days_lease_expiration` refresh daily (both example properties
  show `hs_lastmodifieddate` of 2026-09-08). The SKU dates do not — see 5.4.
  `signal_asof` exists so the board can say which.

### 5.4 The richest known-date family is schema-real and data-dead

~40 dated SKU fields exist. They are populated. They are all in the past:

| Field | Populated | In the future | Next 90 days |
|---|---|---|---|
| `quarterly_creative_refresh_end_date` | 719 | **5** | 5 |
| `landing_page_end_date` | 719 | **5** | 5 |
| `social_posting_standard_end_date` | 717 | **5** | 5 |
| `review_response___review_removal_end_date` | 716 | **5** | 5 |
| `seo_premium_end_date` | 505 | **0** | 0 |
| `seo_standard_end_date` | 443 | **0** | 0 |
| `paid_meta_ads_end_date` | 432 | **0** | 0 |
| `google_ads_performance_max_end_date` | 354 | **0** | 0 |

The mass sits on a handful of bulk-stamp dates — 2025-01-29, 2025-12-17/18/19,
2026-07-23/31 — not on per-property contract windows. Two consequences:

1. A generator that doesn't gate on `end_date >= today` files ~700 tickets for
   contracts that ended nine months ago, on day one.
2. **Rolling those dates forward is the cheapest, highest-leverage step in the
   entire flight plan.** It costs one data cleanup and it lights up the largest
   family of genuinely known forward dates we have. It is worth more than any
   code in this document.

### 5.5 New failure modes the unit feed introduces

The earlier version of this section said the board misses urgency because every
trouble signal was undated. A0 fixes that. What it introduces instead:

- **Volume.** 631 of 694 properties have ≥5 units coming open in 90 days, and
  most have several waves. Ungoverned, A0 alone produces thousands of items.
  The cap has to be per property per week, ranked by units in the wave, with a
  floor (ignore waves under ~4 units, or under ~1% of the property) so the board
  doesn't fill with two-unit noise.
- **Dedup is load-bearing, and silently so.** Skip it and every count is roughly
  2–3× too high while still looking plausible — Solis Hapeville reads "984 units
  coming open" against 305 total. Any change to the dedup key (unit-number
  formatting varies: `1-1216`, `#1216`, `1140` all appear at LYV Broadway)
  silently reinflates the numbers. Assert the reconciliation against
  `atr__ × totalunits` on every run and alarm when the median ratio drifts off
  1.00.
- **The horizon is shorter than the chain.** §4's Skye example: its biggest wave
  was already past its start date. Lead times must be sized to a ~5–6 week
  signal, not to an ideal 42-day flight plan.
- **Scraped data will disagree with the on-site team.** A unit AptIQ shows
  available may be held, down for renovation, or already leased offline. The
  first time an AM is told to spend against a unit the CM knows is unavailable,
  the board loses credibility. Show `Days On Market` and `Last Change Date` on
  every item so the AM can see how fresh the observation is, and make
  "dismissed — not actually available" a first-class dismissal reason feeding
  back into the trigger.
- **We still can't see units on notice that aren't listed yet.** So the board
  reflects *marketing* exposure, not *lease* exposure. That distinction should
  be stated on the surface, or someone will read the curve as a rent roll.

### 5.6 The volume question flipped

The earlier version of this section worried the calendar would yield only three
items per property per quarter. With A0 the problem is the opposite: six for a
healthy 390-unit property, more for a struggling one, times 694 properties. The
question for the AM is no longer "is this enough?" but **"which of these six
would you actually have done, and which is noise?"** — which is a better
question and the one the one-pager now asks.

### 5.7 Identity edge cases

- **34 RPM-Managed properties have no `uuid`** (853/887). Same class as the
  blocking events recorded in `IMMUTABLE_RULES.md`. They are invisible to a
  uuid-keyed board, and R1 forbids fixing it in code — the fix is a deal
  association, upstream.
- **`uuid` is not always `hs_object_id`.** *East at Innovation* is company
  `43743814513` with uuid `38956965778`; *10X Port St. Lucie* is `44983221833`
  with uuid `10559962416` — merged or duplicated records. Dedup on `uuid`, link
  on `company_id`, carry both.

---

## 6. Revised build order

Mostly the proposal's order (lines 136-150), with the data reality folded in.

1. **Ingest the AptIQ unit feed, deduped, into BigQuery.** Daily, filtered to
   RPM `aptiq_property_id`s via the bulk job API, with the
   `atr__ × totalunits` reconciliation asserted on every run. This is now the
   foundation — A0, A0b and A0c all sit on it, and it is the only genuinely new
   plumbing in the plan.
2. **One property on paper, then an AM.** `docs/FLIGHT_PLAN_AM_TEST.md` is
   ready. The question that matters is §5.6's: which of the six is noise.
3. **Roll the SKU end dates forward.** Not code, still free, still lights up the
   largest family of contract dates (§5.4).
4. **`work_items` as a table, read in Slack.** Migration `0016`, copying
   `0015_ticket_profile_proposals.py`. Triggers A0, A0b, A1, A2. No UI.
5. **Wire the specialist executor to `creative_transition.py`'s pattern**,
   scheduled backwards through `launch_policy._add_business_days`. Existing code
   in both halves.
6. **Log `was_edited` on every approval** from the first day items exist. That
   log is the only thing that ever earns a trigger its way to `auto`.
7. **Then the board UI.**

One field definition still worth an email: what
`quarterly_creative_refresh_end_date` actually means (A5).

---

## Provenance

Code claims cite file and line on this branch. Portfolio statistics come from a
read-only HubSpot Search API call on 2026-09-08 over all 887 companies with
`plestatus = "RPM Managed"` (paged, `crm/v3/objects/companies/search`), plus the
company property schema (848 properties). No writes were made; per R1 nothing
touched `uuid`. Property-level figures for LYV Broadway and Skye Reserve are
verbatim HubSpot values as of `hs_lastmodifieddate` 2026-09-08.

Unit-level figures come from a read-only pull of the AptIQ `data_sync`
`report_type=unit` CSV on 2026-09-08 (1.64M rows; 147,102 matching an RPM
`aptiq_property_id`), deduped to the newest `Last Change Date` per
`(Property ID, Unit)` giving 40,577 current unit records across 694 properties.
The deduped `Unit Status = Available` count was reconciled against HubSpot
`atr__ × totalunits` across 667 properties: median ratio 1.00, p25 0.93,
p75 1.00.

**Revision note.** §5.1, §5.5, §5.6 and §4 were rewritten after Kyle pointed out
that AptIQ carries availability data the first pass had not checked. The
original conclusion — that no dated inventory signal existed — was wrong, and
was reached by searching HubSpot company properties alone.
