"""Lease counts for portfolio screens, from the Hyly lake.

Leases are the same definition the monthly report uses
(`skills/workspace_report.py`): distinct `contact_id` with
`event_name = 'h_ms_lease'` in the org's `pai_journey` object, read through the
report's allowlisted, read-only `LakeReader`.

Only properties in the Hyly beta carry a `hyly_property_id`, so every result
says how many of the requested properties it covers. Returns None when BigQuery
is not configured, so callers report "unknown", not zero.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def hyly_ids(props: list) -> dict:
    """company_id → integer Hyly property id, for properties that have one."""
    out = {}
    for p in props:
        raw = str(p.get("hyly_property_id") or "").strip()
        if raw.isdigit():
            out[str(p.get("hubspot_company_id") or "")] = int(raw)
    return out


def leases_by_property(pids: list, start: str, end: str, *, lake=None) -> dict | None:
    """{hyly_property_id: leases} between two ISO dates inclusive, or None."""
    import bigquery_client
    from skills import workspace_report as wr

    if lake is None and not bigquery_client.is_bigquery_configured():
        return None
    ids = sorted({int(p) for p in pids})
    if not ids:
        return {}
    lake = lake or wr.LakeReader()
    # LakeReader takes scalar parameters only; the ids are validated integers,
    # so inlining them cannot inject anything.
    id_list = ", ".join(str(i) for i in ids)
    rows = lake.query(
        f"SELECT property_id, COUNT(DISTINCT contact_id) AS value FROM {lake.ref(lake.lease_object)} "
        f"WHERE property_id IN ({id_list}) AND event_name = 'h_ms_lease' "
        "AND DATE(event_date) BETWEEN @start AND @end GROUP BY property_id",
        [("start", "DATE", start), ("end", "DATE", end)])
    out = {i: 0 for i in ids}
    for r in rows:
        try:
            out[int(r["property_id"])] = int(r.get("value") or 0)
        except (TypeError, ValueError, KeyError):
            continue
    return out
