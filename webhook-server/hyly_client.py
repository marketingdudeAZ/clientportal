"""Hyly BigQuery reader — Convert stage data (ADR 0015, revised by ADR 0022).

WHAT CHANGED FROM ADR 0015
--------------------------
ADR 0015 assumed Hyly would deliver three pre-aggregated tables
(`daily_activity_summary`, `contact_submits`, `website_visits`). They do not
exist. What Hyly actually delivers is a per-contact funnel table,
`ga_hyly_mti`, in a project we do not own. See ADR 0022.

This module therefore reads our OWN rollup — `hyly_daily_activity_v1`, built
by migration 0022 into the landing dataset we control — rather than querying
the vendor's tables directly. Three reasons: the vendor project is named
"gds-prototype-*" and can change under us, the raw GA4 table is 10 GB and must
never be scanned on a page load, and the channel normalisation is ours.

CONFIG
------
  BIGQUERY_HYLY_PROJECT   project holding the rollup (defaults to
                          BIGQUERY_PROJECT_ID for backwards compatibility)
  BIGQUERY_HYLY_DATASET   dataset holding the rollup
  BIGQUERY_SERVICE_ACCOUNT_JSON / BIGQUERY_PROJECT_ID  credentials + billing

FAILURE POLICY
--------------
Unset config is a normal state — Hyly is a 15-property beta — and returns
empty. A query that FAILS while config is present raises `HylyQueryError`.
The previous version swallowed every exception into `[]`, which made a wrong
table name look identical to "this property has no data". That is the failure
shape that hid 46 silent Fluency budget no-ops; we do not repeat it here.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

_cache: dict = {"client": None, "logged_missing": False}

# Hyly's feed is daily. Two days of slack absorbs a weekend hiccup or a single
# failed run; beyond that the portal says so out loud rather than quietly
# showing old numbers as if they were current.
STALE_AFTER_DAYS = 2

# Milestone columns, in funnel order. Keys are the public metric names.
FUNNEL = [
    ("leads", "leads"),
    ("tours_scheduled", "tours_scheduled"),
    ("tours_completed", "tours_completed"),
    ("applications", "applications"),
    ("leases", "leases"),
]


class HylyQueryError(RuntimeError):
    """Raised when Hyly BQ is configured but a query fails.

    Callers should surface this (502), never render it as "no data".
    """


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
        info = json.loads(sa) if sa.strip().startswith("{") else json.load(open(sa))
        creds = service_account.Credentials.from_service_account_info(info)
        _cache["client"] = bigquery.Client(project=project, credentials=creds)
        return _cache["client"]
    except Exception as exc:
        # Client construction failing is a config problem, not a data problem.
        raise HylyQueryError(f"Hyly BQ client init failed: {exc}") from exc


def _hyly_ref() -> Optional[str]:
    """`project.dataset` holding the rollup, or None when unconfigured.

    ADR 0015 assumed the Hyly dataset lived in our own project, so the original
    built this from BIGQUERY_PROJECT_ID. It does not — hence the separate
    BIGQUERY_HYLY_PROJECT, which falls back to the old behaviour when unset.
    """
    dataset = os.environ.get("BIGQUERY_HYLY_DATASET")
    project = (os.environ.get("BIGQUERY_HYLY_PROJECT")
               or os.environ.get("BIGQUERY_PROJECT_ID"))
    if not (project and dataset):
        if not _cache["logged_missing"]:
            logger.info("hyly_client: BIGQUERY_HYLY_DATASET/PROJECT unset — "
                        "returning empty results")
            _cache["logged_missing"] = True
        return None
    return f"{project}.{dataset}"


def rollup_table() -> Optional[str]:
    ref = _hyly_ref()
    return f"{ref}.hyly_daily_activity_v1" if ref else None


def is_configured() -> bool:
    """True when env is set up to read the Hyly rollup."""
    try:
        return _hyly_ref() is not None and _bq() is not None
    except HylyQueryError:
        return False


def _query(sql: str, params: list) -> list[dict]:
    """Run a parameterised query. Empty when unconfigured; raises on failure."""
    client = _bq()
    if not (client and _hyly_ref()):
        return []
    from google.cloud import bigquery
    cfg = bigquery.QueryJobConfig(query_parameters=params)
    try:
        return [dict(r.items()) for r in client.query(sql, job_config=cfg).result()]
    except Exception as exc:
        # Loud on purpose — see FAILURE POLICY above.
        raise HylyQueryError(f"Hyly query failed: {exc}") from exc


def get_data_freshness() -> dict:
    """How current the Hyly rollup is, for display and for alerting.

    The portal must state what it knows rather than implying it is live. A
    dashboard frozen at a past date while looking current is worse than one
    that says so — this is the same failure the 2026-08-11 snapshot produced,
    where nothing refreshed and nothing complained.

    Returns {data_through, days_behind, is_stale, stale_after_days} — or
    {data_through: None} when unconfigured.
    """
    table = rollup_table()
    if not table:
        return {"data_through": None, "days_behind": None,
                "is_stale": None, "stale_after_days": STALE_AFTER_DAYS}
    rows = _query(f"SELECT MAX(activity_date) AS newest FROM `{table}`", [])
    newest = rows[0]["newest"] if rows else None
    if not newest:
        return {"data_through": None, "days_behind": None,
                "is_stale": True, "stale_after_days": STALE_AFTER_DAYS}
    from datetime import date
    days = (date.today() - newest).days
    return {
        "data_through": newest.isoformat(),
        "days_behind": days,
        "is_stale": days > STALE_AFTER_DAYS,
        "stale_after_days": STALE_AFTER_DAYS,
    }


def get_daily_activity(
    hyly_property_id: str,
    *,
    start_date: str,
    end_date: str,
) -> list[dict]:
    """Per-day x per-channel funnel rollup for one property.

    Returns dicts of: activity_date, channel, leads, tours_scheduled,
    tours_completed, applications, leases.
    """
    table = rollup_table()
    if not (table and hyly_property_id):
        return []
    from google.cloud import bigquery
    sql = f"""
      SELECT
        activity_date, channel,
        leads, tours_scheduled, tours_completed, applications, leases
      FROM `{table}`
      WHERE hyly_property_id = @pid
        AND activity_date BETWEEN DATE(@start) AND DATE(@end)
      ORDER BY activity_date DESC, channel
    """
    rows = _query(sql, [
        bigquery.ScalarQueryParameter("pid", "STRING", str(hyly_property_id)),
        bigquery.ScalarQueryParameter("start", "STRING", start_date),
        bigquery.ScalarQueryParameter("end", "STRING", end_date),
    ])
    for r in rows:
        if r.get("activity_date"):
            r["activity_date"] = r["activity_date"].isoformat()
    return rows


def get_channel_summary(
    hyly_property_id: str,
    *,
    start_date: str,
    end_date: str,
) -> dict:
    """Per-channel funnel totals across the range, plus a `_total` key.

    Shape per channel:
      {leads, tours_scheduled, tours_completed, applications, leases,
       lead_to_tour, lead_to_lease}

    NOTE: ADR 0015 specified `visitors` / `known_visitors` here. Hyly's funnel
    table has no visitor counts — those live in `ga4_analytics_events` and are
    not yet ingested — so the funnel now starts at `leads`. Callers that sorted
    on `visitors` must sort on `leads` instead.
    """
    rows = get_daily_activity(hyly_property_id,
                              start_date=start_date, end_date=end_date)
    by_channel: dict = {}
    for row in rows:
        agg = by_channel.setdefault(row.get("channel") or "Unattributed",
                                    {k: 0 for _, k in FUNNEL})
        for _, key in FUNNEL:
            agg[key] += row.get(key) or 0

    total = {k: 0 for _, k in FUNNEL}
    for v in by_channel.values():
        for _, k in FUNNEL:
            total[k] += v[k]

    def _rates(d: dict) -> dict:
        leads = d.get("leads") or 0
        d["lead_to_tour"] = (d["tours_completed"] / leads) if leads else None
        d["lead_to_lease"] = (d["leases"] / leads) if leads else None
        return d

    for v in by_channel.values():
        _rates(v)
    by_channel["_total"] = _rates(total)
    return by_channel


def get_contact_submits(
    hyly_property_id: str,
    *,
    start_date: str,
    end_date: str,
    limit: int = 5000,
) -> list[dict]:
    """Lead-level rows for one property, newest first.

    Reads the vendor funnel table directly (not the rollup) because this is
    contact-grain. Requires BIGQUERY_HYLY_SOURCE_TABLE to be set to the fully
    qualified vendor table; returns empty when it is not.
    """
    src = os.environ.get("BIGQUERY_HYLY_SOURCE_TABLE", "").strip()
    if not (src and hyly_property_id):
        return []
    from google.cloud import bigquery
    sql = f"""
      SELECT
        CAST(contact_id AS STRING)   AS contact_id,
        ANY_VALUE(property_name)     AS property_name,
        MIN(h_cc_event_datetime)     AS lead_at,
        MIN(h_fts_event_datetime)    AS tour_scheduled_at,
        MIN(h_ft_event_datetime)     AS toured_at,
        MIN(h_app_event_datetime)    AS applied_at,
        MIN(h_lease_event_datetime)  AS leased_at,
        ARRAY_AGG(source_mapped IGNORE NULLS ORDER BY event_datetime ASC)[SAFE_OFFSET(0)] AS source_raw
      FROM `{src}`
      WHERE CAST(property_id AS STRING) = @pid
        AND h_cc_event_datetime IS NOT NULL
        AND DATE(h_cc_event_datetime) BETWEEN DATE(@start) AND DATE(@end)
      GROUP BY contact_id
      ORDER BY lead_at DESC
      LIMIT @lim
    """
    rows = _query(sql, [
        bigquery.ScalarQueryParameter("pid", "STRING", str(hyly_property_id)),
        bigquery.ScalarQueryParameter("start", "STRING", start_date),
        bigquery.ScalarQueryParameter("end", "STRING", end_date),
        bigquery.ScalarQueryParameter("lim", "INT64", int(limit)),
    ])
    # No PII leaves Hyly's perimeter: this table carries no email/name columns,
    # so contact_id is the only identifier we surface (ADR 0015 privacy note).
    for r in rows:
        for k in ("lead_at", "tour_scheduled_at", "toured_at", "applied_at", "leased_at"):
            if r.get(k):
                r[k] = r[k].isoformat()
    return rows


def get_website_visits(*_args: Any, **_kwargs: Any) -> list[dict]:
    """Not available.

    ADR 0015 assumed a `website_visits` table. Page-view data lives in
    `ga4_analytics_events`, which is not yet ingested (ADR 0022, open item).
    Returns empty rather than raising so callers degrade cleanly.
    """
    return []
