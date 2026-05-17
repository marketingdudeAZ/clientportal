"""Loop Auto-pilot mode handler (Phase 2 / ADR 0019, ADR 0009).

When a property's HubSpot company has `loop_mode='auto-pilot'`, this
handler auto-approves bounded recommendations and emits the
`recommendation_approved` event as if the property owner had clicked
Approve in the portal.

Bounded means:
  - Budget shift recommendations max 15% of any channel
  - No single absolute change > $500
  - Forecast impact must be net-positive
  - Action must be in AUTO_PILOT_SAFE_ACTIONS
  - Property must have been on auto-pilot for ≥ 7 days
    (warm-up period — new auto-pilot properties get observed before
    auto-applying)

Designed to be called either:
  - From a cron job that scans for unactioned `recommendation_proposed`
    events on auto-pilot properties (recommended)
  - From the Forecasting Engine immediately after forecast_run completes
    (faster but couples Optimize → execution; the cron pattern is
    cleaner for observability)

Module surface:

  process_pending_recommendations(*, lookback_hours=24) -> dict
      Cron entrypoint. Returns summary {scanned, auto_approved, skipped}.

  process_property(uuid, *, dry_run=False) -> list[dict]
      Process all pending recommendations for one property.

  is_safe_to_auto_apply(recommendation, property_uuid) -> (bool, reason)
      The gatekeeper. Returns False + reason for rejection.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)


# Recommendation actions that are SAFE for auto-pilot. Anything else
# falls through to manual co-pilot review even on auto-pilot properties.
AUTO_PILOT_SAFE_ACTIONS = {
    "shift_budget",
    "refresh-seo",
    "generate-aeo-batch",
}

# Bounds applied to budget-shift recommendations
MAX_PERCENT_OF_CHANNEL = 0.15   # ≤ 15% of any channel's spend
MAX_ABSOLUTE_AMOUNT    = 500.0  # ≤ $500 in any single shift

# Property must have been on auto-pilot for this many days before
# recommendations auto-apply (warm-up observation period)
MIN_AUTOPILOT_AGE_DAYS = 7

HS_BASE = "https://api.hubapi.com"


# ── HubSpot lookups ──────────────────────────────────────────────────────────

def _hs_headers() -> dict:
    return {
        "Authorization": f"Bearer {os.environ.get('HUBSPOT_API_KEY', '')}",
        "Content-Type":  "application/json",
    }


def _get_company_by_uuid(uuid: str) -> Optional[dict]:
    """Look up HubSpot company by UUID. Returns the properties dict or None.

    Uses Search API since uuid is a custom prop. Returns the first match
    (uuid is unique per R1).
    """
    if not uuid:
        return None
    body = {
        "filterGroups": [{"filters": [
            {"propertyName": "uuid", "operator": "EQ", "value": uuid},
        ]}],
        "properties": ["name", "uuid", "loop_mode", "seo_tier",
                       "loop_mode_set_at", "loop_autopilot_warmup_until"],
        "limit": 1,
    }
    try:
        r = requests.post(
            f"{HS_BASE}/crm/v3/objects/companies/search",
            headers=_hs_headers(), json=body, timeout=10,
        )
        r.raise_for_status()
        results = r.json().get("results") or []
        if not results:
            return None
        return (results[0].get("properties") or {})
    except Exception as exc:
        logger.warning("autopilot: HubSpot lookup failed for uuid=%s: %s", uuid, exc)
        return None


# ── Bounds checking ──────────────────────────────────────────────────────────

def is_safe_to_auto_apply(rec: dict, property_uuid: str) -> tuple[bool, str]:
    """The gatekeeper. Returns (True, '') if safe, else (False, reason).

    A recommendation is safe to auto-apply only if every check passes.
    """
    action = (rec or {}).get("action")
    if not action:
        return (False, "no action specified")
    if action not in AUTO_PILOT_SAFE_ACTIONS:
        return (False, f"action '{action}' not in auto-pilot safe list")

    impact = rec.get("forecast_impact")
    if impact is None or float(impact) <= 0:
        return (False, "non-positive forecast impact")

    if action == "shift_budget":
        amount = float(rec.get("amount") or 0)
        if amount <= 0:
            return (False, "shift amount zero or negative")
        if amount > MAX_ABSOLUTE_AMOUNT:
            return (False, f"shift amount ${amount} exceeds ${MAX_ABSOLUTE_AMOUNT} cap")
        # Verify the from-channel has enough spend to absorb the shift
        # (this requires a forecast lookup; do it lazily here)
        try:
            import forecasting
            f = forecasting.get_latest_forecast(property_uuid)
            if not f:
                return (False, "no forecast on file to validate shift bounds")
            from_ch = rec.get("from_channel")
            alloc = (f.get("channel_allocation") or {}).get(from_ch) or {}
            from_spend = float(alloc.get("spend") or 0)
            if from_spend <= 0:
                return (False, f"from_channel {from_ch} has zero spend; cannot shift from")
            if amount > from_spend * MAX_PERCENT_OF_CHANNEL:
                return (False, (f"shift ${amount} > {int(MAX_PERCENT_OF_CHANNEL*100)}% of "
                                f"{from_ch} spend ${from_spend:.0f}"))
        except Exception as exc:
            return (False, f"bounds validation failed: {exc}")

    return (True, "")


# ── Cron entrypoint ──────────────────────────────────────────────────────────

def process_pending_recommendations(*, lookback_hours: int = 24) -> dict:
    """Scan recent recommendation_proposed events. Auto-approve the ones
    on auto-pilot properties that pass safety checks.

    Returns summary {scanned, auto_approved, skipped, by_skip_reason}.
    """
    import loop_writer

    client = loop_writer._bq()
    if client is None:
        return {"error": "BQ unavailable"}

    project = os.environ.get("BIGQUERY_PROJECT_ID")
    dataset = os.environ.get("BIGQUERY_DATASET_PROD")

    # 1. Find recent recommendation_proposed events that DON'T already have
    #    a child recommendation_approved or recommendation_rejected event.
    from google.cloud import bigquery
    sql = f"""
      WITH proposed AS (
        SELECT event_id, property_uuid, payload, occurred_at
        FROM `{project}.{dataset}.loop_events`
        WHERE stage = 'optimize'
          AND event_type = 'recommendation_proposed'
          AND occurred_at > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @hours HOUR)
      ),
      decided AS (
        SELECT DISTINCT parent_event_id
        FROM `{project}.{dataset}.loop_events`
        WHERE stage = 'optimize'
          AND event_type IN ('recommendation_approved','recommendation_rejected')
          AND parent_event_id IS NOT NULL
      )
      SELECT p.* FROM proposed p
      LEFT JOIN decided d ON d.parent_event_id = p.event_id
      WHERE d.parent_event_id IS NULL
      ORDER BY p.occurred_at DESC
      LIMIT 200
    """
    params = [bigquery.ScalarQueryParameter("hours", "INT64", lookback_hours)]
    cfg = bigquery.QueryJobConfig(query_parameters=params)

    try:
        pending = list(client.query(sql, job_config=cfg).result())
    except Exception as exc:
        logger.warning("autopilot scan query failed: %s", exc)
        return {"error": str(exc)}

    summary: dict = {
        "scanned":            len(pending),
        "auto_approved":      0,
        "skipped":            0,
        "by_skip_reason":     {},
    }

    import json as _json

    for row in pending:
        uuid = row.property_uuid
        if not uuid:
            summary["skipped"] += 1
            summary["by_skip_reason"]["no_uuid"] = summary["by_skip_reason"].get("no_uuid", 0) + 1
            continue

        # 2. Resolve loop_mode for this property
        props = _get_company_by_uuid(uuid)
        if not props:
            _bump_skip(summary, "hubspot_lookup_failed")
            continue
        mode = (props.get("loop_mode") or "").lower().strip()
        if mode != "auto-pilot":
            _bump_skip(summary, f"mode_{mode or 'unset'}")
            continue

        # 3. Warm-up check
        set_at = props.get("loop_mode_set_at")
        if set_at:
            try:
                set_dt = datetime.fromisoformat(set_at.replace("Z", "+00:00"))
                age = (datetime.now(timezone.utc) - set_dt).days
                if age < MIN_AUTOPILOT_AGE_DAYS:
                    _bump_skip(summary, "in_warmup")
                    continue
            except (ValueError, TypeError):
                pass

        # 4. Parse the recommendation from the event payload
        payload_raw = row.payload or "{}"
        try:
            payload = _json.loads(payload_raw)
        except (TypeError, ValueError):
            _bump_skip(summary, "bad_payload_json")
            continue

        # The recommendation IS the payload for recommendation_proposed events
        rec = payload

        # 5. Apply bounds check
        ok, reason = is_safe_to_auto_apply(rec, uuid)
        if not ok:
            _bump_skip(summary, f"bounds_{reason[:30]}")
            continue

        # 6. Auto-approve: emit recommendation_approved as a child event
        loop_writer.record(
            stage="optimize",
            event_type="recommendation_approved",
            property_uuid=uuid,
            source="loop_autopilot",
            trigger="autopilot",
            payload={
                "recommendation":   rec,
                "auto_approved":    True,
                "approver":         "loop_autopilot",
                "bounds_checked":   True,
            },
            parent_event_id=row.event_id,
        )

        # 7. Fire the downstream action (e.g., budget shift → Fluency)
        try:
            _execute_approved_recommendation(uuid, rec)
        except Exception as exc:
            logger.warning("autopilot execution failed for %s: %s", uuid, exc)

        summary["auto_approved"] += 1

    return summary


def _bump_skip(summary: dict, reason: str) -> None:
    summary["skipped"] += 1
    summary["by_skip_reason"][reason] = summary["by_skip_reason"].get(reason, 0) + 1


# ── Downstream execution ────────────────────────────────────────────────────

def _execute_approved_recommendation(property_uuid: str, rec: dict) -> None:
    """Fire the action implied by an approved recommendation.

    For Phase 2, this is mostly stub: budget shifts get logged as a
    Loop event noting they need to be applied to Fluency. The actual
    Fluency write lands in Phase 2 week 3 (per ADR 0019 sequencing).
    """
    import loop_writer
    action = rec.get("action")

    if action == "shift_budget":
        # Record the intent; the Fluency execution hook lives in
        # fluency_exporter.py (to be extended). For now we just emit
        # an event that downstream tooling can consume.
        loop_writer.record(
            stage="attract",
            event_type="fluency_provisioned",
            property_uuid=property_uuid,
            source="loop_autopilot",
            trigger="autopilot",
            payload={
                "intent":       "budget_shift",
                "amount":       rec.get("amount"),
                "from_channel": rec.get("from_channel"),
                "to_channel":   rec.get("to_channel"),
                "_executed":    False,
                "_note":        "Intent recorded; Fluency write pending Phase 2 week 3 hook",
            },
        )

    elif action == "refresh-seo":
        loop_writer.record(
            stage="attract",
            event_type="cron_started",
            property_uuid=property_uuid,
            source="loop_autopilot",
            trigger="autopilot",
            payload={"action": "refresh-seo", "_note": "Trigger pending impl"},
        )

    elif action == "generate-aeo-batch":
        try:
            import aeo_writer
            aeo_writer.generate_aeo_content(property_uuid)
        except Exception as exc:
            logger.warning("autopilot AEO trigger failed: %s", exc)
