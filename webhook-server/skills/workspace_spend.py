"""Spend sheet — `GET /api/workspace/spend-sheet`.

The same rows the internal spend tracker and `/accounts` show: one per managed
property, from HubSpot deals and line items, built by `spend_sheet.py` and
served through its serve-stale cache (`workspace_cache.spend_rows`). This module
adds no HubSpot call site.

Who sees what, decided server-side:

  * RPM staff see every managed property, every channel, the management fee and
    the deal and quote fields.
  * Clients see the properties they can access (`feature_access.companies_for`).
    INTERNAL_KEYS never appear in `columns`, `values` or `totals`, and their
    totals exclude them. Preview-as-client gets the same stripped columns.

Numbers are the contracted monthly line items on the property's most recent
deal; an absent line item stays null, never 0. Zillow is billed at the company
level, not on the deal, so it is a meta column and is not in the total.
"""

from __future__ import annotations

import logging
from typing import Any

from skills import workspace_common as wc

logger = logging.getLogger(__name__)

SOURCE = "spend_sheet: your signed agreements"

# key → label, in the order the table shows them. Every SKU column spend_sheet
# builds except the management fee, which is internal.
CHANNELS = (
    ("search", "Search"), ("pmax", "Performance Max"), ("paid_social", "Paid social"),
    ("display", "Display"), ("retargeting", "Retargeting"), ("ctv", "CTV / programmatic"),
    ("geofence", "Geofence"), ("demand_gen", "Demand Gen"), ("youtube", "YouTube"),
    ("tiktok", "TikTok"), ("seo", "SEO"), ("social_posting", "Social posting"),
    ("reputation", "Reputation"), ("eblast", "Eblast"), ("email_drip", "Email drip"),
    ("website_hosting", "Website hosting"),
)
META = (
    ("costar_package", "CoStar package"), ("zillow_per_month", "Zillow (monthly, billed separately)"),
    ("zillow_per_lease", "Zillow per lease"), ("cx_bundle", "CX bundle"),
)
# Internal: removed for clients and preview-as-client. `ple_status` is the row's
# `status`, so it is not a value column; it is stripped from rows and filters.
INTERNAL_COLUMNS = (
    ("mgmt_fee", "Mgmt fee"), ("deal_name", "Deal"), ("deal_stage", "Deal stage"),
    ("deal_amount", "Deal amount"), ("close_date", "Close date"), ("deal_id", "Deal ID"),
    ("quote_status", "Quote status"), ("quote_title", "Quote"),
)
INTERNAL_KEYS = frozenset({"mgmt_fee", "deal_id", "deal_name", "deal_stage", "deal_amount",
                           "quote_status", "quote_title", "ple_status", "close_date"})
# Summed into a row's total. The management fee counts only for staff.
TOTAL_KEYS = tuple(k for k, _ in CHANNELS)
STAFF_TOTAL_KEYS = TOTAL_KEYS + ("mgmt_fee",)
NUMERIC_KEYS = frozenset(TOTAL_KEYS + ("mgmt_fee", "deal_amount", "zillow_per_month", "zillow_per_lease"))

ROW_SORT_KEYS = ("property_name", "status", "market", "manager", "total")
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


def columns(internal: bool) -> list:
    out = [{"key": k, "label": label, "group": "channel", "internal": False} for k, label in CHANNELS]
    out += [{"key": k, "label": label, "group": "meta", "internal": False} for k, label in META]
    if internal:
        out += [{"key": k, "label": label, "group": "internal", "internal": True} for k, label in INTERNAL_COLUMNS]
    return out


def _num(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sum(values) -> float | None:
    nums = [v for v in values if v is not None]
    return round(sum(nums), 2) if nums else None


def shape_row(raw: dict, internal: bool) -> dict:
    keys = [c["key"] for c in columns(internal)]
    values = {}
    for key in keys:
        v = raw.get(key)
        values[key] = _num(v) if key in NUMERIC_KEYS else (v if v not in ("",) else None)
    total_keys = STAFF_TOTAL_KEYS if internal else TOTAL_KEYS
    cid = str(raw.get("company_id") or "")
    return {
        "company_id": cid,
        "property_name": raw.get("property_name") or None,
        "status": (raw.get("ple_status") or None) if internal else None,
        "market": raw.get("market") or None,
        "manager": raw.get("marketing_manager") or None,
        "values": values,
        "total": _sum(values.get(k) for k in total_keys),
        "href": f"#/property/{cid}",
    }


def parse_params(args) -> dict:
    """Query string → keyword arguments. Raises WorkspaceError(400)."""
    def text(name):
        return (args.get(name) or "").strip() or None

    direction = (text("dir") or "asc").lower()
    if direction not in ("asc", "desc"):
        raise wc.WorkspaceError(400, "Invalid dir", "asc|desc")
    page, page_size = text("page") or "1", text("page_size") or str(DEFAULT_PAGE_SIZE)
    if not page.isdigit() or int(page) < 1:
        raise wc.WorkspaceError(400, "Invalid page")
    if not page_size.isdigit() or not 1 <= int(page_size) <= MAX_PAGE_SIZE:
        raise wc.WorkspaceError(400, "Invalid page_size", f"1–{MAX_PAGE_SIZE}")
    return {"q": text("q"), "market": text("market"), "manager": text("manager"), "status": text("status"),
            "sort": text("sort"), "direction": direction, "page": int(page), "page_size": int(page_size)}


def _sort_value(row: dict, key: str):
    v = row.get(key) if key in ROW_SORT_KEYS else row["values"].get(key)
    if isinstance(v, str):
        return v.lower()
    return v


def build_spend_sheet(email: str, *, internal: bool, real_internal: bool, q: str | None = None,
                      market: str | None = None, manager: str | None = None, status: str | None = None,
                      sort: str | None = None, direction: str = "asc", page: int = 1,
                      page_size: int = DEFAULT_PAGE_SIZE) -> dict:
    """`internal` is the effective role (False in preview-as-client); scope follows
    the real role, so a previewing staff member sees their rows as a client would."""
    from skills import workspace_cache

    gaps: list = []
    sort = sort or "property_name"
    visible = {c["key"] for c in columns(internal)}
    if sort not in ROW_SORT_KEYS and sort not in visible:
        raise wc.WorkspaceError(400, "Invalid sort", "a column key, property_name, status, market, manager or total")
    if sort == "status" and not internal:
        raise wc.WorkspaceError(400, "Invalid sort", "status is not available")

    try:
        raw_rows, as_of = workspace_cache.spend_rows()
    except Exception as exc:  # noqa: BLE001
        logger.warning("workspace spend sheet unavailable: %s", exc)
        raw_rows, as_of = [], None
        gaps.append(wc.gap("rows", f"The spend sheet could not be built ({type(exc).__name__})",
                           source="spend_sheet"))

    if not real_internal:
        from feature_access import companies_for
        allowed = {str(c) for c in companies_for(email)}
        raw_rows = [r for r in raw_rows if str(r.get("company_id")) in allowed]
        if not allowed:
            gaps.append(wc.gap("rows", "No properties are linked to this account yet"))

    rows = [shape_row(r, internal) for r in raw_rows]
    filters = {
        "markets": sorted({r["market"] for r in rows if r["market"]}),
        "managers": sorted({r["manager"] for r in rows if r["manager"]}),
        "statuses": sorted({r["status"] for r in rows if r["status"]}) if internal else [],
    }

    def match(r):
        if q and q.lower() not in (r["property_name"] or "").lower():
            return False
        if market and (r["market"] or "").lower() != market.lower():
            return False
        if manager and (r["manager"] or "").lower() != manager.lower():
            return False
        if status and internal and (r["status"] or "").lower() != status.lower():
            return False
        return True

    rows = [r for r in rows if match(r)]
    present = [r for r in rows if _sort_value(r, sort) is not None]
    missing = [r for r in rows if _sort_value(r, sort) is None]
    present.sort(key=lambda r: (_sort_value(r, sort), (r["property_name"] or "").lower()),
                 reverse=direction == "desc")
    rows = present + missing          # nulls last in either direction

    numeric_visible = [c["key"] for c in columns(internal) if c["key"] in NUMERIC_KEYS]
    totals = {"values": {k: _sum(r["values"].get(k) for r in rows) for k in numeric_visible},
              "total": _sum(r["total"] for r in rows)}
    start = (page - 1) * page_size
    if rows:
        gaps.append(wc.gap("values", "Amounts are contracted monthly line items on each property's most recent "
                                     "deal, not billed spend", source="spend_sheet"))
    return {
        "as_of": as_of,
        "scope": "portfolio" if internal else "client",
        "source": SOURCE,
        "columns": columns(internal),
        "rows": rows[start:start + page_size],
        "totals": totals,
        "count": len(rows),
        "page": page,
        "page_size": page_size,
        "filters": filters,
        "gaps": wc.gaps_for(gaps, internal),
    }
