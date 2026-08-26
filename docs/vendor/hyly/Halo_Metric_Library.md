# Halo Metric Library

*Each entry defines one Halo metric and the reusable pathway that retrieves it. Pathways name the object and its columns; the dataset is supplied at execution time and is deliberately absent from this document.*

**Library ID:** `halo_metric_library` · **Schema version:** 1.0 · **Generated:** 2026-08-12 · **Role:** master

> Foundation reference. Tenant-agnostic by construction: no dataset or project is named anywhere, so no substitution slot exists. Client instances are generated per tenant from this master and carry that tenant's resolved values only.

---

## Contents

- [Trace context](#trace-context)
- [Metric index](#metric-index)
- [Metric detail](#metric-detail)
- [Pathways](#pathways)
- [Placeholders](#placeholders)
- [Breakdowns](#breakdowns)
- [Cross-checks](#cross-checks)
- [Conflicts](#conflicts)
- [Data gaps](#data-gaps)
- [Corrections](#corrections)
- [Disambiguation](#disambiguation)
- [Governance and configuration](#governance-and-configuration)

---

## Trace context

Every value in this library was traced against a single property and period.

| Field | Value |
|---|---|
| Organization | RPM Living (`1747307582311553649`) |
| Property | The Bromley at Brighton Crossing (`1865695607790353330`) |
| PMS | Yardi, property code `12402` |
| Period | 2026-06-01 to 2026-06-30 |
| Dataset | Not recorded. Supplied by the connector from auth context. |

---

## Metric index

27 metrics across 2 sections.

### Occupancy

| Metric | ID | Value | Period | Semantics | Source object | Pathway | Status |
|---|---|---|---|---|---|---|---|
| [Exposure Rate](#exposure-rate-pct_exposure) | `pct_exposure` | 5.69% | Jun, 2026 | point_in_time_month_end | `t_oc_agg_occupancy_property` | `point_in_time_ratio` | verified |
| [Net Move Ins](#net-move-ins-net_move_ins) | `net_move_ins` | -12 | Jun, 2026 | period_total | `t_ot_agg_resident_activity_property` | `range_pivoted_difference` | verified |
| [Rentable Units](#rentable-units-rentable) | `rentable` | 299 | Jun, 2026 | point_in_time_month_end | `t_oc_agg_occupancy_property` | `point_in_time_value` | verified |
| [Occupied Units](#occupied-units-occupied) | `occupied` | 266 | Jun, 2026 | point_in_time_month_end | `t_oc_agg_occupancy_property` | `point_in_time_value` | verified |
| [Vacant Units](#vacant-units-vacant) | `vacant` | 33 | Jun, 2026 | point_in_time_month_end | `t_oc_agg_occupancy_property` | `point_in_time_value` | verified |
| [Excluded Units](#excluded-units-excluded) | `excluded` | 0 | Jun, 2026 | point_in_time_month_end | `t_oc_agg_occupancy_property` | `point_in_time_value` | verified |
| [Occupancy Trend](#occupancy-trend-pct_trend) | `pct_trend` | 94.31% | Jun, 2026 | point_in_time_month_end | `t_oc_agg_occupancy_property` | `point_in_time_adjusted_ratio` | verified_formulation_unresolved |
| [Leased Rate](#leased-rate-pct_leased) | `pct_leased` | 94.31% | Jun, 2026 | point_in_time_month_end | `t_oc_agg_occupancy_property` | `point_in_time_complement_ratio` | verified_formulation_unresolved |
| [Total Units](#total-units-total_units) | `total_units` | 299 | Jun, 2026 | point_in_time_month_end | `t_oc_agg_occupancy_property` | `point_in_time_value` | verified |
| [Average Occupancy Rate](#average-occupancy-rate-occupancy_rate) | `occupancy_rate` | 93.59% | Jun, 2026 | period_average | `t_occupancy_rate` | `range_average_ratio` | verified |
| [Current Occupancy Rate](#current-occupancy-rate-pct_occupied) | `pct_occupied` | 88.96% | Jun, 2026 | point_in_time_month_end | `t_oc_agg_occupancy_property` | `point_in_time_ratio` | verified |
| [Move-Ins](#move-ins-move_ins) | `move_ins` | 11 | Jun, 2026 | period_total | `t_ot_agg_resident_activity_property` | `range_pivoted_sum` | verified |
| [Move-Outs](#move-outs-move_outs) | `move_outs` | 23 | Jun, 2026 | period_total | `t_ot_agg_resident_activity_property` | `range_pivoted_sum` | verified |
| [Future Leases](#future-leases-leased_future) | `leased_future` | 16 | Jun, 2026 | point_in_time_month_end | `t_oc_agg_occupancy_property` | `point_in_time_value` | verified_source_gap |
| [Available Units](#available-units-available) | `available` | 17 | Jun, 2026 | point_in_time_month_end | `t_oc_agg_occupancy_property` | `point_in_time_value` | verified |
| [Notice Unrented Units](#notice-unrented-units-notice_unrented) | `notice_unrented` | 0 | Jun, 2026 | point_in_time_month_end | `t_oc_agg_occupancy_property` | `point_in_time_value` | verified_source_gap |
| [Notice Rented Units](#notice-rented-units-notice_rented) | `notice_rented` | 0 | Jun, 2026 | point_in_time_month_end | `t_oc_agg_occupancy_property` | `point_in_time_value` | verified_source_gap |
| [Vacant Unrented Units](#vacant-unrented-units-vacant_unrented) | `vacant_unrented` | 17 | Jun, 2026 | point_in_time_month_end | `t_oc_agg_occupancy_property` | `point_in_time_value` | verified |
| [Vacant Rented Units](#vacant-rented-units-vacant_rented) | `vacant_rented` | 16 | Jun, 2026 | point_in_time_month_end | `t_oc_agg_occupancy_property` | `point_in_time_value` | verified |
| [Delayed Move Ins](#delayed-move-ins-delayed_move_ins) | `delayed_move_ins` | 3 | Jun, 2026 | point_in_time_month_end | `t_oc_agg_occupancy_operational` | `point_in_time_value` | verified |

### Lead Generation

| Metric | ID | Value | Period | Semantics | Source object | Pathway | Status |
|---|---|---|---|---|---|---|---|
| [Newly Created Leads](#newly-created-leads-created_contact) | `created_contact` | 92 | Jun, 2026 | period_total | `t_contact_activity` | `range_distinct_count_excluding_status` | verified_definition_inferred |
| [1st Scheduled](#1st-scheduled-scheduled_contact) | `scheduled_contact` | 33 | Jun, 2026 | period_total | `t_contact_activity` | `range_distinct_count_by_milestone` | verified |
| [1st Toured](#1st-toured-toured_contact) | `toured_contact` | 19 | Jun, 2026 | period_total | `t_contact_activity` | `range_distinct_count_by_milestone` | verified |
| [1st Applied](#1st-applied-applied_contact) | `applied_contact` | 14 | Jun, 2026 | period_total | `t_contact_activity` | `range_distinct_count_by_milestone` | verified |
| [Total Applied](#total-applied-total_applied_contact) | `total_applied_contact` | 14 | Jun, 2026 | period_total | `t_contact_activity` | `range_distinct_count_by_milestone` | verified_structural_duplicate |
| [Net Applied](#net-applied-net_applied_contact) | `net_applied_contact` | 11 | Jun, 2026 | period_total | `prospect_journey` | `range_distinct_difference_by_event` | verified |
| [Leased](#leased-leased_contact) | `leased_contact` | 8 | Jun, 2026 | period_total | `pai_journey_1747307582311553649` | `range_distinct_count_by_event` | verified_single_source |

---

## Metric detail

### Occupancy

#### Exposure Rate — `pct_exposure`

The share of the property's units that are open to be leased - empty and not yet rented, or on notice with no replacement signed.

| Field | Value |
|---|---|
| Value | **5.69%** (5.69, 2 dp) |
| Unit / direction | percent · lower_is_better |
| Status | verified |
| Period | Jun, 2026 — point_in_time_month_end |
| Reading date | 2026-06-30 |
| Source object | `t_oc_agg_occupancy_property` (view, PMS, refresh daily) |
| Date column | `as_of_date` |
| Grain | `property_id`, `as_of_date` — point-in-time |
| Pathway | `point_in_time_ratio` |
| Components | `numerator` = `available`, `denominator` = `total_units` |
| Denominator metric | `total_units` |
| Aliases | exposure, exposure rate, % exposure, units exposed |
| Cache tier | 2 |

**Period note.** The label denotes a month but the figure is a single month-end reading, not a monthly average.

**Component meanings**

- `available` — units available to lease
- `total_units` — total units at the property

**Aggregation.** Over time: `not_summable` · Across properties: `recompute_from_components`

> Do not add or average percentages. Sum the numerator and denominator across the group, then divide.

**Verification.** match · reported 5.69 · derived 5.6856 · variance 0.0 pp · numerator 17 · denominator 299 · verified 2026-08-12

- *Method:* Pathway resolved and executed against the live warehouse.

**Constraints.** history starts 2026-06-30 · behaviour `accumulating`

> Daily readings in this object begin 2026-06-30. Daily June data does exist in t_occupancy_rate, but on a different unit base - see the object_disagreement conflict.

**Notes**

- A second object, t_occupancy_exposure_rate, reports a different June figure over a 2-29 June window on a different unit count, and has not been refreshed since 2026-07-01. It is not the source of this metric.
- The PMS reported zero units on notice for this property through late June, so the figure reflects empty-and-unrented units only.
- total_units moves day to day, so the choice of reading date changes the result. Month-end is the convention applied here.
- Affected by the notice data gap before 2026-07-27 - see data_gaps. The June figure is arithmetically correct against the warehouse but the warehouse itself is missing the notice component.

**Open items**

- **`exposure_denominator`** (non-blocking) — This property excludes no units in June, so total_units and rentable are both 299 and the trace cannot distinguish them. Halo publishes a separate metric with metric_field 'rentable', which favours rentable as the unit base, but this is not yet proven.
  - *Evidence:* Halo metric_field 'rentable' exists in this same section (metric_id rentable).
  - *Evidence:* rentable = total_units - excluded; excluded = 0 at this property.
  - *Evidence:* The stale object t_occupancy_exposure_rate used total_units, not rentable.
  - *Resolution:* method: Trace Exposure Rate for this same property for July 2026. · why: From 2026-07-27 excluded = 1, so total_units = 315 and rentable = 314 diverge. · expected_if_rentable: 12.42% · expected_if_total_units: 12.38% · note: The two differ at two decimal places, so a single Halo figure settles it. Tracing Occupancy Trend for July 2026 resolves this and the trend formulation simultaneously. No property switch required.
  - *Also affects:* `pct_trend`

**Data gap refs.** `notice_fields_before_2026_07_27`

#### Net Move Ins — `net_move_ins`

Move-ins less move-outs over the reporting period. Net change in occupied units driven by resident turnover.

| Field | Value |
|---|---|
| Value | **-12** (-12, 0 dp) |
| Unit / direction | count · higher_is_better |
| Status | verified |
| Period | Jun, 2026 — period_total |
| Reading date | 2026-06-01 to 2026-06-30 |
| Source object | `t_ot_agg_resident_activity_property` (table, PMS, refresh daily) |
| Date column | `date` |
| Grain | `property_id`, `date`, `event_type` — summable event count |
| Pathway | `range_pivoted_difference` |
| Components | `positive_event` = `move_in`, `negative_event` = `move_out` |
| Aliases | net move ins, net move-ins, net turnover |
| Cache tier | 2 |

**Period note.** Accumulated across the whole period, unlike the point-in-time occupancy metrics.

**Component meanings**

- `move_in` — residents moving in
- `move_out` — residents moving out

**Aggregation.** Over time: `summable` · Across properties: `summable`

> A signed count. Safe to add across dates and properties, unlike the percentage metrics.

**Verification.** match · reported -12 · derived -12 · variance 0.0 pp · verified 2026-08-17

- *Method:* Pathway resolved and executed against the live warehouse; row grain inspected to rule out double counting.

**Constraints.** history starts 2025-01-01 · behaviour `accumulating`

> This object holds only two event types, move_in and move_out. The other resident-activity events in the Halo specification have no source.

**Notes**

- Halo files this metric under Occupancy, but it is sourced from the resident-activity object rather than the occupancy view.
- unit_types on each row is a descriptive comma-separated string, not a grain key.

**Open items**

- **`future_dated_events`** (non-blocking) — move_out rows extend beyond the current date at portfolio level, representing scheduled departures. June 2026 is fully past so this figure is unaffected, but whether Halo includes scheduled move-outs for an in-flight period is unconfirmed.

#### Rentable Units — `rentable`

The count of units at the property available to be rented, excluding units held back from inventory such as models, offices and offline units.

| Field | Value |
|---|---|
| Value | **299** (299, 0 dp) |
| Unit / direction | count · neutral |
| Status | verified |
| Period | Jun, 2026 — point_in_time_month_end |
| Reading date | 2026-06-30 |
| Source object | `t_oc_agg_occupancy_property` (view, PMS, refresh daily) |
| Date column | `as_of_date` |
| Grain | `property_id`, `as_of_date` — point-in-time stock |
| Pathway | `point_in_time_value` |
| Components | `field` = `rentable` |
| Aliases | rentable units, rentable |
| Cache tier | 2 |

**Period note.** A stock count read at the period end, not accumulated over the period.

**Component meanings**

- `rentable` — units available to be rented

**Aggregation.** Over time: `point_in_time_latest` · Across properties: `summable`

> A stock, not a flow. Add across properties for a portfolio count, but never across dates - take the latest reading instead.

**Verification.** match · reported 299 · derived 299 · variance 0.0 pp · verified 2026-08-17

- *Method:* Pathway resolved and executed against the live warehouse. Identity rentable = total_units - excluded independently confirmed (299 = 299 - 0).

**Constraints.** history starts 2026-06-30 · behaviour `accumulating`

> Daily readings in this object begin 2026-06-30. Daily June data does exist in t_occupancy_rate, but on a different unit base - see the object_disagreement conflict.

**Notes**

- Holds the identity rentable = total_units - excluded. At this property excluded is 0, so rentable and total_units are both 299.
- This field is the denominator candidate for pct_exposure. See that metric's open_items.
- The count is not stable over time: it reads 299 at 2026-06-30 and 327 by August. Period-end is the convention applied here.

#### Occupied Units — `occupied`

The count of rentable units with a resident in place at the period end, including residents who have given notice but not yet departed.

| Field | Value |
|---|---|
| Value | **266** (266, 0 dp) |
| Unit / direction | count · higher_is_better |
| Status | verified |
| Period | Jun, 2026 — point_in_time_month_end |
| Reading date | 2026-06-30 |
| Source object | `t_oc_agg_occupancy_property` (view, PMS, refresh daily) |
| Date column | `as_of_date` |
| Grain | `property_id`, `as_of_date` — point-in-time stock |
| Pathway | `point_in_time_value` |
| Components | `field` = `occupied` |
| Aliases | occupied units, units occupied |
| Cache tier | 2 |

**Period note.** A stock count read at the period end, not accumulated over the period.

**Component meanings**

- `occupied` — units with a resident in place

**Aggregation.** Over time: `point_in_time_latest` · Across properties: `summable`

> A stock, not a flow. Add across properties for a portfolio count, but never across dates - take the latest reading instead.

**Verification.** match · reported 266 · derived 266 · variance 0.0 pp · verified 2026-08-17

- *Method:* Pathway resolved and executed against the live warehouse. Two independent identities confirmed: rentable - vacant = 299 - 33 = 266, and occupied_no_notice + notice_rented + notice_unrented = 266 + 0 + 0 = 266.

**Constraints.** history starts 2026-06-30 · behaviour `accumulating`

> Daily readings in this object begin 2026-06-30. Daily June data does exist in t_occupancy_rate, but on a different unit base - see the object_disagreement conflict.

**Notes**

- Holds two identities: occupied = rentable - vacant, and occupied = occupied_no_notice + notice_rented + notice_unrented.
- Paired with rentable this yields an occupancy rate of 88.96% for June 2026 (266 / 299).
- Because excluded = 0 at this property, dividing by rentable or total_units gives the same rate; this metric does not resolve the pct_exposure denominator question either.

**Open items**

- **`occupied_vs_occupied_no_notice`** (non-blocking) — The PMS reported zero units on notice for this property through June, so occupied and occupied_no_notice both read 266. This trace cannot establish whether Halo's 'occupied' field includes units on notice. Disambiguation is cheap: the same property reports notice_unrented = 21 in August, where the two fields diverge.

#### Vacant Units — `vacant`

The count of rentable units with no resident in place at the period end, whether or not a future resident has already signed.

| Field | Value |
|---|---|
| Value | **33** (33, 0 dp) |
| Unit / direction | count · lower_is_better |
| Status | verified |
| Period | Jun, 2026 — point_in_time_month_end |
| Reading date | 2026-06-30 |
| Source object | `t_oc_agg_occupancy_property` (view, PMS, refresh daily) |
| Date column | `as_of_date` |
| Grain | `property_id`, `as_of_date` — point-in-time stock |
| Pathway | `point_in_time_value` |
| Components | `field` = `vacant` |
| Aliases | vacant units, vacancy, empty units |
| Cache tier | 2 |

**Period note.** A stock count read at the period end, not accumulated over the period.

**Component meanings**

- `vacant` — units with no resident in place

**Aggregation.** Over time: `point_in_time_latest` · Across properties: `summable`

> A stock, not a flow. Add across properties for a portfolio count, but never across dates - take the latest reading instead.

**Verification.** match · reported 33 · derived 33 · variance 0.0 pp · verified 2026-08-17

- *Method:* Pathway resolved and executed against the live warehouse. Two identities confirmed: vacant_rented + vacant_unrented = 16 + 17 = 33, and occupied + vacant = 266 + 33 = 299 = rentable.

**Constraints.** history starts 2026-06-30 · behaviour `accumulating`

> Daily readings in this object begin 2026-06-30. Daily June data does exist in t_occupancy_rate, but on a different unit base - see the object_disagreement conflict.

**Notes**

- Splits into vacant_rented = 16 (already leased to a future resident) and vacant_unrented = 17 (still needing a signature).
- Not the same as exposure. Exposure counts only the unrented portion plus units on notice, so vacant (33) exceeds available (17) here. Vacancy rate for June 2026 is 11.04% against 5.69% exposure.
- In June, available equals vacant_unrented exactly because notice_unrented is 0. In a period with notices on record the two diverge.

#### Excluded Units — `excluded`

The count of units held back from rentable inventory at the period end - models, offices, and units taken offline.

| Field | Value |
|---|---|
| Value | **0** (0, 0 dp) |
| Unit / direction | count · neutral |
| Status | verified |
| Period | Jun, 2026 — point_in_time_month_end |
| Reading date | 2026-06-30 |
| Source object | `t_oc_agg_occupancy_property` (view, PMS, refresh daily) |
| Date column | `as_of_date` |
| Grain | `property_id`, `as_of_date` — point-in-time stock |
| Pathway | `point_in_time_value` |
| Components | `field` = `excluded` |
| Aliases | excluded units, model down admin, offline units |
| Cache tier | 2 |

**Period note.** A stock count read at the period end, not accumulated over the period.

**Component meanings**

- `excluded` — units held back from rentable inventory

**Aggregation.** Over time: `point_in_time_latest` · Across properties: `summable`

> A stock, not a flow. Add across properties for a portfolio count, but never across dates - take the latest reading instead.

**Verification.** match · reported 0 · derived 0 · variance 0.0 pp · verified 2026-08-17 · strength `trivial_zero`

- *Method:* Pathway resolved and executed against the live warehouse. Because a zero matches trivially, field population was checked separately: 720 readings across 15 properties, zero nulls, portfolio range 0 to 3. The zero is a real zero, not an unpopulated field.
- *Caveat:* A reported value of 0 cannot distinguish a correct result from a broken pipeline. Confidence here rests on the separate population check, not on the match.

**Constraints.** history starts 2026-06-30 · behaviour `accumulating`

> Daily readings in this object begin 2026-06-30. Daily June data does exist in t_occupancy_rate, but on a different unit base - see the object_disagreement conflict.

**Notes**

- This is the aggregate of the Model, Down and Admin categories in the Halo specification. The warehouse retains only the total - no per-category breakdown exists, so those three metrics cannot be sourced.
- Holds the identity rentable = total_units - excluded.
- Not permanently zero at this property: excluded becomes 1 from 2026-07-27 onward. Portfolio-wide it never exceeds 3, so the rentable-versus-total_units denominator choice moves any rate by at most a few tenths of a point.

#### Occupancy Trend — `pct_trend`

Occupancy projected forward: units in place, less those on unrented notice, plus units already committed to a future resident.

| Field | Value |
|---|---|
| Value | **94.31%** (94.31, 2 dp) |
| Unit / direction | percent · higher_is_better |
| Status | verified_formulation_unresolved |
| Period | Jun, 2026 — point_in_time_month_end |
| Reading date | 2026-06-30 |
| Source object | `t_oc_agg_occupancy_property` (view, PMS, refresh daily) |
| Date column | `as_of_date` |
| Grain | `property_id`, `as_of_date` — point-in-time |
| Pathway | `point_in_time_adjusted_ratio` |
| Components | `base` = `occupied`, `subtract` = `notice_unrented`, `add` = `leased_future`, `denominator` = `total_units` |
| Denominator metric | `total_units` |
| Aliases | occupancy trend, trend, trended occupancy, % trend |
| Cache tier | 2 |

**Period note.** Despite the name, this is a point-in-time reading, not a movement over the period.

**Component meanings**

- `occupied` — units with a resident in place
- `notice_unrented` — units on notice with no replacement signed
- `leased_future` — units committed to a future resident
- `total_units` — total units at the property

**Aggregation.** Over time: `not_summable` · Across properties: `recompute_from_components`

> Do not add or average percentages. Sum the components across the group, then divide.

**Verification.** match · reported 94.31 · derived 94.3144 · variance 0.0 pp · verified 2026-08-17 · strength `ambiguous_formulation`

- *Method:* Pathway resolved and executed against the live warehouse.
- *Caveat:* Four distinct formulations all return 94.3144% for June 2026, because notice_rented and notice_unrented are both 0. The value is confirmed; the formula behind it is not.

**Constraints.** history starts 2026-06-30 · behaviour `accumulating`

> Daily readings in this object begin 2026-06-30. Daily June data does exist in t_occupancy_rate, but on a different unit base - see the object_disagreement conflict.

**Notes**

- The name is misleading: this is a point-in-time snapshot, not a trend over time. It carries no movement or time-series component.
- If formula 1 holds, pct_trend and pct_exposure are exact complements and must always sum to 100%.
- Confirmed identity at 2026-07-31: leased_future = vacant_rented + notice_rented (9 = 3 + 6).
- Affected by the notice data gap before 2026-07-27 - see data_gaps. The June figure is arithmetically correct against the warehouse but the warehouse itself is missing the notice component.

**Open items**

- **`trend_formulation`** (non-blocking) — Provisionally reassigned to the notice-adjusted projection after Leased Rate was traced with an identical June value. Not verified - June cannot distinguish any of the seven candidates.
  - *Resolution:* method: Trace Occupancy Trend for this same property for July 2026. · why: At 2026-07-31 notice_rented = 6 and notice_unrented = 19, so the four formulas separate. A single Halo figure identifies the formula and settles the denominator at the same time. · resolves: ['trend_formulation', 'exposure_denominator'] · expected_if_registered: 89.21% on total_units, 89.49% on rentable

**Data gap refs.** `notice_fields_before_2026_07_27`

**Revisions**

- date: 2026-08-17 · change: Registered formula changed from the exposure complement to the notice-adjusted projection. · reason: Leased Rate (pct_leased) was traced with the same June value and has the stronger claim to the exposure complement, which is the standard industry pairing. Two distinct metrics cannot share one formula, so the registry would otherwise be self-contradictory. · confidence: convention-based, not verified. July 2026 settles it.

#### Leased Rate — `pct_leased`

The share of units carrying a signed lease - occupied units less those on unrented notice, plus vacant units already leased to a future resident.

| Field | Value |
|---|---|
| Value | **94.31%** (94.31, 2 dp) |
| Unit / direction | percent · higher_is_better |
| Status | verified_formulation_unresolved |
| Period | Jun, 2026 — point_in_time_month_end |
| Reading date | 2026-06-30 |
| Source object | `t_oc_agg_occupancy_property` (view, PMS, refresh daily) |
| Date column | `as_of_date` |
| Grain | `property_id`, `as_of_date` — point-in-time |
| Pathway | `point_in_time_complement_ratio` |
| Components | `numerator` = `available`, `denominator` = `total_units` |
| Denominator metric | `total_units` |
| Aliases | leased rate, % leased, leased percentage |
| Cache tier | 2 |

**Period note.** A point-in-time reading at the period end.

**Component meanings**

- `available` — units available to lease
- `total_units` — total units at the property

**Aggregation.** Over time: `not_summable` · Across properties: `recompute_from_components`

> Do not add or average percentages. Sum the components across the group, then divide.

**Verification.** match · reported 94.31 · derived 94.3144 · variance 0.0 pp · verified 2026-08-17 · strength `ambiguous_formulation`

- *Method:* Pathway resolved and executed against the live warehouse.
- *Caveat:* Seven distinct formulations all return 94.3144% for June 2026 because notice_rented and notice_unrented are both 0. The value is confirmed; the formula is not.

**Constraints.** history starts 2026-06-30 · behaviour `accumulating`

> Daily readings in this object begin 2026-06-30. Daily June data does exist in t_occupancy_rate, but on a different unit base - see the object_disagreement conflict.

**Notes**

- Identical to Occupancy Trend for June 2026 (both 94.31%). The two are distinct Halo metrics, so they must diverge once notices are non-zero.
- If this registration holds, pct_leased and pct_exposure are exact complements and must always sum to 100%.
- Affected by the notice data gap before 2026-07-27 - see data_gaps. The June figure is arithmetically correct against the warehouse but the warehouse itself is missing the notice component.

**Open items**

- **`leased_formulation`** (non-blocking) — Registered as the exact complement of exposure, which is the standard industry pairing (Leased % = 100% - Exposure). Confirmed algebraically: occupied - notice_unrented + vacant_rented = total_units - available, so both expressions are the same formula.
  - *Resolution:* method: Trace Leased Rate for this same property for July 2026. · expected_if_registered: 87.62% on total_units, 87.58% on rentable · why: At 2026-07-31 notice_unrented = 19 and notice_rented = 6, so all seven candidates separate across 87.58% to 95.54%.

**Data gap refs.** `notice_fields_before_2026_07_27`

#### Total Units — `total_units`

The full unit count at the property at the period end, including units held back from rentable inventory.

| Field | Value |
|---|---|
| Value | **299** (299, 0 dp) |
| Unit / direction | count · neutral |
| Status | verified |
| Period | Jun, 2026 — point_in_time_month_end |
| Reading date | 2026-06-30 |
| Source object | `t_oc_agg_occupancy_property` (view, PMS, refresh daily) |
| Date column | `as_of_date` |
| Grain | `property_id`, `as_of_date` — point-in-time stock |
| Pathway | `point_in_time_value` |
| Components | `field` = `total_units` |
| Aliases | total units, unit count, units |
| Cache tier | 2 |

**Period note.** A stock count read at the period end, not accumulated over the period.

**Component meanings**

- `total_units` — full unit count including excluded units

**Aggregation.** Over time: `point_in_time_latest` · Across properties: `summable`

> A stock, not a flow. Add across properties for a portfolio count, but never across dates - take the latest reading instead.

**Verification.** match · reported 299 · derived 299 · variance 0.0 pp · verified 2026-08-17

- *Method:* Pathway resolved and executed against the live warehouse. Identity total_units - excluded = rentable confirmed (299 - 0 = 299).

**Constraints.** history starts 2026-06-30 · behaviour `accumulating`

> Daily readings in this object begin 2026-06-30. Daily June data does exist in t_occupancy_rate, but on a different unit base - see the object_disagreement conflict.

**Notes**

- This is the registered denominator for the three percentage metrics in this section, so its value propagates into Exposure Rate, Leased Rate and Occupancy Trend.
- Equals Rentable Units in June 2026 because excluded is 0. The two diverge from 2026-07-27, where total_units = 315 against rentable = 314. Unlike the rate metrics, the field mapping here is unambiguous: Halo's metric_field names the warehouse column directly.
- Not stable over time: 299 at 2026-06-30, 315 by 2026-07-31.

#### Average Occupancy Rate — `occupancy_rate`

The mean daily occupancy rate across the reporting period - occupied units divided by total units, averaged over every day recorded.

| Field | Value |
|---|---|
| Value | **93.59%** (93.59, 2 dp) |
| Unit / direction | percent · higher_is_better |
| Status | verified |
| Period | Jun, 2026 — period_average |
| Reading date | 2026-06-01 to 2026-06-30 |
| Source object | `t_occupancy_rate` (view, PMS, refresh daily) |
| Date column | `created_at` |
| Grain | `property_id`, `created_at` — daily rate observation |
| Pathway | `range_average_ratio` |
| Components | `numerator` = `occupied`, `denominator` = `units` |
| Aliases | average occupancy, avg occupancy, monthly occupancy, occupancy over the month |
| Cache tier | 2 |

**Period note.** A true period average, unlike the other rate metrics in this section which are point-in-time. Only 28 of June's 30 days are recorded; the object starts 2026-06-03.

**Component meanings**

- `occupied` — units with a resident in place
- `units` — unit count as recorded in this object

**Aggregation.** Over time: `mean_of_daily_rates` · Across properties: `recompute_from_components`

> Already an average over time. Do not average these across properties - pool the daily occupied and unit counts, then divide.

**Verification.** match · reported 93.59 · derived 93.5851 · variance 0.0 pp · verified 2026-08-17

- *Method:* Pathway resolved and executed against the live warehouse. 28 daily observations, units constant at 314, occupied summing to 8228.
- *Caveat:* Mean-of-daily-rates and pooled-ratio both return 93.5851% here because units is constant across the period. The two formulations would diverge if the unit count moved.

**Constraints.** history starts 2026-06-03 · behaviour `accumulating`

> This object records 28 of June's 30 days (2026-06-03 onward). The reported average is over days recorded, not days in the month.

**Notes**

- Sourced from a different object than every other Occupancy metric, on a different unit base: units = 314 here against total_units = 299 in t_oc_agg_occupancy_property for the same property.
- Not comparable to the point-in-time occupancy of 88.96% derived from occupied / total_units at the June 30 snapshot.
- The date column is created_at and lags by a day - the row for created_at 2026-06-30 was written 2026-07-01.

**Open items**

- **`average_formulation`** (non-blocking) — Registered as the mean of daily rates. A pooled ratio (sum of occupied over sum of units) gives an identical result for June because units never changes. A period where the unit count moves would distinguish them.

#### Current Occupancy Rate — `pct_occupied`

The share of units with a resident in place at the period end.

| Field | Value |
|---|---|
| Value | **88.96%** (88.96, 2 dp) |
| Unit / direction | percent · higher_is_better |
| Status | verified |
| Period | Jun, 2026 — point_in_time_month_end |
| Reading date | 2026-06-30 |
| Source object | `t_oc_agg_occupancy_property` (view, PMS, refresh daily) |
| Date column | `as_of_date` |
| Grain | `property_id`, `as_of_date` — point-in-time |
| Pathway | `point_in_time_ratio` |
| Components | `numerator` = `occupied`, `denominator` = `total_units` |
| Denominator metric | `total_units` |
| Aliases | current occupancy, occupancy rate, % occupied, occupancy |
| Cache tier | 2 |

**Period note.** A point-in-time reading at the period end, not a period average.

**Component meanings**

- `occupied` — units with a resident in place
- `total_units` — total units at the property

**Aggregation.** Over time: `not_summable` · Across properties: `recompute_from_components`

> Do not add or average percentages. Sum occupied and total_units across the group, then divide.

**Verification.** match · reported 88.96 · derived 88.9632 · variance 0.0 pp · verified 2026-08-17

- *Method:* Pathway resolved and executed against the live warehouse. Both operands are independently traced metrics: occupied = 266, total_units = 299.

**Constraints.** history starts 2026-06-30 · behaviour `accumulating`

> Daily readings in this object begin 2026-06-30. Daily June data does exist in t_occupancy_rate, but on a different unit base - see the object_disagreement conflict.

**Notes**

- Fully derivable from two traced metrics: occupied / total_units = 266 / 299. No new fields introduced.
- Reports 88.96% while Average Occupancy Rate reports 93.59% for the same property and period. The gap is not an error - the two draw on different objects with different unit bases (299 versus 314) and different semantics (month-end point-in-time versus 28-day average). See the object_disagreement conflict.
- Reuses the same pathway as Exposure Rate, differing only in the numerator.

**Open items**

- **`exposure_denominator`** (non-blocking) — Shares the unresolved rentable-versus-total_units question with the other rate metrics. June cannot distinguish them because excluded is 0.
  - *Resolution:* method: Trace Current Occupancy Rate for this property for July 2026. · expected_if_total_units: 92.38% · expected_if_rentable: 92.68% · why: At 2026-07-31 excluded = 1, so the two denominators diverge by 0.29 points.

#### Move-Ins — `move_ins`

The count of residents taking occupancy during the reporting period.

| Field | Value |
|---|---|
| Value | **11** (11, 0 dp) |
| Unit / direction | count · higher_is_better |
| Status | verified |
| Period | Jun, 2026 — period_total |
| Reading date | 2026-06-01 to 2026-06-30 |
| Source object | `t_ot_agg_resident_activity_property` (table, PMS, refresh daily) |
| Date column | `date` |
| Grain | `property_id`, `date`, `event_type` — summable event count |
| Pathway | `range_pivoted_sum` |
| Components | `event` = `move_in` |
| Aliases | move ins, move-ins |
| Cache tier | 2 |

**Period note.** Accumulated across the whole period.

**Component meanings**

- `move_in` — residents taking occupancy

**Aggregation.** Over time: `summable` · Across properties: `summable`

> An unsigned count. Safe to add across dates and properties.

**Verification.** match · reported 11 · derived 11 · variance 0.0 pp · verified 2026-08-17

- *Method:* Pathway resolved and executed against the live warehouse. 23 source rows in the period across both event types; no future-dated rows in this window.

**Constraints.** history starts 2003-08-01 · behaviour `accumulating`

> History extends back to 2003 from a backfill. This object holds only two event types, move_in and move_out.

**Notes**

- Unlike move_out, move_in rows are not future-dated in this period - zero rows fall beyond the current date, so no CURRENT_DATE guard is needed for June.
- Together with Move-Outs this is the component pair behind Net Move-Ins (11 - 23 = -12).

#### Move-Outs — `move_outs`

The count of residents vacating during the reporting period.

| Field | Value |
|---|---|
| Value | **23** (23, 0 dp) |
| Unit / direction | count · lower_is_better |
| Status | verified |
| Period | Jun, 2026 — period_total |
| Reading date | 2026-06-01 to 2026-06-30 |
| Source object | `t_ot_agg_resident_activity_property` (table, PMS, refresh daily) |
| Date column | `date` |
| Grain | `property_id`, `date`, `event_type` — summable event count |
| Pathway | `range_pivoted_sum` |
| Components | `event` = `move_out` |
| Aliases | move outs, move-outs, vacates |
| Cache tier | 2 |

**Period note.** Accumulated across the whole period.

**Component meanings**

- `move_out` — residents vacating

**Aggregation.** Over time: `summable` · Across properties: `summable`

> An unsigned count. Safe to add across dates and properties.

**Verification.** match · reported 23 · derived 23 · variance 0.0 pp · verified 2026-08-17

- *Method:* Pathway resolved and executed against the live warehouse.

**Constraints.** history starts 2025-01-01 · behaviour `accumulating`

> This object holds only two event types, move_in and move_out.

**Notes**

- Reuses the same pathway as Move-Ins, differing only in the event type.
- Exceeded Move-Ins by 12 in June, which is what drives the negative Net Move-Ins.

**Open items**

- **`future_dated_move_outs`** (non-blocking) — This event type carries scheduled future departures at portfolio level, where rows extend to 2026-09-30. At this property the latest move_out is 2026-08-12 with no rows beyond the current date, so the June figure is unaffected. A period ending today or later, or a property with scheduled departures on record, needs the CURRENT_DATE guard documented on the pathway.

#### Future Leases — `leased_future`

The count of units committed to a resident who has signed but not yet taken occupancy, whether the unit is currently vacant or occupied by someone on notice.

| Field | Value |
|---|---|
| Value | **16** (16, 0 dp) |
| Unit / direction | count · higher_is_better |
| Status | verified_source_gap |
| Period | Jun, 2026 — point_in_time_month_end |
| Reading date | 2026-06-30 |
| Source object | `t_oc_agg_occupancy_property` (view, PMS, refresh daily) |
| Date column | `as_of_date` |
| Grain | `property_id`, `as_of_date` — point-in-time stock |
| Pathway | `point_in_time_value` |
| Components | `field` = `leased_future` |
| Aliases | future leases, committed units, signed not moved in |
| Cache tier | 2 |

**Period note.** A stock count read at the period end, not accumulated over the period.

**Component meanings**

- `leased_future` — units committed to a future resident

**Aggregation.** Over time: `point_in_time_latest` · Across properties: `summable`

> A stock, not a flow. Add across properties for a portfolio count, but never across dates - take the latest reading instead.

**Verification.** match · reported 16 · derived 16 · variance 0.0 pp · verified 2026-08-17

- *Method:* Pathway resolved and executed against the live warehouse. Identity leased_future = vacant_rented + notice_rented confirmed at two readings: 16 = 16 + 0 at 2026-06-30, and 9 = 3 + 6 at 2026-07-31.

**Constraints.** history starts 2026-06-30 · behaviour `accumulating`

> Daily readings in this object begin 2026-06-30. Daily June data does exist in t_occupancy_rate, but on a different unit base - see the object_disagreement conflict.

**Notes**

- Holds the identity leased_future = vacant_rented + notice_rented. This is why formula 4 for Occupancy Trend coincided with the others in June - leased_future equalled vacant_rented only because notice_rented was 0.
- Equals Vacant Rented in June 2026 (both 16) but diverges at 2026-07-31 (9 against 3). Unlike the rate metrics, the field mapping is unambiguous: Halo's metric_field names the warehouse column directly.
- Supplies the 'add' component of the registered Occupancy Trend pathway.
- Affected by the notice data gap before 2026-07-27 - see data_gaps. June's 16 is the vacant_rented component alone; the notice_rented component was not captured.

**Open items**

- **`june_notice_data_gap`** (non-blocking) — Understates in June because notice_rented was not populated. The metric is arithmetically correct against the warehouse but the warehouse is missing one of its two components.

**Data gap refs.** `notice_fields_before_2026_07_27`

#### Available Units — `available`

The count of units open to be leased - vacant with no signed resident, plus units on notice with no replacement signed.

| Field | Value |
|---|---|
| Value | **17** (17, 0 dp) |
| Unit / direction | count · lower_is_better |
| Status | verified |
| Period | Jun, 2026 — point_in_time_month_end |
| Reading date | 2026-06-30 |
| Source object | `t_oc_agg_occupancy_property` (view, PMS, refresh daily) |
| Date column | `as_of_date` |
| Grain | `property_id`, `as_of_date` — point-in-time stock |
| Pathway | `point_in_time_value` |
| Components | `field` = `available` |
| Aliases | available units, units available to lease |
| Cache tier | 2 |

**Period note.** A stock count read at the period end, not accumulated over the period.

**Component meanings**

- `available` — units open to be leased

**Aggregation.** Over time: `point_in_time_latest` · Across properties: `summable`

> A stock, not a flow. Add across properties for a portfolio count, but never across dates - take the latest reading instead.

**Verification.** match · reported 17 · derived 17 · variance 0.0 pp · verified 2026-08-17

- *Method:* Pathway resolved and executed against the live warehouse. Identity available = vacant_unrented + notice_unrented confirmed at two readings: 17 = 17 + 0 at 2026-06-30, and 39 = 20 + 19 at 2026-07-31.

**Constraints.** history starts 2026-06-30 · behaviour `accumulating`

> Daily readings in this object begin 2026-06-30. Daily June data does exist in t_occupancy_rate, but on a different unit base - see the object_disagreement conflict.

**Notes**

- This is the numerator of both Exposure Rate and Leased Rate, so its value propagates into two published percentages.
- Equals Vacant Unrented in June 2026 (both 17) because notice_unrented is 0. At 2026-07-31 they diverge (39 against 20).
- Distinct from Vacant Units: vacant counts every empty unit including those already leased, so vacant (33) exceeds available (17) here.
- Affected by the notice data gap before 2026-07-27 - see data_gaps. The June figure is arithmetically correct against the warehouse but the warehouse itself is missing the notice component.

**Data gap refs.** `notice_fields_before_2026_07_27`

#### Notice Unrented Units — `notice_unrented`

The count of occupied units whose resident has given notice to vacate with no replacement resident signed.

| Field | Value |
|---|---|
| Value | **0** (0, 0 dp) |
| Unit / direction | count · lower_is_better |
| Status | verified_source_gap |
| Period | Jun, 2026 — point_in_time_month_end |
| Reading date | 2026-06-30 |
| Source object | `t_oc_agg_occupancy_property` (view, PMS, refresh daily) |
| Date column | `as_of_date` |
| Grain | `property_id`, `as_of_date` — point-in-time stock |
| Pathway | `point_in_time_value` |
| Components | `field` = `notice_unrented` |
| Aliases | notice unrented, on notice not rented |
| Cache tier | 2 |

**Period note.** A stock count read at the period end.

**Component meanings**

- `notice_unrented` — units on notice with no replacement signed

**Aggregation.** Over time: `point_in_time_latest` · Across properties: `summable`

> A stock, not a flow. Add across properties for a portfolio count, but never across dates.

**Verification.** match_but_source_incomplete · reported 0 · derived 0 · variance 0.0 pp · verified 2026-08-17 · strength `zero_from_data_gap`

- *Method:* Pathway resolved and executed against the live warehouse. The reported figure matches the warehouse exactly.
- *Caveat:* The match is real but the underlying value is not. Notice tracking begins 2026-07-27: on 2026-07-26 all 15 properties report 0 notice units, and on 2026-07-27 all 15 report non-zero, totalling 320 unrented and 81 rented. A simultaneous step change across every property is an integration start, not real-world behaviour. June's zero is an absence of measurement.

**Constraints.** history starts 2026-07-27 · behaviour `accumulating`

> The field exists from 2026-06-30 but is populated only from 2026-07-27. Readings before that date are structurally zero, not measured.

**Notes**

- Once live the field is stable, ranging 287 to 320 portfolio-wide across 2026-07-27 to 2026-08-16, so the zero period is clearly delimited rather than intermittent.
- At this property the first recorded value is 2026-07-27, reaching 19 by 2026-07-31 against 315 total units, about 6% of inventory.
- This gap is the root cause of the June rate degeneracy: with notice_unrented and notice_rented both zero, seven distinct formulations for Occupancy Trend and Leased Rate collapse to one value.

**Open items**

- **`june_notice_data_gap`** (blocking) — This metric is not fit for reporting for any period before 2026-07-27. Publishing 0 implies no residents gave notice at a 299-unit property for a full month, which is implausible.

#### Notice Rented Units — `notice_rented`

The count of occupied units whose resident has given notice to vacate and where a replacement resident has already signed.

| Field | Value |
|---|---|
| Value | **0** (0, 0 dp) |
| Unit / direction | count · higher_is_better |
| Status | verified_source_gap |
| Period | Jun, 2026 — point_in_time_month_end |
| Reading date | 2026-06-30 |
| Source object | `t_oc_agg_occupancy_property` (view, PMS, refresh daily) |
| Date column | `as_of_date` |
| Grain | `property_id`, `as_of_date` — point-in-time stock |
| Pathway | `point_in_time_value` |
| Components | `field` = `notice_rented` |
| Aliases | notice rented, on notice pre-leased |
| Cache tier | 2 |

**Period note.** A stock count read at the period end.

**Component meanings**

- `notice_rented` — units on notice with a replacement signed

**Aggregation.** Over time: `point_in_time_latest` · Across properties: `summable`

> A stock, not a flow. Add across properties for a portfolio count, but never across dates.

**Verification.** match_but_source_incomplete · reported 0 · derived 0 · variance 0.0 pp · verified 2026-08-17 · strength `zero_from_data_gap`

- *Method:* Pathway resolved and executed against the live warehouse. The reported figure matches the warehouse exactly.
- *Caveat:* Shares the notice data gap with notice_unrented. At this property the field steps from 0 on 2026-07-26 to 6 on 2026-07-27, the same day all 15 properties begin reporting. June's zero is an absence of measurement, not a measured zero.

**Constraints.** history starts 2026-07-27 · behaviour `accumulating`

> The field exists from 2026-06-30 but is populated only from 2026-07-27. Readings before that date are structurally zero, not measured.

**Notes**

- Portfolio-wide this field runs 78 to 89 once live, against 0 for the preceding 27 readings.
- Supplies one of the two components of Future Leases, so the June gap propagates there: leased_future = vacant_rented + notice_rented, and June's 16 is the vacant_rented component alone.

**Open items**

- **`june_notice_data_gap`** (blocking) — Not fit for reporting for any period before 2026-07-27.

**Data gap refs.** `notice_fields_before_2026_07_27`

#### Vacant Unrented Units — `vacant_unrented`

The count of empty units with no signed resident - units the leasing team still needs to fill.

| Field | Value |
|---|---|
| Value | **17** (17, 0 dp) |
| Unit / direction | count · lower_is_better |
| Status | verified |
| Period | Jun, 2026 — point_in_time_month_end |
| Reading date | 2026-06-30 |
| Source object | `t_oc_agg_occupancy_property` (view, PMS, refresh daily) |
| Date column | `as_of_date` |
| Grain | `property_id`, `as_of_date` — point-in-time stock |
| Pathway | `point_in_time_value` |
| Components | `field` = `vacant_unrented` |
| Aliases | vacant unrented, unrented vacant |
| Cache tier | 2 |

**Period note.** A stock count read at the period end, not accumulated over the period.

**Component meanings**

- `vacant_unrented` — empty units with no signed resident

**Aggregation.** Over time: `point_in_time_latest` · Across properties: `summable`

> A stock, not a flow. Add across properties for a portfolio count, but never across dates - take the latest reading instead.

**Verification.** match · reported 17 · derived 17 · variance 0.0 pp · verified 2026-08-17

- *Method:* Pathway resolved and executed against the live warehouse. Confirmed at two readings: 17 at 2026-06-30 and 20 at 2026-07-31.

**Constraints.** history starts 2026-06-30 · behaviour `accumulating`

> Daily readings in this object begin 2026-06-30. Daily June data does exist in t_occupancy_rate, but on a different unit base - see the object_disagreement conflict.

**Notes**

- Not affected by the notice data gap. This field is populated throughout and is the one component of Available Units that June actually captured.
- Equals Available Units in June (both 17) only because notice_unrented was unpopulated. At 2026-07-31 they diverge sharply: vacant_unrented 20 against available 39.
- Together with Vacant Rented this is the split of Vacant Units: 16 + 17 = 33 at 2026-06-30.

#### Vacant Rented Units — `vacant_rented`

The count of empty units already leased to a resident who has signed but not yet moved in.

| Field | Value |
|---|---|
| Value | **16** (16, 0 dp) |
| Unit / direction | count · higher_is_better |
| Status | verified |
| Period | Jun, 2026 — point_in_time_month_end |
| Reading date | 2026-06-30 |
| Source object | `t_oc_agg_occupancy_property` (view, PMS, refresh daily) |
| Date column | `as_of_date` |
| Grain | `property_id`, `as_of_date` — point-in-time stock |
| Pathway | `point_in_time_value` |
| Components | `field` = `vacant_rented` |
| Aliases | vacant rented, pre-leased vacant |
| Cache tier | 2 |

**Period note.** A stock count read at the period end, not accumulated over the period.

**Component meanings**

- `vacant_rented` — empty units already leased to a future resident

**Aggregation.** Over time: `point_in_time_latest` · Across properties: `summable`

> A stock, not a flow. Add across properties for a portfolio count, but never across dates - take the latest reading instead.

**Verification.** match · reported 16 · derived 16 · variance 0.0 pp · verified 2026-08-17

- *Method:* Pathway resolved and executed against the live warehouse. Confirmed at two readings: 16 at 2026-06-30 and 3 at 2026-07-31.

**Constraints.** history starts 2026-06-30 · behaviour `accumulating`

> Daily readings in this object begin 2026-06-30. Daily June data does exist in t_occupancy_rate, but on a different unit base - see the object_disagreement conflict.

**Notes**

- Not affected by the notice data gap; the field is populated throughout.
- This is the difference between Vacancy and Exposure. Of 33 vacant units in June, 16 were already leased, leaving 17 exposed.
- Equals Future Leases in June (both 16) only because notice_rented was unpopulated. At 2026-07-31 they diverge: vacant_rented 3 against leased_future 9.

#### Delayed Move Ins — `delayed_move_ins`

The count of scheduled move-ins that did not occur on their planned date.

| Field | Value |
|---|---|
| Value | **3** (3, 0 dp) |
| Unit / direction | count · lower_is_better |
| Status | verified |
| Period | Jun, 2026 — point_in_time_month_end |
| Reading date | 2026-06-30 |
| Source object | `t_oc_agg_occupancy_operational` (table, PMS, refresh daily) |
| Date column | `as_of_date` |
| Grain | `property_id`, `as_of_date` — point-in-time stock |
| Pathway | `point_in_time_value` |
| Components | `field` = `delayed_move_ins` |
| Aliases | delayed move ins, late move-ins |
| Cache tier | 2 |

**Period note.** Registered as a period-end reading. Only one June reading exists in this object, so a snapshot and a June total cannot be distinguished from this trace.

**Component meanings**

- `delayed_move_ins` — move-ins that missed their planned date

**Aggregation.** Over time: `point_in_time_latest` · Across properties: `summable`

> Registered as a stock. If it proves to be a daily flow, summing across dates would be correct instead - see open items.

**Verification.** match · reported 3 · derived 3 · variance 0.0 pp · verified 2026-08-17

- *Method:* Pathway resolved and executed against the live warehouse. Field is fully populated: 525 of 525 rows across all 15 properties.

**Constraints.** history starts 2026-06-30 · behaviour `stalled`

> This object last refreshed 2026-08-03. The occupancy view alongside it reaches 2026-08-16, so this object is roughly two weeks behind.

**Notes**

- Sourced from a third object, distinct from both the occupancy view and the daily rate view.
- The only populated measure in this object at this property and period. Turn time and lease-up lag are both null here with sample_n = 0.

**Open items**

- **`stock_or_flow`** (non-blocking) — Only one June reading exists, so it is not possible to tell whether Halo reads the period-end snapshot or sums daily values across the month. Registered as a snapshot. A period with several readings would distinguish them - if Halo's July figure exceeds the 2026-07-31 reading, it is summing.
  - *Resolution:* method: Compare Halo's July figure against the 2026-07-31 reading for this property.
- **`object_staleness`** (non-blocking) — The source object stopped refreshing on 2026-08-03 while its sibling objects reach 2026-08-16. Does not affect the June figure but will affect any current-period reporting from this object.

### Lead Generation

#### Newly Created Leads — `created_contact`

Distinct named contacts created during the period whose status is not Leased. The definition was reverse-engineered from the reported value, not supplied.

| Field | Value |
|---|---|
| Value | **92** (92, 0 dp) |
| Unit / direction | count · higher_is_better |
| Status | verified_definition_inferred |
| Period | Jun, 2026 — period_total |
| Reading date | 2026-06-01 to 2026-06-30 |
| Source object | `t_contact_activity` (BASE TABLE, Hyly + PMS, refresh daily) |
| Date column | `contact_created_date` |
| Grain | `property_id`, `contact_id` — one row per contact |
| Pathway | `range_distinct_count_excluding_status` |
| Components | `dedup_key` = `contact_name`, `status_field` = `contact_status`, `exclude_status` = `Leased` |
| Aliases | newly created leads, new leads, leads created |
| Cache tier | 2 |

**Component meanings**

- `contact_name` — deduplication key - distinct names, not distinct contact ids
- `contact_status` — current lifecycle status
- `Leased` — excluded status value

**Aggregation.** Over time: `not_summable` · Across properties: `not_summable`

> A distinct count cannot be summed across periods or properties without double counting - the same name may appear in more than one group. Re-run the distinct count over the wider scope instead.

**Verification.** match · reported 92 · derived 92 · verified 2026-08-17 · strength `definition_inferred_by_search_single_object`

- *Method:* Reproduced exactly after testing roughly forty populations across nine objects. Confirmed identical with raw and case-normalised name keys.
- *Caveat:* The value reproduces exactly, but the definition was found by search rather than supplied. Three independent choices each move the answer, so an exact match on a single figure is suggestive rather than conclusive.

**Constraints.** behaviour `accumulating`

> Sensitive to three independent choices, each of which changes the result.

**Notes**

- Placeholder contacts named 'do not answer lead' are present in the source and counted as leads before deduplication - a data-quality issue independent of this metric.
- hybrid_contact_created_date is a column, not a table. It appears in 24 objects across the two datasets.
- The purpose-built breakdown object t_pai_crstal_breakdown_by_source_medium is empty, so by-source metrics will need prospect_journey or this object instead.

**Open items**

- **`rule_inconsistent_with_scheduled_contact`** (non-blocking) — 1st Scheduled reproduces with distinct contact_id and no status filter, corroborated by three objects. Newly Created Leads requires distinct contact_name and a Leased exclusion, corroborated by one. The asymmetry suggests the created rule may be coincidental.
- **`confirm_inferred_definition`** (non-blocking) — Reproduces 92 for this property and period. Should be validated against a second property or period before the definition is treated as established.
- **`name_based_deduplication_is_lossy`** (non-blocking) — The 94-to-92 reduction comes from two name collisions. One is a genuine duplicate person entered twice (two contact ids, both Former Resident). The other is a placeholder, 'do not answer lead', appearing twice with different statuses - Active and Canceled Applicant.
- **`leased_exclusion_may_undercount`** (non-blocking) — Excluding contact_status = 'Leased' removes any lead that converted before the report was generated. A fast-converting lead created in June and leased in June is dropped from a 'newly created leads' count, which may not be intended.

#### 1st Scheduled — `scheduled_contact`

Distinct contacts who booked their first tour appointment during the period.

| Field | Value |
|---|---|
| Value | **33** (33, 0 dp) |
| Unit / direction | count · higher_is_better |
| Status | verified |
| Period | Jun, 2026 — period_total |
| Reading date | 2026-06-01 to 2026-06-30 |
| Source object | `t_contact_activity` (BASE TABLE, Hyly + PMS, refresh daily) |
| Date column | `first_scheduled_dt` |
| Grain | `property_id`, `contact_id` — one row per contact |
| Pathway | `range_distinct_count_by_milestone` |
| Components | `dedup_key` = `contact_id` |
| Aliases | 1st scheduled, first scheduled, tours booked, appointments set |
| Cache tier | 2 |

**Period note.** Event-based: counts the first-schedule milestone in the month it occurred, not the month the contact was created.

**Component meanings**

- `contact_id` — deduplication key - distinct contact ids

**Aggregation.** Over time: `not_summable` · Across properties: `not_summable`

> A distinct count. Re-run over the wider scope rather than summing, since the same contact may appear in more than one group.

**Verification.** match · reported 33 · derived 33 · verified 2026-08-17 · strength `corroborated_across_three_objects`

- *Method:* Reproduced from t_contact_activity and independently confirmed in two other objects.

**Constraints.** behaviour `accumulating`

> No status exclusion applies, unlike Newly Created Leads.

**Notes**

- Three independent objects agree at 33, which is materially stronger evidence than a single-object match.
- The pai_journey h_ms_ prefix denotes a milestone. The raw h_schedule_tour event gives 34, so the milestone convention is the one that aligns with first_scheduled_dt.
- Rule differs from Newly Created Leads: this metric deduplicates on contact_id and applies no status filter. See the leadgen_rule_inconsistency conflict.

#### 1st Toured — `toured_contact`

Distinct contacts who completed their first property tour during the period.

| Field | Value |
|---|---|
| Value | **19** (19, 0 dp) |
| Unit / direction | count · higher_is_better |
| Status | verified |
| Period | Jun, 2026 — period_total |
| Reading date | 2026-06-01 to 2026-06-30 |
| Source object | `t_contact_activity` (BASE TABLE, Hyly + PMS, refresh daily) |
| Date column | `first_completed_dt` |
| Grain | `property_id`, `contact_id` — one row per contact |
| Pathway | `range_distinct_count_by_milestone` |
| Components | `dedup_key` = `contact_id` |
| Aliases | 1st toured, first toured, tours completed |
| Cache tier | 2 |

**Period note.** Event-based: counts the tour-completed milestone in the month it occurred, not the month the contact was created.

**Component meanings**

- `contact_id` — deduplication key - distinct contact ids

**Aggregation.** Over time: `not_summable` · Across properties: `not_summable`

> A distinct count. Re-run over the wider scope rather than summing.

**Verification.** match · reported 19 · derived 19 · verified 2026-08-17 · strength `corroborated_across_three_objects`

- *Method:* Reproduced from t_contact_activity via first_completed_dt and independently confirmed in two other objects.

**Constraints.** behaviour `accumulating`

> Must read first_completed_dt. The similarly named first_toured_dt is entirely unpopulated.

**Notes**

- Field-name trap: first_toured_dt is entirely null at this property. The tour milestone is carried by first_completed_dt.
- Three independent objects agree at 19.
- Adding a Leased exclusion returns 0, meaning every contact who toured in June has since leased. This confirms the status filter is not applied to milestone metrics.
- Consistent with 1st Scheduled: contact_id dedup, no status filter, milestone-date basis. Two of three Lead Generation metrics now share one rule.

#### 1st Applied — `applied_contact`

Distinct contacts who submitted their first application during the period.

| Field | Value |
|---|---|
| Value | **14** (14, 0 dp) |
| Unit / direction | count · higher_is_better |
| Status | verified |
| Period | Jun, 2026 — period_total |
| Reading date | 2026-06-01 to 2026-06-30 |
| Source object | `t_contact_activity` (BASE TABLE, Hyly + PMS, refresh daily) |
| Date column | `first_application_dt` |
| Grain | `property_id`, `contact_id` — one row per contact |
| Pathway | `range_distinct_count_by_milestone` |
| Components | `dedup_key` = `contact_id` |
| Aliases | 1st applied, first applied, applications |
| Cache tier | 2 |

**Period note.** Event-based: counts the application milestone in the month it occurred, not the month the contact was created.

**Component meanings**

- `contact_id` — deduplication key - distinct contact ids

**Aggregation.** Over time: `not_summable` · Across properties: `not_summable`

> A distinct count. Re-run over the wider scope rather than summing.

**Verification.** match · reported 14 · derived 14 · verified 2026-08-17 · strength `corroborated_across_three_objects`

- *Method:* Reproduced from t_contact_activity and independently confirmed in two other objects. A fourth column, min_application_date, also returns 14.

**Constraints.** behaviour `accumulating`

> first_application_dt is populated (340 of 2,269 rows at this property).

**Notes**

- The most robust match in the section so far: four independent columns across three objects all return 14, and the dedup key makes no difference.
- Adding a Leased exclusion returns 3, so 11 of 14 June applicants have since leased. Further confirmation that the status filter is not applied to milestone metrics.
- Consistent with 1st Scheduled and 1st Toured: contact_id dedup, no status filter, milestone-date basis.

#### Total Applied — `total_applied_contact`

Total applications submitted during the period. In this warehouse it is indistinguishable from 1st Applied - see open items.

| Field | Value |
|---|---|
| Value | **14** (14, 0 dp) |
| Unit / direction | count · higher_is_better |
| Status | verified_structural_duplicate |
| Period | Jun, 2026 — period_total |
| Reading date | 2026-06-01 to 2026-06-30 |
| Source object | `t_contact_activity` (BASE TABLE, Hyly + PMS, refresh daily) |
| Date column | `first_application_dt` |
| Grain | `property_id`, `contact_id` — one row per contact |
| Pathway | `range_distinct_count_by_milestone` |
| Components | `dedup_key` = `contact_id` |
| Aliases | total applied, total applications, applications submitted |

**Period note.** Event-based: counts the application milestone in the month it occurred.

**Component meanings**

- `contact_id` — deduplication key - distinct contact ids

**Aggregation.** Over time: `not_summable` · Across properties: `not_summable`

> A distinct count. Re-run over the wider scope rather than summing.

**Verification.** match · reported 14 · derived 14 · verified 2026-08-17 · strength `matches_but_indistinguishable_from_applied_contact`

- *Method:* Reproduced via the same pathway as 1st Applied. Structural equivalence tested across 14 consecutive months.

**Constraints.** behaviour `accumulating`

> No application-level identifier exists: pms_application_id is null on all 14 rows. The warehouse has no grain finer than one application per contact.

**Notes**

- Returns the same value as 1st Applied for structural reasons, not because June happened to be quiet.
- The cohort reading gives 12 rather than 14, so two June applicants were created before June.
- Cancellations exist in prospect_journey (pms_CancelApplication, 3 contacts in June) but not in t_contact_activity.

**Open items**

- **`structural_duplicate_of_applied_contact`** (non-blocking) — 1st Applied and Total Applied resolve to the same pathway, the same object, the same column and the same dedup key. They returned identical values in all 14 months tested. If Halo intends Total Applied to count multiple applications per contact, that intent has no support in the warehouse - there is no application-level identifier.
- **`net_applied_has_no_source`** (non-blocking) — Net Applied requires application cancellations. first_cancelled_dt is null on every row of t_contact_activity for this property. prospect_journey does record pms_CancelApplication - 3 contacts in June 2026 - so a Net Applied of 11 would be derivable from that object, but it is a different source with a different grain and has not been verified against a Halo figure.

#### Net Applied — `net_applied_contact`

Applications submitted during the period less application cancellations recorded during the period. A net flow, not a survival rate.

| Field | Value |
|---|---|
| Value | **11** (11, 0 dp) |
| Unit / direction | count · higher_is_better |
| Status | verified |
| Period | Jun, 2026 — period_total |
| Reading date | 2026-06-01 to 2026-06-30 |
| Source object | `prospect_journey` (BASE TABLE, PMS, refresh daily) |
| Date column | `activity_dt` |
| Grain | `property_id`, `contact_id`, `event_type`, `activity_dt` — one row per contact event |
| Pathway | `range_distinct_difference_by_event` |
| Components | `positive_event` = `pms_Application`, `negative_event` = `pms_CancelApplication` |
| Aliases | net applied, net applications, applications net of cancellations |

**Period note.** Both components are counted in the period they occurred. The cancellations need not relate to the applications.

**Component meanings**

- `pms_Application` — application submitted
- `pms_CancelApplication` — application cancelled

**Aggregation.** Over time: `summable` · Across properties: `summable`

> A signed net flow of distinct-contact counts. Additive across periods and properties, unlike the distinct counts it is built from.

**Verification.** match · reported 11 · derived 11 · verified 2026-08-17 · strength `reproduced_and_decomposed`

- *Method:* Reproduced as 14 applications less 3 cancellations. Components verified independently and the cohort relationship tested.

**Constraints.** history starts 2023-09-26 · behaviour `accumulating`

> Must use prospect_journey. t_contact_activity carries first_cancelled_dt but it is null on every row for this property, so cancellations are unavailable there.

**Notes**

- Same shape as Net Move Ins: a difference of two independent period counts rather than a cohort outcome.
- Sourced from prospect_journey, the fifth object in the library and the second in the Lead Generation section.
- Cancellation data exists only in this object. first_cancelled_dt in t_contact_activity is null throughout.

**Open items**

- **`net_flow_not_survival_rate`** (non-blocking) — The figure is arithmetically 14 minus 3, but it does not mean 11 of June's applicants remain active - all 14 do. The 3 cancellations belong to earlier applications. A reader will almost certainly interpret 'Net Applied' as the former.

#### Leased — `leased_contact`

Distinct contacts who signed a lease during the period.

| Field | Value |
|---|---|
| Value | **8** (8, 0 dp) |
| Unit / direction | count · higher_is_better |
| Status | verified_single_source |
| Period | Jun, 2026 — period_total |
| Reading date | 2026-06-01 to 2026-06-30 |
| Source object | `pai_journey_1747307582311553649` (BASE TABLE, Hyly, refresh daily) |
| Date column | `event_date` |
| Grain | `property_id`, `contact_id`, `event_name`, `event_date` — one row per contact event |
| Pathway | `range_distinct_count_by_event` |
| Components | `dedup_key` = `contact_id`, `event_field` = `event_name`, `event` = `h_ms_lease` |
| Aliases | leased, leases signed, signed leases, conversions |

**Period note.** Event-based: counts the lease milestone in the month it occurred.

**Component meanings**

- `h_ms_lease` — lease milestone
- `event_name` — event column in this object

**Aggregation.** Over time: `not_summable` · Across properties: `not_summable`

> A distinct count. Re-run over the wider scope rather than summing.

**Verification.** match · reported 8 · derived 8 · verified 2026-08-17 · strength `single_source_with_cross_object_disagreement`

- *Method:* Reproduced from the lease milestone event. Four alternative sources were tested and none returns 8.
- *Caveat:* Unlike 1st Scheduled, 1st Toured and 1st Applied, which were each corroborated by three objects, this metric matches in one object only. Three independent sources report 9 and one reports 5.

**Constraints.** behaviour `accumulating`

> pms_first_leased_dt in t_contact_activity is null on all rows for this property, so the object used by the rest of the section cannot supply this metric.

**Notes**

- Sixth object in the library and the first metric registered against pai_journey.
- The h_ms_ milestone convention is now confirmed across three funnel stages, always one below the raw event.
- Funnel remains monotonic: 33 scheduled, 19 toured, 14 applied, 8 leased.

**Open items**

- **`cross_object_lease_disagreement`** (non-blocking) — Five sources give three different answers: 8 from the lease milestone, 9 from the raw lease event, PMS lease-sign event and lease_approved_date, and 5 from the conversion base. Halo matches the milestone, but the 9s agree with each other more than any of them agree with 8.

---

## Pathways

Reusable retrieval templates. The dataset is never named — the connector prefixes it from authenticated context at execution time.

| Pathway | Returns | Required placeholders |
|---|---|---|
| [`point_in_time_ratio`](#point_in_time_ratio) | single row, single numeric column named value | `object`, `property_id`, `period_end`, `numerator`, `denominator`, `date_column` |
| [`range_pivoted_difference`](#range_pivoted_difference) | single row, single numeric column named value | `object`, `property_id`, `period_start`, `period_end`, `positive_event`, `negative_event`, `date_column` |
| [`point_in_time_value`](#point_in_time_value) | single row, single column named value | `object`, `property_id`, `period_end`, `field`, `date_column` |
| [`point_in_time_complement_ratio`](#point_in_time_complement_ratio) | single row, single numeric column named value | `object`, `property_id`, `period_end`, `numerator`, `denominator`, `date_column` |
| [`point_in_time_adjusted_ratio`](#point_in_time_adjusted_ratio) | single row, single numeric column named value | `object`, `property_id`, `period_end`, `base`, `subtract`, `add`, `denominator`, `date_column` |
| [`range_average_ratio`](#range_average_ratio) | single row, single numeric column named value | `object`, `property_id`, `date_column`, `period_start`, `period_end`, `numerator`, `denominator` |
| [`range_pivoted_sum`](#range_pivoted_sum) | single row, single numeric column named value | `object`, `property_id`, `date_column`, `period_start`, `period_end`, `event` |
| [`range_distinct_count_excluding_status`](#range_distinct_count_excluding_status) | single row, single numeric column named value | `object`, `property_id`, `date_column`, `period_start`, `period_end`, `dedup_key`, `status_field`, `exclude_status` |
| [`range_distinct_count_by_milestone`](#range_distinct_count_by_milestone) | single row, single numeric column named value | `object`, `property_id`, `date_column`, `period_start`, `period_end`, `dedup_key` |
| [`range_distinct_difference_by_event`](#range_distinct_difference_by_event) | single row, single numeric column named value | `object`, `property_id`, `date_column`, `period_start`, `period_end`, `positive_event`, `negative_event` |
| [`range_distinct_count_by_event`](#range_distinct_count_by_event) | single row, single numeric column named value | `object`, `property_id`, `event_field`, `event`, `date_column`, `period_start`, `period_end`, `dedup_key` |
| [`ranked_breakdown_distinct_count`](#ranked_breakdown_distinct_count) | one row per dimension value: label and value, ordered descending | `object`, `dimension_field`, `dedup_expression`, `property_id`, `date_column`, `period_start`, `period_end`, `status_field`, `exclude_status` |
| [`ranked_breakdown_by_milestone`](#ranked_breakdown_by_milestone) | one row per dimension value: label and value, ordered descending | `object`, `dimension_field`, `dedup_expression`, `property_id`, `date_column`, `period_start`, `period_end` |
| [`ranked_breakdown_distinct_difference_by_event`](#ranked_breakdown_distinct_difference_by_event) | one row per dimension value: label and net value, ordered descending | `object`, `dimension_object`, `dimension_field`, `event_field`, `positive_event`, `negative_event`, `property_id`, `date_column`, `period_start`, `period_end` |
| [`ranked_breakdown_by_event_joined_dimension`](#ranked_breakdown_by_event_joined_dimension) | one row per dimension value: label and value, ordered descending | `object`, `dimension_object`, `dimension_field`, `event_field`, `event`, `property_id`, `date_column`, `period_start`, `period_end` |

### point_in_time_ratio

Ratio of two counts taken from the newest daily snapshot on or before the period end.

```sql
SELECT {numerator} / {denominator} AS value
FROM   `{object}`
WHERE  property_id = {property_id}
  AND  {date_column} <= {period_end}
ORDER BY {date_column} DESC
LIMIT  1
```

**Returns:** single row, single numeric column named value

- Uses <= period_end with ORDER BY DESC rather than an exact date match, because daily history does not extend to every date. An exact match would return zero rows instead of a visible error.
- Compare the returned as_of_date against period_end to detect a substituted reading.
- The FROM clause names the object only. The connector prefixes the authenticated dataset at execution time.

### range_pivoted_difference

Net difference between two event types accumulated over the reporting period, read from a long-format event table.

```sql
SELECT SUM(IF(event_type = '{positive_event}', count, 0))
     - SUM(IF(event_type = '{negative_event}', count, 0)) AS value
FROM   `{object}`
WHERE  property_id = {property_id}
  AND  {date_column} BETWEEN {period_start} AND {period_end}
```

**Returns:** single row, single numeric column named value

- The source table is long-format: one row per property, date and event type, with count already aggregated across unit types. Summing count does not double-count.
- This table carries future-dated scheduled events. For a period ending today or later, add AND date <= CURRENT_DATE() to exclude events that have not yet occurred.
- The FROM clause names the object only. The connector prefixes the authenticated dataset at execution time.

### point_in_time_value

A single stored field read from the newest daily snapshot on or before the period end. No arithmetic applied.

```sql
SELECT {field} AS value
FROM   `{object}`
WHERE  property_id = {property_id}
  AND  {date_column} <= {period_end}
ORDER BY {date_column} DESC
LIMIT  1
```

**Returns:** single row, single column named value

- Uses <= period_end with ORDER BY DESC rather than an exact date match, because daily history does not extend to every date.
- Compare the returned as_of_date against period_end to detect a substituted reading.
- The FROM clause names the object only. The connector prefixes the authenticated dataset at execution time.

### point_in_time_complement_ratio

One minus a ratio, taken from the newest daily snapshot on or before the period end. The complement of a point_in_time_ratio metric.

```sql
SELECT ({denominator} - {numerator}) / {denominator} AS value
FROM   `{object}`
WHERE  property_id = {property_id}
  AND  {date_column} <= {period_end}
ORDER BY {date_column} DESC
LIMIT  1
```

**Returns:** single row, single numeric column named value

- Uses <= period_end with ORDER BY DESC rather than an exact date match, because daily history does not extend to every date.
- The FROM clause names the object only. The connector prefixes the authenticated dataset at execution time.

### point_in_time_adjusted_ratio

A rate whose numerator adjusts a base stock by subtracting one field and adding another, taken from the newest daily snapshot on or before the period end.

```sql
SELECT ({base} - {subtract} + {add}) / {denominator} AS value
FROM   `{object}`
WHERE  property_id = {property_id}
  AND  {date_column} <= {period_end}
ORDER BY {date_column} DESC
LIMIT  1
```

**Returns:** single row, single numeric column named value

- The FROM clause names the object only. The connector prefixes the authenticated dataset at execution time.

### range_average_ratio

Mean of a daily ratio across every day recorded in the reporting period.

```sql
SELECT AVG({numerator} / {denominator}) AS value
FROM   `{object}`
WHERE  property_id = {property_id}
  AND  {date_column} BETWEEN {period_start} AND {period_end}
```

**Returns:** single row, single numeric column named value

- Averages only the days present. If the object is missing days, the divisor is the days recorded, not the days in the period.
- The FROM clause names the object only. The connector prefixes the authenticated dataset at execution time.

### range_pivoted_sum

Total count of one event type accumulated over the reporting period, read from a long-format event table.

```sql
SELECT SUM(IF(event_type = '{event}', count, 0)) AS value
FROM   `{object}`
WHERE  property_id = {property_id}
  AND  {date_column} BETWEEN {period_start} AND {period_end}
```

**Returns:** single row, single numeric column named value

- The source table is long-format: one row per property, date and event type, with count already aggregated across unit types. Summing count does not double-count.
- This table carries future-dated scheduled events. For a period ending today or later, add AND {date_column} <= CURRENT_DATE() to exclude events that have not yet occurred.
- The FROM clause names the object only. The connector prefixes the authenticated dataset at execution time.

### range_distinct_count_excluding_status

Distinct count of a deduplication key over the reporting period, excluding one status value.

```sql
SELECT COUNT(DISTINCT {dedup_key}) AS value
FROM   `{object}`
WHERE  property_id = {property_id}
  AND  {date_column} BETWEEN {period_start} AND {period_end}
  AND  {status_field} != '{exclude_status}'
```

**Returns:** single row, single numeric column named value

- Deduplicating on a name field is inherently lossy - it collapses distinct people who share a name. Confirm this is intended before relying on it.
- The FROM clause names the object only. The connector prefixes the authenticated dataset at execution time.

### range_distinct_count_by_milestone

Distinct count of contacts whose milestone timestamp falls within the reporting period. Event-based, not cohort-based.

```sql
SELECT COUNT(DISTINCT {dedup_key}) AS value
FROM   `{object}`
WHERE  property_id = {property_id}
  AND  DATE({date_column}) BETWEEN {period_start} AND {period_end}
```

**Returns:** single row, single numeric column named value

- Counts the milestone in the period it occurred, regardless of when the contact was created. A contact created in May whose first tour is booked in June counts in June.
- The DATE() cast is required because milestone fields are DATETIME while the period bounds are DATE.
- The FROM clause names the object only. The connector prefixes the authenticated dataset at execution time.

### range_distinct_difference_by_event

Net difference between distinct contacts recording two event types over the reporting period, read from an event-grain object.

```sql
SELECT COUNT(DISTINCT IF(event_type = '{positive_event}', contact_id, NULL))
     - COUNT(DISTINCT IF(event_type = '{negative_event}', contact_id, NULL)) AS value
FROM   `{object}`
WHERE  property_id = {property_id}
  AND  DATE({date_column}) BETWEEN {period_start} AND {period_end}
```

**Returns:** single row, single numeric column named value

- The two event sets are independent. A negative event in the period need not correspond to a positive event in the same period, so the result is a period net flow, not a cohort survival rate.
- The FROM clause names the object only. The connector prefixes the authenticated dataset at execution time.
- One row per event in this object, so distinct contact counting is required - there is no pre-aggregated count column.

### range_distinct_count_by_event

Distinct contacts recording a single named event within the reporting period, read from an event-grain object.

```sql
SELECT COUNT(DISTINCT {dedup_key}) AS value
FROM   `{object}`
WHERE  property_id = {property_id}
  AND  {event_field} = '{event}'
  AND  {date_column} BETWEEN {period_start} AND {period_end}
```

**Returns:** single row, single numeric column named value

- The h_ms_ prefix in this object denotes a milestone. Raw events without the prefix return higher counts - h_ms_lease 8 against h_lease 9, h_ms_tour 19 against h_tour 20, h_ms_schedule_tour 33 against h_schedule_tour 34. Always use the milestone form.
- The FROM clause names the object only. The connector prefixes the authenticated dataset at execution time.

### ranked_breakdown_distinct_count

Ranked breakdown of a distinct count across a dimension, with share of a declared denominator.

```sql
SELECT {dimension_field} AS label,
       COUNT(DISTINCT {dedup_expression}) AS value
FROM   `{object}`
WHERE  property_id = {property_id}
  AND  {date_column} BETWEEN {period_start} AND {period_end}
  AND  {status_field} != '{exclude_status}'
GROUP BY label
ORDER BY value DESC
```

**Returns:** one row per dimension value: label and value, ordered descending

- Rank is assigned by the consumer from the ordered result, not by the query.
- Share is computed against a declared denominator which may differ from the sum of the returned values - see share_denominator on the breakdown.
- The FROM clause names the object only. The connector prefixes the authenticated dataset at execution time.

### ranked_breakdown_by_milestone

Ranked breakdown of distinct contacts across a dimension, filtered on a milestone timestamp. No status exclusion.

```sql
SELECT {dimension_field} AS label,
       COUNT(DISTINCT {dedup_expression}) AS value
FROM   `{object}`
WHERE  property_id = {property_id}
  AND  DATE({date_column}) BETWEEN {period_start} AND {period_end}
GROUP BY label
ORDER BY value DESC
```

**Returns:** one row per dimension value: label and value, ordered descending

- Rank is assigned by the consumer from the ordered result, not by the query.
- The DATE() cast is required because milestone fields are DATETIME while period bounds are DATE.
- The FROM clause names the object only. The connector prefixes the authenticated dataset at execution time.

### ranked_breakdown_distinct_difference_by_event

Ranked breakdown of a net event difference across a dimension held on a second object, joined on contact.

```sql
WITH ev AS (
  SELECT contact_id, {event_field}
  FROM   `{object}`
  WHERE  property_id = {property_id}
    AND  {event_field} IN ('{positive_event}','{negative_event}')
    AND  DATE({date_column}) BETWEEN {period_start} AND {period_end}
),
src AS (
  SELECT contact_id, ANY_VALUE({dimension_field}) AS label
  FROM   `{dimension_object}`
  WHERE  property_id = {property_id}
  GROUP BY contact_id
)
SELECT s.label,
       COUNT(DISTINCT IF(ev.{event_field}='{positive_event}', ev.contact_id, NULL))
     - COUNT(DISTINCT IF(ev.{event_field}='{negative_event}', ev.contact_id, NULL)) AS value
FROM   ev LEFT JOIN src s USING (contact_id)
GROUP BY s.label
ORDER BY value DESC
```

**Returns:** one row per dimension value: label and net value, ordered descending

- The only pathway in the library spanning two objects. The event object holds no marketing attribution, so the dimension is joined from the contact object on contact_id.
- A net value can be zero or negative when cancellations equal or exceed applications for a source. Zero rows still appear and still consume a rank.
- The FROM clauses name objects only. The connector prefixes the authenticated dataset at execution time.

### ranked_breakdown_by_event_joined_dimension

Ranked breakdown of distinct contacts recording a named event, with the dimension joined from a second object on contact.

```sql
WITH ev AS (
  SELECT DISTINCT contact_id
  FROM   `{object}`
  WHERE  property_id = {property_id}
    AND  {event_field} = '{event}'
    AND  {date_column} BETWEEN {period_start} AND {period_end}
),
src AS (
  SELECT contact_id, ANY_VALUE({dimension_field}) AS label
  FROM   `{dimension_object}`
  WHERE  property_id = {property_id}
  GROUP BY contact_id
)
SELECT s.label,
       COUNT(DISTINCT ev.contact_id) AS value
FROM   ev LEFT JOIN src s USING (contact_id)
GROUP BY s.label
ORDER BY value DESC
```

**Returns:** one row per dimension value: label and value, ordered descending

- Second cross-object pathway. The event object holds no marketing attribution, so the dimension is joined from the contact object on contact_id.
- A LEFT JOIN is used deliberately so that any event contact without a matching source surfaces as a null label rather than being silently dropped.
- The FROM clauses name objects only. The connector prefixes the authenticated dataset at execution time.

---

## Placeholders

| Placeholder | Type | Filled from | Meaning | Resolution |
|---|---|---|---|---|
| `object` | `identifier` | `metric` | Warehouse object to read from | Take verbatim from the metric's source.object |
| `property_id` | `int64` | `context` | Internal property identifier | 19-digit warehouse identifier - not the PMS or Yardi property code |
| `period_start` | `date` | `context` | First day of the reporting period | Supply as DATE 'YYYY-MM-DD'. Used by period-total pathways only. |
| `period_end` | `date` | `context` | Last day of the reporting period | Supply as DATE 'YYYY-MM-DD' |
| `numerator` | `column` | `components` | Numerator field for a ratio metric | Read from the metric's query.components |
| `denominator` | `column` | `components` | Denominator field for a ratio metric | Read from the metric's query.components |
| `positive_event` | `string` | `components` | Event type counted as an increase | Read from the metric's query.components |
| `negative_event` | `string` | `components` | Event type counted as a decrease | Read from the metric's query.components |
| `field` | `column` | `components` | The single field carrying the metric value | Read from the metric's query.components |
| `base` | `column` | `components` | Base stock field before adjustment | Read from the metric's query.components |
| `subtract` | `column` | `components` | Field deducted from the base | Read from the metric's query.components |
| `add` | `column` | `components` | Field added to the base | Read from the metric's query.components |
| `date_column` | `identifier` | `source` | The date column of the source object | Read from the metric's source.date_column. Objects differ: as_of_date, date, created_at. |
| `event` | `string` | `components` | The event type to count | Read from the metric's query.components |
| `dedup_key` | `column` | `components` | Field used to deduplicate the population | Read from the metric's query.components |
| `status_field` | `column` | `components` | Status field carrying the exclusion | Read from the metric's query.components |
| `exclude_status` | `string` | `components` | Status value excluded from the count | Read from the metric's query.components |
| `event_field` | `identifier` | `components` | Column holding the event name or type | Read from the metric's query.components. Objects differ: event_name, event_type. |
| `dimension_field` | `expression` | `components` | Column holding the dimension values | Read from the breakdown's query.components |
| `dedup_expression` | `expression` | `components` | Expression used to deduplicate within each dimension value | Read from the breakdown's query.components |
| `dimension_object` | `identifier` | `components` | Object holding the dimension, joined on contact_id | Read from the breakdown's query.components |

---

## Breakdowns

14 ranked dimensional breakdowns of parent metrics.

### Newly Created Leads by Lead Gen Sources — `created_contact_by_source`

| Field | Value |
|---|---|
| Section | Lead Generation |
| Parent metric | `created_contact` |
| Dimension | Lead Gen Sources — field `mta_first_source_name` |
| Direction | Top |
| Data source label | All |
| Status | verified_dedup_inconsistent_with_parent |
| Period | Jun, 2026 — period_total (2026-06-01 to 2026-06-30) |
| Source object | `t_contact_activity` (BASE TABLE, Hyly + PMS, refresh daily) |
| Date column | `contact_created_date` |
| Grain | `property_id`, `contact_id` — one row per contact |
| Pathway | `ranked_breakdown_distinct_count` |
| Components | `dimension_field` = `mta_first_source_name`, `dedup_expression` = `CONCAT(contact_name,'\|',IFNULL(contact_sub_status,''))`, `status_field` = `contact_status`, `exclude_status` = `Leased` |
| Share denominator | 92 (parent metric created_contact; not the sum of the breakdown values, which is 93) |
| Share rounding | One decimal place, displayed with two. See share_rounding_rule at registry level. |

| Rank | Label | Value | Share |
|---|---|---|---|
| 1 | Property Website | 53 | 57.60% |
| 2 | Google My Business/Maps | 13 | 14.10% |
| 3 | Google PayPerClick (PPC) | 7 | 7.60% |
| 4 | Zillow | 5 | 5.40% |
| 5 | Apple Maps | 4 | 4.30% |
| 5 | Google.com | 4 | 4.30% |
| 5 | Walking / Driving By | 4 | 4.30% |
| 8 | ApartmentList.com | 1 | 1.10% |
| 8 | Bing | 1 | 1.10% |
| 8 | Social Posting | 1 | 1.10% |

**Verification**

- *status:* match
- *verification_strength:* rank_1_verified_exactly
- *variance:* 0
- *verified_on:* 2026-08-17
- *method:* Full ten-row breakdown derived. Rank 1 label, value and share all reproduce.
- *why_53:* Two name collisions exist and both fall inside Property Website. Joshua Stubbs appears twice with the same sub-status, Former Resident, so it collapses. Do Not Answer Lead appears twice with different sub-statuses, Active and Canceled Applicant, so it does not. 54 rows less one collapse gives 53.

**Notes**

- Rank is not stable at ties: Apple Maps, Google.com and Walking / Driving By all hold 4, and ApartmentList.com, Bing and Social Posting all hold 1. Ranks 5 to 7 and 8 to 10 are interchangeable without a declared tie-break. Alphabetical order was applied here.
- Ten source values in total, so Top and Bottom of this dimension are drawn from the same ten rows.
- The dimension field is mta_first_source_name, first-touch attribution. pms_source_name gives a materially different picture at 30 for Property Website.

**Open items**

- *item:* dedup_key_differs_from_parent · *blocking:* no · *detail:* The parent metric deduplicates on contact_name alone and totals 92. This breakdown deduplicates on contact_name plus contact_sub_status and totals 93. The two rules disagree by one contact. · *impact:* The breakdown does not sum to the metric it breaks down. · *recommendation:* Align the two rules. Using name plus sub-status for both would make the parent 93; using name alone for both would make Property Website 52.
- *item:* share_denominator_is_not_the_breakdown_sum · *blocking:* no · *detail:* Shares are computed against the parent value of 92 while the breakdown sums to 93, so the shares total 101.09% rather than 100%. · *recommendation:* State the denominator on the report, or reconcile the dedup rules so the two agree.
- *item:* share_truncated_not_rounded · *blocking:* no · *detail:* 53/92 is 57.6087%. Reported as 57.60%, which is truncation. Standard rounding gives 57.61%. · *recommendation:* Confirm truncation is intended; it will understate every share by up to 0.01 points.
- *item:* placeholder_contact_counted_as_a_lead · *blocking:* no · *detail:* Do Not Answer Lead contributes two of Property Website's 53. It is a placeholder, not a prospect, and the differing sub-statuses are what keep both rows in the count. · *recommendation:* Exclude placeholder names upstream. This inflates the top-ranked source specifically.

### 1st Scheduled by Lead Gen Sources — `scheduled_contact_by_source`

| Field | Value |
|---|---|
| Section | Lead Generation |
| Parent metric | `scheduled_contact` |
| Dimension | Lead Gen Sources — field `mta_first_source_name` |
| Direction | Top |
| Data source label | All |
| Status | verified |
| Period | Jun, 2026 — period_total (2026-06-01 to 2026-06-30) |
| Source object | `t_contact_activity` (BASE TABLE, Hyly + PMS, refresh daily) |
| Date column | `first_scheduled_dt` |
| Grain | `property_id`, `contact_id` — one row per contact |
| Pathway | `ranked_breakdown_by_milestone` |
| Components | `dimension_field` = `mta_first_source_name`, `dedup_expression` = `contact_id` |
| Share denominator | 33 (parent metric scheduled_contact) |
| Share rounding | One decimal place, displayed with two. See share_rounding_rule at registry level. |

| Rank | Label | Value | Share |
|---|---|---|---|
| 1 | Property Website | 10 | 30.30% |
| 2 | Google.com | 6 | 18.20% |
| 2 | Zillow | 6 | 18.20% |
| 4 | Google PayPerClick (PPC) | 5 | 15.20% |
| 5 | Walking / Driving By | 4 | 12.10% |
| 6 | Apple Maps | 1 | 3.00% |
| 6 | Bing | 1 | 3.00% |

**Verification**

- *status:* match
- *verification_strength:* rank_1_verified_and_breakdown_reconciles_to_parent
- *variance:* 0
- *verified_on:* 2026-08-17
- *method:* Full seven-row breakdown derived. Rank 1 label, value and share all reproduce, and the rows sum to the parent metric.
- *note:* contact_id and name-plus-sub-status both return 10 here, so this breakdown cannot distinguish them. contact_id is registered because it matches the parent metric's rule.

**Notes**

- Reconciles cleanly to its parent, unlike the Newly Created Leads breakdown which sums to 93 against a parent of 92.
- Seven source values against ten for Newly Created Leads: Google My Business/Maps, ApartmentList.com and Social Posting produced leads in June but no scheduled tours.
- Ranks 2 and 3 are tied at 6, and ranks 6 and 7 are tied at 1. Alphabetical order applied.
- Property Website converts leads to scheduled tours at a materially lower rate than its lead share: 57.60% of leads but 30.30% of first schedules.

### 1st Toured by Lead Gen Sources — `toured_contact_by_source`

| Field | Value |
|---|---|
| Section | Lead Generation |
| Parent metric | `toured_contact` |
| Dimension | Lead Gen Sources — field `mta_first_source_name` |
| Direction | Top |
| Data source label | All |
| Status | verified |
| Period | Jun, 2026 — period_total (2026-06-01 to 2026-06-30) |
| Source object | `t_contact_activity` (BASE TABLE, Hyly + PMS, refresh daily) |
| Date column | `first_completed_dt` |
| Grain | `property_id`, `contact_id` — one row per contact |
| Pathway | `ranked_breakdown_by_milestone` |
| Components | `dimension_field` = `mta_first_source_name`, `dedup_expression` = `contact_id` |
| Share denominator | 19 (parent metric toured_contact) |
| Share rounding | One decimal place, displayed with two. See share_rounding_rule at registry level. |

| Rank | Label | Value | Share |
|---|---|---|---|
| 1 | Property Website | 6 | 31.60% |
| 2 | Google.com | 4 | 21.10% |
| 3 | Walking / Driving By | 3 | 15.80% |
| 4 | Google PayPerClick (PPC) | 2 | 10.50% |
| 4 | Zillow | 2 | 10.50% |
| 6 | Apple Maps | 1 | 5.30% |
| 6 | Bing | 1 | 5.30% |

**Verification**

- *status:* match
- *verification_strength:* rank_1_verified_and_breakdown_reconciles_to_parent
- *variance:* 0
- *verified_on:* 2026-08-17
- *method:* Full seven-row breakdown derived. Rank 1 label, value and share reproduce, and the rows sum to the parent metric.
- *note:* All three dedup keys agree at 6, so this breakdown cannot distinguish them. contact_id is registered to match the parent rule.
- *rounding_significance:* This breakdown is what disproved 2dp truncation. 6/19 is 31.5789%: truncation gives 31.57%, 2dp rounding gives 31.58%, and only 1dp rounding gives the reported 31.60%.

**Notes**

- Reconciles to its parent, like 1st Scheduled and unlike Newly Created Leads.
- Seven sources, the same set as 1st Scheduled.
- Property Website's share continues to fall through the funnel: 57.60% of leads, 30.30% of schedules, 31.60% of tours.
- Zillow drops sharply from 18.20% of schedules to 10.50% of tours - six schedules produced two completed tours.

### 1st Applied by Lead Gen Sources — `applied_contact_by_source`

| Field | Value |
|---|---|
| Section | Lead Generation |
| Parent metric | `applied_contact` |
| Dimension | Lead Gen Sources — field `mta_first_source_name` |
| Direction | Top |
| Data source label | All |
| Status | verified |
| Period | Jun, 2026 — period_total (2026-06-01 to 2026-06-30) |
| Source object | `t_contact_activity` (BASE TABLE, Hyly + PMS, refresh daily) |
| Date column | `first_application_dt` |
| Grain | `property_id`, `contact_id` — one row per contact |
| Pathway | `ranked_breakdown_by_milestone` |
| Components | `dimension_field` = `mta_first_source_name`, `dedup_expression` = `contact_id` |
| Share denominator | 14 (parent metric applied_contact) |
| Share rounding | One decimal place, displayed with two. See share_rounding_rule at registry level. |

| Rank | Label | Value | Share |
|---|---|---|---|
| 1 | Property Website | 10 | 71.40% |
| 2 | Zillow | 2 | 14.30% |
| 3 | Apple Maps | 1 | 7.10% |
| 3 | Google My Business/Maps | 1 | 7.10% |

**Verification**

- *status:* match
- *verification_strength:* rank_1_verified_and_breakdown_reconciles_to_parent
- *variance:* 0
- *verified_on:* 2026-08-17
- *method:* Full four-row breakdown derived. Rank 1 label, value and share reproduce, and the rows sum to the parent metric.
- *rounding_significance:* Independently confirms the one-decimal rule. 10/14 is 71.4286%: two-decimal rounding gives 71.43%, truncation gives 71.42%, and only one-decimal rounding gives the reported 71.40%.

**Notes**

- Reconciles to its parent. Three of four breakdowns now reconcile; only Newly Created Leads does not.
- Four sources only, down from seven at the tour stage. Google.com, Walking / Driving By, Google PayPerClick and Bing produced tours in June but no applications.
- Property Website's share jumps sharply at this stage: 57.60% of leads, 30.30% of schedules, 31.60% of tours, 71.40% of applications. Ten of its six June tourers applied, which is only possible because these are event counts rather than a cohort - some applicants toured before June.
- Google My Business/Maps reappears at rank 4 having been absent from both the schedule and tour breakdowns, further evidence that the stages do not track the same contacts.
- Duplicated by total_applied_contact_by_source, which is identical in every row. See the applied_metrics_structurally_identical conflict.

### Total Applied by Lead Gen Sources — `total_applied_contact_by_source`

| Field | Value |
|---|---|
| Section | Lead Generation |
| Parent metric | `total_applied_contact` |
| Dimension | Lead Gen Sources — field `mta_first_source_name` |
| Direction | Top |
| Data source label | All |
| Status | verified_structural_duplicate |
| Period | Jun, 2026 — period_total (2026-06-01 to 2026-06-30) |
| Source object | `t_contact_activity` (BASE TABLE, Hyly + PMS, refresh daily) |
| Date column | `first_application_dt` |
| Grain | `property_id`, `contact_id` — one row per contact |
| Pathway | `ranked_breakdown_by_milestone` |
| Components | `dimension_field` = `mta_first_source_name`, `dedup_expression` = `contact_id` |
| Share denominator | 14 (parent metric total_applied_contact) |
| Share rounding | One decimal place, displayed with two. See share_rounding_rule at registry level. |

| Rank | Label | Value | Share |
|---|---|---|---|
| 1 | Property Website | 10 | 71.40% |
| 2 | Zillow | 2 | 14.30% |
| 3 | Apple Maps | 1 | 7.10% |
| 3 | Google My Business/Maps | 1 | 7.10% |

**Verification**

- *status:* match
- *verification_strength:* matches_but_identical_to_applied_contact_by_source
- *variance:* 0
- *verified_on:* 2026-08-17
- *method:* Query executed independently and compared row by row against applied_contact_by_source. Every label, value and share is identical.

**Notes**

- Second structural duplicate in the library, and the first at breakdown level.
- The duplication now appears twice in the document: once in the ledger and once in the breakdowns.

**Open items**

- *item:* duplicate_breakdown · *blocking:* no · *detail:* This breakdown is identical to applied_contact_by_source in object, pathway, dedup key, every row value and every share. It inherits the structural duplication already recorded between the parent metrics applied_contact and total_applied_contact. · *document_impact:* Rendering both produces two indistinguishable pages in the client-facing document. · *recommendation:* Resolve at the metric level. If Total Applied is meant to count repeat applications, an application-level identifier is required upstream; pms_application_id is null on every row.

### Net Applied by Lead Gen Sources — `net_applied_contact_by_source`

| Field | Value |
|---|---|
| Section | Lead Generation |
| Parent metric | `net_applied_contact` |
| Dimension | Lead Gen Sources — field `mta_first_source_name` |
| Direction | Top |
| Data source label | All |
| Status | verified |
| Period | Jun, 2026 — period_total (2026-06-01 to 2026-06-30) |
| Source object | `prospect_journey` (BASE TABLE, PMS, refresh daily) |
| Date column | `activity_dt` |
| Grain | `property_id`, `contact_id`, `event_type`, `activity_dt` — one row per contact event |
| Pathway | `ranked_breakdown_distinct_difference_by_event` |
| Components | `dimension_object` = `t_contact_activity`, `dimension_field` = `mta_first_source_name`, `event_field` = `event_type`, `positive_event` = `pms_Application`, `negative_event` = `pms_CancelApplication` |
| Share denominator | 11 (parent metric net_applied_contact) |
| Share rounding | One decimal place, displayed with two. See share_rounding_rule at registry level. |

| Rank | Label | Value | Share |
|---|---|---|---|
| 1 | Property Website | 8 | 72.70% |
| 2 | Zillow | 2 | 18.20% |
| 3 | Apple Maps | 1 | 9.10% |
| 4 | Google My Business/Maps | 0 | 0.00% |

**Verification**

- *status:* match
- *verification_strength:* rank_1_verified_and_breakdown_reconciles_to_parent
- *variance:* 0
- *verified_on:* 2026-08-17
- *method:* Cross-object breakdown derived: applications and cancellations from the event object, first-touch source joined from the contact object.
- *join_integrity:* Every contact in the event set matched a source in the contact object. No unmatched rows.
- *rounding_significance:* Fifth confirmation of the one-decimal rule. 8/11 is 72.7272%: two-decimal rounding gives 72.73%, truncation gives 72.72%, and only one-decimal rounding gives 72.70%.

**Notes**

- First cross-object breakdown in the library and the reason for a new pathway: the event object holds no first-touch attribution.
- All three June cancellations are accounted for: two Property Website, one Google My Business/Maps.
- Google My Business/Maps produced one application in June and cancelled it, so it contributes nothing net despite appearing in the applied breakdown.
- Property Website's share rises again at this stage - 71.40% of applications, 72.70% net - because its cancellation rate (2 of 10) is below the portfolio rate (3 of 14).

**Open items**

- *item:* zero_value_row_holds_a_rank · *blocking:* no · *detail:* Google My Business/Maps nets to 0 - one application, one cancellation - and still occupies rank 4 with a 0.00% share. Whether Halo suppresses zero rows or ranks them is unconfirmed, since only rank 1 was supplied. · *recommendation:* Confirm the zero-row policy. It affects rank numbering for every breakdown where a source cancels out.
- *item:* negative_values_possible · *blocking:* no · *detail:* A source with more cancellations than applications in a period would net negative and sort below zero. No such case in June 2026, but the ranking and share logic must tolerate it. · *recommendation:* Define how a negative share is displayed before it occurs.

### Leased by Lead Gen Sources — `leased_contact_by_source`

| Field | Value |
|---|---|
| Section | Lead Generation |
| Parent metric | `leased_contact` |
| Dimension | Lead Gen Sources — field `mta_first_source_name` |
| Direction | Top |
| Data source label | All |
| Status | verified |
| Period | Jun, 2026 — period_total (2026-06-01 to 2026-06-30) |
| Source object | `pai_journey_1747307582311553649` (BASE TABLE, Hyly, refresh daily) |
| Date column | `event_date` |
| Grain | `property_id`, `contact_id`, `event_name`, `event_date` — one row per contact event |
| Pathway | `ranked_breakdown_by_event_joined_dimension` |
| Components | `dimension_object` = `t_contact_activity`, `dimension_field` = `mta_first_source_name`, `event_field` = `event_name`, `event` = `h_ms_lease` |
| Share denominator | 8 (parent metric leased_contact) |
| Share rounding | One decimal place, displayed with two. 8/8 is exactly 100%, so the rule is not exercised here. |

| Rank | Label | Value | Share |
|---|---|---|---|
| 1 | Property Website | 8 | 100.00% |

**Verification**

- *status:* match
- *verification_strength:* rank_1_verified_and_breakdown_reconciles_to_parent
- *variance:* 0
- *verified_on:* 2026-08-17
- *method:* Cross-object breakdown derived: lease milestone from the event object, first-touch source joined from the contact object.
- *join_integrity:* All 8 lease contacts matched a source. No null labels, so the LEFT JOIN produced no orphans.
- *single_row:* The breakdown returned exactly one row. Property Website accounts for every June lease at this property.

**Notes**

- Second cross-object breakdown and the reason for a fourteenth pathway variant: a single event rather than a net difference.
- Completes the funnel by source. Property Website: 57.60% of leads, 30.30% of schedules, 31.60% of tours, 71.40% of applications, 100% of leases.
- Zillow reached 2 net applications in June but no leases; Apple Maps the same at 1.
- The one-decimal rounding rule is not tested by this breakdown since 8/8 is exact.

**Open items**

- *item:* single_source_concentration · *blocking:* no · *detail:* One source holding 100% of leases is a legitimate result at a 299-unit property with 8 monthly leases, but it means Top and Bottom of this dimension are the same row and any source-level lease comparison is uninformative for this period. · *recommendation:* Consider suppressing the by-source view for a metric where a single label holds the entire total, or widen the period so the dimension has spread.
- *item:* attribution_concentration_worth_checking · *blocking:* no · *detail:* Property Website holds 57.60% of leads but 100% of leases. Every other source produced leads, tours or applications in June and none produced a lease. Whether that reflects genuine channel performance or first-touch attribution collapsing onto the website is not established from this trace. · *recommendation:* Compare against pms_source_name, which attributes materially differently - 30 for Property Website at the lead stage against 53 on first-touch.

### Newly Created Leads by Lead Gen Mediums — `created_contact_by_medium`

| Field | Value |
|---|---|
| Section | Lead Generation |
| Parent metric | `created_contact` |
| Dimension | Lead Gen Mediums — field `mta_first_medium_name` |
| Direction | Top |
| Data source label | All |
| Status | partially_verified |
| Period | Jun, 2026 — period_total (2026-06-01 to 2026-06-30) |
| Source object | `t_contact_activity` (BASE TABLE, Hyly + PMS, refresh daily) |
| Date column | `contact_created_date` |
| Grain | `property_id`, `contact_id` — one row per contact |
| Pathway | `ranked_breakdown_distinct_count` |
| Components | `dimension_field` = `mta_first_medium_name`, `dedup_expression` = `contact_name`, `status_field` = `contact_status`, `exclude_status` = `Leased` |
| Share denominator | 92 (parent metric created_contact) |
| Share rounding | One decimal place, displayed with two. All six shares confirm the rule. |

| Rank | Label | Value | Share |
|---|---|---|---|
| 1 | Organic | 67 | 72.80% |
| 2 | Direct | 17 | 18.50% |
| 3 | Affiliate | 4 | 4.30% |
| 4 | Print | 2 | 2.20% |
| 5 | CPC | 1 | 1.10% |
| 5 | Social | 1 | 1.10% |

**Verification**

- *status:* partial_match
- *verification_strength:* four_of_six_rows_exact_two_differ_by_a_four_contact_reclassification
- *verified_on:* 2026-08-17
- *method:* Full six-row breakdown derived with contact_name dedup. Sums to 92, matching the parent and Halo. Four labels reproduce exactly; Organic and Direct differ by exactly four contacts in opposite directions.
- *all_shares_reproduce:* Every one of the six Halo shares reproduces from the Halo value over 92 under the one-decimal rule, including the tied pair at 1.10%.

**Notes**

- Upgraded from unreconciled once the full six rows were supplied. Four rows exact, two off by a single four-contact reclassification.
- Halo assigns the same rank to tied values - CPC and Social both hold rank 5 - so ranking is competition-style rather than sequential.
- The medium taxonomy has six values against ten sources.

**Open items**

- *item:* four_contact_reclassification_unexplained · *blocking:* no · *detail:* Four contacts are Organic in mta_first_medium_name and Direct in Halo. Apple Maps is the likely group since it has no medium mapping in mktg_tags, but Google.com holds the same count and cannot be excluded arithmetically. · *recommendation:* Confirm whether Halo applies a fallback for sources with no default_medium_tag_id. That would explain the shift and predict it elsewhere.
- *item:* dedup_differs_between_breakdowns_of_one_parent · *blocking:* no · *detail:* This medium breakdown sums to 92 and matches the parent. The source breakdown of the same parent sums to 93. Halo's own figures confirm the two use different dedup keys - contact_name here, contact_name plus contact_sub_status there. · *significance:* Previously inferred; now demonstrated from Halo's published values rather than from our reconstruction. · *recommendation:* Align the two. The medium rule is the one consistent with the parent.
- *item:* paid_search_under_organic · *blocking:* no · *detail:* Google PayPerClick (PPC) contributes 6 contacts to Organic and 1 to CPC in the denormalised field, despite mapping to CPC in mktg_tags. A paid source sitting mostly under Organic understates paid performance. · *recommendation:* Raise independently of this reconciliation.

### 1st Scheduled by Lead Gen Mediums — `scheduled_contact_by_medium`

| Field | Value |
|---|---|
| Section | Lead Generation |
| Parent metric | `scheduled_contact` |
| Dimension | Lead Gen Mediums — field `mta_first_medium_name` |
| Direction | Top |
| Data source label | All |
| Status | verified |
| Period | Jun, 2026 — period_total (2026-06-01 to 2026-06-30) |
| Source object | `t_contact_activity` (BASE TABLE, Hyly + PMS, refresh daily) |
| Date column | `first_scheduled_dt` |
| Grain | `property_id`, `contact_id` — one row per contact |
| Pathway | `ranked_breakdown_by_milestone` |
| Components | `dimension_field` = `mta_first_medium_name`, `dedup_expression` = `contact_id` |
| Share denominator | 33 (parent metric scheduled_contact) |
| Share rounding | One decimal place, displayed with two. |

| Rank | Label | Value | Share |
|---|---|---|---|
| 1 | Organic | 23 | 69.70% |
| 2 | Direct | 4 | 12.10% |
| 2 | Affiliate | 4 | 12.10% |
| 4 | Print | 2 | 6.10% |

**Verification**

- *status:* match
- *verification_strength:* rank_1_verified_and_breakdown_reconciles_to_parent
- *variance:* 0
- *verified_on:* 2026-08-17
- *method:* Four-row breakdown derived with contact_id dedup, matching the parent rule. Reproduced on the first attempt with no reclassification gap.
- *contrast_with_created_by_medium:* The Newly Created Leads medium breakdown required contact_name dedup and still left four contacts reclassified between Organic and Direct. This one reconciles exactly with the parent's own dedup key, so the medium taxonomy itself is not systematically broken.

**Notes**

- Second medium breakdown and the first to reconcile exactly. Confirms the medium dimension works where the parent's dedup rule is applied consistently.
- Four mediums against six for Newly Created Leads: CPC and Social produced leads in June but no scheduled tours.
- Direct and Affiliate tie at 4 and share rank 2 under Halo's convention.
- Organic falls from 72.80% of leads to 69.70% of schedules. Affiliate rises sharply from 4.30% to 12.10%, driven by Zillow's six schedules.

### 1st Toured by Lead Gen Mediums — `toured_contact_by_medium`

| Field | Value |
|---|---|
| Section | Lead Generation |
| Parent metric | `toured_contact` |
| Dimension | Lead Gen Mediums — field `mta_first_medium_name` |
| Direction | Top |
| Data source label | All |
| Status | verified |
| Period | Jun, 2026 — period_total (2026-06-01 to 2026-06-30) |
| Source object | `t_contact_activity` (BASE TABLE, Hyly + PMS, refresh daily) |
| Date column | `first_completed_dt` |
| Grain | `property_id`, `contact_id` — one row per contact |
| Pathway | `ranked_breakdown_by_milestone` |
| Components | `dimension_field` = `mta_first_medium_name`, `dedup_expression` = `contact_id` |
| Share denominator | 19 (parent metric toured_contact) |
| Share rounding | One decimal place, displayed with two. |

| Rank | Label | Value | Share |
|---|---|---|---|
| 1 | Organic | 16 | 84.20% |
| 2 | Print | 2 | 10.50% |
| 3 | Direct | 1 | 5.30% |

**Verification**

- *status:* match
- *verification_strength:* rank_1_verified_and_breakdown_reconciles_to_parent
- *variance:* 0
- *verified_on:* 2026-08-17
- *method:* Three-row breakdown derived with contact_id dedup. Reproduced on the first attempt.

**Notes**

- Third medium breakdown, second to reconcile exactly on the first attempt.
- Only three mediums remain. Affiliate produced four scheduled tours in June but no completed ones, so Zillow's schedules did not convert to tours.
- Organic rises through the funnel by medium: 72.80% of leads, 69.70% of schedules, 84.20% of tours.
- No tied values, so ranks are sequential here.

### Total Applied by Lead Gen Mediums — `total_applied_contact_by_medium`

| Field | Value |
|---|---|
| Section | Lead Generation |
| Parent metric | `total_applied_contact` |
| Dimension | Lead Gen Mediums — field `mta_first_medium_name` |
| Direction | Top |
| Data source label | All |
| Status | verified |
| Period | Jun, 2026 — period_total (2026-06-01 to 2026-06-30) |
| Source object | `t_contact_activity` (BASE TABLE, Hyly + PMS, refresh daily) |
| Date column | `first_application_dt` |
| Grain | `property_id`, `contact_id` — one row per contact |
| Pathway | `ranked_breakdown_by_milestone` |
| Components | `dimension_field` = `mta_first_medium_name`, `dedup_expression` = `contact_id` |
| Share denominator | 14 (parent metric total_applied_contact) |
| Share rounding | One decimal place, displayed with two. |

| Rank | Label | Value | Share |
|---|---|---|---|
| 1 | Organic | 10 | 71.40% |
| 2 | Direct | 4 | 28.60% |

**Verification**

- *status:* match
- *verification_strength:* rank_1_verified_and_breakdown_reconciles_to_parent
- *variance:* 0
- *verified_on:* 2026-08-17
- *method:* Two-row breakdown derived with contact_id dedup. Reproduced on the first attempt.
- *note_on_zillow:* Zillow's two applications appear under Organic here, while the Newly Created Leads medium breakdown places Zillow under Affiliate. The per-contact denormalised medium is not stable across the funnel for the same source.

**Notes**

- Fourth medium breakdown, third to reconcile on the first attempt.
- Only two mediums remain at the application stage, down from six at lead creation. Affiliate, Print, CPC and Social all produced earlier-stage activity in June but no applications.
- Organic holds 71.40% here, identical to Property Website's share in the source breakdown of the same metric - coincidence, since the two dimensions group different contacts.
- No ties, so ranks are sequential.
- Duplicated by applied_contact_by_medium, identical in both rows. Folded into this page.

**Open items**

- *item:* first_applied_by_medium_not_supplied · *blocking:* no · *detail:* Halo supplied Total Applied by medium but not 1st Applied by medium. Because the two parent metrics are structurally identical - same object, column, dedup key, and no application-level identifier exists - the 1st Applied medium breakdown must return the same two rows, Organic 10 and Direct 4. · *status:* confirmed by derivation on 2026-08-17 · *recommendation:* If 1st Applied by medium is supplied later and does not read 10 and 4, the structural-duplicate finding on the parent metrics is wrong and should be revisited. · *outcome:* applied_contact_by_medium derives Organic 10 and Direct 4, exactly as predicted.
- *item:* medium_unstable_across_funnel_for_one_source · *blocking:* no · *detail:* Zillow is Affiliate in the Newly Created Leads medium breakdown and Organic in this one. Same source, same property, same period, different medium at different funnel stages. · *assessment:* The denormalised mta_first_medium_name is per-contact, so different Zillow contacts can carry different mediums. That is legitimate mechanically but means medium is not a stable property of a source. · *recommendation:* Worth stating on the report if clients compare medium across stages.

### 1st Applied by Lead Gen Mediums — `applied_contact_by_medium`

| Field | Value |
|---|---|
| Section | Lead Generation |
| Parent metric | `applied_contact` |
| Dimension | Lead Gen Mediums — field `mta_first_medium_name` |
| Direction | Top |
| Data source label | All |
| Status | verified_structural_duplicate |
| Period | Jun, 2026 — period_total (2026-06-01 to 2026-06-30) |
| Source object | `t_contact_activity` (BASE TABLE, Hyly + PMS, refresh daily) |
| Date column | `first_application_dt` |
| Grain | `property_id`, `contact_id` — one row per contact |
| Pathway | `ranked_breakdown_by_milestone` |
| Components | `dimension_field` = `mta_first_medium_name`, `dedup_expression` = `contact_id` |
| Share denominator | 14 (parent metric applied_contact) |
| Share rounding | One decimal place, displayed with two. |

| Rank | Label | Value | Share |
|---|---|---|---|
| 1 | Organic | 10 | 71.40% |
| 2 | Direct | 4 | 28.60% |

**Verification**

- *status:* derived_prediction_confirmed
- *verification_strength:* no_halo_value_supplied_for_this_metric_name
- *reported:* —
- *verified_on:* 2026-08-17
- *method:* Derived independently, then compared against the Halo-verified Total Applied medium breakdown. Both rows identical.
- *prediction_outcome:* Confirmed. The prediction recorded on total_applied_contact_by_medium was Organic 10 and Direct 4, and the derivation returns exactly that.
- *significance:* This is the first opportunity to test the structural-duplicate finding on the parent metrics with an independent derivation, and it held.
- *provenance_caveat:* Halo supplied no figure under the name 1st Applied by medium. The values shown are Halo-verified only through the Total Applied breakdown, which is byte-identical. They are not independently confirmed under this metric name.

**Notes**

- Registered as a duplicate so it renders on the same page as Total Applied by medium rather than producing a page of values Halo never published.
- Second structural duplicate at breakdown level, mirroring applied_contact_by_source and total_applied_contact_by_source in the source dimension.

**Open items**

- *item:* no_direct_halo_confirmation · *blocking:* no · *detail:* Every other breakdown in the library carries a Halo-reported rank-1 row that was reproduced. This one does not - it is derived, and validated only by matching its structural duplicate. · *recommendation:* If Halo publishes 1st Applied by medium, confirm it reads Organic 10 and Direct 4.

### Net Applied by Lead Gen Mediums — `net_applied_contact_by_medium`

| Field | Value |
|---|---|
| Section | Lead Generation |
| Parent metric | `net_applied_contact` |
| Dimension | Lead Gen Mediums — field `mta_first_medium_name` |
| Direction | Top |
| Data source label | All |
| Status | verified |
| Period | Jun, 2026 — period_total (2026-06-01 to 2026-06-30) |
| Source object | `prospect_journey` (BASE TABLE, PMS, refresh daily) |
| Date column | `activity_dt` |
| Grain | `property_id`, `contact_id`, `event_type`, `activity_dt` — one row per contact event |
| Pathway | `ranked_breakdown_distinct_difference_by_event` |
| Components | `dimension_object` = `t_contact_activity`, `dimension_field` = `mta_first_medium_name`, `event_field` = `event_type`, `positive_event` = `pms_Application`, `negative_event` = `pms_CancelApplication` |
| Share denominator | 11 (parent metric net_applied_contact) |
| Share rounding | One decimal place, displayed with two. |

| Rank | Label | Value | Share |
|---|---|---|---|
| 1 | Organic | 7 | 63.60% |
| 2 | Direct | 4 | 36.40% |

**Verification**

- *status:* match
- *verification_strength:* rank_1_verified_and_breakdown_reconciles_to_parent
- *variance:* 0
- *verified_on:* 2026-08-17
- *method:* Cross-object breakdown derived: applications and cancellations from the event object, first-touch medium joined from the contact object.
- *join_integrity:* Every contact in the event set matched a medium. No null labels.
- *cancellation_concentration:* All three June cancellations fall under Organic. Direct recorded none, so its four applications carry through to net unchanged.
- *reconciles_with_source_version:* The source breakdown attributed the three cancellations to Property Website (2) and Google My Business/Maps (1). Both are Organic-medium contacts here, which is consistent.

**Notes**

- Second cross-object breakdown in the medium dimension, reusing the pathway built for the source version without modification.
- Only two mediums, matching the application stage.
- Organic falls from 71.40% of applications to 63.60% net because it absorbs all three cancellations. Direct rises from 28.60% to 36.40% by absorbing none.
- The cancellation concentration is the inverse of the source picture, where Property Website's cancellation rate looked better than the portfolio's. By medium, Organic looks worse.

**Open items**

- *item:* zero_and_negative_values · *blocking:* no · *detail:* No medium nets to zero or below in June, unlike the source version where Google My Business/Maps netted zero and held a rank. The display rule for zero and negative rows remains undefined. · *recommendation:* Same open question as the source breakdown; carried forward rather than duplicated.

### Leased by Lead Gen Mediums — `leased_contact_by_medium`

| Field | Value |
|---|---|
| Section | Lead Generation |
| Parent metric | `leased_contact` |
| Dimension | Lead Gen Mediums — field `mta_first_medium_name` |
| Direction | Top |
| Data source label | All |
| Status | verified |
| Period | Jun, 2026 — period_total (2026-06-01 to 2026-06-30) |
| Source object | `pai_journey_1747307582311553649` (BASE TABLE, Hyly, refresh daily) |
| Date column | `event_date` |
| Grain | `property_id`, `contact_id`, `event_name`, `event_date` — one row per contact event |
| Pathway | `ranked_breakdown_by_event_joined_dimension` |
| Components | `dimension_object` = `t_contact_activity`, `dimension_field` = `mta_first_medium_name`, `event_field` = `event_name`, `event` = `h_ms_lease` |
| Share denominator | 8 (parent metric leased_contact) |
| Share rounding | One decimal place, displayed with two. 7/8 is exactly 87.5%, so the rule is not exercised. |

| Rank | Label | Value | Share |
|---|---|---|---|
| 1 | Organic | 7 | 87.50% |
| 2 | Direct | 1 | 12.50% |

**Verification**

- *status:* match
- *verification_strength:* rank_1_verified_and_breakdown_reconciles_to_parent
- *variance:* 0
- *verified_on:* 2026-08-17
- *method:* Cross-object breakdown derived: lease milestone from the event object, first-touch medium joined from the contact object.
- *join_integrity:* All 8 lease contacts matched a medium. No null labels.
- *both_rows_same_source:* Every one of the 8 leases came from Property Website. The medium split is entirely within that one source: 7 Organic, 1 Direct.
- *prediction_outcome:* Confirmed. When the source version returned a single row at 100%, the prediction was that the medium version would split into two rows because Property Website carries both mediums. It does.

**Notes**

- Completes the medium dimension: all seven Lead Generation metrics now have both a source and a medium breakdown.
- Reuses the cross-object pathway built for the source version without modification.
- The 7 to 1 split sits entirely inside Property Website, so medium here distinguishes traffic type within one source rather than between sources.
- Organic's share peaks at this final stage: 72.80% of leads, 69.70% of schedules, 84.20% of tours, 71.40% of applications, 63.60% net, 87.50% of leases.

**Open items**

- *item:* medium_more_informative_than_source_here · *blocking:* no · *detail:* The source breakdown of this metric is a single row at 100% and carries no comparative information. The medium breakdown splits the same eight leases 7 to 1, so it is the more useful view for this metric and period. · *recommendation:* Where a source breakdown collapses to one label, prefer the medium view rather than suppressing the dimension entirely.

---

## Cross-checks

Identities that must hold between traced metrics.

| Check | Expression | Status | Observed | Verified | Metrics | Note |
|---|---|---|---|---|---|---|
| `unit_balance` | `rentable = occupied + vacant` | pass | 299 = 266 + 33 | 2026-08-17 | `rentable`, `occupied`, `vacant` | All three operands are independently traced metrics, so this check runs entirely from the registry. |
| `vacant_split` | `vacant = vacant_rented + vacant_unrented` | pass | 33 = 16 + 17 at 2026-06-30; 23 = 3 + 20 at 2026-07-31 | 2026-08-17 | `vacant`, `vacant_rented`, `vacant_unrented` | Fully self-contained and non-degenerate: both operands are non-zero at both readings, so this check can genuinely fail. |
| `exposure_numerator` | `available = vacant_unrented + notice_unrented` | pass | 17 = 17 + 0 at 2026-06-30 (passes only because notice_unrented is unpopulated); 39 = 20 + 19 at 2026-07-31 (meaningful) | 2026-08-17 | `available`, `vacant_unrented`, `notice_unrented` | Now fully self-contained - all three operands are traced metrics. But the registered June values make it degenerate: it would still pass if the notice component were missing, which it is. The 2026-07-31 reading is the real test. |
| `rentable_definition` | `rentable = total_units - excluded` | pass | 299 = 299 - 0 | 2026-08-17 | `rentable`, `total_units`, `excluded` | All three operands are now traced metrics, so this check runs entirely from the registry. Still passes trivially in June because excluded is 0 - re-run from 2026-07-27 onward. |
| `exposure_leased_complement` | `pct_exposure + pct_leased = 100` | pass | 5.69 + 94.31 = 100.00 | 2026-08-17 | `pct_exposure`, `pct_leased` | Holds only if pct_leased is the exposure complement as registered. Will fail once notices are non-zero if that assumption is wrong, which makes it a useful tripwire. |
| `occupancy_rate_from_components` | `pct_occupied = occupied / total_units * 100` | pass | 88.96 = 266 / 299 * 100 | 2026-08-17 | `pct_occupied`, `occupied`, `total_units` | Fully self-contained - runs from the registry with no warehouse access. Not trivial: it would fail on any inconsistency between the three recorded values. |
| `net_move_ins_from_components` | `net_move_ins = move_ins - move_outs` | pass | -12 = 11 - 23 | 2026-08-17 | `net_move_ins`, `move_ins`, `move_outs` | All three operands are traced metrics, so this check runs entirely from the registry. Not trivial - it would fail on any inconsistency between the three. |
| `leased_future_definition` | `leased_future = vacant_rented + notice_rented` | pass | 16 = 16 + 0 at 2026-06-30 (degenerate); 9 = 3 + 6 at 2026-07-31 (non-degenerate) | 2026-08-17 | `leased_future`, `vacant_rented`, `notice_rented` | Fully self-contained, but the registered June values make it degenerate - it passes whether or not notice_rented was captured, and it was not. The 2026-07-31 reading (9 = 3 + 6) is the real test. |
| `exposure_from_components` | `pct_exposure = available / total_units * 100` | pass | 5.69 = 17 / 299 * 100 | 2026-08-17 | `pct_exposure`, `available`, `total_units` | Fully self-contained. Runs from the registry with no warehouse access. |
| `leased_from_components` | `pct_leased = (total_units - available) / total_units * 100` | pass | 94.31 = (299 - 17) / 299 * 100 | 2026-08-17 | `pct_leased`, `available`, `total_units` | Fully self-contained, conditional on the registered leased formulation. |
| `leadgen_funnel_monotonic` | `scheduled_contact >= toured_contact >= applied_contact` | pass | 33 >= 19 >= 14 | 2026-08-17 | `scheduled_contact`, `toured_contact`, `applied_contact` | A sanity check on the funnel shape rather than an identity. Note it is not a strict cohort funnel - each metric counts milestones occurring in June regardless of when the contact was created, so a violation would not necessarily be an error. Flag for review rather than treating as a hard failure. |
| `net_applied_from_components` | `net_applied_contact = applied_contact - cancelled_applications` | pass | 11 = 14 - 3 | 2026-08-17 | `net_applied_contact`, `applied_contact` | Cross-object: the application component comes from prospect_journey while applied_contact reads t_contact_activity. Both report 14, so the objects agree on applications. Closes fully if cancellations are ever published as their own metric. |
| `leadgen_funnel_monotonic_full` | `scheduled_contact >= toured_contact >= applied_contact >= leased_contact` | pass | 33 >= 19 >= 14 >= 8 | 2026-08-17 | `scheduled_contact`, `toured_contact`, `applied_contact`, `leased_contact` | Soft check on funnel shape. Not a strict cohort funnel - each stage counts milestones occurring in the period regardless of when the contact entered, so a violation warrants review rather than automatic failure. |

---

## Conflicts

### `june_rate_degeneracy`

Both report 94.31% for June 2026. Seven candidate formulations all return 94.3144% at this reading because notice_rented and notice_unrented are both 0, so June cannot distinguish them. Formula assignments for both metrics are convention-based.

**Affected metrics:** `pct_trend`, `pct_leased`

**Candidate values july 2026**

| Candidate | Value |
|---|---|
| `(rentable - available) / rentable` | 87.58% |
| `(total_units - available) / total_units` | 87.62% |
| `(occupied - notice_unrented + leased_future) / total_units` | 89.21% |
| `(occupied - notice_unrented + leased_future) / rentable` | 89.49% |
| `(occupied + vacant_rented) / rentable` | 93.63% |
| `(occupied + leased_future) / total_units` | 95.24% |
| `(occupied + leased_future) / rentable` | 95.54% |

**Resolution path:** Trace both Occupancy Trend and Leased Rate for July 2026 at this property. Two figures resolve both formulations and the exposure denominator.

**Resolves:** `trend_formulation`, `leased_formulation`, `exposure_denominator`

### `object_disagreement_june_occupancy`

Three objects tell different stories about June 2026 occupancy at this property.

**Affected metrics:** `occupancy_rate`, `occupied`, `total_units`, `rentable`, `pct_exposure`, `pct_occupied`

**Observations:** `{'object': 't_occupancy_rate', 'window': '2026-06-03 to 2026-06-30 (28 days)', 'unit_base': 314, 'june_average': '93.59%', 'status': 'live', 'note': "Halo's source for Average Occupancy Rate."}`, `{'object': 't_occupancy_exposure_rate', 'window': '2026-06-02 to 2026-06-29 (28 days)', 'unit_base': 315, 'june_average': '93.29%', 'status': 'frozen 2026-07-01', 'note': 'Not used by Halo.'}`, `{'object': 't_oc_agg_occupancy_property', 'window': '2026-06-30 only', 'unit_base': 299, 'june_average': '88.96% (point-in-time, not an average)', 'status': 'live', 'note': 'Source for the other nine Occupancy metrics.'}`

**Impact:** Unit base differs by up to 16 units and occupied differs by 22 on the same date (288 versus 266 at 2026-06-30). Metrics drawn from different objects in this section are not arithmetically reconcilable.

**Resolution path:** Establish which object is authoritative for unit counts before publishing portfolio rollups that mix the two.

**Client facing risk:** Halo publishes both Current Occupancy Rate (88.96%) and Average Occupancy Rate (93.59%) in the same section for the same period. A 4.63 point gap between two similarly named metrics will read as an error to a client unless the difference in object, unit base and time semantics is stated on the report itself.

### `lead_creation_count_disagreement`

Three warehouse objects report different counts of contacts created at this property in June 2026, and Halo reports a fourth number lower than all of them.

**Affected metrics:** `created_contact`

**Observations:** `{'source': 'Halo report', 'value': 92}`, `{'source': 'contact_info', 'value': 290, 'note': 'consistent across all four creation-date columns'}`, `{'source': 'conversion_triple_base_v1', 'value': 134, 'note': 'on the equivalent hybrid date column'}`, `{'source': 'pai_journey h_ms_create_contact', 'value': 695, 'note': 'distinct contacts'}`, `{'source': 'prospect_journey', 'value': 761, 'note': 'contacts whose first ever activity falls in June; 2,130 were active in June'}`

**Resolution path:** Obtain the Halo lead definition before registering a pathway.

**Status:** resolved

**Resolution:** Halo uses t_contact_activity with contact_created_date, excludes contact_status = 'Leased', and counts distinct contact_name. The other object and column combinations remain valid populations for other questions but are not this metric.

### `contact_creation_count_across_objects`

Six objects report different counts of contacts created at this property in June 2026 on comparable date columns.

**Affected metrics:** `created_contact`

**Observations:** `{'object': 'contact_info', 'value': 290}`, `{'object': 't_contact_activity', 'value': 279}`, `{'object': 'conversion_triple_base_v1', 'value': 134}`, `{'object': 'v_contact_activity__hybrid_pms_contact_created_date', 'value': 61}`, `{'object': 'prospect_journey', 'value': 761, 'note': 'first-ever activity, backfill-inflated'}`, `{'object': 'Halo report', 'value': 92}`

**Note:** The 290 versus 279 gap is the most tractable: same grain, same date column, 11 rows apart. Resolving it would establish which object is authoritative for the section.

**Resolution path:** Obtain the Halo lead definition, then reconcile contact_info against t_contact_activity at contact level.

**Status:** resolved

**Resolution:** Halo uses t_contact_activity with contact_created_date, excludes contact_status = 'Leased', and counts distinct contact_name. The other object and column combinations remain valid populations for other questions but are not this metric.

### `leadgen_rule_inconsistency`

Two metrics in the same section require different rules to reproduce their reported values.

**Affected metrics:** `created_contact`, `scheduled_contact`

**Observations:** `{'metric': 'scheduled_contact', 'dedup_key': 'contact_id', 'status_filter': 'none', 'date_basis': 'milestone date', 'corroborating_objects': 3}`, `{'metric': 'created_contact', 'dedup_key': 'contact_name', 'status_filter': "contact_status != 'Leased'", 'date_basis': 'contact_created_date', 'corroborating_objects': 1}`

**Assessment:** Either Halo genuinely applies different deduplication and filtering per metric, or the created_contact rule is a false positive found by searching a large parameter space. Applying the scheduled_contact rule to creation gives 287, not 92.

**Impact:** Confidence in the created_contact definition is lower than its exact match suggests.

**Resolution path:** Trace both metrics for a second property. If scheduled_contact holds and created_contact does not, the created rule was coincidental.

### `applied_metrics_structurally_identical`

Two distinct Halo metrics resolve to an identical pathway and cannot diverge in this data model.

**Affected metrics:** `applied_contact`, `applied_contact_by_medium`, `applied_contact_by_source`, `total_applied_contact`, `total_applied_contact_by_medium`, `total_applied_contact_by_source`

**Evidence:** `pms_application_id is null on all rows - no application-level grain exists.`, `Application events equal distinct applicants in all 14 months from 2025-07 to 2026-08.`, `Both metrics reported 14 for June 2026.`

**Distinction from june degeneracy:** Unlike the Occupancy Trend and Leased Rate pair, these do not merely coincide in June. No period could separate them without a change to the data model.

**Resolution path:** Confirm whether the duplication is intended. If Total Applied should count repeat applications, an application-level identifier must be added upstream.

**Scope extended:** The duplication also propagates to the source breakdowns, which are identical across all four rows.

**Independent test:** The medium dimension provided an independent test: 1st Applied by medium was derived without a Halo figure and matched Total Applied by medium exactly. The structural duplication holds across both dimensions.

---

## Data gaps

### `notice_fields_before_2026_07_27`

| Field | Value |
|---|---|
| Fields | `notice_unrented`, `notice_rented` |
| Object | `t_oc_agg_occupancy_property` |
| Populated from | 2026-07-27 |
| Assessment | Integration start, not real-world behaviour. Zero notice activity across roughly 5,400 units for 27 consecutive days is not plausible in multifamily. |

**Evidence**

- 2026-06-30 to 2026-07-26: notice_unrented and notice_rented are 0 for all 15 properties on all 27 readings, with zero nulls.
- 2026-07-27: all 15 properties report non-zero simultaneously - 320 unrented, 81 rented.
- 2026-07-27 to 2026-08-16: stable range 287 to 320 unrented portfolio-wide.
- At this property leased_future tracks vacant_rented exactly until 2026-07-26 (both 8), then diverges on 2026-07-27 (9 against 3) - the same date the notice fields begin reporting.

**Downstream impact**

| Metric | Impact |
|---|---|
| `available` | June available (17) counts vacant_unrented only. The notice component is missing entirely. |
| `pct_exposure` | June 5.69% understates true exposure. For scale, this property carried 19 notice-unrented units against 315 total by 2026-07-31, about 6 points of additional exposure. |
| `pct_leased` | June 94.31% correspondingly overstates. |
| `pct_trend` | June 94.31% correspondingly overstates. |
| `note` | The direction of the error is certain; the magnitude for June is unknowable because the data was never captured. |
| `leased_future` | June Future Leases (16) counts vacant_rented only and understates. Confirmed at this property: leased_future equals vacant_rented exactly on every reading through 2026-07-26, then splits from 2026-07-27 (leased_future 9 = vacant_rented 3 + notice_rented 6). |

**Affects july disambiguation.** No. The 2026-07-31 month-end readings fall after 2026-07-27, so notice data is present and the planned July point-in-time traces remain valid. A July period average would be contaminated, since notices exist for only the final five days of that month.

### `operational_measures_incomplete`

| Field | Value |
|---|---|
| Object | `t_oc_agg_occupancy_operational` |

**Assessed on.** 2026-08-17

**Field population**

| Metric | Impact |
|---|---|
| `delayed_move_ins` | 525 of 525 rows - fully populated |
| `turn_time_avg_days` | 120 of 525 rows - sparse, roughly 23 percent; maximum sample_n is 38 |
| `turn_time_median_days` | same sparsity as turn_time_avg_days |
| `leaseup_lag_avg_days` | 0 of 525 rows - never populated |
| `leaseup_lag_median_days` | 0 of 525 rows - never populated |
| `caveat` | 0 of 525 rows - never populated |

**Impact.** Delayed Move Ins is safe to publish. Any Turn Time metric will be null for this property and period, and would be sparse across the portfolio. Any Lease-Up Lag metric has no source data whatsoever.

**Object staleness.** Last refresh 2026-08-03 against 2026-08-16 for t_oc_agg_occupancy_property - approximately two weeks behind.

**Recommendation.** Treat Lease-Up Lag as unsourced rather than zero. Confirm whether the refresh stall is intentional before relying on this object for current-period reporting.

### `t_contact_activity_milestone_fields_unpopulated`

| Field | Value |
|---|---|
| Object | `t_contact_activity` |
| Assessment | Six of thirteen milestone fields are entirely unpopulated. Field names are not a reliable guide to which is live. |

**Dataset.** —

**Assessed on.** 2026-08-17

**Scope.** The Bromley at Brighton Crossing, all 2,269 rows

**Field population**

| Metric | Impact |
|---|---|
| `first_scheduled_dt` | 693 |
| `first_tour_slot_dt` | 299 |
| `first_completed_dt` | 299 |
| `first_application_dt` | 340 |
| `pms_first_move_in_dt` | 203 |
| `first_rescheduled_dt` | 3 |
| `first_cancelled_dt` | 0 |
| `first_no_show_dt` | 0 |
| `first_unmanaged_dt` | 0 |
| `first_active_dt` | 0 |
| `first_appointment_dt` | 0 |
| `first_toured_dt` | 0 |
| `pms_first_leased_dt` | 0 |

**Field mapping**

| Metric | Impact |
|---|---|
| `tour scheduled` | first_scheduled_dt (not first_appointment_dt, which is dead) |
| `tour completed` | first_completed_dt (not first_toured_dt, which is dead) |
| `application` | first_application_dt |
| `move-in` | pms_first_move_in_dt |

**Impact on upcoming metrics**

| Metric | Impact |
|---|---|
| `Leased` | pms_first_leased_dt is entirely null. A Leased metric will need conversion_triple_base_v1.lease_date or pai_journey h_ms_lease instead. |
| `Cancelled / No-Show` | first_cancelled_dt and first_no_show_dt are both null - no source in this object. |
| `Net Applied` | first_cancelled_dt being null means application cancellations cannot be netted from this object. |

**Recommendation.** Check field population before assuming a milestone is available, and prefer conversion_triple_base_v1 or pai_journey where this object is null.

**Dataset note.** Injected by the connector.

---

## Corrections

### 2026-08-17

- **Earlier claim:** An earlier assessment recorded t_occupancy_rate as dead and June 1-29 occupancy as permanently unavailable.
- **Reality:** t_occupancy_rate is a live view with 1,130 rows carrying daily observations from 2026-06-03. The earlier check inspected t_occupancy_rate_bckup_, which is empty and frozen, and wrongly generalized from it. The view is not built from that table.
- **Consequence:** History constraints are object-scoped, not dataset-wide. A _bckup_ table being empty says nothing about the view above it.

### 2026-08-17

- **Earlier claim:** An earlier assessment recorded prospect_journey as having zero rows.
- **Reality:** prospect_journey is a populated BASE TABLE with 402,581 rows covering all 15 properties from 2023-09-26 through 2026-08-17. It was either backfilled after the earlier check on 2026-08-05, or the earlier reading was wrong.
- **Consequence:** Do not treat __TABLES__ row_count as an emptiness test. It reports 0 for views regardless of content, and any single reading can be stale. Confirm with a direct COUNT(*) before recording an object as empty.
- **Related:** The same error pattern produced the earlier incorrect finding about t_occupancy_rate.

---

## Disambiguation

### occupancy, occupancy rate, % occupied, how full

> **Warning.** Three metrics named or read as occupancy span 88.96% to 94.31% for the same property and period. Never answer a bare occupancy question without stating which basis was used.

| Metric | Traced value | Basis |
|---|---|---|
| `pct_occupied` | 88.96% | month-end point-in-time, occupied / total_units |
| `occupancy_rate` | 93.59% | mean of daily rates over the period, different object and unit base |
| `pct_leased` | 94.31% | includes units committed but not yet occupied |

| If the question contains | Resolve to |
|---|---|
| current, as of, at month end, right now, today | `pct_occupied` |
| average, avg, over the month, across the month | `occupancy_rate` |
| leased, committed, signed | `pct_leased` |

**Default:** `ask_user`

**Answer must include:** `metric_name`, `value`, `basis`, `value_as_of`

### leads, new leads, how many leads

> **Warning.** The definition of this metric was inferred by search, not supplied, and is corroborated by one object only. Six warehouse populations for the same question range from 61 to 761. State that the figure follows Halo's reported definition rather than presenting it as independently derived.

| Metric | Traced value | Basis |
|---|---|---|
| `created_contact` | 92 | — |

**Default:** `created_contact`

### exposure, how much to lease

> **Warning.** For periods before 2026-07-27 this understates: the notice component was not captured. Always surface that caveat.

| Metric | Traced value | Basis |
|---|---|---|
| `pct_exposure` | 5.69% | — |

**Default:** `pct_exposure`

---

## Governance and configuration

### Warehouse and tenancy

| Field | Value |
|---|---|
| Tenancy | Dataset is supplied by the connector from its authenticated context. It is deliberately absent from this registry. |
| Isolation rule | No pathway, note or example in this registry may name a dataset or project. A parameterised dataset is a substitution slot; an absent one cannot be filled from a prompt. |
| Platform bytes-billed cap | 2 GB |
| Recommended cap | 50 MB |
| Rationale | Every registered pathway executes under 5 MB. A 50 MB ceiling catches a malformed or unscoped query long before the 2 GB platform limit. |

### Object allowlist

**Allowed objects:** `conversion_triple_base_v1`, `pai_journey_1747307582311553649`, `prospect_journey`, `t_contact_activity`, `t_oc_agg_occupancy_operational`, `t_oc_agg_occupancy_property`, `t_occupancy_rate`, `t_ot_agg_resident_activity_property`

**Enforcement.** Refuse any query naming an object outside this list. Do not rewrite - a rewrite hides the attempt.

**Rationale.** An allowlist closes cross-tenant objects, dev copies and cost traps in one rule rather than several denylists.

| Denied pattern | Reason |
|---|---|
| `*_bckup_` | Orphaned backup copies frozen at 2026-03-18/19. Some are view-backing storage and must be reached via their view, never directly. |
| `*_hayley*` | Person-named development copies. Not a supported interface. |
| `ca_aggregations` | Contains no rows for this tenant. Out of scope. |
| `v_contact_activity__*` | Unclustered wide-scan view: 1.34 GB for a single property against a 4 MB client-scoped equivalent. |
| `v_leadgen_12hrs_*` | Unclustered wide-scan view returning zero rows for this tenant. |

### Property scope

| Field | Value |
|---|---|
| All properties | no |
| Authorized property count | 15 |
| Enforcement | Refuse any property_id outside the authenticated authorized set. This is the second tenant boundary after dataset and catches a valid-dataset, wrong-property request. |
| Identifier resolution | Resolve names and PMS codes to internal ids server-side. Never let the model construct a property_id. |
| Example | Bromley, The Bromley at Brighton Crossing, 12402 → `1865695607790353330` |

### Source index

**Primary sources**

| Object | Metrics | Metric IDs |
|---|---|---|
| `t_oc_agg_occupancy_property` | 15 | `available`, `excluded`, `leased_future`, `notice_rented`, `notice_unrented`, `occupied`, `pct_exposure`, `pct_leased`, `pct_occupied`, `pct_trend`, `rentable`, `total_units`, `vacant`, `vacant_rented`, `vacant_unrented` |
| `t_contact_activity` | 4 | `applied_contact`, `created_contact`, `scheduled_contact`, `toured_contact` |
| `t_ot_agg_resident_activity_property` | 3 | `move_ins`, `move_outs`, `net_move_ins` |
| `t_occupancy_rate` | 1 | `occupancy_rate` |
| `t_oc_agg_occupancy_operational` | 1 | `delayed_move_ins` |
| `pai_journey_1747307582311553649` | 1 | `leased_contact` |
| `prospect_journey` | 1 | `net_applied_contact` |

**Corroborating sources**

| Object | Metric IDs |
|---|---|
| `conversion_triple_base_v1` | `applied_contact`, `scheduled_contact`, `toured_contact` |
| `t_contact_activity` | `applied_contact`, `scheduled_contact`, `toured_contact` |

> Object names only. Datasets are never recorded here.

### Cache policy

| Tier | Contents | Source | Cached | Invalidation | Note |
|---|---|---|---|---|---|
| `tier_0` | org_id, dataset, authorized property set | auth context | no | — | Never derived from data and never cached from a query result. |
| `tier_1` | property identity map, aliases, pathways, definitions, disambiguation rules | this registry | yes | deploy-time | — |
| `tier_2` | metric values for closed periods | — | yes | source object last_modified_time changed | — |
| `tier_3` | current-period values, freshness, row counts | — | no | — | t_oc_agg_occupancy_operational stalled for two weeks undetected. A cached current value there would have gone silently stale. |

### Share rounding rule

**Rule.** Round the percentage to one decimal place, then display with two decimals (trailing zero).

**Derivation.** Inferred from three observed Halo shares. It is the only candidate rule that fits all three.

| Observation | Exact | Halo | 2dp round | 2dp truncate | 1dp round |
|---|---|---|---|---|---|
| 53/92 | 57.6087% | 57.60% | 57.61% (fails) | 57.60% (fits) | 57.6% (fits) |
| 10/33 | 30.3030% | 30.30% | 30.30% (fits) | 30.30% (fits) | 30.3% (fits) |
| 6/19 | 31.5789% | 31.60% | 31.58% (fails) | 31.57% (fails) | 31.6% (fits) |
| 10/14 | 71.4286% | 71.40% | 71.43% (fails) | 71.42% (fails) | 71.4% (fits) |
| 8/11 | 72.7272% | 72.70% | 72.73% (fails) | 72.72% (fails) | 72.7% (fits) |

**Confidence.** Fits 5 of 5 observations and is the sole surviving candidate. Three of the five - 6/19, 10/14 and 8/11 - are individually decisive, since neither two-decimal rounding nor truncation reproduces them. Still inferred from rank-1 rows only.

**Consequence.** Displaying two decimals implies precision the figure does not carry. The second decimal is always zero.

### Rank convention

| Field | Value |
|---|---|
| Rule | Tied values share a rank. CPC and Social both hold rank 5 in the medium breakdown of Newly Created Leads. |
| SQL equivalent | `RANK() OVER (ORDER BY value DESC), not ROW_NUMBER().` |
| Evidence | Supplied directly by Halo: two rows at value 1 both carry rank 5. |
| Consequence | Our earlier breakdowns assigned sequential ranks with an alphabetical tie-break, which is wrong wherever values tie. |
| Affected breakdowns | `created_contact_by_source`, `scheduled_contact_by_source`, `toured_contact_by_source`, `applied_contact_by_source`, `total_applied_contact_by_source` |
| Status | applied to all breakdowns on 2026-08-17 |

### Column map

> Maps the rendered PDF columns A-G to JSON paths, so the document and this registry cannot drift.

| PDF column | JSON path |
|---|---|
| **A** | `metrics[].section` |
| **B** | `metrics[].metric_name` |
| **C** | `metrics[].date_range` |
| **D** | `metrics[].value` |
| **E** | `metrics[].source` |
| **F** | `metrics[].grain` |
| **G** | `pathways[metrics[].query.pathway_id] + metrics[].query.components` |

### Artifact

| Field | Value |
|---|---|
| Role | master |
| Audience | Hyly internal, and the client-facing MCP connector |
| Description | Foundation reference. Tenant-agnostic by construction: no dataset or project is named anywhere, so no substitution slot exists. |
| Companion | Client instances are generated per tenant from this master and carry that tenant's resolved values only. |

---

*Generated from `halo_metric_library.json` (schema 1.0, dated 2026-08-12).*
