"""Workspace read views — /me, /property, /performance, /plan.

Every number is `{value, source, as_of}` or null with a named gap. Sources:

* HubSpot company record, one read via `workspace_inbox.load_context`
  (hubspot_client): name, address, units, people, platform ids, brief override.
* AptIQ daily CSV and floor-plan CSV (services/fluency_ingestion): occupancy,
  available units, floor plans. `as_of` is when this process fetched the export,
  because the export carries no date of its own.
* Spend sheet (spend_sheet.get_company_monthly_spend): the contracted monthly
  line items on the property's deals. `as_of` is when the sheet was built.

What no feed on main provides, returned as null with a gap: units vacant 90+
days, upcoming vacancies as unit counts, coming-open-by-week, cost per lease,
and what each channel is pointed at.
"""

from __future__ import annotations

import logging
import os

from skills import workspace_common as wc
from skills import workspace_inbox as wi

logger = logging.getLogger(__name__)

SPEND_SOURCE = "hubspot_line_items"
APTIQ_SOURCE = "aptiq"

CHANNEL_LABELS = {
    "paid_search": "Paid search",
    "paid_social": "Paid social",
    "seo": "SEO",
    "reputation": "Reputation",
    "creative": "Creative and email",
    "fees": "Management and hosting",
}

PLAN_CAVEAT = (
    "Amounts are the contracted monthly line items on this property's deals, not "
    "measured spend. Cost per lease is not available as data yet: it exists only "
    "inside the Red Light report PDF. Pending changes counts open, unsigned deals "
    "on the property."
)


# ── /me ──────────────────────────────────────────────────────────────────────

def _company_summary(props: dict, company_id: str) -> dict:
    return {
        "company_id": str(company_id),
        "uuid": props.get("uuid") or None,
        "name": props.get("name") or None,
        "city": props.get("city") or None,
        "state": props.get("state") or None,
        "units": wc.to_int(props.get("totalunits")),
        "source": "hubspot_company",
    }


def build_me(email: str, *, verified: bool) -> dict:
    """Who is calling, their role, and the properties they work on.

    Internal staff may open any property; `companies` lists the ones HubSpot
    assigns to them. Clients get the companies they are scoped to.
    """
    from feature_access import ROLE_INTERNAL, companies_for, role_for
    from skills import workspace_portfolio

    role = role_for(email)
    gaps: list = []
    companies: list = []
    if role == ROLE_INTERNAL:
        try:
            for p in workspace_portfolio.assigned_properties(email):
                companies.append(_company_summary(p, p.get("hubspot_company_id") or ""))
        except Exception as exc:  # noqa: BLE001
            logger.warning("workspace me: assignment lookup failed for %s: %s", email, exc)
            gaps.append(wc.gap("companies", f"Assigned properties could not be read ({type(exc).__name__})"))
    else:
        import hubspot_client
        for cid in sorted(companies_for(email)):
            try:
                props = hubspot_client.get_company(cid, ["uuid", "name", "city", "state", "totalunits"])
                companies.append(_company_summary(props or {}, cid))
            except Exception as exc:  # noqa: BLE001
                logger.warning("workspace me: company %s unreadable: %s", cid, exc)
                gaps.append(wc.gap(f"companies:{cid}", "Company record could not be read"))
    companies.sort(key=lambda c: (c["name"] or "").lower())
    return {
        "email": email,
        "role": role,
        "verified": bool(verified),
        "portfolio_wide": role == ROLE_INTERNAL,
        "companies": companies,
        "gaps": wc.public(gaps),
    }


# ── shared readers ───────────────────────────────────────────────────────────

def aptiq_snapshot(ctx: wi.PropertyContext, gaps: list) -> tuple[dict | None, str | None]:
    """The property's AptIQ daily-CSV row, normalized, plus its fetch time."""
    pid = str(ctx.props.get("aptiq_property_id") or "").strip()
    if not pid:
        gaps.append(wc.gap("aptiq", "No aptiq_property_id on the company record"))
        return None, None
    if not os.environ.get("APT_IQ_DAILY_SHEET_URL"):
        gaps.append(wc.gap("aptiq", "APT_IQ_DAILY_SHEET_URL is not set"))
        return None, None
    try:
        from services.fluency_ingestion import apt_iq_csv_client, apt_iq_reader
        read = apt_iq_reader.read_property({
            "aptiq_property_id": pid,
            "aptiq_market_id": ctx.props.get("aptiq_market_id") or "",
        })
        loaded = apt_iq_csv_client._cache_loaded_at
    except Exception as exc:  # noqa: BLE001
        logger.warning("workspace aptiq read failed for %s: %s", ctx.company_id, exc)
        gaps.append(wc.gap("aptiq", f"AptIQ daily export could not be read ({type(exc).__name__})"))
        return None, None
    if not read.get("matched"):
        gaps.append(wc.gap("aptiq", read.get("reason") or "No AptIQ row for this property"))
        return None, None
    return read, (wc.to_iso_ts(loaded) if loaded else None)


def spend(company_id: str, gaps: list, field: str = "monthly_plan") -> tuple[dict | None, str | None]:
    try:
        import spend_sheet
        row = spend_sheet.get_company_monthly_spend(company_id)
        cached = spend_sheet._cache.get("data")
        as_of = wc.to_iso_ts(cached[0]) if cached else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("workspace spend read failed for %s: %s", company_id, exc)
        gaps.append(wc.gap(field, f"Spend sheet could not be read ({type(exc).__name__})"))
        return None, None
    if not row.get("deal_id") and not row.get("by_sku"):
        gaps.append(wc.gap(field, "Property is not in the spend sheet (no deal line items)"))
        return None, None
    return row, as_of


def channel_amounts(by_sku: dict) -> dict:
    """SKU amounts → channel amounts (spend_sheet_to_channels), fees kept apart."""
    from spend_sheet_to_channels import CHANNELS, SKU_TO_CHANNEL

    out = {c: 0.0 for c in CHANNELS}
    out["fees"] = 0.0
    for sku, amt in (by_sku or {}).items():
        a = wc.to_float(amt) or 0.0
        out[SKU_TO_CHANNEL.get(sku, "fees")] += a
    return {k: round(v, 2) for k, v in out.items() if v > 0}


# ── /property ────────────────────────────────────────────────────────────────

def _brief(ctx: wi.PropertyContext, gaps: list, internal: bool) -> dict:
    import community_brief

    field = next((f for _, fields in community_brief.SECTIONS for f in fields if f.key == "romance"), None)
    empty = {"text": None, "curated": False, "edited_by": None, "edited_at": None}
    if field is None:
        gaps.append(wc.gap("brief", "Community brief has no romance field"))
        return empty
    text = community_brief.resolve_value(ctx.props, field.hs_resolved, field.hs_override) or None
    curated = bool(field.hs_override and community_brief._nonblank(ctx.props.get(field.hs_override)))
    if not text:
        gaps.append(wc.gap("brief", "No brief paragraph on the property yet"))
        return empty
    edited_by = edited_at = None
    if curated:
        try:
            import property_brief_audit
            edit = next((e for e in property_brief_audit.recent_edits(ctx.company_id, 50)
                         if e.get("field_key") == field.key), None)
        except Exception as exc:  # noqa: BLE001
            logger.warning("workspace brief audit read failed for %s: %s", ctx.company_id, exc)
            edit = None
        if edit:
            edited_by = edit.get("edited_by") or None
            edited_at = wc.to_iso_date(edit.get("edited_at"))
        else:
            gaps.append(wc.gap("brief.edited_by", "No audit row for the last brief edit"))
    flags = wc.fair_housing_flags(text)
    if flags:
        gaps.append(wc.gap("brief.text", f"Brief held for Fair Housing review: {', '.join(flags)}"))
        if not internal:
            text = None
    return {"text": text, "curated": curated, "edited_by": edited_by, "edited_at": edited_at}


def _floorplans(ctx: wi.PropertyContext, gaps: list) -> list:
    pid = str(ctx.props.get("aptiq_property_id") or "").strip()
    if not pid:
        return []
    if not os.environ.get("APT_IQ_FLOOR_PLAN_SHEET_URL"):
        gaps.append(wc.gap("floorplans", "APT_IQ_FLOOR_PLAN_SHEET_URL is not set"))
        return []
    try:
        from services.fluency_ingestion import apt_iq_csv_client, apt_iq_reader
        plans = apt_iq_reader.read_floor_plans(pid)
        loaded = apt_iq_csv_client._fp_loaded_at
    except Exception as exc:  # noqa: BLE001
        logger.warning("workspace floor plans failed for %s: %s", ctx.company_id, exc)
        gaps.append(wc.gap("floorplans", f"AptIQ floor-plan export could not be read ({type(exc).__name__})"))
        return []
    if not plans:
        gaps.append(wc.gap("floorplans", "No rows for this property in the AptIQ floor-plan export"))
    as_of = wc.to_iso_ts(loaded) if loaded else None
    return [{"code": pl.get("name"), "beds": pl.get("beds"), "sqft": pl.get("sqft"),
             "available": pl.get("available"), "source": "aptiq_floor_plans", "as_of": as_of}
            for pl in plans]


def _people(ctx: wi.PropertyContext, gaps: list) -> list:
    p = ctx.props
    people = []
    owner_id = str(p.get("hubspot_owner_id") or "").strip()
    if owner_id:
        # The AM is the company record owner; there is no account_manager
        # property (config.PORTAL_TICKET_PREFILL_SOURCES documents this).
        try:
            import portal_tickets
            name = portal_tickets._owner_name(owner_id)
        except Exception:  # noqa: BLE001
            name = ""
        if name:
            people.append({"name": name, "role": "Account manager", "email": None})
        else:
            gaps.append(wc.gap("people", "Record owner could not be resolved to a name"))
    mm_email = str(p.get("marketing_manager_email") or "").strip() or None
    mm_name = str(p.get("marketing_manager") or "").strip() or None
    if mm_email or mm_name:
        people.append({"name": mm_name or mm_email, "role": "Property marketing manager", "email": mm_email})
    for key, role in (("marketing_director_email", "Marketing director"),
                      ("marketing_rvp_email", "Marketing RVP")):
        email = str(p.get(key) or "").strip()
        if email:
            people.append({"name": email, "role": role, "email": email})
    if not people:
        gaps.append(wc.gap("people", "No owner or marketing contacts on the company record"))
    return people


def _connections(ctx: wi.PropertyContext, aptiq: dict | None, aptiq_as_of: str | None,
                 gaps: list) -> list:
    p = ctx.props
    out = []
    if aptiq:
        out.append({"name": "ApartmentIQ", "status": "connected", "synced_at": aptiq_as_of})
    else:
        out.append({"name": "ApartmentIQ", "status": "not_connected", "synced_at": None})
    # An id on the record says the property is linked; nothing on main verifies
    # a sync for these, so they are "linked", never "connected".
    for name, key in (("Hyly", "hyly_property_id"), ("GA4", "ga4_property_id"),
                      ("Google Ads", "google_ads_customer_id")):
        linked = bool(str(p.get(key) or "").strip())
        out.append({"name": name, "status": "linked" if linked else "not_connected", "synced_at": None})
    out.append({"name": "Yardi", "status": "not_connected", "synced_at": None})
    gaps.append(wc.gap("connections",
                       "Hyly, GA4 and Google Ads show whether an id is on the record, not a "
                       "verified sync; nothing reports when the property last synced to Fluency"))
    return out


def build_property(ctx: wi.PropertyContext, *, internal: bool = True) -> dict:
    p = ctx.props
    gaps: list = []
    street = str(p.get("address") or "").strip()
    locality = " ".join(x for x in (str(p.get("city") or "").strip(),
                                    str(p.get("state") or "").strip(),
                                    str(p.get("zip") or "").strip()) if x)
    address = ", ".join(x for x in (street, locality) if x) or None
    domain = str(p.get("domain") or p.get("website") or "").strip()
    for prefix in ("https://", "http://"):
        if domain.startswith(prefix):
            domain = domain[len(prefix):]
    managed = wc.to_iso_date(p.get("managementstart"))
    if not managed:
        gaps.append(wc.gap("managed_since", "No management start date on the company record"))

    aptiq, aptiq_as_of = aptiq_snapshot(ctx, gaps)
    return {
        "name": ctx.name or None,
        "address": address,
        "domain": domain.rstrip("/") or None,
        "managed_since": managed[:7] if managed else None,
        "brief": _brief(ctx, gaps, internal),
        "floorplans": _floorplans(ctx, gaps),
        "people": _people(ctx, gaps),
        "connections": _connections(ctx, aptiq, aptiq_as_of, gaps),
        "gaps": wc.public(gaps),
    }


# ── /performance ─────────────────────────────────────────────────────────────

RANGES = (30, 90, 365)


def build_performance(ctx: wi.PropertyContext, *, range_days: int = 30) -> dict:
    p = ctx.props
    gaps: list = []
    aptiq, as_of = aptiq_snapshot(ctx, gaps)
    total = wc.to_int(p.get("totalunits"))

    occ = wc.ratio(aptiq.get("occupancy_pct")) if aptiq else None
    occupied = wc.metric(
        occ, APTIQ_SOURCE, as_of,
        units=(round(occ * total) if (occ is not None and total) else None),
        total=total,
        total_source="hubspot_company",
        target=wc.ratio(p.get("target_occupancy")),
    )
    if aptiq and occ is None:
        gaps.append(wc.gap("occupied", "AptIQ row has no occupancy value"))

    avail = wc.to_int(aptiq.get("available_units")) if aptiq else None
    available = wc.metric(avail, APTIQ_SOURCE, as_of, stale_90_plus=None)
    if aptiq and avail is None:
        gaps.append(wc.gap("available_now", "AptIQ row has no available-units value"))
    gaps.append(wc.gap("available_now.stale_90_plus", "No feed on main reports how long a unit has been vacant"))
    gaps.append(wc.gap("coming_open_90d",
                       "AptIQ reports 90-day exposure as a percentage; no feed on main gives "
                       "upcoming vacancies as unit counts by bedroom"))
    gaps.append(wc.gap("coming_by_week", "No feed on main gives upcoming vacancies by week"))

    row, spend_as_of = spend(ctx.company_id, gaps)
    monthly_plan = None
    if row:
        monthly_plan = wc.metric(row.get("total"), SPEND_SOURCE, spend_as_of,
                                 by_channel=channel_amounts(row.get("by_sku") or {}))
    if range_days != 30:
        gaps.append(wc.gap("range", "Occupancy history is not available as data; values are current"))
    return {
        "range": range_days,
        "occupied": occupied,
        "available_now": available,
        "coming_open_90d": None,
        "coming_by_week": [],
        "monthly_plan": monthly_plan,
        "note": None,
        "gaps": wc.public(gaps),
    }


# ── /plan ────────────────────────────────────────────────────────────────────

def build_plan(ctx: wi.PropertyContext) -> dict:
    gaps: list = []
    row, as_of = spend(ctx.company_id, gaps, field="monthly_total")
    channels = []
    total = None
    if row:
        total = row.get("total")
        amounts = channel_amounts(row.get("by_sku") or {})
        order = list(CHANNEL_LABELS)
        for key in sorted(amounts, key=lambda k: (order.index(k) if k in order else 99)):
            amt = amounts[key]
            channels.append({
                "channel": CHANNEL_LABELS.get(key, key),
                "monthly": amt,
                "share": round(amt / total, 4) if total else None,
                "cost_per_lease": None,
                "pointed_at": None,
                "status": "active",
                "status_note": None,
            })
        if channels:
            gaps.append(wc.gap("channels.cost_per_lease", "Cost per lease exists only inside the Red Light report PDF"))
            gaps.append(wc.gap("channels.pointed_at", "No feed on main says which floor plans a channel targets"))

    pending = None
    try:
        import hubspot_client
        pending = len(hubspot_client.get_open_deals_for_company(ctx.company_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("workspace plan: open deals read failed for %s: %s", ctx.company_id, exc)
        gaps.append(wc.gap("pending_changes", f"Open deals could not be read ({type(exc).__name__})"))

    return {
        "monthly_total": total,
        "channel_count": len(channels),
        "pending_changes": pending,
        "channels": channels,
        "caveat": PLAN_CAVEAT,
        "source": SPEND_SOURCE,
        "as_of": as_of,
        "gaps": wc.public(gaps),
    }
