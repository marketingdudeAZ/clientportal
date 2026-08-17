# ADR 0022 — What Hyly Actually Delivered (supersedes ADR 0015 §Context/§2/§3)

**Status:** Accepted
**Date:** 2026-08-17
**Supersedes:** ADR 0015 sections "Context", "2. Hyly client skill",
"3. The Hyly × AptIQ join". ADR 0015's decisions on identity
(`hyly_property_id` on the HubSpot company), privacy, and the
`loop_convert_v1` contract still stand.

## Context

ADR 0015 was written on 2026-05-16, before Hyly's beta shipped, and assumed
three pre-aggregated tables in a dataset inside our own project:
`daily_activity_summary`, `contact_submits`, `website_visits`.

**None of those tables exist.** They never did. What Hyly actually delivers,
verified 2026-08-17:

| Project | Dataset | Table | Rows | Access |
|---|---|---|---|---|
| `data-and-reporting-483421` | `hyly` | `ga_hyly_mti` | 328,608 | granted |
| `data-and-reporting-483421` | `hyly` | `ga4_analytics_events` | 10,382,589 | granted |
| `gds-prototype-20190629` | `rpm_living_nrt` | `prospect_journey` | ? | **blocked** |
| `gds-prototype-20190629` | `rpm_living_nrt` | `t_crstal_events` | ? | **blocked** |

Three consequences the original ADR did not anticipate:

1. **The data is in projects we do not own**, plural. ADR 0015's code built
   table refs as `{BIGQUERY_PROJECT_ID}.{dataset}`, which cannot address them.
2. **Hyly does not aggregate.** `ga_hyly_mti` is one row per (contact, event)
   with contact-level funnel milestones denormalised onto every row. We own
   the aggregation layer — that scope did not exist before.
3. **`gds-prototype-20190629` is a vendor prototype project** from 2019 that we
   cannot be granted on. Its NRT tables may be the freshest source available,
   and we cannot reach them from code.

Because `hyly_client` caught every exception and returned `[]`, all of this
presented as "this property has no data" rather than as an error. Nothing
failed. Nothing alerted.

## Decision

### 1. We own the aggregation layer

A rollup, `hyly_daily_activity_v1`, materialised at **property × date ×
channel** into a landing dataset we control (`hyly-data.GA4_Hyly`, US to match
the source). `loop_convert_v1` reads the rollup; nothing downstream reads
vendor tables directly.

This isolates three risks at once: the vendor renaming things, the 10 GB GA4
table ever being scanned on a page load, and channel definitions being Hyly's
rather than ours.

### 2. Attribution is first-touch, and that is a real choice

23.4% of contacts (19,222 of 82,036) carry more than one `source_mapped`
value. We take the earliest non-null value per contact. Switching to
last-touch is one `ORDER BY` in `connectors/hyly/rollup_daily_activity.sql`
and would change every channel number the client sees.

### 3. Channels are normalised by pattern, not by enumeration

Hyly emits **314 distinct `source_mapped` values** with heavy near-duplication
(`Locator/Realtor/Referral` vs `Locator/Realtor referral`; `google` vs
`Google.com` vs `Google Search` vs `GMB`). We normalise with regex rules into
14 channels. New vendor values land in a sensible bucket instead of silently
becoming a new row in a client's chart.

### 4. Sentinel dates are floored at 2015-01-01

Hyly carries `h_cc_event_datetime` values back to **2002-10-26**. Without a
floor every portal chart renders a 24-year x-axis.

### 5. Failure is loud

`hyly_client` now distinguishes *unconfigured* (returns empty — correct, Hyly
is a 15-property beta) from *configured but failing* (raises `HylyQueryError`,
which `/api/loop/channels` surfaces as 502). `tests/test_hyly_contract.py`
asserts the declared vendor columns still exist, the rollup grain is unique,
no sentinel dates leaked, and every Hyly property maps to a HubSpot company.

### 6. Sources are declared, not hardcoded

`connectors/hyly/sources.json` is the registry. Adding a future Hyly table is
an entry plus a rollup SQL file — not a new hardcoded function and a Flask
deploy.

### 7. `visitors` / `known_visitors` are NULL for now

Hyly's funnel table has no page-view counts; those are in
`ga4_analytics_events`, not yet ingested. The columns are retained in
`loop_convert_v1` as NULL to preserve the ADR 0015 contract. The portal's
channel table now leads with **Leads → Toured → Applied → Leased → Lead-to-Lease**
instead of visitor counts.

### 8. Identity is by stored ID only — never by name

Hyly's "The Emery" is HubSpot's **"The Westlyn"** (a rebrand; the domain is
still `theemeryapartments.com`). Name matching would have silently dropped the
property. Unmapped Hyly properties fail the contract test for a human to
resolve; nothing guesses.

## Consequences

**What we gain:** a true per-contact funnel (lead → tour scheduled → toured →
applied → leased) which is *better* than ADR 0015 planned for — it measures
conversion directly instead of inferring it from AptIQ's `leases_last_30`.

**What this costs:** we maintain the aggregation and the channel map. When Hyly
adds a source value, it lands in `Other` until someone extends the rules.

**Scope reality:** this is **15 properties**, not 700. Volume, Hampton Lakes,
Brentwood Downs, Reserve at Orange Park, Ashton on West Dallas, Arbors at
Winters Chapel, LYV Broadway, Remi West Dallas, Lux on Main, The Westlyn, East
at Innovation, The Maddux at Shadowood, The Bromley at Brighton Crossing, The
Beach Club Residences, The Brighton Garden Oaks.

**`emit_loop_events_for_recent_submits` was removed.** It had no callers and
was built against `contact_submits`, which does not exist. Loop event emission
for Hyly leads needs rebuilding against `ga_hyly_mti` — see open items.

## Open items (blocked on Hyly)

1. **The feed is stale.** Both tables last wrote 2026-08-11; no data after
   2026-08-10. Is this a live pipeline that stalled, or a one-time load?
2. **A supported location.** `gds-prototype-20190629` is a prototype project.
   Where does the production equivalent live, and can the service account be
   granted on it?
3. **Attribution quality.** `source_mapped` is null on 22.9% of rows. Is there
   a canonical channel mapping Hyly can share?
4. **Ingesting `ga4_analytics_events`** restores the visitor half of the funnel.

## References

- ADR 0009 — Multifamily Loop (Convert stage)
- ADR 0015 — Hyly Integration (partially superseded here)
- `connectors/hyly/sources.json`, `connectors/hyly/rollup_daily_activity.sql`
- `migrations/0014_hyly_daily_activity_rollup.py`
- `tests/test_hyly_contract.py`
