"""ILS Performance API blueprint — /api/ils/* (ADR 0021).

Serves the client-facing "ILS Performance" portal page from the
apartments.com data landed in BigQuery (apartmentscom_ils_resolved_v1).

Endpoints:
  GET  /api/ils/summary?uuid=X&days=30   Period totals + prev-period delta + meta
  GET  /api/ils/trend?uuid=X&days=90     Daily series for charting
  POST /api/internal/apartmentscom-sync  Ingest one date (cron/admin only)

Auth model (mirrors routes/loop.py):
  * Portal user via X-Portal-Email  (reads, uuid-scoped)
  * Internal/server via X-Internal-Key (reads + the sync endpoint)

All reads are uuid-scoped — a property only ever sees its own rows. Rows
whose CoStar id hasn't been mapped to a uuid yet are simply absent from
these results (they still sit in the raw table for later backfill).
"""

from __future__ import annotations

import logging
import os

from flask import Blueprint, jsonify, request

from _route_utils import preflight_response

logger = logging.getLogger(__name__)

ils_bp = Blueprint("ils", __name__)

# Metric columns exposed to the page, grouped by Loop stage.
_ATTRACT_METRICS = [
    "search_result_impressions",
    "details_page_impressions",
    "total_impressions",
    "total_media_views",
    "hd_video_views",
    "tour_3d_views",
    "property_map_views",
]
_ENGAGE_METRICS = [
    "total_leads",
    "phone_leads",
    "email_leads",
    "property_website_leads",
    "request_to_tour_leads",
    "request_to_apply_leads",
    "unit_application_leads",
]
_ALL_METRICS = _ATTRACT_METRICS + _ENGAGE_METRICS


def _is_authorized(req) -> bool:
    if req.headers.get("X-Portal-Email", "").strip():
        return True
    key = req.headers.get("X-Internal-Key", "")
    return bool(key and key == os.environ.get("INTERNAL_API_KEY", ""))


def _is_internal(req) -> bool:
    key = req.headers.get("X-Internal-Key", "")
    return bool(key and key == os.environ.get("INTERNAL_API_KEY", ""))


def _bq_and_ref():
    """Return (client, project, dataset, view_fqn) or (None, ...) if BQ down."""
    import loop_writer
    client = loop_writer._bq()
    project = os.environ.get("BIGQUERY_PROJECT_ID")
    dataset = os.environ.get("BIGQUERY_DATASET_PROD")
    from config import BIGQUERY_APARTMENTSCOM_DAILY_TABLE  # noqa: F401 (ensure config import ok)
    view = f"{project}.{dataset}.apartmentscom_ils_resolved_v1"
    return client, project, dataset, view


def _days_param(default: int, cap: int = 90) -> int:
    try:
        d = int(request.args.get("days", default))
    except (TypeError, ValueError):
        d = default
    return max(1, min(d, cap))


# ── GET /api/ils/summary ─────────────────────────────────────────────────────

@ils_bp.route("/api/ils/summary", methods=["GET", "OPTIONS"])
def ils_summary():
    if request.method == "OPTIONS":
        return preflight_response()
    if not _is_authorized(request):
        return jsonify({"error": "auth required"}), 401
    uuid = (request.args.get("uuid") or "").strip()
    if not uuid:
        return jsonify({"error": "uuid required"}), 400

    days = _days_param(30)
    client, project, dataset, view = _bq_and_ref()
    if client is None:
        return jsonify({"error": "BQ unavailable"}), 503

    from google.cloud import bigquery
    sums = ",\n".join(f"SUM({m}) AS {m}" for m in _ALL_METRICS)
    # Two windows: current [days] and the immediately-preceding [days] for delta.
    sql = f"""
      WITH base AS (
        SELECT * FROM `{view}` WHERE property_uuid = @uuid
      )
      SELECT
        period,
        MAX(listing_ct) AS listings,
        {sums}
      FROM (
        SELECT
          CASE
            WHEN record_date >  DATE_SUB(CURRENT_DATE(), INTERVAL @days DAY)
              THEN 'current'
            WHEN record_date >  DATE_SUB(CURRENT_DATE(), INTERVAL @days2 DAY)
              THEN 'previous'
            ELSE 'older'
          END AS period,
          COUNT(DISTINCT costar_listing_id) OVER () AS listing_ct,
          {", ".join(_ALL_METRICS)}
        FROM base
        WHERE record_date > DATE_SUB(CURRENT_DATE(), INTERVAL @days2 DAY)
      )
      WHERE period IN ('current','previous')
      GROUP BY period
    """
    cfg = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("uuid", "STRING", uuid),
        bigquery.ScalarQueryParameter("days", "INT64", days),
        bigquery.ScalarQueryParameter("days2", "INT64", days * 2),
    ])

    current = {m: 0 for m in _ALL_METRICS}
    previous = {m: 0 for m in _ALL_METRICS}
    listings = 0
    try:
        for r in client.query(sql, job_config=cfg).result():
            bucket = current if r.period == "current" else previous
            for m in _ALL_METRICS:
                bucket[m] = int(getattr(r, m) or 0)
            if r.period == "current":
                listings = int(r.listings or 0)
    except Exception as exc:
        logger.warning("ils_summary query failed: %s", exc)
        return jsonify({"error": str(exc)[:200]}), 500

    # Property meta (latest row) — package + name.
    meta = _latest_meta(client, view, uuid)

    def _delta(m):
        cur, prev = current[m], previous[m]
        if prev == 0:
            return None
        return round((cur - prev) / prev * 100, 1)

    return jsonify({
        "uuid": uuid,
        "days": days,
        "listings": listings,
        "meta": meta,
        "attract": {m: current[m] for m in _ATTRACT_METRICS},
        "engage": {m: current[m] for m in _ENGAGE_METRICS},
        "previous": {m: previous[m] for m in _ALL_METRICS},
        "delta_pct": {m: _delta(m) for m in _ALL_METRICS},
    })


def _latest_meta(client, view, uuid) -> dict:
    from google.cloud import bigquery
    sql = f"""
      SELECT property_name, ad_package, city, state, costar_property_id,
             MAX(record_date) AS last_date
      FROM `{view}`
      WHERE property_uuid = @uuid
      GROUP BY property_name, ad_package, city, state, costar_property_id
      ORDER BY last_date DESC
      LIMIT 1
    """
    cfg = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("uuid", "STRING", uuid),
    ])
    try:
        for r in client.query(sql, job_config=cfg).result():
            return {
                "property_name": r.property_name,
                "ad_package": r.ad_package,
                "city": r.city,
                "state": r.state,
                "costar_property_id": r.costar_property_id,
                "last_date": r.last_date.isoformat() if r.last_date else None,
            }
    except Exception as exc:
        logger.warning("ils meta query failed: %s", exc)
    return {}


# ── GET /api/ils/trend ───────────────────────────────────────────────────────

@ils_bp.route("/api/ils/trend", methods=["GET", "OPTIONS"])
def ils_trend():
    if request.method == "OPTIONS":
        return preflight_response()
    if not _is_authorized(request):
        return jsonify({"error": "auth required"}), 401
    uuid = (request.args.get("uuid") or "").strip()
    if not uuid:
        return jsonify({"error": "uuid required"}), 400

    days = _days_param(90)
    client, project, dataset, view = _bq_and_ref()
    if client is None:
        return jsonify({"error": "BQ unavailable"}), 503

    from google.cloud import bigquery
    sums = ",\n".join(f"SUM({m}) AS {m}" for m in _ALL_METRICS)
    sql = f"""
      SELECT record_date, {sums}
      FROM `{view}`
      WHERE property_uuid = @uuid
        AND record_date > DATE_SUB(CURRENT_DATE(), INTERVAL @days DAY)
      GROUP BY record_date
      ORDER BY record_date
    """
    cfg = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("uuid", "STRING", uuid),
        bigquery.ScalarQueryParameter("days", "INT64", days),
    ])
    series = []
    try:
        for r in client.query(sql, job_config=cfg).result():
            row = {"date": r.record_date.isoformat()}
            for m in _ALL_METRICS:
                row[m] = int(getattr(r, m) or 0)
            series.append(row)
    except Exception as exc:
        logger.warning("ils_trend query failed: %s", exc)
        return jsonify({"error": str(exc)[:200]}), 500

    return jsonify({"uuid": uuid, "days": days, "series": series})


# ── POST /api/internal/apartmentscom-sync ────────────────────────────────────

@ils_bp.route("/api/internal/apartmentscom-sync", methods=["POST", "OPTIONS"])
def apartmentscom_sync():
    """Ingest one date from apartments.com. Cron/admin only.

    Body: {"date": "YYYY-MM-DD"} (optional; omit for yesterday).
    Auth: X-Internal-Key = INTERNAL_API_KEY.
    Runs synchronously — one API call + one BQ insert, a few seconds.
    """
    if request.method == "OPTIONS":
        return preflight_response()
    if not _is_internal(request):
        return jsonify({"error": "internal auth required"}), 401

    body = request.get_json(silent=True) or {}
    date = (body.get("date") or "").strip() or None

    import apartmentscom_client as ac
    import apartmentscom_ingestion as ing
    try:
        result = ing.ingest_date(date)
    except ac.ApartmentsComRateLimitError as exc:
        return jsonify({"error": "rate_limited", "detail": str(exc)}), 429
    except ac.ApartmentsComBadDateError as exc:
        return jsonify({"error": "bad_date", "detail": str(exc)}), 400
    except ac.ApartmentsComAuthError as exc:
        return jsonify({"error": "auth", "detail": str(exc)}), 502
    except ac.ApartmentsComError as exc:
        return jsonify({"error": "connector", "detail": str(exc)}), 502
    except Exception as exc:  # BQ or unexpected
        logger.exception("apartmentscom-sync failed")
        return jsonify({"error": "ingest_failed", "detail": str(exc)[:200]}), 500

    return jsonify({"ok": True, **result})
