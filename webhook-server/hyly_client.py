"""Hyly BigQuery client (ADR 0015).

Read-only access to Hyly's three tables:
  - daily_activity_summary  (per-day × per-source rollup)
  - contact_submits         (lead-level events)
  - website_visits          (page-view-level journey)

Join key: `property_id` (Hyly's id, stored on HubSpot company records
as `hyly_property_id` custom property — per ADR 0015).

Hyly's data lives in their own BQ dataset within our project. Configure
via `BIGQUERY_HYLY_DATASET` env var. When unset, every function returns
an empty list and logs once — useful before Hyly beta lands so callers
don't error.

All queries are property-scoped + date-bounded for cost control.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, date, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# ── BQ client (shared with loop_writer to avoid duplicate auth) ──────────────

_cache: dict = {"client": None, "logged_missing": False}


def _bq():
    if _cache["client"] is not None:
        return _cache["client"]
    project = os.environ.get("BIGQUERY_PROJECT_ID")
    sa = os.environ.get("BIGQUERY_SERVICE_ACCOUNT_JSON", "")
    if not (project and sa):
        return None
    try:
        from google.cloud import bigquery
        from google.oauth2 import service_account
        if sa.strip().startswith("{"):
            info = json.loads(sa)
        else:
            with open(sa) as fp:
                info = json.load(fp)
        creds = service_account.Credentials.from_service_account_info(info)
        _cache["client"] = bigquery.Client(project=project, credentials=creds)
        return _cache["client"]
    except Exception as exc:
        logger.warning("hyly_client BQ init failed: %s", exc)
        return None


def _hyly_ref() -> Optional[str]:
    project = os.environ.get("BIGQUERY_PROJECT_ID")
    dataset = os.environ.get("BIGQUERY_HYLY_DATASET")
    if not (project and dataset):
        if not _cache["logged_missing"]:
            logger.info("hyly_client: BIGQUERY_HYLY_DATASET unset — returning empty results")
            _cache["logged_missing"] = True
        return None
    return f"{project}.{dataset}"


def is_configured() -> bool:
    """True when env is set up to read Hyly tables."""
    return _hyly_ref() is not None and _bq() is not None


# ── Daily activity (per-channel rollup) ──────────────────────────────────────

def get_daily_activity(
    hyly_property_id: str,
    *,
    start_date: str,
    end_date: str,
) -> list[dict]:
    """Per-day × per-source rollup for one property.

    start_date / end_date: 'YYYY-MM-DD', inclusive.
    Returns list of dicts: {event_date, source, visitors, known_visitors,
                             total_views, converted_contacts, ...}.
    """
    client = _bq()
    ref = _hyly_ref()
    if not (client and ref and hyly_property_id):
        return []

    from google.cloud import bigquery
    sql = f"""
      SELECT
        event_date,
        source,
        SAFE_CAST(visitors AS INT64) AS visitors,
        SAFE_CAST(known_visitors AS INT64) AS known_visitors,
        SAFE_CAST(total_views AS INT64) AS total_views,
        SAFE_CAST(converted_contacts AS INT64) AS converted_contacts,
        SAFE_CAST(visitors_ndls AS INT64) AS visitors_ndls,
        SAFE_CAST(known_visitors_ndls AS INT64) AS known_visitors_ndls,
        SAFE_CAST(total_views_ndls AS INT64) AS total_views_ndls,
        SAFE_CAST(converted_contacts_ndls AS INT64) AS converted_contacts_ndls
      FROM `{ref}.daily_activity_summary`
      WHERE CAST(property_id AS STRING) = @pid
        AND DATE(event_date) BETWEEN DATE(@start) AND DATE(@end)
      ORDER BY event_date DESC, source
    """
    params = [
        bigquery.ScalarQueryParameter("pid", "STRING", str(hyly_property_id)),
        bigquery.ScalarQueryParameter("start", "STRING", start_date),
        bigquery.ScalarQueryParameter("end", "STRING", end_date),
    ]
    cfg = bigquery.QueryJobConfig(query_parameters=params)
    try:
        out = []
        for r in client.query(sql, job_config=cfg).result():
            d = dict(r.items())
            if d.get("event_date"):
                d["event_date"] = d["event_date"].isoformat()
            out.append(d)
        return out
    except Exception as exc:
        logger.warning("hyly_client.get_daily_activity failed for %s: %s",
                       hyly_property_id, exc)
        return []


def get_channel_summary(
    hyly_property_id: str,
    *,
    start_date: str,
    end_date: str,
) -> dict:
    """Aggregated per-channel summary across the date range.

    Returns:
      {
        "Google PayPerClick (PPC)": {visitors, known_visitors, contacts, conv_rate},
        "apartments.com": {...},
        ...
        "_total": {...}
      }
    """
    rows = get_daily_activity(hyly_property_id,
                              start_date=start_date, end_date=end_date)
    by_source: dict = {}
    for row in rows:
        s = row.get("source") or "(unknown)"
        agg = by_source.setdefault(s, {
            "visitors": 0, "known_visitors": 0,
            "total_views": 0, "contacts": 0,
        })
        agg["visitors"] += row.get("visitors") or 0
        agg["known_visitors"] += row.get("known_visitors") or 0
        agg["total_views"] += row.get("total_views") or 0
        agg["contacts"] += row.get("converted_contacts") or 0

    total = {"visitors": 0, "known_visitors": 0,
             "total_views": 0, "contacts": 0}
    for v in by_source.values():
        for k in total:
            total[k] += v[k]
        v["conv_rate"] = (v["contacts"] / v["visitors"]) if v["visitors"] else None

    total["conv_rate"] = (total["contacts"] / total["visitors"]) if total["visitors"] else None
    by_source["_total"] = total
    return by_source


# ── Contact submits (lead-level) ─────────────────────────────────────────────

def get_contact_submits(
    hyly_property_id: str,
    *,
    start_date: str,
    end_date: str,
    limit: int = 5000,
) -> list[dict]:
    """Lead-level events with full attribution. Newest first.

    Returns dicts with the columns the BQ table exposes — see ADR 0015
    for the full list. Caller typically cares about:
      created_at, email, utm_source, utm_medium, utm_campaign,
      detected_source, hybeacon_channel_name, page, act_url
    """
    client = _bq()
    ref = _hyly_ref()
    if not (client and ref and hyly_property_id):
        return []

    from google.cloud import bigquery
    sql = f"""
      SELECT
        created_at,
        email,
        first_name,
        last_name,
        page_url,
        referrer,
        gclid,
        detected_source,
        act_url,
        `api.in_utm_source`     AS utm_source,
        `api.in_utm_medium`     AS utm_medium,
        `api.in_utm_campaign`   AS utm_campaign,
        `api.in_utm_term`       AS utm_term,
        `api.in_utm_content`    AS utm_content,
        `api.hybeacon_channel_name` AS hybeacon_channel,
        `api.hybeacon_source_name`  AS hybeacon_source,
        `api.hybeacon_method_name`  AS hybeacon_method,
        Page AS page,
        counted
      FROM `{ref}.contact_submits`
      WHERE CAST(property_id AS STRING) = @pid
        AND DATE(created_at) BETWEEN DATE(@start) AND DATE(@end)
      ORDER BY created_at DESC
      LIMIT @lim
    """
    params = [
        bigquery.ScalarQueryParameter("pid", "STRING", str(hyly_property_id)),
        bigquery.ScalarQueryParameter("start", "STRING", start_date),
        bigquery.ScalarQueryParameter("end", "STRING", end_date),
        bigquery.ScalarQueryParameter("lim", "INT64", limit),
    ]
    cfg = bigquery.QueryJobConfig(query_parameters=params)
    try:
        out = []
        for r in client.query(sql, job_config=cfg).result():
            d = dict(r.items())
            if d.get("created_at"):
                d["created_at"] = d["created_at"].isoformat()
            out.append(d)
        return out
    except Exception as exc:
        logger.warning("hyly_client.get_contact_submits failed for %s: %s",
                       hyly_property_id, exc)
        return []


# ── Website visits (page-view-level) ─────────────────────────────────────────

def get_website_visits(
    hyly_property_id: str,
    *,
    start_date: str,
    end_date: str,
    limit: int = 10000,
) -> list[dict]:
    """Page-view-level journey data. Useful for building prospect journey
    timelines + retroactive attribution analysis. Same column shape as
    contact_submits with type=visit instead of type=note.
    """
    client = _bq()
    ref = _hyly_ref()
    if not (client and ref and hyly_property_id):
        return []

    from google.cloud import bigquery
    sql = f"""
      SELECT
        created_at,
        email,
        page_url,
        referrer,
        gclid,
        detected_source,
        `api.in_utm_source`     AS utm_source,
        `api.in_utm_medium`     AS utm_medium,
        `api.in_utm_campaign`   AS utm_campaign,
        `api.hybeacon_channel_name` AS hybeacon_channel,
        Page AS page
      FROM `{ref}.website_visits`
      WHERE CAST(property_id AS STRING) = @pid
        AND DATE(created_at) BETWEEN DATE(@start) AND DATE(@end)
      ORDER BY created_at DESC
      LIMIT @lim
    """
    params = [
        bigquery.ScalarQueryParameter("pid", "STRING", str(hyly_property_id)),
        bigquery.ScalarQueryParameter("start", "STRING", start_date),
        bigquery.ScalarQueryParameter("end", "STRING", end_date),
        bigquery.ScalarQueryParameter("lim", "INT64", limit),
    ]
    cfg = bigquery.QueryJobConfig(query_parameters=params)
    try:
        out = []
        for r in client.query(sql, job_config=cfg).result():
            d = dict(r.items())
            if d.get("created_at"):
                d["created_at"] = d["created_at"].isoformat()
            out.append(d)
        return out
    except Exception as exc:
        logger.warning("hyly_client.get_website_visits failed for %s: %s",
                       hyly_property_id, exc)
        return []


# ── Loop event emission ──────────────────────────────────────────────────────

def emit_loop_events_for_recent_submits(
    hyly_property_id: str,
    property_uuid: str,
    *,
    since_hours: int = 24,
) -> int:
    """Pull recent contact submits and write them as Loop convert events.
    Returns the count written.

    Designed to be called by a daily cron (or a webhook from Hyly if
    they ever support one). Idempotent — `source_id=act_url` is the
    dedupe key, so re-running over the same window doesn't duplicate.
    """
    import loop_writer

    if not (hyly_property_id and property_uuid):
        return 0

    since = datetime.utcnow() - timedelta(hours=since_hours)
    end_dt = datetime.utcnow()
    submits = get_contact_submits(
        hyly_property_id,
        start_date=since.strftime("%Y-%m-%d"),
        end_date=end_dt.strftime("%Y-%m-%d"),
        limit=5000,
    )

    n = 0
    for s in submits:
        # Privacy: hash email before writing to loop_events (PII stays in Hyly)
        email = (s.get("email") or "").strip().lower()
        if email:
            import hashlib
            email_hash = hashlib.sha256(email.encode()).hexdigest()[:16]
        else:
            email_hash = None

        # Idempotency: use act_url as the source_id (unique per Hyly lead)
        source_id = s.get("act_url") or None
        if not source_id:
            continue  # can't dedupe without a stable id

        # Parse the created_at back to datetime
        created_at = s.get("created_at")
        if isinstance(created_at, str):
            try:
                created_at = datetime.fromisoformat(created_at)
            except ValueError:
                created_at = None

        loop_writer.record(
            stage="convert",
            event_type="lead_submitted",
            property_uuid=property_uuid,
            source="hyly",
            source_id=source_id,
            occurred_at=created_at,
            magnitude=1.0,
            trigger="cron",
            payload={
                "email_hash":     email_hash,
                "utm_source":     s.get("utm_source"),
                "utm_medium":     s.get("utm_medium"),
                "utm_campaign":   s.get("utm_campaign"),
                "utm_term":       s.get("utm_term"),
                "utm_content":    s.get("utm_content"),
                "gclid":          s.get("gclid"),
                "detected_source": s.get("detected_source"),
                "hybeacon_channel": s.get("hybeacon_channel"),
                "hybeacon_source":  s.get("hybeacon_source"),
                "page":           s.get("page"),
            },
        )
        n += 1
    return n
