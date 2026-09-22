"""Workspace read views — /me, /property, /performance, /plan.

Every number is `{value, source, as_of}` or null with a gap. Sources:

* HubSpot company record, one read via `workspace_inbox.load_context`
  (hubspot_client): name, address, units, people, platform ids, brief fields.
* AptIQ daily CSV and floor-plan CSV (services/fluency_ingestion), served
  through `workspace_cache` so a cold export never blocks a read: advertised
  occupancy, available units, floor plans with days on market. `as_of` is the
  export's own "Report Generation Date", falling back to the fetch time.
* Spend sheet (deal line items), also through `workspace_cache`. `as_of` is when
  the sheet was built.

What no feed on main provides, returned as null with a gap: units vacant 90+
days, upcoming vacancies as unit counts, coming-open-by-week, cost per lease,
what each channel is pointed at, and a planned go-live date.
"""

from __future__ import annotations

import logging

from skills import workspace_common as wc
from skills import workspace_inbox as wi

logger = logging.getLogger(__name__)

SPEND_SOURCE = "hubspot_line_items"
APTIQ_SOURCE = "aptiq"
# Both AptIQ exports stamp every row with the date AptIQ produced it.
REPORT_DATE_COL = "Report Generation Date"

CHANNEL_LABELS = {
    "paid_search": "Paid search",
    "paid_social": "Paid social",
    "seo": "SEO",
    "reputation": "Reputation",
    "creative": "Creative and email",
    "fees": "Management and hosting",
}

PLAN_STATUSES = ("running", "pending", "ended", "paused")

PLAN_CAVEAT = (
    "Amounts are the contracted monthly line items on this property's deals, not "
    "measured spend. Cost per lease is not available as data yet: it exists only "
    "inside the Red Light report PDF. Pending changes counts open, unsigned deals "
    "on the property."
)

# The brief paragraph (Phase 2 amendment 6): the curated romance paragraph, then
# these company fields verbatim, joined, then null.
BRIEF_COMPOSE_FIELDS = ("what_makes_this_property_unique_", "property_voice_and_tone",
                        "additional_selling_points")


# ── /me ──────────────────────────────────────────────────────────────────────

def _company_summary(props: dict, company_id: str) -> dict:
    return {
        "company_id": str(company_id),
        "uuid": props.get("uuid") or None,
        "name": props.get("name") or None,
        "city": props.get("city") or None,
        "state": props.get("state") or None,
        "market": str(props.get("rpmmarket") or "").strip() or None,
        "units": wc.to_int(props.get("totalunits")),
        "source": "hubspot_company",
    }


def build_me(email: str, *, verified: bool, can_decide: bool | None = None,
             preview_role: str | None = None, signed_link: bool = False) -> dict:
    """Who is calling, their effective role, and the properties they work on.

    Internal staff may open any property; `companies` lists the ones HubSpot
    assigns to them. Clients get the companies they are scoped to. In "Preview
    as client" mode `role` is "client" and `can_decide` is false.
    """
    from feature_access import ROLE_INTERNAL, companies_for, role_for
    from skills import workspace_portfolio

    real_role = role_for(email)
    role = preview_role or real_role
    gaps: list = []
    companies: list = []
    if real_role == ROLE_INTERNAL:
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
                props = hubspot_client.get_company(cid, ["uuid", "name", "city", "state", "totalunits", "rpmmarket"])
                companies.append(_company_summary(props or {}, cid))
            except Exception as exc:  # noqa: BLE001
                logger.warning("workspace me: company %s unreadable: %s", cid, exc)
                gaps.append(wc.gap("companies", f"Company {cid} could not be read"))
    companies.sort(key=lambda c: (c["name"] or "").lower())
    decide = bool(verified) if can_decide is None else bool(can_decide)
    return {
        "email": email,
        "role": role,
        "verified": bool(verified),
        "can_decide": decide and not preview_role,
        "preview_as": preview_role,
        "signed_link": bool(signed_link),
        "portfolio_wide": role == ROLE_INTERNAL,
        "companies": companies,
        "gaps": wc.gaps_for(gaps, internal=True),
    }


# ── shared readers ───────────────────────────────────────────────────────────

def aptiq_snapshot(ctx: wi.PropertyContext, gaps: list) -> tuple[dict | None, str | None]:
    """The property's AptIQ daily-CSV row, normalized, plus its as_of."""
    from skills import workspace_cache

    pid = str(ctx.props.get("aptiq_property_id") or "").strip()
    if not pid:
        gaps.append(wc.gap("aptiq", "No aptiq_property_id on the company record", source="aptiq"))
        return None, None
    try:
        rows, loaded = workspace_cache.aptiq_daily()
    except workspace_cache.SourceUnavailable as exc:
        gaps.append(wc.gap("aptiq", str(exc), source="aptiq"))
        return None, None
    except Exception as exc:  # noqa: BLE001
        logger.warning("workspace aptiq read failed for %s: %s", ctx.company_id, exc)
        gaps.append(wc.gap("aptiq", f"AptIQ daily export could not be read ({type(exc).__name__})",
                           source="aptiq"))
        return None, None
    row = rows.get(pid)
    if not row:
        gaps.append(wc.gap("aptiq", f"Property ID {pid} not in the AptIQ daily export", source="aptiq"))
        return None, None
    from services.fluency_ingestion import apt_iq_reader
    read = {
        "matched": True,
        "occupancy_pct": wc.to_float(apt_iq_reader._resolve_col(row, "occupancy_pct")),
        "available_units": wc.to_int(apt_iq_reader._resolve_col(row, "available_units")),
        "exposure_90d_pct": wc.to_float(apt_iq_reader._resolve_col(row, "exposure_90d_pct")),
        "raw": row,
    }
    as_of = wc.to_iso_ts(row.get(REPORT_DATE_COL)) or loaded
    return read, as_of


def spend(company_id: str, gaps: list, field: str = "monthly_plan") -> tuple[dict | None, str | None]:
    from skills import workspace_cache
    try:
        row, as_of = workspace_cache.monthly_spend(company_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("workspace spend read failed for %s: %s", company_id, exc)
        gaps.append(wc.gap(field, f"Spend sheet could not be read ({type(exc).__name__})",
                           source=SPEND_SOURCE))
        return None, None
    if not row.get("deal_id") and not row.get("by_sku") and not row.get("zero_skus"):
        gaps.append(wc.gap(field, "Property is not in the spend sheet (no deal line items)",
                           source=SPEND_SOURCE))
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


def _channel_of(sku: str) -> str:
    from spend_sheet_to_channels import SKU_TO_CHANNEL
    return SKU_TO_CHANNEL.get(sku, "fees")


# ── /property ────────────────────────────────────────────────────────────────

def _brief(ctx: wi.PropertyContext, gaps: list, internal: bool) -> dict:
    import community_brief

    empty = {"text": None, "curated": False, "edited_by": None, "edited_at": None, "source": None}
    field = next((f for _, fields in community_brief.SECTIONS for f in fields if f.key == "romance"), None)
    text, curated, source = None, False, None
    if field is not None:
        text = community_brief.resolve_value(ctx.props, field.hs_resolved, field.hs_override) or None
        curated = bool(text and field.hs_override
                       and community_brief._nonblank(ctx.props.get(field.hs_override)))
        source = (field.hs_override or field.hs_resolved) if text else None
    if not text:
        parts = [str(ctx.props.get(k)).strip() for k in BRIEF_COMPOSE_FIELDS
                 if community_brief._nonblank(ctx.props.get(k))]
        if parts:
            text, curated, source = " ".join(parts), False, "composed"
    if not text:
        gaps.append(wc.gap("brief.text",
                           "No romance paragraph and none of the unique / voice / selling-point "
                           "fields are filled in", source="hubspot_company"))
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
            gaps.append(wc.gap("brief.edited_by", "No audit row for the last brief edit", internal=True))

    out = {"text": text, "curated": curated, "edited_by": edited_by, "edited_at": edited_at,
           "source": source}
    review = wc.fair_housing_review(text)
    if review:
        logger.info("workspace fair housing review: brief %s severity=%s terms=%s",
                    ctx.company_id, review["severity"], review["terms"])
        gaps.append(wc.gap("brief.text",
                           f"{review['severity'].capitalize()} severity Fair Housing match in the brief "
                           f"({', '.join(review['terms'])})", internal=True))
        if internal:
            out["fair_housing_review"] = review
        elif review["severity"] == "high":
            out["text"] = None
    return out


def _floorplans(ctx: wi.PropertyContext, gaps: list) -> list:
    from skills import workspace_cache

    pid = str(ctx.props.get("aptiq_property_id") or "").strip()
    if not pid:
        return []
    try:
        by_pid, loaded = workspace_cache.aptiq_floor_plans()
    except workspace_cache.SourceUnavailable as exc:
        gaps.append(wc.gap("floorplans", str(exc), source="aptiq_floor_plans"))
        return []
    except Exception as exc:  # noqa: BLE001
        logger.warning("workspace floor plans failed for %s: %s", ctx.company_id, exc)
        gaps.append(wc.gap("floorplans", f"AptIQ floor-plan export could not be read ({type(exc).__name__})",
                           source="aptiq_floor_plans"))
        return []
    raw_rows = by_pid.get(pid) or []
    if not raw_rows:
        gaps.append(wc.gap("floorplans", "No rows for this property in the AptIQ floor-plan export",
                           source="aptiq_floor_plans"))
        return []
    # Same normalization as apt_iq_reader.read_floor_plans, plus the two columns
    # it drops: Days on Market and the report date.
    out, seen, report_date = [], set(), None
    for row in raw_rows:
        report_date = report_date or row.get(REPORT_DATE_COL)
        name = (row.get("Floor Plan Name") or "").strip()
        if not name:
            continue
        plan = {
            "code": name,
            "beds": wc.to_int(row.get("Beds")),
            "sqft": wc.to_int(row.get("Avg Sq Ft")),
            "available": wc.to_int(row.get("Available Units")),
            "days_on_market": wc.to_int(row.get("Days on Market")),
        }
        ident = (name, plan["beds"], wc.to_float(row.get("Baths")), plan["sqft"])
        if ident in seen:
            continue
        seen.add(ident)
        out.append(plan)
    as_of = wc.to_iso_ts(report_date) or loaded
    out.sort(key=lambda r: ((r["beds"] if r["beds"] is not None else 99), r["code"]))
    for plan in out:
        plan.update({"source": "aptiq_floor_plans", "as_of": as_of})
    return out


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


def hubspot_company_url(company_id: str) -> str | None:
    from config import HUBSPOT_PORTAL_ID
    if not HUBSPOT_PORTAL_ID or not company_id:
        return None
    return f"https://app.hubspot.com/contacts/{HUBSPOT_PORTAL_ID}/record/0-2/{company_id}"


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

    hubspot_url = hubspot_company_url(ctx.company_id) if internal else None
    if internal and not hubspot_url:
        gaps.append(wc.gap("hubspot_url", "HUBSPOT_PORTAL_ID is not configured", internal=True))
    gaps.append(wc.gap("brief_edit_url",
                       "The brief editor opens by one-time token; there is no stable edit URL to link",
                       internal=True))

    aptiq, aptiq_as_of = aptiq_snapshot(ctx, gaps)
    return {
        "name": ctx.name or None,
        "address": address,
        "domain": domain.rstrip("/") or None,
        "managed_since": managed[:7] if managed else None,
        "hubspot_url": hubspot_url,
        "brief_edit_url": None,
        "brief": _brief(ctx, gaps, internal),
        "floorplans": _floorplans(ctx, gaps),
        "people": _people(ctx, gaps),
        "connections": _connections(ctx, aptiq, aptiq_as_of, gaps),
        "gaps": wc.gaps_for(gaps, internal),
    }


# ── /performance ─────────────────────────────────────────────────────────────

RANGES = (30, 90, 365)


def build_performance(ctx: wi.PropertyContext, *, range_days: int = 30,
                      internal: bool = True) -> dict:
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
        gaps.append(wc.gap("occupied", "AptIQ row has no occupancy value", source="aptiq"))

    avail = aptiq.get("available_units") if aptiq else None
    available = wc.metric(avail, APTIQ_SOURCE, as_of, stale_90_plus=None)
    if aptiq and avail is None:
        gaps.append(wc.gap("available_now", "AptIQ row has no available-units value", source="aptiq"))
    gaps.append(wc.gap("available_now.stale_90_plus",
                       "AptIQ reports days on market per floor plan (see Property floor plans), "
                       "not per unit, so units vacant 90+ days cannot be counted", source="aptiq"))
    gaps.append(wc.gap("coming_open_90d",
                       "AptIQ reports 90-day exposure as a percentage; no feed on main gives "
                       "upcoming vacancies as unit counts by bedroom", source="aptiq"))
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
        "gaps": wc.gaps_for(gaps, internal),
    }


# ── /plan ────────────────────────────────────────────────────────────────────

def build_plan(ctx: wi.PropertyContext, *, internal: bool = True) -> dict:
    """Plan & Spend. Channel `status`: `running` when its line items total more
    than $0, `ended` when every line item in it is at $0 (cancellations keep
    their SKUs at $0 under the IO process). `pending` and `paused` need a signal
    no source carries per channel; open deals are counted in `pending_changes`."""
    gaps: list = []
    row, as_of = spend(ctx.company_id, gaps, field="monthly_total")
    channels = []
    total = None
    if row:
        total = row.get("total")
        amounts = channel_amounts(row.get("by_sku") or {})
        ended = {_channel_of(sku) for sku in row.get("zero_skus") or []} - set(amounts)
        order = list(CHANNEL_LABELS)
        for key in sorted(set(amounts) | ended, key=lambda k: (order.index(k) if k in order else 99)):
            amt = amounts.get(key, 0.0)
            channels.append({
                "channel": CHANNEL_LABELS.get(key, key),
                "monthly": amt,
                "share": round(amt / total, 4) if total else None,
                "cost_per_lease": None,
                "pointed_at": None,
                "status": "running" if amt > 0 else "ended",
                "status_note": None if amt > 0 else "Line items at $0",
            })
        if channels:
            gaps.append(wc.gap("channels.cost_per_lease", "Cost per lease exists only inside the Red Light report PDF"))
            gaps.append(wc.gap("channels.pointed_at", "No feed on main says which floor plans a channel targets"))
            gaps.append(wc.gap("channels.status",
                               "Pending and paused are not tracked per channel; open deals are "
                               "counted in pending_changes"))

    pending = None
    try:
        import hubspot_client
        pending = len(hubspot_client.get_open_deals_for_company(ctx.company_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("workspace plan: open deals read failed for %s: %s", ctx.company_id, exc)
        gaps.append(wc.gap("pending_changes", f"Open deals could not be read ({type(exc).__name__})"))

    return {
        "monthly_total": total,
        "channel_count": sum(1 for c in channels if c["status"] == "running"),
        "pending_changes": pending,
        "channels": channels,
        "caveat": PLAN_CAVEAT,
        "source": SPEND_SOURCE,
        "as_of": as_of,
        "gaps": wc.gaps_for(gaps, internal),
    }
