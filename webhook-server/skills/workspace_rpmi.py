"""RPMI portfolio — `GET /api/workspace/rpmi`, the owner-group roll-up.

One internal screen for the RPM Investments book: how much of it we can
actually see, and which properties carry the most exposure.

The roster is every HubSpot company whose `client` field is RPMI or
RPM Investments. Of those, the managed ones (the same `plestatus` +
`uuid` + management-end rule `portfolio.fetch_portfolio` applies) are what the
screen reports on.

| field                        | source                                                  |
|------------------------------|---------------------------------------------------------|
| totals.units                 | HubSpot `totalunits`                                     |
| totals.occupancy             | unit-weighted AptIQ advertised occupancy (daily export)  |
| totals.available_units       | sum of AptIQ advertised available units                  |
| totals.leases_last_month     | Hyly lake leases, last full month (Hyly properties only) |
| totals.spend_monthly         | contracted deal line items, management fee excluded      |
| totals.open_recommendations  | HubDB recommendation rows still pending                  |
| properties[].units_at_risk   | AptIQ advertised available units (see the caveat below)  |
| properties[].cost_per_lease  | contracted monthly spend ÷ last full month's Hyly leases |
| coverage                     | how many properties carry each source's join key         |

Measured facts this is built to (2026-09-17): 116 RPMI records, 109 managed,
and on those 109 — availability 87, analytics 79, paid 77, funnel 14, uuid 109.
Hyly is NOT everywhere, so every funnel number here is null-with-a-gap on 95 of
109 properties and the screen says so rather than printing a zero.

`market` is blank on 104 of the 109, so nothing groups by it. Rows carry the
value when it is set and `"Market not set"` is the honest answer otherwise;
`grouping` names state as the field that is actually populated.

Cost control: every read here is portfolio-wide and batched — one roster search,
the shared AptIQ and spend caches, one HubDB read and one BigQuery query. There
is no per-property fan-out; that is the shape that took the tickets tab down
(see `skills/workspace_portfolio.py`).

Caveat kept from the Portfolio contract: "units at risk" in the build plan means
units vacant 90+ days, and no per-unit feed exists for that. What is reported is
AptIQ's advertised available units, named as such in the receipt.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import date, timedelta

from skills import workspace_common as wc

logger = logging.getLogger(__name__)

# The HubSpot `client` values that mean RPM Investments.
CLIENT_VALUES = ("RPMI", "RPM Investments")
# portfolio.fetch_portfolio's rule, applied to the roster search result.
MANAGED_STATUSES = ("RPM Managed", "Onboarding")

# TODO: `skills/rpmi_roster.py` is the intended home for this roster; another
# agent owns that module. When it lands, delete `_search_roster` / `roster`
# below and call it instead — nothing outside this module reads them.
ROSTER_FIELDS = (
    "name", "client", "plestatus", "uuid", "city", "state", "zip", "domain",
    "totalunits", "rpmmarket", "managementend", "disposition_retained",
    "aptiq_property_id", "hyly_property_id", "ga4_property_id", "google_ads_customer_id",
)
ROSTER_TTL = float(os.environ.get("WORKSPACE_RPMI_TTL", "900"))
_ROSTER_PAGE = 100
_ROSTER_MAX_PAGES = 20

# key → (join field on the company, client-safe label, the sentence a row shows
# when the key is missing).
SOURCES = (
    ("aptiq", "aptiq_property_id", "Availability",
     "Availability and occupancy aren’t connected here yet, so units at risk is unknown."),
    ("ga4", "ga4_property_id", "Analytics",
     "Website analytics isn’t connected here yet."),
    ("google_ads", "google_ads_customer_id", "Paid",
     "Paid search isn’t connected here yet."),
    ("hyly", "hyly_property_id", "Funnel",
     "The leasing funnel isn’t connected here, so leases and cost per lease are unknown."),
)
MISSING_MESSAGE = dict((key, message) for key, _, _, message in SOURCES)
# Management fee is not marketing money, so it stays out of spend and out of
# cost per lease. Every other contracted SKU counts.
SPEND_EXCLUDED_SKUS = ("mgmt_fee",)

_roster_cache: dict = {"rows": None, "at": 0.0}
_roster_lock = threading.Lock()


# ── roster ───────────────────────────────────────────────────────────────────

def _search_roster() -> list:
    """Every HubSpot company whose `client` is one of `CLIENT_VALUES`.

    Paged CRM search. The set is ~116 records, so this is one or two round
    trips — far short of the portfolio-scale pagination flakiness
    `portfolio.fetch_portfolio` works around with a full LIST enumeration.
    """
    import hubspot_client

    payload = {
        "filterGroups": [{"filters": [{"propertyName": "client", "operator": "IN",
                                       "values": list(CLIENT_VALUES)}]}],
        "properties": list(ROSTER_FIELDS),
        "limit": _ROSTER_PAGE,
    }
    rows, seen, after = [], set(), None
    for _ in range(_ROSTER_MAX_PAGES):
        body = dict(payload)
        if after:
            body["after"] = after
        resp = hubspot_client._request(
            "POST", f"{hubspot_client.API_BASE}/crm/v3/objects/companies/search", json=body)
        data = resp.json()
        for company in data.get("results") or []:
            cid = str(company.get("id") or "")
            if not cid or cid in seen:
                continue
            seen.add(cid)
            props = dict(company.get("properties") or {})
            props["hubspot_company_id"] = cid
            rows.append(props)
        after = ((data.get("paging") or {}).get("next") or {}).get("after")
        if not after:
            break
    return rows


def roster() -> tuple:
    """(every RPMI company record, as_of). Served stale inside `ROSTER_TTL`."""
    with _roster_lock:
        rows, at = _roster_cache["rows"], _roster_cache["at"]
    if rows is not None and (time.time() - at) < ROSTER_TTL:
        return rows, wc.to_iso_ts(at)
    rows = _search_roster()
    now = time.time()
    with _roster_lock:
        _roster_cache["rows"], _roster_cache["at"] = rows, now
    return rows, wc.to_iso_ts(now)


def clear_cache() -> None:
    with _roster_lock:
        _roster_cache["rows"], _roster_cache["at"] = None, 0.0


def _management_ended(value) -> bool:
    d = wc.to_date(value)
    return bool(d and d < date.today())


def is_managed(p: dict) -> bool:
    """portfolio.fetch_portfolio's rule: a uuid, and either retained or active."""
    if not str(p.get("uuid") or "").strip():
        return False
    retained = str(p.get("disposition_retained") or "").strip().lower() in ("true", "yes", "1")
    if retained:
        return True
    if str(p.get("plestatus") or "").strip() not in MANAGED_STATUSES:
        return False
    return not _management_ended(p.get("managementend"))


def managed_roster() -> tuple:
    """(the managed RPMI properties, the full record count, as_of)."""
    rows, as_of = roster()
    return [p for p in rows if is_managed(p)], len(rows), as_of


# ── portfolio-wide reads, one call each ──────────────────────────────────────

def _aptiq(gaps: list) -> tuple:
    from skills import workspace_cache
    try:
        return workspace_cache.aptiq_daily()
    except Exception as exc:  # noqa: BLE001 — one missing source never blanks the screen
        logger.warning("workspace rpmi: AptIQ export unavailable: %s", exc)
        gaps.append(wc.gap("occupancy", "Availability data could not be read just now, so occupancy "
                                        "and units at risk are unknown.", source="aptiq"))
        return {}, None


def _spend(gaps: list) -> tuple:
    """{company_id: monthly contracted $ excluding the management fee}, as_of."""
    from skills import workspace_cache
    try:
        rows, as_of = workspace_cache.spend_rows()
    except Exception as exc:  # noqa: BLE001
        logger.warning("workspace rpmi: spend sheet unavailable: %s", exc)
        gaps.append(wc.gap("spend_monthly", "Contracted spend could not be read just now.",
                           source="hubspot_line_items"))
        return {}, None
    import spend_sheet
    keys = [k for k in spend_sheet._SPEND_COLUMN_KEYS if k not in SPEND_EXCLUDED_SKUS]
    out: dict = {}
    for row in rows or []:
        total = 0.0
        for k in keys:
            amt = wc.to_float(row.get(k))
            if amt and amt > 0:
                total += amt
        out[str(row.get("company_id") or "")] = round(total, 2)
    return out, as_of


def _open_recommendations(props: list, gaps: list) -> tuple:
    """{uuid: pending recommendation cards}, as_of — one HubDB read, not 109."""
    from config import HUBDB_RECOMMENDATIONS_TABLE_ID
    if not HUBDB_RECOMMENDATIONS_TABLE_ID:
        gaps.append(wc.gap("open_recommendations", "Recommendations aren’t connected, so open counts "
                                                   "are unknown.", source="hubdb_rec"))
        return None, None
    from hubdb_helpers import read_rows
    wanted = {str(p.get("uuid") or "").strip() for p in props}
    wanted.discard("")
    counts = {u: 0 for u in wanted}
    for row in read_rows(HUBDB_RECOMMENDATIONS_TABLE_ID, limit=1000) or []:
        uuid = str(row.get("property_uuid") or "").strip()
        if uuid in counts and str(row.get("status") or "").strip().lower() == "pending":
            counts[uuid] += 1
    return counts, wc.now_iso()


def last_full_month(today: date) -> tuple:
    """(start ISO, end ISO, "YYYY-MM") for the month before `today`'s."""
    end = today.replace(day=1) - timedelta(days=1)
    return end.replace(day=1).isoformat(), end.isoformat(), end.strftime("%Y-%m")


def _leases(props: list, today: date, gaps: list) -> tuple:
    """{company_id: leases last full month} for the Hyly properties, and the period."""
    from skills import workspace_leasing as wlease
    by_company = wlease.hyly_ids(props)
    start, end, month = last_full_month(today)
    if not by_company:
        return {}, month, None
    try:
        counts = wlease.leases_by_property(list(by_company.values()), start, end)
    except Exception as exc:  # noqa: BLE001
        logger.warning("workspace rpmi: lease counts unavailable: %s", exc)
        counts = None
    if counts is None:
        gaps.append(wc.gap("leases_last_month", "Leasing funnel counts aren’t available right now, so "
                                                "leases and cost per lease are unknown.", source="hyly"))
        return {}, month, None
    return ({cid: counts.get(pid) for cid, pid in by_company.items() if counts.get(pid) is not None},
            month, wc.month_end(month))


# ── per property ─────────────────────────────────────────────────────────────

def _aptiq_metrics(p: dict, rows: dict, loaded):
    """(occupancy, available_units) receipts from the AptIQ daily export."""
    from services.fluency_ingestion import apt_iq_reader
    row = rows.get(str(p.get("aptiq_property_id") or "").strip()) if rows else None
    if not row:
        return None, None
    as_of = wc.to_iso_ts(row.get("Report Generation Date")) or loaded
    occ = wc.ratio(apt_iq_reader._resolve_col(row, "occupancy_pct"))
    avail = wc.to_int(apt_iq_reader._resolve_col(row, "available_units"))
    return wc.metric(occ, "aptiq", as_of), wc.metric(avail, "aptiq", as_of)


def sources_for(p: dict) -> dict:
    """{source key: True when the property carries that join key}."""
    return {key: bool(str(p.get(field) or "").strip()) for key, field, _, _ in SOURCES}


def _row(p, aptiq_rows, aptiq_as_of, spend, spend_as_of, leases, lease_as_of, recs, recs_as_of,
         roster_as_of):
    cid = str(p.get("hubspot_company_id") or "")
    uuid = str(p.get("uuid") or "").strip()
    have = sources_for(p)
    gaps: list = []

    occupancy, available = _aptiq_metrics(p, aptiq_rows, aptiq_as_of)
    if occupancy is None and available is None:
        gaps.append(wc.gap("units_at_risk", MISSING_MESSAGE["aptiq"], source="aptiq"))

    lease_count = leases.get(cid)
    if lease_count is None:
        gaps.append(wc.gap("leases_last_month", MISSING_MESSAGE["hyly"], source="hyly"))
    for key in ("ga4", "google_ads"):
        if not have[key]:
            gaps.append(wc.gap(key, MISSING_MESSAGE[key], source=key))

    monthly = spend.get(cid)
    if monthly is None:
        gaps.append(wc.gap("spend_monthly", "No contracted line items are on file, so monthly spend "
                                            "is unknown.", source="hubspot_line_items"))
    cost = None
    if monthly is not None and lease_count:
        cost = wc.metric(round(monthly / lease_count, 2), "hubspot_line_items+hyly", lease_as_of,
                         leases=lease_count, spend=monthly)

    market = str(p.get("rpmmarket") or "").strip() or None
    open_recs = None if recs is None else wc.metric(recs.get(uuid, 0), "hubdb_rec", recs_as_of)
    return {
        "company_id": cid,
        "name": str(p.get("name") or "").strip() or None,
        "city": str(p.get("city") or "").strip() or None,
        "state": str(p.get("state") or "").strip() or None,
        "market": market,
        "units": wc.metric(wc.to_int(p.get("totalunits")), "hubspot_company", roster_as_of),
        "occupancy": occupancy,
        "available_units": available,
        # The build plan's "units at risk" (vacant 90+ days) has no per-unit
        # feed; this is AptIQ's advertised available units, ranked descending.
        "units_at_risk": available,
        "leases_last_month": (wc.metric(lease_count, "hyly", lease_as_of)
                              if lease_count is not None else None),
        "cost_per_lease": cost,
        "spend_monthly": (wc.metric(monthly, "hubspot_line_items", spend_as_of)
                          if monthly is not None else None),
        "open_recommendations": open_recs,
        "sources": have,
        "status": str(p.get("plestatus") or "").strip() or None,
        "href": "#/property/" + cid,
        "gaps": gaps,
    }


def _rank_key(row: dict):
    risk = row["units_at_risk"]
    return (-(risk["value"] if risk else -1), (row["name"] or "").lower())


# ── payload ──────────────────────────────────────────────────────────────────

def build_rpmi(email: str = "", *, today: date = None, internal: bool = True) -> dict:
    today = today or date.today()
    gaps: list = []

    props, record_count, roster_as_of = managed_roster()
    if not props:
        gaps.append(wc.gap("properties", "No RPM Investments property is set up for reporting yet."))

    aptiq_rows, aptiq_as_of = _aptiq(gaps) if props else ({}, None)
    spend, spend_as_of = _spend(gaps) if props else ({}, None)
    recs, recs_as_of = _open_recommendations(props, gaps) if props else (None, None)
    leases, lease_month, lease_as_of = _leases(props, today, gaps) if props else ({}, None, None)

    rows = [_row(p, aptiq_rows, aptiq_as_of, spend, spend_as_of, leases, lease_as_of,
                 recs, recs_as_of, roster_as_of) for p in props]
    rows.sort(key=_rank_key)

    coverage = {"property_count": len(props), "sources": [
        {"key": key, "label": label, "count": sum(1 for r in rows if r["sources"][key])}
        for key, _, label, _ in SOURCES]}
    for entry in coverage["sources"]:
        entry["missing"] = len(props) - entry["count"]

    no_market = sum(1 for r in rows if not r["market"])
    if no_market:
        gaps.append(wc.gap("market", f"Market is not set on {no_market} of {len(rows)} properties, so "
                                     "the list groups by state instead.", source="hubspot_company"))

    return {
        "as_of": wc.now_iso(),
        "scope_label": f"RPM Investments · {len(props)} managed "
                       f"{'property' if len(props) == 1 else 'properties'}",
        "client_values": list(CLIENT_VALUES),
        "record_count": record_count,
        "property_count": len(props),
        "period": {"leases_month": lease_month},
        "totals": _totals(rows, props, aptiq_rows, aptiq_as_of, spend_as_of, lease_as_of, recs_as_of,
                          roster_as_of),
        "coverage": coverage,
        "grouping": {"field": "state", "label": "State",
                     "note": "Grouped by state: market is not filled in on most of these properties."},
        "properties": rows,
        "gaps": wc.gaps_for(gaps, internal=internal),
    }


def _totals(rows, props, aptiq_rows, aptiq_as_of, spend_as_of, lease_as_of, recs_as_of, roster_as_of):
    units = sum(r["units"]["value"] for r in rows if r["units"])
    avail = [r["available_units"]["value"] for r in rows if r["available_units"]]
    spends = [r["spend_monthly"]["value"] for r in rows if r["spend_monthly"]]
    leases = [r["leases_last_month"]["value"] for r in rows if r["leases_last_month"]]
    recs = [r["open_recommendations"]["value"] for r in rows if r["open_recommendations"]]

    from skills import workspace_dashboard as wdash
    occupancy = wdash.occupancy_kpi(props, aptiq_rows, aptiq_as_of) if props else None

    spend_total = round(sum(spends), 2) if spends else None
    lease_total = sum(leases) if leases else None
    cost = (round(spend_total / lease_total, 2)
            if spend_total and lease_total else None)
    return {
        "units": wc.metric(units or None, "hubspot_company", roster_as_of),
        "occupancy": occupancy,
        "available_units": (wc.metric(sum(avail), "aptiq", aptiq_as_of, properties=len(avail))
                            if avail else None),
        "units_at_risk": (wc.metric(sum(avail), "aptiq", aptiq_as_of, properties=len(avail))
                          if avail else None),
        "leases_last_month": (wc.metric(lease_total, "hyly", lease_as_of, properties=len(leases))
                              if lease_total is not None else None),
        "spend_monthly": (wc.metric(spend_total, "hubspot_line_items", spend_as_of,
                                    properties=len(spends)) if spend_total is not None else None),
        "cost_per_lease": (wc.metric(cost, "hubspot_line_items+hyly", lease_as_of,
                                     properties=len(leases)) if cost is not None else None),
        "open_recommendations": (wc.metric(sum(recs), "hubdb_rec", recs_as_of)
                                 if recs else None),
    }
