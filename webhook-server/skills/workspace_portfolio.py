"""Workspace portfolio — the account manager's first screen.

Two views (Phase 2):

* `view=needs_me` — properties whose HubSpot company lists the caller in
  `marketing_manager_email`, `marketing_director_email` or `marketing_rvp_email`
  (the same assignment fields portfolio.py's role filters use). Only properties
  with open items are listed; the rest are counted in `quiet_count`.
* `view=all` — every managed property, ranked by AptIQ available units, 50 per
  page. Items are read only for the page being shown. This is the default for
  an internal user with no assigned properties (amendment 9).

Per property this reads only the inbox sources that cost one HubDB or company
read (`PORTFOLIO_SOURCES`). Tickets, forecast recommendations and ticket-profile
proposals are left to each property's Work screen, and a gap says so: fanning
ClickUp and BigQuery across a portfolio on every page load is the shape that took
the tickets tab down (see start.py). The managed-property list and the AptIQ
export come from `workspace_cache`, so they are served stale rather than rebuilt
on a request.
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
VIEWS = ("needs_me", "all")
PAGE_SIZE = 50
_MAX_WORKERS = 8


def managed_properties() -> list[dict]:
    from skills import workspace_cache
    rows, _ = workspace_cache.portfolio_rows()
    return rows


def assigned_properties(email: str) -> list[dict]:
    """Managed properties that name `email` in an assignment field."""
    target = (email or "").strip().lower()
    if not target:
        return []
    return [p for p in managed_properties()
            if any(str(p.get(f) or "").strip().lower() == target for f in ASSIGNMENT_FIELDS)]


def _aptiq_rows(gaps: list) -> tuple[dict, str | None]:
    """All AptIQ daily-CSV rows keyed by AptIQ property id, plus as_of."""
    from skills import workspace_cache
    try:
        return workspace_cache.aptiq_daily()
    except Exception as exc:  # noqa: BLE001
        logger.warning("workspace portfolio: AptIQ export unavailable: %s", exc)
        gaps.append(wc.gap("occupancy", f"AptIQ daily export could not be read ({exc})", source="aptiq"))
        return {}, None


def _risk(p: dict, aptiq: dict, fallback_as_of: str | None):
    from services.fluency_ingestion import apt_iq_reader
    row = aptiq.get(str(p.get("aptiq_property_id") or "").strip()) if aptiq else None
    if not row:
        return None, None
    as_of = wc.to_iso_ts(row.get("Report Generation Date")) or fallback_as_of
    occ = wc.ratio(apt_iq_reader._resolve_col(row, "occupancy_pct"))
    avail = wc.to_int(apt_iq_reader._resolve_col(row, "available_units"))
    return wc.metric(occ, "aptiq", as_of), wc.metric(avail, "aptiq", as_of)


def _rank_key(row: dict):
    risk = row["units_at_risk"]
    return (-(risk["value"] if risk else -1), row["start_by"] or "9999-12-31", (row["name"] or "").lower())


def build_portfolio(email: str, *, view: str | None = None, page: int = 1,
                    today: date | None = None, market: str | None = None) -> dict:
    today = today or date.today()
    horizon = today + timedelta(days=6)
    gaps: list = []

    assigned = assigned_properties(email)
    view = view if view in VIEWS else ("needs_me" if assigned else "all")
    if view == "needs_me":
        scope = assigned
        scope_label = f"Assigned to you · {len(scope)} properties"
        if not scope:
            gaps.append(wc.gap("properties",
                               "No managed property lists this email as marketing manager, "
                               "director or RVP; use view=all to see every property"))
    else:
        scope = managed_properties()
        scope_label = f"All managed properties · {len(scope)}"
    from skills import workspace_scope as wscope
    markets = wscope.markets_in(scope)
    scope = wscope.in_market(scope, market)
    gaps.append(wc.gap("items",
                       "Portfolio counts cover recommendation cards, call prep, video variants "
                       "and onboarding gaps; tickets, forecast recommendations and profile "
                       "proposals are on each property's Work screen"))

    aptiq, aptiq_as_of = _aptiq_rows(gaps) if scope else ({}, None)

    # Rank by the cheap signal first, so view=all reads items for one page only.
    base = []
    for p in scope:
        occupancy, risk = _risk(p, aptiq, aptiq_as_of)
        base.append({"p": p, "occupancy": occupancy, "units_at_risk": risk,
                     "start_by": None, "name": p.get("name") or None})
    base.sort(key=_rank_key)
    page = max(1, int(page or 1))
    if view == "all":
        start = (page - 1) * PAGE_SIZE
        to_read = base[start:start + PAGE_SIZE]
        next_page = page + 1 if start + PAGE_SIZE < len(base) else None
    else:
        to_read, next_page = base, None

    def _one(entry: dict):
        cid = str(entry["p"].get("hubspot_company_id") or "")
        try:
            ctx = wi.load_context(cid)
            items, _ = wi.collect(ctx, sources=PORTFOLIO_SOURCES, today=today, with_history=False)
            return entry, items, None
        except Exception as exc:  # noqa: BLE001 — one property never blanks the screen
            logger.warning("workspace portfolio: %s failed: %s", cid, exc)
            return entry, [], exc

    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        results = list(pool.map(_one, to_read))

    failed = 0
    rows, quiet, item_count, starting = [], 0, 0, 0
    for entry, items, err in results:
        p = entry["p"]
        if err is not None:
            failed += 1
        to_do = [i for i in items if i["status"] == "to_do"]
        item_count += len(to_do)
        starting += sum(1 for i in to_do if wi.item_date(i) and today <= wi.item_date(i) <= horizon)
        if not to_do:
            quiet += 1
            if view == "needs_me":
                continue
        top = sorted(to_do, key=wi._sort_key)[0] if to_do else None
        top_date = wi.item_date(top) if top else None
        rows.append({
            "company_id": str(p.get("hubspot_company_id") or ""),
            "name": p.get("name") or None,
            "city": p.get("city") or None,
            "state": p.get("state") or None,
            "market": wscope.market_of(p),
            "units": wc.to_int(p.get("totalunits")),
            "units_source": "hubspot_company",
            "occupancy": entry["occupancy"],
            "top_item": ({"id": top["id"], "title": wi.view_item(top)["title"],
                          "needs_approval": bool(top["needs_approval"])} if top else None),
            "more_items": max(len(to_do) - 1, 0),
            "start_by": top_date.isoformat() if top_date else None,
            "overdue": bool(top_date and top_date < today),
            # AptIQ advertised available units. The contract's "units at risk"
            # (units vacant 90+ days) has no per-unit feed; see Contract changes.
            "units_at_risk": entry["units_at_risk"],
        })
    if failed:
        gaps.append(wc.gap("properties", f"{failed} propert(ies) could not be read"))
    if rows and any(r["units_at_risk"] is None for r in rows):
        gaps.append(wc.gap("units_at_risk", "Some properties have no AptIQ row; they rank last", source="aptiq"))

    rows.sort(key=_rank_key)
    return {
        "as_of": wc.now_iso(),
        "view": view,
        "scope_label": scope_label,
        "property_count": len(scope),
        "markets": markets,
        "market": (market or "").strip() or None,
        "item_count": item_count,
        "starting_this_week": starting,
        "properties": rows,
        "quiet_count": quiet,
        "page": page,
        "page_size": PAGE_SIZE if view == "all" else len(rows),
        "next_page": next_page,
        "gaps": wc.gaps_for(gaps, internal=True),
    }
