# Pre-lead funnel skill (Layer 2)

Answers one question for a single property: **is low lead volume caused by
traffic not arriving, or by traffic arriving and failing to convert?**

Built for the August 2026 Atwood at Rivulon thread. Property-agnostic.
Findings, data-availability position and the strategic framing live in
[`docs/analysis/atwood-rivulon-pre-lead-funnel.md`](../../docs/analysis/atwood-rivulon-pre-lead-funnel.md).

```bash
python3 -m skills.pre_lead_funnel \
  --property "The Atwood at Rivulon" \
  --start 2026-01-01 --end 2026-08-10 \
  --page-pattern "%/floorplans%" --page-pattern "%/availability%"
```

| Module | Role |
|---|---|
| `probe.py` | Establishes empirically what can be filled — credentials, table, session key, coverage, which GA4 events the site fires. Run this first. |
| `queries.py` | Session-grain SQL against Hyly's GA4 export copy. |
| `stats.py` | `Rate` (always carries numerator/denominator + Wilson CI + instability flag), `Unavailable`, `Cohort`. |
| `report.py` | Widget assembly and the availability gates. |
| `render.py` | Markdown output. |

## Rules the code enforces

- Nothing smoothed, modelled or interpolated — there is no fill logic anywhere.
- Every rate shows its raw numerator and denominator.
- A zero denominator renders **undefined**, never 0%.
- Unstable months flagged inline: <30 conversions, or a 95% Wilson interval
  wider than ±35% of the estimate. The flag never adjusts the value.
- Unavailable metrics are named as unavailable with their blocker. No proxy is
  ever substituted; adjacent metrics are offered as an explicit choice.
- The CLI **refuses to render** without warehouse access, rather than emitting
  a report of empty widgets that reads as "no traffic" (ADR 0022).

## Env

`BIGQUERY_PROJECT_ID`, `BIGQUERY_SERVICE_ACCOUNT_JSON` (required);
`BIGQUERY_HYLY_PROJECT`, `BIGQUERY_HYLY_DATASET` (for the CRM lead cross-check);
`GOOGLE_ADS_*` (widgets 7–8, not yet provisioned).

## Prerequisites that are not code

1. Atwood must carry a `hyly_property_id` — session data exists only for the
   15-property Hyly beta.
2. `ga4_analytics_events` must be ingested (ADR 0022 open item).
3. `--page-pattern` values must be verified against real `page_location`
   values; the widgets that need them report unavailable rather than guess.
