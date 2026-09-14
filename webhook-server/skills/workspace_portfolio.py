"""Workspace portfolio — the account manager's first screen.

Which properties: the ones whose HubSpot company lists the caller in
`marketing_manager_email`, `marketing_director_email` or `marketing_rvp_email`
(the same assignment fields portfolio.py's role filters use). The list is read
from `portfolio.fetch_portfolio`, which enumerates managed properties with a
15-minute cache.

Per property this reads only the inbox sources that cost one HubDB or company
read (`PORTFOLIO_SOURCES`). Tickets, forecast recommendations and ticket-profile
proposals are left to each property's Work screen, and a gap says so; fanning
ClickUp and BigQuery out across a whole portfolio on every page load is the
shape that took the tickets tab down (see start.py).
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from skills import workspace_common as wc
from skills import workspace_inbox as wi

logger = logging.getLogger(__name__)

ASSIGNMENT_FIELDS = ("marketing_manager_email", "marketing_director_email", "marketing_rvp_email")
PORTFOLIO_SOURCES = ("hubdb_rec", "call_prep", "video_variant", "onboarding_gap")
_MAX_WORKERS = 8


def assigned_properties(email: str) -> list[dict]:
    """Managed properties that name `email` in an assignment field."""
    import portfolio

    target = (email or "").strip().lower()
    if not target:
        return []
    rows = portfolio.fetch_portfolio(target, "marketing_rvp") or []
    return [p for p in rows
            if any(str(p.get(f) or "").strip().lower() == target for f in ASSIGNMENT_FIELDS)]


def _aptiq_rows(gaps: list) -> tuple[dict, str | None]:
    """All AptIQ daily-CSV rows keyed by AptIQ property id, plus the fetch time."""
    try:
        from services.fluency_ingestion import apt_iq_csv_client
        rows = apt_iq_csv_client.get_all_rows() or {}
        loaded = apt_iq_csv_client._cache_loaded_at
    except Exception as exc:  # noqa: BLE001
        logger.warning("workspace portfolio: AptIQ CSV unavailable: %s", exc)
        gaps.append(wc.gap("occupancy", f"AptIQ daily export could not be read ({type(exc).__name__})"))
        return {}, None
    if not rows:
        gaps.append(wc.gap("occupancy", "AptIQ daily export is empty or APT_IQ_DAILY_SHEET_URL is not set"))
    return rows, (wc.to_iso_ts(loaded) if loaded else None)


def _top(to_do: list):
    return sorted(to_do, key=wi._sort_key)[0] if to_do else None


def build_portfolio(email: str, *, today: date | None = None) -> dict:
    today = today or date.today()
    horizon = today + timedelta(days=6)
    gaps: list = []

    props_list = assigned_properties(email)
    if not props_list:
        gaps.append(wc.gap("properties",
                           "No managed property lists this email as marketing manager, "
                           "director or RVP"))
    gaps.append(wc.gap("items",
                       "Portfolio counts cover recommendation cards, call prep, video variants "
                       "and onboarding gaps; tickets, forecast recommendations and profile "
                       "proposals are on each property's Work screen"))

    aptiq, aptiq_as_of = _aptiq_rows(gaps) if props_list else ({}, None)
    from services.fluency_ingestion import apt_iq_reader

    def _one(p: dict):
        cid = str(p.get("hubspot_company_id") or "")
        try:
            ctx = wi.load_context(cid)
            items, _ = wi.collect(ctx, sources=PORTFOLIO_SOURCES, today=today, with_history=False)
            return p, items, None
        except Exception as exc:  # noqa: BLE001 — one property never blanks the screen
            logger.warning("workspace portfolio: %s failed: %s", cid, exc)
            return p, [], exc

    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        results = list(pool.map(_one, props_list))

    failed = 0
    rows, quiet, item_count, starting = [], 0, 0, 0
    for p, items, err in results:
        if err is not None:
            failed += 1
        to_do = [i for i in items if i["status"] == "to_do"]
        item_count += len(to_do)
        starting += sum(1 for i in to_do if wi.item_date(i) and today <= wi.item_date(i) <= horizon)
        if not to_do:
            quiet += 1
            continue
        top = _top(to_do)
        top_date = wi.item_date(top)
        row = aptiq.get(str(p.get("aptiq_property_id") or "").strip()) if aptiq else None
        occ = wc.ratio(apt_iq_reader._resolve_col(row, "occupancy_pct")) if row else None
        avail = wc.to_int(apt_iq_reader._resolve_col(row, "available_units")) if row else None
        rows.append({
            "company_id": str(p.get("hubspot_company_id") or ""),
            "name": p.get("name") or None,
            "city": p.get("city") or None,
            "state": p.get("state") or None,
            "units": wc.to_int(p.get("totalunits")),
            "units_source": "hubspot_company",
            "occupancy": wc.metric(occ, "aptiq", aptiq_as_of),
            "top_item": {"id": top["id"], "title": top["title"]},
            "more_items": len(to_do) - 1,
            "start_by": top_date.isoformat() if top_date else None,
            "overdue": bool(top_date and top_date < today),
            "units_at_risk": wc.metric(avail, "aptiq", aptiq_as_of),
        })
    if failed:
        gaps.append(wc.gap("properties", f"{failed} propert(ies) could not be read and are not listed"))
    if rows and any(r["units_at_risk"] is None for r in rows):
        gaps.append(wc.gap("units_at_risk", "Some properties have no AptIQ row; they rank last"))

    rows.sort(key=lambda r: (
        -(r["units_at_risk"]["value"] if r["units_at_risk"] else -1),
        r["start_by"] or "9999-12-31",
    ))
    return {
        "as_of": wc.now_iso(),
        "property_count": len(props_list),
        "item_count": item_count,
        "starting_this_week": starting,
        "properties": rows,
        "quiet_count": quiet,
        "gaps": wc.public(gaps),
    }
