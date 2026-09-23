"""Which properties a portfolio-level workspace screen covers, and their health.

Clients: the companies `feature_access.companies_for` grants them.
Internal staff: the properties HubSpot assigns them (marketing manager, director
or RVP email); with none assigned, every managed property.

Health is the Red Light score on the HubSpot company record, in five bands:
healthy >= 75, attention >= 60, warning >= 50, critical < 50, new (no score).
The 75 / 50 edges match `portfolio.compute_rollups`; "attention" splits its
50-74 "warning" band so the v3 tiles can tell a slipping property from a failing
one.
"""

from __future__ import annotations

import logging

from skills import workspace_common as wc

logger = logging.getLogger(__name__)

SCOPE_FIELDS = ["name", "city", "state", "totalunits", "uuid", "aptiq_property_id",
                "redlight_report_score", "red_light_report_score", "red_light_run_date",
                "plestatus", "marketing_manager_email", "marketing_director_email",
                "marketing_rvp_email", "hyly_property_id", "rpmmarket"]

# How many properties a portfolio screen reads work items for, worst health first.
MAX_ITEM_PROPERTIES = 40


def market_of(props: dict) -> str | None:
    """The RPM market on the company record. `rpmmarket`, not `market`: the
    bare field is set on a handful of properties, `rpmmarket` on the portfolio."""
    return str(props.get("rpmmarket") or "").strip() or None


def markets_in(props: list) -> list[str]:
    """Every market the properties sit in, for a filter's options.

    One entry per market regardless of case, since `in_market` matches that way:
    "Phoenix" and "phoenix" are one choice, spelled as first seen."""
    seen: dict[str, str] = {}
    for m in (market_of(p) for p in props):
        if m:
            seen.setdefault(m.lower(), m)
    return sorted(seen.values(), key=str.lower)


def in_market(props: list, market: str | None) -> list:
    """The properties in `market` (case-insensitive); all of them when blank."""
    want = (market or "").strip().lower()
    if not want:
        return props
    return [p for p in props if (market_of(p) or "").lower() == want]


def health_score(props: dict) -> float | None:
    for key in ("red_light_report_score", "redlight_report_score"):
        v = wc.to_float(props.get(key))
        if v is not None:
            return v
    return None


def health_band(score: float | None) -> str:
    if score is None:
        return "new"
    if score >= 75:
        return "healthy"
    if score >= 60:
        return "attention"
    if score >= 50:
        return "warning"
    return "critical"


def properties_in_scope(email: str, internal: bool) -> dict:
    """{properties: [company props + hubspot_company_id], label, gaps}."""
    gaps: list = []
    if not internal:
        import hubspot_client
        from concurrent.futures import ThreadPoolExecutor
        from feature_access import companies_for

        def _one(cid):
            try:
                p = dict(hubspot_client.get_company(cid, SCOPE_FIELDS) or {})
            except Exception as exc:  # noqa: BLE001
                logger.warning("workspace scope: company %s unreadable: %s", cid, exc)
                return cid, None
            p["hubspot_company_id"] = cid
            return cid, p

        # One HubSpot GET per company, but concurrently: read serially, a
        # 35-property book spent 35 round trips before any screen could start,
        # and this runs on every portfolio screen. Bounded so a large book does
        # not open a socket per property.
        cids = sorted(companies_for(email))
        props = []
        if cids:
            with ThreadPoolExecutor(max_workers=min(8, len(cids))) as pool:
                for cid, p in pool.map(_one, cids):   # map keeps the sorted order
                    if p is None:
                        gaps.append(wc.gap("properties", f"Property {cid} could not be read"))
                    else:
                        props.append(p)
        return {"properties": props, "label": f"Your properties · {len(props)}", "gaps": gaps}

    from skills import workspace_portfolio
    assigned = workspace_portfolio.assigned_properties(email)
    if assigned:
        return {"properties": assigned, "label": f"Assigned to you · {len(assigned)} properties", "gaps": gaps}
    managed = workspace_portfolio.managed_properties()
    return {"properties": managed, "label": f"All managed properties · {len(managed)}", "gaps": gaps}


def worst_first(props: list) -> list:
    return sorted(props, key=lambda p: (health_score(p) is None, health_score(p) or 0,
                                        (p.get("name") or "").lower()))
