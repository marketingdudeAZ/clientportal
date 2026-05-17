# ADR 0015 — Hyly Integration for Convert + Optimize

**Status:** Accepted
**Date:** 2026-05-16

## Context

The Convert stage of the Loop (ADR 0009) was the gap that nothing else
could compound without filling. Today we have AptIQ giving us
`leases_last_30` per property — useful but only the very end of the
funnel. We have no visibility into:
- Where leads come from (which channel/campaign drove which lead)
- The journey before conversion (what pages did the prospect visit, what
  brought them, was it the first or twelfth visit)
- Per-channel cost-per-lead and cost-per-lease

Hyly fills this gap. Their beta rollout (June 2026) gives us three BQ
tables in a dataset they manage:

1. **`daily_activity_summary`** — per-property × per-day × per-source
   rollup: visitors, known_visitors, total_views, converted_contacts
2. **`contact_submits`** — every form fill with full UTM stack
   (utm_source, utm_medium, utm_campaign, utm_term, utm_content), gclid,
   detected_source, hybeacon enrichment, page context, and `act_url`
   deep-linking back to Hyly CRM
3. **`website_visits`** — every known-visitor page view with the same
   attribution columns

The join key is **`hyly_property_id`** — Hyly's property ID (a string
like `"1839261086288116013"`), which will live on the HubSpot company
record as a new custom property mirroring how `aptiq_property_id` works.

## Decision

### 1. New HubSpot company property

Add custom property `hyly_property_id` (string) to HubSpot CRM
companies. Owned by the same backfill flow that owns `aptiq_property_id`
(maps from a daily Hyly properties listing). Until Hyly's beta ships,
the property is empty on every record.

### 2. Hyly client skill — `webhook-server/hyly_client.py`

A read-only BQ client (Hyly's data lands in BQ, we query it there).

```python
# All functions assume Hyly's BQ dataset is configured via env
# BIGQUERY_HYLY_DATASET (project is the same as our prod project).

def get_daily_activity(
    hyly_property_id: str,
    *,
    start_date: str,   # YYYY-MM-DD
    end_date: str,
) -> list[dict]:
    """Per-day × per-source rollup for one property."""

def get_contact_submits(
    hyly_property_id: str,
    *,
    start_date: str,
    end_date: str,
    limit: int = 5000,
) -> list[dict]:
    """Lead-level events with UTM attribution. Newest first."""

def get_website_visits(
    hyly_property_id: str,
    *,
    start_date: str,
    end_date: str,
    limit: int = 10000,
) -> list[dict]:
    """Page-view-level journey data."""

def get_channel_summary(
    hyly_property_id: str,
    *,
    start_date: str,
    end_date: str,
) -> dict:
    """Aggregated by channel: visitors, contacts, conversion rate.
    Used by Convert stage + forecast inputs.
    Returns: {
      "Google PayPerClick (PPC)": {visitors, known_visitors, contacts, conv_rate},
      "apartments.com": {...},
      ...
    }
    """
```

### 3. The Hyly × AptIQ join — Convert stage backbone

A BigQuery view created by a migration:

```sql
CREATE OR REPLACE VIEW `{project}.{dataset}.loop_convert_v1` AS

WITH hyly_monthly_channel AS (
  SELECT
    h.property_id AS hyly_property_id,
    DATE_TRUNC(DATE(h.event_date), MONTH) AS month,
    h.source,
    SUM(h.visitors) AS visitors,
    SUM(h.known_visitors) AS known_visitors,
    SUM(h.converted_contacts) AS contacts,
  FROM `{hyly_project}.{hyly_dataset}.daily_activity_summary` h
  GROUP BY hyly_property_id, month, source
),

property_map AS (
  -- Joins Hyly's property_id to HubSpot uuid via the rpm_properties
  -- dimension table (synced nightly from HubSpot).
  SELECT
    rp.property_uuid,
    rp.hyly_property_id,
    rp.aptiq_property_id,
    rp.name,
    rp.market
  FROM `{project}.{dataset}.rpm_properties` rp
  WHERE rp.hyly_property_id IS NOT NULL
)

SELECT
  pm.property_uuid,
  pm.name,
  pm.market,
  hm.month,
  hm.source,
  hm.visitors,
  hm.known_visitors,
  hm.contacts,
  a.leases_last_30 AS aptiq_leases_last_30,
  a.applications_last_30,
  SAFE_DIVIDE(hm.contacts, hm.visitors) AS contact_rate,
  SAFE_DIVIDE(a.leases_last_30, hm.contacts) AS lead_to_lease_rate
FROM hyly_monthly_channel hm
JOIN property_map pm USING (hyly_property_id)
LEFT JOIN `{project}.{dataset}.aptiq_snapshots` a
  ON a.property_uuid = pm.property_uuid
 AND DATE(a.snapshot_month) = hm.month
```

This is the canonical Convert-stage view. The Optimize stage and
Forecasting Engine read from here, never from raw Hyly/AptIQ tables.

### 4. Loop event emission for Hyly data

Every Hyly contact submit becomes a Loop event:

```python
# Daily cron (Hyly has no webhook — we poll)
new_submits = hyly_client.get_contact_submits(
    hyly_property_id=h_pid,
    start_date=yesterday,
    end_date=today,
)
for submit in new_submits:
    loop_writer.record(
        stage="convert",
        event_type="lead_submitted",
        property_uuid=uuid,
        source="hyly",
        source_id=submit["act_url"],          # idempotency key
        occurred_at=submit["created_at"],
        magnitude=1.0,
        payload={
            "email_hash": hash(submit["email"]),  # privacy
            "utm_source": submit["api.in_utm_source"],
            "utm_medium": submit["api.in_utm_medium"],
            "utm_campaign": submit["api.in_utm_campaign"],
            "detected_source": submit["detected_source"],
            "hybeacon_channel": submit["api.hybeacon_channel_name"],
            "page": submit["Page"],
        },
    )
```

Lead-level visibility flows into the unified Loop timeline.

### 5. The backfill flow

When `hyly_property_id` is populated on a company for the first time:
1. Trigger `/api/internal/hyly-backfill?company_id=X&days_back=180`
2. Pulls 6 months of Hyly history into Loop events
3. Materializes the new property in `loop_convert_v1` retroactively

This mirrors the AptIQ historical backfill pattern (built 2026-05-15).

### 6. Privacy

- We hash the renter's email before writing to Loop events (we never
  store PII in the Loop store)
- The `act_url` from Hyly is stored as the source_id — it deep-links
  authorized users to the full Hyly CRM record where the un-hashed
  identity lives
- This keeps personally identifiable info in Hyly's compliance perimeter

## Consequences

**Trade-offs accepted:**
- Hyly data lives in a separate BQ dataset we don't own — schema changes
  on their side could break our queries silently. Mitigation: the
  `loop_convert_v1` view is defined by us; if Hyly renames a column,
  the view fails loudly on the next forecast run.
- Daily polling rather than webhooks (Hyly doesn't push). 24h lag on
  the freshest leads — acceptable for forecast inputs.
- Beta rollout = the first few properties might have data quality
  issues we'll discover the hard way.

**What we gain:**
- True per-channel attribution from click → lease
- Lead-level events in the Loop timeline (client sees real prospect
  activity, not aggregate counts)
- Forecast inputs with channel granularity (better predictions)
- Foundation for the Optimize stage's budget-shift recommendations

## References

- ADR 0009 — Multifamily Loop (Convert stage definition)
- ADR 0010 — Loop Event Bus (where lead_submitted events live)
- `webhook-server/hyly_client.py` (BQ reader)
- `migrations/0005_loop_convert_v1_view.py`
- `docs/RUNBOOKS/hyly-onboarding.md` (when beta rolls out)
