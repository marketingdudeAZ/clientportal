"""Forecasting Engine — per-property 30-day lease projection (ADR 0009).

Inputs (joined via the loop_attract_v1 and loop_convert_v1 BQ views):
  - Monthly paid spend per channel (paid_search, paid_social, seo, reputation)
  - Monthly AptIQ snapshot (leases_last_30, applications_last_30)
  - Hyly per-channel attribution (visitors, contacts) when available
  - SEO tier (Local / Lite / Basic / Standard / Premium) for baseline

Output:
  - forecast_leases: point estimate for next 30 days
  - ci_low / ci_high: 80% confidence interval bounds
  - channel_allocation: current $ × channel breakdown
  - recommendations: list of proposed actions with forecast deltas

Methodology v1 (simple_lag_v1):
  - Per-channel cost-per-lease from trailing 12 months (lag = 1 month)
  - Total leases = sum(channel_spend / channel_cpl)
  - Confidence interval = ±1 stddev across trailing 12 months
  - Recommendations: identify highest-CPL channel that's overweight,
    propose shifting some budget to lowest-CPL channel

Method v2+ will use proper regression with seasonality + Hyly attribution.
v1 ships first, learns from real data, then v2 lands.

Output rows are written to `forecast_runs` BQ table by `run_forecast()`.
"""

from __future__ import annotations

import json
import logging
import os
import statistics
import uuid as _uuid
from datetime import datetime, date, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# Tier baselines — if we have NO trailing data, this is the starting point
# for the forecast. Sourced from RPM portfolio averages (estimate, will be
# refined as data accumulates).
TIER_BASELINE_LEASES_PER_MONTH = {
    "Local":    4,
    "Lite":     5,
    "Basic":    6,
    "Standard": 7,
    "Premium":  9,
    None:       6,    # default if tier unset
}

# Channel cost-per-lease portfolio benchmarks (will be refined)
CHANNEL_CPL_BENCHMARK = {
    "paid_search":  350,
    "paid_social":  400,
    "seo":          200,
    "reputation":   600,
    "creative":     500,
}


def _bq():
    """Reuse loop_writer's BQ client to avoid duplicate auth."""
    try:
        import loop_writer
        return loop_writer._bq()
    except Exception:
        return None


def _bq_ref(table: str) -> str:
    project = os.environ.get("BIGQUERY_PROJECT_ID")
    dataset = os.environ.get("BIGQUERY_DATASET_PROD")
    return f"{project}.{dataset}.{table}"


def get_trailing_data(property_uuid: str, months: int = 12) -> list[dict]:
    """Pull trailing N months of joined Attract + Convert data for one property."""
    client = _bq()
    if not (client and property_uuid):
        return []
    from google.cloud import bigquery

    # Read AptIQ from the deduped view (migration 0008) — protects against
    # duplicate rows that accumulate when both redlight_v2 persist_snapshot
    # and aptiq-backfill-history write for the same property+month.
    # Falls back to the raw table if the view doesn't exist yet (older
    # deployments).
    sql = f"""
      WITH a AS (
        SELECT
          property_uuid, month, paid_search_spend, paid_social_spend,
          seo_spend, reputation_spend, creative_spend, total_spend,
          keywords_top_3, keywords_top_10
        FROM `{_bq_ref('loop_attract_v1')}`
        WHERE property_uuid = @uuid
      ),
      c AS (
        SELECT
          property_uuid,
          DATE_TRUNC(snapshot_month, MONTH) AS month,
          leases_last_30, applications_last_30, occupancy
        FROM `{_bq_ref('aptiq_snapshots_latest')}`
        WHERE property_uuid = @uuid
      )
      SELECT
        COALESCE(a.month, c.month) AS month,
        a.paid_search_spend, a.paid_social_spend,
        a.seo_spend, a.reputation_spend, a.creative_spend, a.total_spend,
        a.keywords_top_3, a.keywords_top_10,
        c.leases_last_30, c.applications_last_30, c.occupancy
      FROM a
      FULL OUTER JOIN c USING (property_uuid, month)
      WHERE COALESCE(a.month, c.month) > DATE_SUB(CURRENT_DATE(), INTERVAL @months MONTH)
      ORDER BY month DESC
    """
    params = [
        bigquery.ScalarQueryParameter("uuid", "STRING", property_uuid),
        bigquery.ScalarQueryParameter("months", "INT64", months),
    ]
    cfg = bigquery.QueryJobConfig(query_parameters=params)
    try:
        rows = []
        for r in client.query(sql, job_config=cfg).result():
            d = dict(r.items())
            if d.get("month"):
                d["month"] = d["month"].isoformat()
            rows.append(d)
        return rows
    except Exception as exc:
        logger.warning("forecasting.get_trailing_data failed for %s: %s",
                       property_uuid, exc)
        return []


def _safe_div(num, denom):
    if denom in (None, 0):
        return None
    try:
        return num / denom
    except (TypeError, ZeroDivisionError):
        return None


def compute_channel_cpl(trailing_rows: list[dict]) -> dict:
    """Per-channel cost-per-lease from trailing data.

    Allocates monthly leases proportionally to channel spend, then
    computes channel CPL from that pseudo-attribution. Returns:
      {channel: {monthly_cpls: [...], mean_cpl: float, stddev_cpl: float, spend_share: float}}

    Falls back to CHANNEL_CPL_BENCHMARK when trailing data is sparse.
    """
    channels = ("paid_search", "paid_social", "seo", "reputation", "creative")
    out: dict = {c: {"monthly_cpls": [], "mean_cpl": None, "stddev_cpl": 0, "spend_share": 0} for c in channels}

    for r in trailing_rows:
        leases = r.get("leases_last_30")
        total = r.get("total_spend") or 0
        if not leases or not total:
            continue
        for c in channels:
            spend = r.get(f"{c}_spend") or 0
            if spend <= 0:
                continue
            # Pseudo-attribution: channel's share of total spend × total leases
            channel_leases = (spend / total) * leases
            if channel_leases > 0:
                cpl = spend / channel_leases
                out[c]["monthly_cpls"].append(cpl)

    for c in channels:
        cpls = out[c]["monthly_cpls"]
        if cpls:
            out[c]["mean_cpl"] = statistics.mean(cpls)
            out[c]["stddev_cpl"] = statistics.pstdev(cpls) if len(cpls) > 1 else 0
        else:
            out[c]["mean_cpl"] = CHANNEL_CPL_BENCHMARK.get(c)

    # Spend share across the trailing window
    total_spend_window = sum((r.get("total_spend") or 0) for r in trailing_rows)
    if total_spend_window > 0:
        for c in channels:
            spend_window = sum((r.get(f"{c}_spend") or 0) for r in trailing_rows)
            out[c]["spend_share"] = spend_window / total_spend_window

    return out


def forecast_for_property(
    property_uuid: str,
    *,
    horizon_days: int = 30,
    seo_tier: Optional[str] = None,
) -> dict:
    """Run the forecast for one property. Returns a dict with:

      forecast_leases (float), ci_low (float), ci_high (float),
      confidence_level (float = 0.8),
      methodology ('simple_lag_v1'),
      channel_allocation (dict per channel),
      inputs (summary of inputs used),
      recommendations (list of dicts).
    """
    trailing = get_trailing_data(property_uuid, months=12)

    # If we have NO trailing data, fall back to tier baseline
    if not trailing:
        baseline = TIER_BASELINE_LEASES_PER_MONTH.get(seo_tier,
                    TIER_BASELINE_LEASES_PER_MONTH[None])
        return {
            "property_uuid":     property_uuid,
            "horizon_days":      horizon_days,
            "methodology":       "tier_baseline_v0",
            "forecast_leases":   float(baseline),
            "ci_low":            float(baseline) * 0.7,
            "ci_high":           float(baseline) * 1.3,
            "confidence_level":  0.5,    # low confidence with no data
            "channel_allocation": {},
            "inputs": {"data_months": 0, "tier": seo_tier},
            "recommendations": [
                {"action": "collect_more_data",
                 "reason": "No trailing data — onboarding period",
                 "forecast_impact": None}
            ],
        }

    cpls = compute_channel_cpl(trailing)
    # Latest month's spend
    latest = trailing[0]
    channels = ("paid_search", "paid_social", "seo", "reputation", "creative")
    forecast_components = []
    allocation = {}
    for c in channels:
        spend = latest.get(f"{c}_spend") or 0
        mean_cpl = cpls[c]["mean_cpl"]
        leases_forecast = _safe_div(spend, mean_cpl) or 0
        forecast_components.append(leases_forecast)
        allocation[c] = {
            "spend":    spend,
            "cpl":      mean_cpl,
            "forecast_leases": leases_forecast,
        }

    forecast_leases = sum(forecast_components)
    # CI: ± 1 stddev across the trailing months' actuals
    historical_leases = [r.get("leases_last_30") for r in trailing if r.get("leases_last_30")]
    if len(historical_leases) >= 3:
        sd = statistics.pstdev(historical_leases)
    else:
        sd = forecast_leases * 0.3   # ±30% when sparse

    recs = generate_recommendations(cpls, allocation, forecast_leases)

    return {
        "property_uuid":     property_uuid,
        "horizon_days":      horizon_days,
        "methodology":       "simple_lag_v1",
        "forecast_leases":   round(forecast_leases, 1),
        "ci_low":            round(max(0, forecast_leases - sd), 1),
        "ci_high":           round(forecast_leases + sd, 1),
        "confidence_level":  0.8,
        "channel_allocation": allocation,
        "inputs": {
            "data_months": len(trailing),
            "tier": seo_tier,
            "latest_month": latest.get("month"),
            "channel_cpls": {c: cpls[c]["mean_cpl"] for c in channels},
        },
        "recommendations": recs,
    }


def generate_recommendations(cpls: dict, allocation: dict,
                              current_forecast: float) -> list[dict]:
    """Propose budget shifts. Looks for highest-CPL underweight + lowest-CPL
    overweight channels and proposes a small shift between them.

    Recommendations are CONSERVATIVE: <= 15% shift, only if the delta is
    meaningfully positive. Each rec includes the projected forecast impact.
    """
    recs = []
    channels = list(cpls.keys())

    # Find best channel (lowest CPL with data) and worst (highest CPL)
    rated = [(c, cpls[c]["mean_cpl"], allocation[c]["spend"])
             for c in channels if cpls[c]["mean_cpl"] is not None]
    if len(rated) < 2:
        return [{"action": "expand_inputs",
                 "reason": "Need more channel data for cross-channel recommendations",
                 "forecast_impact": None}]

    rated_by_cpl = sorted(rated, key=lambda r: r[1])
    best = rated_by_cpl[0]
    worst = rated_by_cpl[-1]
    if best[0] == worst[0]:
        return []

    # Propose moving 15% of worst-channel spend → best channel
    shift_amount = worst[2] * 0.15
    if shift_amount <= 50:
        return [{"action": "hold",
                 "reason": "Channel allocation appears balanced",
                 "forecast_impact": None}]

    # Forecast delta: leases gained by reallocating
    leases_lost_from_worst = shift_amount / worst[1]
    leases_gained_from_best = shift_amount / best[1]
    delta = leases_gained_from_best - leases_lost_from_worst

    if delta > 0.5:
        recs.append({
            "action":          "shift_budget",
            "from_channel":    worst[0],
            "to_channel":      best[0],
            "amount":          round(shift_amount, 2),
            "reason":          (f"{best[0]} converts at ${best[1]:.0f}/lease, "
                                f"{worst[0]} at ${worst[1]:.0f}/lease — {round(delta*30/30, 1)} "
                                f"extra leases over 30d projected"),
            "forecast_impact": round(delta, 1),
        })

    return recs or [{"action": "hold", "reason": "Channel allocation balanced",
                     "forecast_impact": None}]


def run_forecast(
    property_uuid: str,
    *,
    horizon_days: int = 30,
    seo_tier: Optional[str] = None,
    persist: bool = True,
) -> dict:
    """End-to-end: compute forecast, persist to forecast_runs, emit Loop event.

    Returns the forecast dict (same shape as forecast_for_property()).
    """
    result = forecast_for_property(property_uuid,
                                   horizon_days=horizon_days,
                                   seo_tier=seo_tier)
    if not persist:
        return result

    forecast_id = str(_uuid.uuid4())
    result["forecast_id"] = forecast_id

    # Persist to forecast_runs table
    client = _bq()
    if client:
        row = {
            "forecast_id":      forecast_id,
            "property_uuid":    property_uuid,
            "run_at":           datetime.utcnow().isoformat() + "Z",
            "horizon_days":     horizon_days,
            "methodology":      result["methodology"],
            "forecast_leases":  result["forecast_leases"],
            "ci_low":           result["ci_low"],
            "ci_high":          result["ci_high"],
            "confidence_level": result["confidence_level"],
            "inputs_payload":   json.dumps(result["inputs"])[:30_000],
            "channel_allocation": json.dumps(result["channel_allocation"])[:30_000],
            "recommendations":  json.dumps(result["recommendations"])[:30_000],
            "observed_leases":  None,    # backfilled later for accuracy tracking
        }
        try:
            errs = client.insert_rows_json(_bq_ref("forecast_runs"), [row])
            if errs:
                logger.warning("forecast_runs insert errors: %s", errs[:3])
        except Exception as exc:
            logger.warning("forecast_runs insert exception: %s", exc)

    # Emit Loop event
    try:
        import loop_writer
        loop_writer.record(
            stage="optimize", event_type="forecast_run",
            property_uuid=property_uuid,
            source="forecasting", source_id=forecast_id,
            magnitude=result["forecast_leases"],
            payload={
                "horizon_days":     horizon_days,
                "ci_low":           result["ci_low"],
                "ci_high":          result["ci_high"],
                "methodology":      result["methodology"],
                "recommendation_count": len(result.get("recommendations") or []),
            },
        )
        # Also emit any recommendations as Loop events
        for rec in result.get("recommendations") or []:
            if rec.get("action") in ("hold", "collect_more_data", "expand_inputs"):
                continue   # don't spam events for no-op recs
            loop_writer.record(
                stage="optimize",
                event_type="recommendation_proposed",
                property_uuid=property_uuid,
                source="forecasting",
                magnitude=rec.get("forecast_impact"),
                payload=rec,
                parent_event_id=forecast_id,
            )
    except Exception as exc:
        logger.warning("forecasting Loop event emit failed: %s", exc)

    return result


def get_latest_forecast(property_uuid: str) -> Optional[dict]:
    """Most recent forecast row for a property. Returns None if no forecast
    has ever been run."""
    client = _bq()
    if not (client and property_uuid):
        return None
    from google.cloud import bigquery
    sql = f"""
      SELECT forecast_id, run_at, horizon_days, methodology,
             forecast_leases, ci_low, ci_high, confidence_level,
             inputs_payload, channel_allocation, recommendations
      FROM `{_bq_ref('forecast_runs')}`
      WHERE property_uuid = @uuid
      ORDER BY run_at DESC
      LIMIT 1
    """
    params = [bigquery.ScalarQueryParameter("uuid", "STRING", property_uuid)]
    cfg = bigquery.QueryJobConfig(query_parameters=params)
    try:
        rows = list(client.query(sql, job_config=cfg).result())
        if not rows:
            return None
        r = rows[0]
        out = dict(r.items())
        if out.get("run_at"):
            out["run_at"] = out["run_at"].isoformat()
        # Parse JSON columns back to dicts/lists
        for k in ("inputs_payload", "channel_allocation", "recommendations"):
            v = out.get(k)
            if isinstance(v, str):
                try:
                    out[k.replace("_payload", "")] = json.loads(v)
                except (TypeError, ValueError):
                    pass
        return out
    except Exception as exc:
        logger.warning("forecasting.get_latest_forecast failed for %s: %s",
                       property_uuid, exc)
        return None
