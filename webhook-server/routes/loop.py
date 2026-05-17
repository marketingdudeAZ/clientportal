"""Loop API blueprint — /api/loop/* (ADR 0010, 0018).

Endpoints:
  GET  /api/loop/status?uuid=X            4-stage health summary
  GET  /api/loop/events?uuid=X&...        Recent timeline
  GET  /api/loop/forecast?uuid=X          Latest forecast row
  POST /api/loop/forecast/run             Trigger a fresh forecast for one property
  GET  /api/loop/recommendations?uuid=X   Open recommendations
  POST /api/loop/approve                  Approve a recommendation
  POST /api/loop/reject                   Reject a recommendation
  GET  /api/loop/channels?uuid=X          Hyly per-channel summary
  GET  /api/loop/convert/leads?uuid=X     Recent lead submits (PII-scrubbed)

Auth model — dual:
  * Portal user via X-Portal-Email (clients see their own property data)
  * Internal/server-to-server via X-Internal-Key (cron jobs, admin)

The property authorization model is intentionally simple right now:
authenticated portal users may read any property they pass in `uuid`.
HubSpot Memberships already authorizes the portal session itself. A
follow-on ADR will tighten this when multi-tenant client login lands.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request

from _route_utils import preflight_response

logger = logging.getLogger(__name__)

loop_bp = Blueprint("loop", __name__)


# ── Auth helpers ─────────────────────────────────────────────────────────────

def _is_authorized(req) -> bool:
    """Either X-Portal-Email (logged-in user) or X-Internal-Key (server)."""
    if req.headers.get("X-Portal-Email", "").strip():
        return True
    key = req.headers.get("X-Internal-Key", "")
    return bool(key and key == os.environ.get("INTERNAL_API_KEY", ""))


def _is_internal(req) -> bool:
    """Internal-only endpoints (e.g. POST /forecast/run) need X-Internal-Key."""
    key = req.headers.get("X-Internal-Key", "")
    return bool(key and key == os.environ.get("INTERNAL_API_KEY", ""))


# ── GET /api/loop/status ─────────────────────────────────────────────────────

@loop_bp.route("/api/loop/status", methods=["GET", "OPTIONS"])
def loop_status():
    if request.method == "OPTIONS":
        return preflight_response()
    if not _is_authorized(request):
        return jsonify({"error": "auth required"}), 401

    uuid = (request.args.get("uuid") or "").strip()
    if not uuid:
        return jsonify({"error": "uuid required"}), 400

    import loop_writer
    stages = loop_writer.query_stage_status(uuid)

    # Derive a simple per-stage health label
    def _health(stage_data):
        if not stage_data:
            return "no_data"
        last_at = stage_data.get("last_at")
        if not last_at:
            return "no_data"
        try:
            dt = datetime.fromisoformat(last_at.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return "unknown"
        age_days = (datetime.utcnow().replace(tzinfo=dt.tzinfo) - dt).days
        if age_days <= 1:
            return "healthy"
        if age_days <= 7:
            return "ok"
        if age_days <= 30:
            return "stale"
        return "no_data"

    response = {
        "property_uuid": uuid,
        "stages": {
            stage: {
                "health": _health(data),
                "last_event_type": (data or {}).get("last_event_type"),
                "last_at":         (data or {}).get("last_at"),
                "magnitude":       (data or {}).get("magnitude"),
                "status":          (data or {}).get("status"),
            }
            for stage, data in stages.items()
        },
    }

    # Enrich the Convert stage card with AptIQ trailing summary — gives the
    # portal Status panel actual numbers to show instead of just "last event
    # 2d ago". Best-effort: if the query fails, response shape stays the same.
    try:
        client = loop_writer._bq()
        if client is not None:
            project = os.environ.get("BIGQUERY_PROJECT_ID")
            dataset = os.environ.get("BIGQUERY_DATASET_PROD")
            from google.cloud import bigquery
            sql = f"""
              SELECT
                MAX(DATE(snapshot_month)) AS latest_month,
                ANY_VALUE(occupancy)      AS occupancy_latest,
                SUM(leases_last_30)       AS leases_trailing_total,
                COUNTIF(snapshot_month > DATE_SUB(CURRENT_DATE(), INTERVAL 13 MONTH)) AS months_with_data
              FROM `{project}.{dataset}.aptiq_snapshots_latest`
              WHERE property_uuid = @uuid
                AND snapshot_month > DATE_SUB(CURRENT_DATE(), INTERVAL 13 MONTH)
            """
            cfg = bigquery.QueryJobConfig(query_parameters=[
                bigquery.ScalarQueryParameter("uuid", "STRING", uuid),
            ])
            rows = list(client.query(sql, job_config=cfg).result())
            if rows and rows[0].months_with_data:
                r = rows[0]
                response["convert_aptiq"] = {
                    "latest_month":         r.latest_month.isoformat() if r.latest_month else None,
                    "occupancy_latest":     r.occupancy_latest,
                    "leases_trailing_total": int(r.leases_trailing_total or 0),
                    "months_with_data":     int(r.months_with_data),
                }
    except Exception as exc:
        logger.debug("loop_status AptIQ enrichment skipped: %s", exc)

    # Same for the Optimize stage — latest forecast headline
    try:
        import forecasting
        f = forecasting.get_latest_forecast(uuid)
        if f:
            response["optimize_forecast"] = {
                "forecast_leases":  f.get("forecast_leases"),
                "ci_low":           f.get("ci_low"),
                "ci_high":          f.get("ci_high"),
                "methodology":      f.get("methodology"),
                "run_at":           f.get("run_at"),
                "recommendations":  len(f.get("recommendations") or []),
            }
    except Exception as exc:
        logger.debug("loop_status forecast enrichment skipped: %s", exc)

    return jsonify(response)


# ── GET /api/loop/events ─────────────────────────────────────────────────────

@loop_bp.route("/api/loop/events", methods=["GET", "OPTIONS"])
def loop_events():
    if request.method == "OPTIONS":
        return preflight_response()
    if not _is_authorized(request):
        return jsonify({"error": "auth required"}), 401

    uuid = (request.args.get("uuid") or "").strip()
    if not uuid:
        return jsonify({"error": "uuid required"}), 400

    stage = (request.args.get("stage") or "").strip() or None
    try:
        limit = int(request.args.get("limit") or 50)
    except (TypeError, ValueError):
        limit = 50
    limit = max(1, min(limit, 500))

    since_param = request.args.get("since")
    since = None
    if since_param:
        try:
            since = datetime.fromisoformat(since_param.replace("Z", "+00:00"))
        except ValueError:
            return jsonify({"error": "since must be ISO 8601"}), 400

    import loop_writer
    events = loop_writer.query_recent(uuid, limit=limit, stage=stage, since=since)
    return jsonify({"property_uuid": uuid, "events": events, "count": len(events)})


# ── GET /api/loop/forecast ───────────────────────────────────────────────────

@loop_bp.route("/api/loop/forecast", methods=["GET", "OPTIONS"])
def loop_forecast():
    if request.method == "OPTIONS":
        return preflight_response()
    if not _is_authorized(request):
        return jsonify({"error": "auth required"}), 401

    uuid = (request.args.get("uuid") or "").strip()
    if not uuid:
        return jsonify({"error": "uuid required"}), 400

    import forecasting
    forecast = forecasting.get_latest_forecast(uuid)
    if not forecast:
        return jsonify({
            "property_uuid": uuid,
            "forecast": None,
            "message": "No forecast has been run for this property yet.",
        })
    return jsonify({"property_uuid": uuid, "forecast": forecast})


# ── POST /api/loop/forecast/run ──────────────────────────────────────────────

@loop_bp.route("/api/loop/forecast/run", methods=["POST", "OPTIONS"])
def loop_forecast_run():
    """Trigger a fresh forecast computation for one property.

    Body: {"company_id": "...", "seo_tier": "Standard" (optional)}
    Returns the computed forecast inline.
    """
    if request.method == "OPTIONS":
        return preflight_response()
    if not _is_internal(request):
        return jsonify({"error": "X-Internal-Key required"}), 401

    payload = request.get_json(silent=True) or {}
    company_id = (payload.get("company_id") or "").strip()
    uuid_in = (payload.get("uuid") or "").strip()
    seo_tier = (payload.get("seo_tier") or "").strip() or None

    # Resolve uuid via HubSpot if company_id provided
    if company_id and not uuid_in:
        import requests as _req
        try:
            hk = os.environ.get("HUBSPOT_API_KEY", "")
            r = _req.get(
                f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}"
                "?properties=uuid,seo_tier",
                headers={"Authorization": f"Bearer {hk}"},
                timeout=15,
            )
            r.raise_for_status()
            props = r.json().get("properties", {})
            uuid_in = (props.get("uuid") or "").strip()
            if not seo_tier:
                seo_tier = (props.get("seo_tier") or "").strip() or None
        except Exception as exc:
            return jsonify({"error": f"HubSpot lookup failed: {exc}"}), 502

    if not uuid_in:
        return jsonify({"error": "uuid or company_id required"}), 400

    import forecasting
    result = forecasting.run_forecast(uuid_in, seo_tier=seo_tier)
    return jsonify({"status": "ok", "forecast": result})


# ── GET /api/loop/recommendations ────────────────────────────────────────────

@loop_bp.route("/api/loop/recommendations", methods=["GET", "OPTIONS"])
def loop_recommendations():
    """Open recommendations for a property — derived from latest forecast +
    any recommendation_proposed events that haven't been approved/rejected."""
    if request.method == "OPTIONS":
        return preflight_response()
    if not _is_authorized(request):
        return jsonify({"error": "auth required"}), 401

    uuid = (request.args.get("uuid") or "").strip()
    if not uuid:
        return jsonify({"error": "uuid required"}), 400

    import forecasting
    forecast = forecasting.get_latest_forecast(uuid) or {}
    recs = forecast.get("recommendations") or []

    # Filter out no-op recs
    actionable = [r for r in recs
                  if r.get("action") not in ("hold", "collect_more_data", "expand_inputs")]

    return jsonify({
        "property_uuid":  uuid,
        "forecast_id":    forecast.get("forecast_id"),
        "forecast_run_at": forecast.get("run_at"),
        "recommendations": actionable,
        "count":          len(actionable),
    })


# ── POST /api/loop/approve / /api/loop/reject ────────────────────────────────

@loop_bp.route("/api/loop/approve", methods=["POST", "OPTIONS"])
def loop_approve():
    """Approve a recommendation. Records the approval Loop event; downstream
    automation reads pending recommendation_approved events and acts on them
    (e.g., the budget shift gets written to Fluency on the next cron).

    Body: {"uuid": "...", "recommendation_id": "...", "comment": "...?"}

    Note: in v1 recommendations don't have stable IDs (they're generated
    per-forecast). The approve flow stores a snapshot of the recommendation
    in the event payload so downstream automation has everything it needs
    without re-querying.
    """
    if request.method == "OPTIONS":
        return preflight_response()
    if not _is_authorized(request):
        return jsonify({"error": "auth required"}), 401

    payload = request.get_json(silent=True) or {}
    uuid = (payload.get("uuid") or "").strip()
    if not uuid:
        return jsonify({"error": "uuid required"}), 400

    rec_snapshot = payload.get("recommendation") or {}
    comment = (payload.get("comment") or "").strip() or None
    parent = (payload.get("forecast_id") or "").strip() or None

    import loop_writer
    event_id = loop_writer.record(
        stage="optimize",
        event_type="recommendation_approved",
        property_uuid=uuid,
        source="client_action",
        trigger="client_action",
        payload={
            "recommendation": rec_snapshot,
            "comment":        comment,
            "approver_email": request.headers.get("X-Portal-Email") or None,
        },
        parent_event_id=parent,
    )
    return jsonify({"status": "approved", "event_id": event_id})


@loop_bp.route("/api/loop/reject", methods=["POST", "OPTIONS"])
def loop_reject():
    """Reject (defer / counter-propose) a recommendation."""
    if request.method == "OPTIONS":
        return preflight_response()
    if not _is_authorized(request):
        return jsonify({"error": "auth required"}), 401

    payload = request.get_json(silent=True) or {}
    uuid = (payload.get("uuid") or "").strip()
    if not uuid:
        return jsonify({"error": "uuid required"}), 400

    rec_snapshot = payload.get("recommendation") or {}
    reason = (payload.get("reason") or "").strip() or None
    counter = payload.get("counter_proposal") or None
    parent = (payload.get("forecast_id") or "").strip() or None

    import loop_writer
    event_id = loop_writer.record(
        stage="optimize",
        event_type="recommendation_rejected",
        property_uuid=uuid,
        source="client_action",
        trigger="client_action",
        payload={
            "recommendation":   rec_snapshot,
            "reason":           reason,
            "counter_proposal": counter,
            "rejecter_email":   request.headers.get("X-Portal-Email") or None,
        },
        parent_event_id=parent,
    )
    return jsonify({"status": "rejected", "event_id": event_id})


# ── GET /api/loop/channels ───────────────────────────────────────────────────

@loop_bp.route("/api/loop/channels", methods=["GET", "OPTIONS"])
def loop_channels():
    """Hyly per-channel summary for a property. Needs hyly_property_id on
    the HubSpot company. Date window defaults to last 30 days."""
    if request.method == "OPTIONS":
        return preflight_response()
    if not _is_authorized(request):
        return jsonify({"error": "auth required"}), 401

    company_id = (request.args.get("company_id") or "").strip()
    if not company_id:
        return jsonify({"error": "company_id required"}), 400

    days = max(1, min(int(request.args.get("days") or 30), 365))

    # Lookup hyly_property_id via HubSpot
    import requests as _req
    try:
        hk = os.environ.get("HUBSPOT_API_KEY", "")
        r = _req.get(
            f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}"
            "?properties=hyly_property_id,uuid",
            headers={"Authorization": f"Bearer {hk}"},
            timeout=15,
        )
        r.raise_for_status()
        props = r.json().get("properties", {})
    except Exception as exc:
        return jsonify({"error": f"HubSpot lookup failed: {exc}"}), 502

    hyly_id = (props.get("hyly_property_id") or "").strip()
    uuid = (props.get("uuid") or "").strip()
    if not hyly_id:
        return jsonify({
            "property_uuid":   uuid,
            "company_id":      company_id,
            "hyly_property_id": None,
            "channels":        {},
            "message":         "No Hyly Property ID on this company yet — Hyly beta hasn't reached this property.",
        })

    end = datetime.utcnow().date()
    start = end - timedelta(days=days)
    import hyly_client
    channels = hyly_client.get_channel_summary(
        hyly_id,
        start_date=start.isoformat(),
        end_date=end.isoformat(),
    )
    return jsonify({
        "property_uuid":   uuid,
        "company_id":      company_id,
        "hyly_property_id": hyly_id,
        "window_days":     days,
        "channels":        channels,
    })


# ── POST /api/loop/forecasts/batch ────────────────────────────────────────────

@loop_bp.route("/api/loop/forecasts/batch", methods=["POST", "OPTIONS"])
def loop_forecasts_batch():
    """Bulk-fetch latest forecasts for many properties at once.

    Used by the /accounts table to decorate each row with its current
    Loop forecast in a single request (vs N fetches one-per-row).

    Body JSON:
      uuids: ["...","...","..."]   — up to 500 property UUIDs

    Returns:
      { forecasts: { uuid: { forecast_leases, ci_low, ci_high,
                             run_at, methodology, recommendations_count }, ... } }
    """
    if request.method == "OPTIONS":
        return preflight_response()
    if not _is_authorized(request):
        return jsonify({"error": "auth required"}), 401

    payload = request.get_json(silent=True) or {}
    uuids_in = payload.get("uuids") or []
    if not isinstance(uuids_in, list):
        return jsonify({"error": "uuids must be a list"}), 400
    uuids = [str(u).strip() for u in uuids_in if u][:500]
    if not uuids:
        return jsonify({"forecasts": {}})

    import loop_writer
    client = loop_writer._bq()
    if client is None:
        return jsonify({"forecasts": {}, "note": "BQ unavailable"})

    project = os.environ.get("BIGQUERY_PROJECT_ID")
    dataset = os.environ.get("BIGQUERY_DATASET_PROD")

    from google.cloud import bigquery
    sql = f"""
      WITH ranked AS (
        SELECT
          property_uuid, run_at, horizon_days, methodology,
          forecast_leases, ci_low, ci_high, confidence_level,
          recommendations,
          ROW_NUMBER() OVER (PARTITION BY property_uuid ORDER BY run_at DESC) AS rn
        FROM `{project}.{dataset}.forecast_runs`
        WHERE property_uuid IN UNNEST(@uuids)
      )
      SELECT * EXCEPT(rn) FROM ranked WHERE rn = 1
    """
    cfg = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ArrayQueryParameter("uuids", "STRING", uuids),
    ])
    out: dict = {}
    try:
        import json as _json
        for r in client.query(sql, job_config=cfg).result():
            recs_raw = r.recommendations
            rec_count = 0
            if recs_raw:
                try:
                    rec_list = _json.loads(recs_raw) if isinstance(recs_raw, str) else recs_raw
                    rec_count = sum(1 for x in rec_list
                                    if x.get("action") not in
                                       ("hold", "collect_more_data", "expand_inputs"))
                except (TypeError, ValueError):
                    pass
            out[r.property_uuid] = {
                "forecast_leases":      r.forecast_leases,
                "ci_low":               r.ci_low,
                "ci_high":              r.ci_high,
                "confidence_level":     r.confidence_level,
                "methodology":          r.methodology,
                "horizon_days":         r.horizon_days,
                "run_at":               r.run_at.isoformat() if r.run_at else None,
                "recommendations_count": rec_count,
            }
    except Exception as exc:
        logger.warning("loop_forecasts_batch failed: %s", exc)
        return jsonify({"forecasts": {}, "error": str(exc)[:200]}), 500

    return jsonify({"forecasts": out, "count": len(out)})


# ── GET /api/loop/accuracy ───────────────────────────────────────────────────

@loop_bp.route("/api/loop/accuracy", methods=["GET", "OPTIONS"])
def loop_accuracy():
    """Forecast accuracy summary — uses the forecast_accuracy view
    (migration 0010) to surface how well simple_lag_v1 has done so far.

    Query params:
      uuid       — single property (optional; omit for portfolio summary)
      months     — lookback window in months (default 6)

    Returns per-property hit rate, mean abs error, mean rel error, plus
    portfolio totals when no uuid is supplied.
    """
    if request.method == "OPTIONS":
        return preflight_response()
    if not _is_authorized(request):
        return jsonify({"error": "auth required"}), 401

    uuid = (request.args.get("uuid") or "").strip() or None
    try:
        months = max(1, min(int(request.args.get("months") or 6), 24))
    except (TypeError, ValueError):
        months = 6

    import loop_writer
    client = loop_writer._bq()
    if client is None:
        return jsonify({"error": "BQ unavailable"}), 503

    project = os.environ.get("BIGQUERY_PROJECT_ID")
    dataset = os.environ.get("BIGQUERY_DATASET_PROD")

    from google.cloud import bigquery
    where = ["run_at > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @months MONTH)"]
    params = [bigquery.ScalarQueryParameter("months", "INT64", months)]
    if uuid:
        where.append("property_uuid = @uuid")
        params.append(bigquery.ScalarQueryParameter("uuid", "STRING", uuid))

    sql = f"""
      SELECT
        property_uuid,
        COUNT(*) AS forecasts_evaluated,
        COUNTIF(realized_leases IS NOT NULL) AS forecasts_with_outcome,
        AVG(IF(realized_leases IS NOT NULL, abs_error, NULL)) AS mean_abs_error,
        AVG(IF(realized_leases IS NOT NULL, rel_error, NULL)) AS mean_rel_error,
        COUNTIF(ci_hit IS TRUE) AS ci_hits,
        SAFE_DIVIDE(COUNTIF(ci_hit IS TRUE),
                     COUNTIF(realized_leases IS NOT NULL)) AS ci_hit_rate,
        COUNTIF(bias_direction = 'under_forecast') AS under_forecasts,
        COUNTIF(bias_direction = 'over_forecast')  AS over_forecasts
      FROM `{project}.{dataset}.forecast_accuracy`
      WHERE {' AND '.join(where)}
      GROUP BY property_uuid
      ORDER BY forecasts_with_outcome DESC
    """
    cfg = bigquery.QueryJobConfig(query_parameters=params)
    try:
        rows = list(client.query(sql, job_config=cfg).result())
    except Exception as exc:
        logger.warning("loop_accuracy query failed: %s", exc)
        return jsonify({"error": str(exc)}), 500

    by_property = []
    portfolio = {
        "total_forecasts":          0,
        "with_outcome":             0,
        "ci_hits":                  0,
        "weighted_mean_abs_error":  0.0,
        "weighted_mean_rel_error":  0.0,
    }
    for r in rows:
        d = dict(r.items())
        by_property.append(d)
        portfolio["total_forecasts"] += int(d.get("forecasts_evaluated") or 0)
        portfolio["with_outcome"]    += int(d.get("forecasts_with_outcome") or 0)
        portfolio["ci_hits"]         += int(d.get("ci_hits") or 0)
        # Weighted by forecasts_with_outcome
        n = int(d.get("forecasts_with_outcome") or 0)
        if n > 0:
            portfolio["weighted_mean_abs_error"] += (d.get("mean_abs_error") or 0) * n
            portfolio["weighted_mean_rel_error"] += (d.get("mean_rel_error") or 0) * n

    if portfolio["with_outcome"] > 0:
        portfolio["weighted_mean_abs_error"] /= portfolio["with_outcome"]
        portfolio["weighted_mean_rel_error"] /= portfolio["with_outcome"]
        portfolio["ci_hit_rate"] = portfolio["ci_hits"] / portfolio["with_outcome"]

    return jsonify({
        "uuid":         uuid,
        "months":       months,
        "portfolio":    portfolio,
        "by_property":  by_property,
    })


# ── GET /api/loop/convert/leads ──────────────────────────────────────────────

@loop_bp.route("/api/loop/convert/leads", methods=["GET", "OPTIONS"])
def loop_convert_leads():
    """Recent lead submits for a property — sourced from loop_events
    (where lead_submitted events have been emitted by the daily Hyly pull
    cron). PII is already stripped (only email_hash present in events).
    """
    if request.method == "OPTIONS":
        return preflight_response()
    if not _is_authorized(request):
        return jsonify({"error": "auth required"}), 401

    uuid = (request.args.get("uuid") or "").strip()
    if not uuid:
        return jsonify({"error": "uuid required"}), 400

    limit = max(1, min(int(request.args.get("limit") or 50), 500))
    days = max(1, min(int(request.args.get("days") or 30), 365))

    since = datetime.utcnow() - timedelta(days=days)
    import loop_writer
    events = loop_writer.query_recent(
        uuid, limit=limit, stage="convert", since=since,
    )
    leads = [e for e in events if e.get("event_type") == "lead_submitted"]
    return jsonify({
        "property_uuid": uuid,
        "window_days":   days,
        "leads":         leads,
        "count":         len(leads),
    })
