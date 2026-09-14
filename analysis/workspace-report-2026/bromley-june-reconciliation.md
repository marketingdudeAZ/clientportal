# Workspace report reconciliation — The Bromley at Brighton Crossing, June 2026

*Generated 2026-09-14 by `analysis/workspace-report-2026/reconcile_bromley.py`. Read-only.*

Definitive source: Hyly's data-lake dashboard export for this property and month (680 metric rows). Definitions: the Halo metric library.

## Summary

| Comparison | Match | Mismatch | Unreachable |
|---|---|---|---|
| Export vs. report assembler | 158 | 8 | 0 |
| Export vs. live lake (portal BigQuery client) | 0 | 0 | 166 |

**Live status.** Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. The Hyly MCP connector, the other read-only path, needed re-authorization, so no lake query could run from this session. Every lake value below is therefore unverified live; the SQL the assembler would run is untested against the warehouse (see *Live SQL to verify*).

**Live reads that did run.**

- HubSpot: company 26136316506 (The Bromley at Brighton Crossing, Brighton, Colorado) carries hyly_property_id 1865695607790353330, the id in the export header. Resolved by stored id, not by name (ADR 0022 §8).

## Mismatches

| Section | Metric | Export | Report | Reason |
|---|---|---|---|---|
| Funnel | Created → scheduled rate | 20.7% | 35.9% | Different definitions: Hyly's ratio card is a cohort conversion; the report divides the month's step counts (the June report designs did the same). |
| Funnel | Scheduled → toured rate | 62.5% | 57.6% | Different definitions: Hyly's ratio card is a cohort conversion; the report divides the month's step counts (the June report designs did the same). |
| Funnel | Toured → applied rate | 10.5% | 73.7% | Different definitions: Hyly's ratio card is a cohort conversion; the report divides the month's step counts (the June report designs did the same). |
| Funnel | Applied → leased rate | 64.3% | 57.1% | Different definitions: Hyly's ratio card is a cohort conversion; the report divides the month's step counts (the June report designs did the same). |
| Spend | Zillow leads (spend-source basis) | 12 | 6 | The spend-source basis counts about twice the first-touch leads; the report uses the vendor basis and lists this under known discrepancies. |
| Spend | Google Ads (PPC) leads (spend-source basis) | 14 | 7 | The spend-source basis counts about twice the first-touch leads; the report uses the vendor basis and lists this under known discrepancies. |
| Paid search | Click-through rate recomputed from clicks ÷ impressions | 5.20% | 5.21% | The export rounds impressions to 21.5K, so recomputing gives 5.21% against the platform's 5.20%. The report shows the platform's reported rate. |
| Paid search | Cost per conversion recomputed from spend ÷ conversions | $71.80 | 71.85 | Spend is shown rounded to the dollar in the export; the report shows the platform's reported cost per conversion. |

## Biggest discrepancies worth Kyle's attention

These are differences between sources, not errors in the report. Each is either shown on the report under *Known discrepancies* or listed as an open question.

1. **Google Ads spend: $12,140 (spend manager) vs. $5,269 (ad platform).** A $6,871 gap, 2.3×. The report uses the spend manager for vendor spend and the platform for paid-search efficiency, and states both.
2. **Funnel conversion: step rates vs. Hyly's ratio cards.** June step counts give 35.9% created → scheduled; Hyly's ratio card gives 20.7%. Toured → applied is 73.7% vs. 10.5%. The cards are cohort conversions; the June report designs (and this report) divide step counts. Both are valid, but they must never share a label.
3. **Lead basis doubles.** The spend-source basis shows Zillow 12 and Google PPC 14 leads; the vendor basis and first-touch source both show 6 and 7. The 2× pattern suggests the spend-source view double-counts.
4. **Ad conversions vs. CRM leads: 73.33 vs. 7.** The platform counts about ten conversions per CRM lead, and CPC is the medium on only 1 of those 7 Google Ads first-touch leads.
5. **Occupancy: 88.96% month-end vs. 93.59% daily average.** Different objects and unit bases (299 vs. 314). Notice data is missing before 2026-07-27, so June exposure (5.69%) and future leases (16) read low.
6. **The design PDFs carried numbers the export doesn't support.** "Hyly assistant 3 created" matches the listing section's 3 leads, not a lead source; Google Ads toured shows 0 in the designs but 1 in the export's multi-touch toured breakdown; Meta's "1 lead" has no matching first-touch source. The report follows the export: Meta leads are null with a gap.
7. **The metric library's own breakdown disagrees with the export.** The library derived Google Business 13 / Zillow 5 / Google.com 4 new leads by source; the export reports 10 / 6 / 6. The live first-touch query follows the library, so expect these three rows to differ live until the dedupe rule is settled.

## Full table

Status compares the export with the report assembler's value. *Live* is the value the portal's live gatherer returned, or why it has none.

| Section | Metric | Export | Report | Status | Live | Reason |
|---|---|---|---|---|---|---|
| Occupancy | Total units | 299 | 299 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| Occupancy | Occupied units | 266 | 266 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| Occupancy | Vacant units | 33 | 33 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| Occupancy | Available units | 17 | 17 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| Occupancy | Vacant rented | 16 | 16 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| Occupancy | Vacant unrented | 17 | 17 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| Occupancy | Future leases | 16 | 16 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| Occupancy | Move-ins | 11 | 11 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| Occupancy | Move-outs | 23 | 23 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| Occupancy | Net move-ins (recomputed) | -12 | -12 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| Occupancy | Delayed move-ins | 3 | 3 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| Occupancy | Leased rate (recomputed) | 94.31% | 94.31% | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| Occupancy | Current occupancy (recomputed) | 88.96% | 88.96% | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| Occupancy | Exposure rate (recomputed) | 5.69% | 5.69% | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| Occupancy | Average occupancy rate | 93.59% | 93.59% | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| Funnel | Newly created leads | 92 | 92 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| Funnel | 1st scheduled | 33 | 33 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| Funnel | 1st toured | 19 | 19 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| Funnel | 1st applied | 14 | 14 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| Funnel | Leased | 8 | 8 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| Funnel | Net applied | 11 | 11 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| Funnel | Days lead → scheduled | 11.02083333 | 11.02083333 | match | unreachable: No definition in the metric library; the live gatherer returns a gap. |  |
| Funnel | Days scheduled → toured | 2.010416667 | 2.010416667 | match | unreachable: No definition in the metric library; the live gatherer returns a gap. |  |
| Funnel | Days toured → applied | 4.333333333 | 4.333333333 | match | unreachable: No definition in the metric library; the live gatherer returns a gap. |  |
| Funnel | Days applied → leased | 17.75 | 17.75 | match | unreachable: No definition in the metric library; the live gatherer returns a gap. |  |
| Funnel | Days lead → leased | 32.80208333 | 32.80208333 | match | unreachable: No definition in the metric library; the live gatherer returns a gap. |  |
| Funnel | Created → scheduled rate | 20.7% | 35.9% | mismatch | unreachable: Derived in the report. | Different definitions: Hyly's ratio card is a cohort conversion; the report divides the month's step counts (the June report designs did the same). |
| Funnel | Scheduled → toured rate | 62.5% | 57.6% | mismatch | unreachable: Derived in the report. | Different definitions: Hyly's ratio card is a cohort conversion; the report divides the month's step counts (the June report designs did the same). |
| Funnel | Toured → applied rate | 10.5% | 73.7% | mismatch | unreachable: Derived in the report. | Different definitions: Hyly's ratio card is a cohort conversion; the report divides the month's step counts (the June report designs did the same). |
| Funnel | Applied → leased rate | 64.3% | 57.1% | mismatch | unreachable: Derived in the report. | Different definitions: Hyly's ratio card is a cohort conversion; the report divides the month's step counts (the June report designs did the same). |
| Spend | Total spend | $15,715.00 | 15,715 | match | unreachable: Outside the metric library allowlist; the live gatherer returns a gap. |  |
| Spend | Cost per lease (recomputed) | $1,964.38 | 1,964.38 | match | unreachable: Outside the metric library allowlist; the live gatherer returns a gap. |  |
| Spend | Google Ads spend | $12,140 | 12,140 | match | unreachable: Outside the metric library allowlist; the live gatherer returns a gap. |  |
| Spend | Google Ads share of spend (recomputed) | 77.30% | 77.25% | match | unreachable: Outside the metric library allowlist; the live gatherer returns a gap. |  |
| Spend | Zillow spend | $1,875 | 1,875 | match | unreachable: Outside the metric library allowlist; the live gatherer returns a gap. |  |
| Spend | Zillow share of spend (recomputed) | 11.90% | 11.93% | match | unreachable: Outside the metric library allowlist; the live gatherer returns a gap. |  |
| Spend | Paid social (Meta) spend | $1,200 | 1,200 | match | unreachable: Outside the metric library allowlist; the live gatherer returns a gap. |  |
| Spend | Paid social (Meta) share of spend (recomputed) | 7.60% | 7.64% | match | unreachable: Outside the metric library allowlist; the live gatherer returns a gap. |  |
| Spend | Zillow cost per lead (recomputed) | $312.50 | 312.50 | match | unreachable: Outside the metric library allowlist; the live gatherer returns a gap. |  |
| Spend | Zillow leads (vendor basis) | 6 | 6 | match | unreachable: Outside the metric library allowlist; the live gatherer returns a gap. |  |
| Spend | Google Ads cost per lead (recomputed) | $1,734.29 | 1,734.29 | match | unreachable: Outside the metric library allowlist; the live gatherer returns a gap. |  |
| Spend | Google Ads leads (vendor basis) | 7 | 7 | match | unreachable: Outside the metric library allowlist; the live gatherer returns a gap. |  |
| Spend | Zillow leads (spend-source basis) | 12 | 6 | mismatch | unreachable: Outside the metric library allowlist; the live gatherer returns a gap. | The spend-source basis counts about twice the first-touch leads; the report uses the vendor basis and lists this under known discrepancies. |
| Spend | Google Ads (PPC) leads (spend-source basis) | 14 | 7 | mismatch | unreachable: Outside the metric library allowlist; the live gatherer returns a gap. | The spend-source basis counts about twice the first-touch leads; the report uses the vendor basis and lists this under known discrepancies. |
| First touch | New leads · Property website | 53 | 53 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| First touch | New leads · Google Business / Maps | 10 | 10 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| First touch | New leads · Google Ads (PPC) | 7 | 7 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| First touch | New leads · Google.com | 6 | 6 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| First touch | New leads · Zillow | 6 | 6 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| First touch | New leads · Apple Maps | 4 | 4 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| First touch | New leads · Walking / driving by | 4 | 4 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| First touch | New leads · Bing | 1 | 1 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| First touch | New leads · Social posting | 1 | 1 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| First touch | 1st Scheduled · Property website | 10 | 10 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| First touch | 1st Scheduled · Google.com | 6 | 6 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| First touch | 1st Scheduled · Zillow | 6 | 6 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| First touch | 1st Scheduled · Google Ads (PPC) | 5 | 5 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| First touch | 1st Scheduled · Walking / driving by | 4 | 4 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| First touch | 1st Scheduled · Apple Maps | 1 | 1 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| First touch | 1st Scheduled · Bing | 1 | 1 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| First touch | 1st Toured · Property website | 6 | 6 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| First touch | 1st Toured · Google.com | 4 | 4 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| First touch | 1st Toured · Walking / driving by | 3 | 3 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| First touch | 1st Toured · Google Ads (PPC) | 2 | 2 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| First touch | 1st Toured · Zillow | 2 | 2 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| First touch | 1st Toured · Apple Maps | 1 | 1 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| First touch | 1st Toured · Bing | 1 | 1 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| First touch | 1st Applied · Property website | 10 | 10 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| First touch | 1st Applied · Zillow | 2 | 2 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| First touch | 1st Applied · Apple Maps | 1 | 1 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| First touch | 1st Applied · Google Business / Maps | 1 | 1 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| First touch | Leased · Property website | 8 | 8 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| First touch | New leads by medium · Organic | 67 | 67 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| First touch | Medium share · Organic (recomputed) | 72.80% | 72.83% | match | unreachable: Derived in the report. |  |
| First touch | New leads by medium · Direct | 17 | 17 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| First touch | Medium share · Direct (recomputed) | 18.50% | 18.48% | match | unreachable: Derived in the report. |  |
| First touch | New leads by medium · Affiliate | 4 | 4 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| First touch | Medium share · Affiliate (recomputed) | 4.30% | 4.35% | match | unreachable: Derived in the report. |  |
| First touch | New leads by medium · Print | 2 | 2 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| First touch | Medium share · Print (recomputed) | 2.20% | 2.17% | match | unreachable: Derived in the report. |  |
| First touch | New leads by medium · CPC | 1 | 1 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| First touch | Medium share · CPC (recomputed) | 1.10% | 1.09% | match | unreachable: Derived in the report. |  |
| First touch | New leads by medium · Social | 1 | 1 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| First touch | Medium share · Social (recomputed) | 1.10% | 1.09% | match | unreachable: Derived in the report. |  |
| Multi-touch | Created prospects | 477 | 477 | match | unreachable: Multi-touch influence isn't defined in the metric library; the live gatherer returns a gap. |  |
| Multi-touch | Scheduled prospects | 33 | 33 | match | unreachable: Multi-touch influence isn't defined in the metric library; the live gatherer returns a gap. |  |
| Multi-touch | Toured prospects | 19 | 19 | match | unreachable: Multi-touch influence isn't defined in the metric library; the live gatherer returns a gap. |  |
| Multi-touch | Applied prospects | 14 | 14 | match | unreachable: Multi-touch influence isn't defined in the metric library; the live gatherer returns a gap. |  |
| Multi-touch | Leased prospects | 8 | 8 | match | unreachable: Multi-touch influence isn't defined in the metric library; the live gatherer returns a gap. |  |
| Multi-touch | Created Prospects · Property website | 422 | 422 | match | unreachable: Multi-touch influence isn't defined in the metric library; the live gatherer returns a gap. |  |
| Multi-touch | Created Prospects · Zillow | 38 | 38 | match | unreachable: Multi-touch influence isn't defined in the metric library; the live gatherer returns a gap. |  |
| Multi-touch | Created Prospects · Google Ads (PPC) | 35 | 35 | match | unreachable: Multi-touch influence isn't defined in the metric library; the live gatherer returns a gap. |  |
| Multi-touch | Created Prospects · Google Business / Maps | 22 | 22 | match | unreachable: Multi-touch influence isn't defined in the metric library; the live gatherer returns a gap. |  |
| Multi-touch | Created Prospects · ApartmentList.com | 14 | 14 | match | unreachable: Multi-touch influence isn't defined in the metric library; the live gatherer returns a gap. |  |
| Multi-touch | Created Prospects · Apple Maps | 11 | 11 | match | unreachable: Multi-touch influence isn't defined in the metric library; the live gatherer returns a gap. |  |
| Multi-touch | Created Prospects · Google.com | 6 | 6 | match | unreachable: Multi-touch influence isn't defined in the metric library; the live gatherer returns a gap. |  |
| Multi-touch | Created Prospects · Walking / driving by | 5 | 5 | match | unreachable: Multi-touch influence isn't defined in the metric library; the live gatherer returns a gap. |  |
| Multi-touch | Created Prospects · RENTCafe.com ILS | 2 | 2 | match | unreachable: Multi-touch influence isn't defined in the metric library; the live gatherer returns a gap. |  |
| Multi-touch | Created Prospects · Transfer unit | 2 | 2 | match | unreachable: Multi-touch influence isn't defined in the metric library; the live gatherer returns a gap. |  |
| Multi-touch | Created Prospects · google (untagged) | 1 | 1 | match | unreachable: Multi-touch influence isn't defined in the metric library; the live gatherer returns a gap. |  |
| Multi-touch | Created Prospects · Former / referral resident | 1 | 1 | match | unreachable: Multi-touch influence isn't defined in the metric library; the live gatherer returns a gap. |  |
| Multi-touch | Created Prospects · AirBnB | 1 | 1 | match | unreachable: Multi-touch influence isn't defined in the metric library; the live gatherer returns a gap. |  |
| Multi-touch | Created Prospects · Bing | 1 | 1 | match | unreachable: Multi-touch influence isn't defined in the metric library; the live gatherer returns a gap. |  |
| Multi-touch | Created Prospects · Social posting | 1 | 1 | match | unreachable: Multi-touch influence isn't defined in the metric library; the live gatherer returns a gap. |  |
| Multi-touch | Scheduled Prospects · Property website | 18 | 18 | match | unreachable: Multi-touch influence isn't defined in the metric library; the live gatherer returns a gap. |  |
| Multi-touch | Scheduled Prospects · Zillow | 6 | 6 | match | unreachable: Multi-touch influence isn't defined in the metric library; the live gatherer returns a gap. |  |
| Multi-touch | Scheduled Prospects · Google Ads (PPC) | 5 | 5 | match | unreachable: Multi-touch influence isn't defined in the metric library; the live gatherer returns a gap. |  |
| Multi-touch | Scheduled Prospects · Walking / driving by | 4 | 4 | match | unreachable: Multi-touch influence isn't defined in the metric library; the live gatherer returns a gap. |  |
| Multi-touch | Scheduled Prospects · Apple Maps | 1 | 1 | match | unreachable: Multi-touch influence isn't defined in the metric library; the live gatherer returns a gap. |  |
| Multi-touch | Scheduled Prospects · google (untagged) | 1 | 1 | match | unreachable: Multi-touch influence isn't defined in the metric library; the live gatherer returns a gap. |  |
| Multi-touch | Toured Prospects · Hyly assistant | 9 | 9 | match | unreachable: Multi-touch influence isn't defined in the metric library; the live gatherer returns a gap. |  |
| Multi-touch | Toured Prospects · Property website | 7 | 7 | match | unreachable: Multi-touch influence isn't defined in the metric library; the live gatherer returns a gap. |  |
| Multi-touch | Toured Prospects · Walking / driving by | 3 | 3 | match | unreachable: Multi-touch influence isn't defined in the metric library; the live gatherer returns a gap. |  |
| Multi-touch | Toured Prospects · google (untagged) | 3 | 3 | match | unreachable: Multi-touch influence isn't defined in the metric library; the live gatherer returns a gap. |  |
| Multi-touch | Toured Prospects · Google.com | 2 | 2 | match | unreachable: Multi-touch influence isn't defined in the metric library; the live gatherer returns a gap. |  |
| Multi-touch | Toured Prospects · Zillow | 1 | 1 | match | unreachable: Multi-touch influence isn't defined in the metric library; the live gatherer returns a gap. |  |
| Multi-touch | Toured Prospects · Apple Maps | 1 | 1 | match | unreachable: Multi-touch influence isn't defined in the metric library; the live gatherer returns a gap. |  |
| Multi-touch | Toured Prospects · Google Ads (PPC) | 1 | 1 | match | unreachable: Multi-touch influence isn't defined in the metric library; the live gatherer returns a gap. |  |
| Multi-touch | Applied Prospects · Property website | 9 | 9 | match | unreachable: Multi-touch influence isn't defined in the metric library; the live gatherer returns a gap. |  |
| Multi-touch | Applied Prospects · Zillow | 2 | 2 | match | unreachable: Multi-touch influence isn't defined in the metric library; the live gatherer returns a gap. |  |
| Multi-touch | Applied Prospects · Google Business / Maps | 1 | 1 | match | unreachable: Multi-touch influence isn't defined in the metric library; the live gatherer returns a gap. |  |
| Multi-touch | Applied Prospects · Apple Maps | 1 | 1 | match | unreachable: Multi-touch influence isn't defined in the metric library; the live gatherer returns a gap. |  |
| Multi-touch | Applied Prospects · Google.com | 1 | 1 | match | unreachable: Multi-touch influence isn't defined in the metric library; the live gatherer returns a gap. |  |
| Multi-touch | Applied Prospects · Hyly assistant | 1 | 1 | match | unreachable: Multi-touch influence isn't defined in the metric library; the live gatherer returns a gap. |  |
| Multi-touch | Applied Prospects · RentCafe | 1 | 1 | match | unreachable: Multi-touch influence isn't defined in the metric library; the live gatherer returns a gap. |  |
| Multi-touch | Leased Prospects · Property website | 7 | 7 | match | unreachable: Multi-touch influence isn't defined in the metric library; the live gatherer returns a gap. |  |
| Multi-touch | Leased Prospects · Zillow | 1 | 1 | match | unreachable: Multi-touch influence isn't defined in the metric library; the live gatherer returns a gap. |  |
| Website | Users | 1,818 | 1,818 | match | unreachable: GA4 has no connector on the portal; the live gatherer returns a gap. |  |
| Website | Sessions | 2,566 | 2,566 | match | unreachable: GA4 has no connector on the portal; the live gatherer returns a gap. |  |
| Website | Engaged sessions | 1,412 | 1,412 | match | unreachable: GA4 has no connector on the portal; the live gatherer returns a gap. |  |
| Website | Conversions | 36 | 36 | match | unreachable: GA4 has no connector on the portal; the live gatherer returns a gap. |  |
| Website | Engagement rate (recomputed) | 55.03% | 55.03% | match | unreachable: GA4 has no connector on the portal; the live gatherer returns a gap. |  |
| Website | Average engagement time (s) | 55s | 55 | match | unreachable: GA4 has no connector on the portal; the live gatherer returns a gap. |  |
| Website | Bounce rate (1 − engagement, recomputed) | 44.97% | 44.97% | match | unreachable: GA4 has no connector on the portal; the live gatherer returns a gap. |  |
| Website | Floor plans page views | 1,568 | 1,568 | match | unreachable: GA4 has no connector on the portal; the live gatherer returns a gap. |  |
| Website | Sessions · google | 1,478 | 1,478 | match | unreachable: GA4 has no connector on the portal; the live gatherer returns a gap. |  |
| Website | Engaged sessions · google | 1,022 | 1,022 | match | unreachable: GA4 has no connector on the portal; the live gatherer returns a gap. |  |
| Website | Conversions · google | 29 | 29 | match | unreachable: GA4 has no connector on the portal; the live gatherer returns a gap. |  |
| Website | Sessions · (direct) | 499 | 499 | match | unreachable: GA4 has no connector on the portal; the live gatherer returns a gap. |  |
| Website | Engaged sessions · (direct) | 192 | 192 | match | unreachable: GA4 has no connector on the portal; the live gatherer returns a gap. |  |
| Website | Conversions · (direct) | 6 | 6 | match | unreachable: GA4 has no connector on the portal; the live gatherer returns a gap. |  |
| Website | Sessions · Zillow | 352 | 352 | match | unreachable: GA4 has no connector on the portal; the live gatherer returns a gap. |  |
| Website | Engaged sessions · Zillow | 51 | 51 | match | unreachable: GA4 has no connector on the portal; the live gatherer returns a gap. |  |
| Website | Conversions · Zillow | 0 | 0 | match | unreachable: GA4 has no connector on the portal; the live gatherer returns a gap. |  |
| Website | Bounce rate · Zillow (recomputed) | 85.51% | 85.51% | match | unreachable: GA4 has no connector on the portal; the live gatherer returns a gap. |  |
| Website | Sessions · bing | 104 | 104 | match | unreachable: GA4 has no connector on the portal; the live gatherer returns a gap. |  |
| Website | Engaged sessions · bing | 70 | 70 | match | unreachable: GA4 has no connector on the portal; the live gatherer returns a gap. |  |
| Website | Conversions · bing | 1 | 1 | match | unreachable: GA4 has no connector on the portal; the live gatherer returns a gap. |  |
| Website | Views per user · floor plan B2 | 3.74 | 3.74 | match | unreachable: GA4 has no connector on the portal; the live gatherer returns a gap. |  |
| Website | Views per user · floor plan A1 | 3.58 | 3.58 | match | unreachable: GA4 has no connector on the portal; the live gatherer returns a gap. |  |
| Website | Views per user · floor plan B1 | 3.17 | 3.17 | match | unreachable: GA4 has no connector on the portal; the live gatherer returns a gap. |  |
| Website | Views per user · floor plan A2 | 3.04 | 3.04 | match | unreachable: GA4 has no connector on the portal; the live gatherer returns a gap. |  |
| Website | Views per user · floor plan S2 | 2.17 | 2.17 | match | unreachable: GA4 has no connector on the portal; the live gatherer returns a gap. |  |
| Website | Views per user · floor plan A3 | 2.08 | 2.08 | match | unreachable: GA4 has no connector on the portal; the live gatherer returns a gap. |  |
| Paid search | Impressions | 21.5K | 21.5K | match | unreachable: The Google Ads API has no connector on the portal; the live gatherer returns a gap. |  |
| Paid search | Clicks | 1121 | 1,121 | match | unreachable: The Google Ads API has no connector on the portal; the live gatherer returns a gap. |  |
| Paid search | Click-through rate | 5.20% | 5.20% | match | unreachable: The Google Ads API has no connector on the portal; the live gatherer returns a gap. |  |
| Paid search | Cost per click | $4.70 | 4.70 | match | unreachable: The Google Ads API has no connector on the portal; the live gatherer returns a gap. |  |
| Paid search | Ad conversions | 73.33 | 73.33 | match | unreachable: The Google Ads API has no connector on the portal; the live gatherer returns a gap. |  |
| Paid search | Cost per conversion | $71.80 | 71.80 | match | unreachable: The Google Ads API has no connector on the portal; the live gatherer returns a gap. |  |
| Paid search | Platform spend | $5,269 | 5,269 | match | unreachable: The Google Ads API has no connector on the portal; the live gatherer returns a gap. |  |
| Paid search | Click-through rate recomputed from clicks ÷ impressions | 5.20% | 5.21% | mismatch | unreachable: The Google Ads API has no connector on the portal; the live gatherer returns a gap. | The export rounds impressions to 21.5K, so recomputing gives 5.21% against the platform's 5.20%. The report shows the platform's reported rate. |
| Paid search | Cost per conversion recomputed from spend ÷ conversions | $71.80 | 71.85 | mismatch | unreachable: The Google Ads API has no connector on the portal; the live gatherer returns a gap. | Spend is shown rounded to the dollar in the export; the report shows the platform's reported cost per conversion. |
| Listings | Listing impressions | 1,718 | 1,718 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| Listings | Listing leads | 3 | 3 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |
| Listings | Listing media views | 4 | 4 | match | unreachable: Lake unreachable locally: the configured BigQuery service-account key file isn't present on this machine. |  |

## Export metrics the report doesn't carry

Occupancy Trend (94.31%, formulation unresolved in the library), Rentable and Excluded units, the notice fields (unpopulated before 2026-07-27), Total Applied (a structural duplicate of 1st Applied), the paid-source cost-per-stage cards, GA4 new users, views, event counts and page titles, Google Ads CPM, conversion rate, revenue and the unlabeled campaign and keyword rows, the weekly bump-chart series, and Facebook / Instagram organic social.

## Live SQL to verify

When credentials are available, run this script with `--live --env-file <path>`. The gatherer issues these read-only queries (50 MB byte cap, metric-library allowlist):

- `t_oc_agg_occupancy_property`: newest `as_of_date` ≤ period end; stocks.
- `t_ot_agg_resident_activity_property`: `SUM(IF(event_type=…, count, 0))` over `date`.
- `t_oc_agg_occupancy_operational`: `delayed_move_ins` at the newest `as_of_date`.
- `t_occupancy_rate`: `AVG(SAFE_DIVIDE(occupied, units))` over `DATE(created_at)`.
- `t_contact_activity`: created (distinct `contact_name`, `contact_status != 'Leased'`), scheduled / toured / applied (distinct `contact_id` on `first_scheduled_dt` / `first_completed_dt` / `first_application_dt`), and the `mta_first_source_name` / `mta_first_medium_name` breakdowns.
- `pai_journey_<org>`: distinct `contact_id` with `event_name = 'h_ms_lease'`, plus the joined source breakdown.
- `prospect_journey`: `pms_Application` less `pms_CancelApplication` distinct contacts.

Assumptions to confirm on the first live run: `property_id` is INT64 in every object; HubSpot's `hyly_property_id` is the same 19-digit id; `contact_created_date` and `event_date` cast cleanly with `DATE()`.

