"""Layer 2 — portal context, shaped for an external agent platform.

WHY THIS EXISTS
    An outside agent platform can reach the ad channels and its own warehouse.
    It cannot reach what makes our answers different from a generic agency's:
    the community brief, current availability, the leasing funnel, what spend is
    actually authorized, and the work already in flight. This module hands those
    over as RESOLVED ANSWERS rather than raw connector passthroughs, so the
    agent never performs a join we already know how to do correctly.

    Two of the tools return rules rather than data. An agent that can read our
    counting rules and the housing-advertising constraints carries them into its
    own reasoning, instead of being told again in every prompt.

RULES THIS MODULE KEEPS
    * READ ONLY. Nothing here writes anywhere, so R1 (code never writes `uuid`)
      cannot be violated through this path. Live changes stay on the portal's
      approval flow, where the Fair Housing and spend-authorization gates live.
    * Internal-only values never cross the boundary: internal brief fields, the
      management fee, internal deal labels.
    * A missing source returns null plus a `gaps` entry naming the source and
      the reason. Never a zero, never an invented number.
    * Routes do not call connectors; they call this module (CLAUDE.md, Layer 2).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Sources whose absence is normal and must be reported, not guessed.
_SRC_APTIQ = "aptiq"
_SRC_HYLY = "hyly"
_SRC_CLICKUP = "clickup"

# Never leaves the process, whatever the caller asks for.
_INTERNAL_SPEND_KEYS = {"mgmt_fee", "management_fee", "mgmtfee"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _receipt(value: Any, source: str, as_of: Optional[str] = None) -> Dict[str, Any]:
    """Every number carries where it came from and when it was true."""
    return {"value": value, "source": source, "as_of": as_of or _now_iso()}


def _gap(field: str, source: str, message: str) -> Dict[str, str]:
    return {"field": field, "source": source, "message": message}


def _slug(name: str) -> str:
    return "_".join(name.lower().replace("&", "and").split())


def month_bounds(month: Optional[str]) -> Tuple[str, str, str]:
    """('2026-08', '2026-08-01', '2026-08-31'). Defaults to the last full month.

    Reports and funnels are monthly here, and "this month so far" invites an
    unfair comparison against a complete month.
    """
    if month:
        parts = str(month).split("-")
        year, mon = int(parts[0]), int(parts[1])
    else:
        today = date.today()
        last_full = today.replace(day=1) - timedelta(days=1)
        year, mon = last_full.year, last_full.month
    start = date(year, mon, 1)
    end = date(year + (mon == 12), (mon % 12) + 1, 1) - timedelta(days=1)
    return "%04d-%02d" % (year, mon), start.isoformat(), end.isoformat()


def _resolve(identifier: str):
    """Identifier (name, address, uuid, company id) → identity, or an error dict."""
    from skills import property_resolver as pr

    try:
        return pr.resolve(identifier), None
    except pr.AmbiguousProperty as exc:
        return None, {"error": "ambiguous_property", "detail": str(exc),
                      "hint": "Pass the company_id or uuid instead of the name."}
    except pr.PropertyNotFound:
        return None, {"error": "property_not_found", "identifier": str(identifier)}
    except Exception as exc:  # noqa: BLE001 — connector down, bad credentials
        logger.error("mcp resolve failed for %r: %s", identifier, exc, exc_info=True)
        return None, {"error": "lookup_unavailable", "detail": type(exc).__name__}


# --- tools -----------------------------------------------------------------

def find_property(identifier: str) -> Dict[str, Any]:
    identity, err = _resolve(identifier)
    if err:
        return err
    d = identity.to_dict()
    return {
        "company_id": d.get("company_id"),
        "uuid": d.get("uuid"),
        "name": d.get("name"),
        "market": d.get("market"),
        "units": d.get("unit_count"),
        "property_code": d.get("property_code"),
        "occupancy_status": d.get("occupancy"),
        "is_managed": identity.is_managed,
        "is_lease_up": identity.is_lease_up,
        "available_data": {
            "leasing_funnel": bool(d.get("hyly_property_id")),
            "availability": bool(d.get("aptiq_property_id")),
            "google_ads": bool(d.get("google_ads_customer_id")),
            "ga4": bool(d.get("ga4_property_id")),
        },
        "missing_ids": identity.missing(),
        "as_of": _now_iso(),
    }


def get_property_brief(company_id: str, section: Optional[str] = None) -> Dict[str, Any]:
    import community_brief as cb

    available = [_slug(name) for name, _ in cb.SECTIONS]
    wanted = _slug(section) if section else None
    if wanted and wanted not in available:
        return {"error": "unknown_section", "requested": section,
                "available_sections": available}
    try:
        props = cb.load_company_state(company_id)
    except Exception as exc:  # noqa: BLE001
        logger.error("mcp brief load failed for %s: %s", company_id, exc, exc_info=True)
        return {"error": "brief_unavailable", "detail": type(exc).__name__}

    sections: List[Dict[str, Any]] = []
    for section_name, fields in cb.SECTIONS:
        slug = _slug(section_name)
        if wanted and slug != wanted:
            continue
        out_fields = []
        for f in fields:
            if getattr(f, "internal", False):       # never crosses the boundary
                continue
            value = cb.resolve_value(props, f.hs_resolved, f.hs_override)
            override_raw = props.get(f.hs_override) if f.hs_override else None
            out_fields.append({
                "key": f.key,
                "label": f.label,
                "value": value or None,
                "type": f.type,
                "human_edited": bool(override_raw and str(override_raw).strip()),
            })
        if out_fields:
            sections.append({"section": section_name, "slug": slug, "fields": out_fields})

    filled = sum(1 for s in sections for f in s["fields"] if f["value"])
    total = sum(len(s["fields"]) for s in sections)
    return {
        "company_id": company_id,
        "sections": sections,
        "completeness": {"filled": filled, "of": total,
                         "percent": int(round(100.0 * filled / total)) if total else None},
        "source": "hubspot_community_brief",
        "as_of": _now_iso(),
        "note": ("Override-wins: a human-edited value is authoritative over any "
                 "machine-resolved one. Do not contradict a human-edited field."),
    }


def get_availability(company_id: str) -> Dict[str, Any]:
    identity, err = _resolve(company_id)
    if err:
        return err
    aptiq_id = identity.to_dict().get("aptiq_property_id")
    empty = {"company_id": company_id, "occupancy": None, "available_units": None,
             "floor_plans": []}
    if not aptiq_id:
        empty["gaps"] = [_gap("occupancy", _SRC_APTIQ,
                              "This property has no availability id, so occupancy and "
                              "unit availability are not connected for it.")]
        return empty

    import apartmentiq_client as aptiq

    try:
        snap = aptiq.get_property_snapshot(str(aptiq_id))
    except Exception as exc:  # noqa: BLE001
        logger.error("mcp availability failed for %s: %s", aptiq_id, exc, exc_info=True)
        empty["gaps"] = [_gap("occupancy", _SRC_APTIQ,
                              "The availability source is temporarily unavailable.")]
        return empty
    if not snap:
        empty["gaps"] = [_gap("occupancy", _SRC_APTIQ,
                              "No availability snapshot exists for this property today.")]
        return empty

    src = "aptiq_api" if snap.get("_source") == "api" else "aptiq_daily_csv"
    as_of = snap.get("as_of") or snap.get("snapshot_date") or _now_iso()
    out = {
        "company_id": company_id,
        "units": identity.to_dict().get("unit_count"),
        "occupancy": _receipt(snap.get("occupancy"), src, as_of),
        "leased_percent": _receipt(snap.get("leased_percent"), src, as_of),
        "exposure": _receipt(snap.get("exposure"), src, as_of),
        "available_units": _receipt(snap.get("available_units"), src, as_of),
        "leases_last_30": _receipt(snap.get("leases_last_30"), src, as_of),
        "floor_plans": snap.get("floor_plans") or [],
        "gaps": [],
        "note": ("Vacant units are lost revenue every day they stay vacant. Spend "
                 "should follow availability, not the other way round."),
    }
    if not out["floor_plans"]:
        out["gaps"].append(_gap("floor_plans", src,
                                "Floor-plan level availability is not in this snapshot; "
                                "property-level availability is included."))
    return out


def get_leasing_funnel(company_id: str, month: Optional[str] = None) -> Dict[str, Any]:
    identity, err = _resolve(company_id)
    if err:
        return err
    hyly_id = identity.to_dict().get("hyly_property_id")
    month_key, start, end = month_bounds(month)
    base = {"company_id": company_id, "month": month_key, "channels": {}}

    if not hyly_id:
        base["gaps"] = [_gap("funnel", _SRC_HYLY,
                             "This property is not in the leasing-funnel program, so "
                             "lead-to-lease data does not exist for it. Do not infer "
                             "leases from leads.")]
        return base
    if month_key == "2026-06":
        base["gaps"] = [_gap("funnel", _SRC_HYLY,
                             "June 2026 is a backfill artifact in the source and is "
                             "excluded by rule. Choose another month.")]
        return base

    import hyly_client as hyly

    if not hyly.is_configured():
        base["gaps"] = [_gap("funnel", _SRC_HYLY,
                             "The funnel source is not configured on this server.")]
        return base
    try:
        channels = hyly.get_channel_summary(str(hyly_id), start_date=start, end_date=end)
        fresh = hyly.get_data_freshness() or {}
    except Exception as exc:  # noqa: BLE001
        logger.error("mcp funnel failed for %s: %s", hyly_id, exc, exc_info=True)
        base["gaps"] = [_gap("funnel", _SRC_HYLY,
                             "The funnel source is temporarily unavailable.")]
        return base

    return {
        "company_id": company_id,
        "month": month_key,
        "range": {"start": start, "end": end},
        "channels": channels,
        "source": "hyly_rollup",
        "as_of": fresh.get("max_date") or _now_iso(),
        "note": ("`_total` is the total across channels — do not add it to the channel "
                 "rows. Judge channels on leases, not on platform conversions."),
        "gaps": [],
    }


def get_spend_authorization(company_id: str) -> Dict[str, Any]:
    import spend_sheet

    try:
        data = spend_sheet.get_company_monthly_spend(company_id)
    except Exception as exc:  # noqa: BLE001
        logger.error("mcp spend failed for %s: %s", company_id, exc, exc_info=True)
        return {"error": "spend_unavailable", "detail": type(exc).__name__}

    by_service = {k: v for k, v in (data.get("by_sku") or {}).items()
                  if k.lower() not in _INTERNAL_SPEND_KEYS}
    return {
        "company_id": company_id,
        "authorized_monthly_total": _receipt(data.get("total"),
                                             "hubspot_deal_line_items"),
        "by_service": by_service,
        "deal_id": data.get("deal_id"),
        "as_of": _now_iso(),
        "rule": ("Authorization comes from a signed deal. A change that exceeds these "
                 "amounts needs a new signature and can never be agent-approved. A "
                 "recommendation is not authorization."),
    }


def get_open_work(company_id: str, limit: int = 20) -> Dict[str, Any]:
    identity, err = _resolve(company_id)
    if err:
        return err

    import config
    from clickup_client import get_tasks

    name = (identity.to_dict().get("name") or "").lower()
    list_ids = [v for k, v in vars(config).items()
                if k.startswith("CLICKUP_LIST_") and isinstance(v, str) and v.strip()]
    cap = max(1, min(int(limit or 20), 50))
    if not list_ids:
        return {"company_id": company_id, "items": [],
                "gaps": [_gap("open_work", _SRC_CLICKUP,
                              "No ticket lists are configured on this server.")]}

    items: List[Dict[str, Any]] = []
    for list_id in list_ids[:6]:
        try:
            tasks = get_tasks(list_id, params={"include_closed": "false"}) or []
        except Exception as exc:  # noqa: BLE001
            logger.warning("mcp clickup list %s unavailable: %s", list_id, exc)
            continue
        for t in tasks:
            title = t.get("name") or ""
            if name and name not in title.lower():
                continue
            items.append({
                "title": title,
                "status": (t.get("status") or {}).get("status"),
                "created": t.get("date_created"),
                "due": t.get("due_date"),
                "url": t.get("url"),
            })
            if len(items) >= cap:
                break
        if len(items) >= cap:
            break

    return {"company_id": company_id, "items": items, "source": _SRC_CLICKUP,
            "as_of": _now_iso(), "gaps": [],
            "note": "Check this before proposing work that may already be in flight."}


def get_metric_rules() -> Dict[str, Any]:
    """The counting rules. Each exists because ignoring it produced a wrong number."""
    return {
        "rules": [
            {"rule": "In the reporting warehouse the 'Website / Traffic' bucket is the "
                     "TOTAL, not a peer channel. Never sum it with the other channels.",
             "verified_over": "14,558 account-months, zero exceptions"},
            {"rule": "Site sessions and leads come from analytics as the total. Paid "
                     "conversions ride alongside and are never added to it; paid "
                     "conversions exceed the analytics total in about 21% of "
                     "account-months."},
            {"rule": "June 2026 leasing-funnel data is a backfill artifact. Exclude it "
                     "from every trend and comparison."},
            {"rule": "Judge channels on leases, not platform conversions. Platform "
                     "conversion counts have overstated verified leads by up to 9x on "
                     "some properties."},
            {"rule": "The leasing funnel covers a subset of properties. Where it is "
                     "absent, say so — never infer leases from leads."},
            {"rule": "Every number needs a numerator, a denominator and a source. If "
                     "any is missing, return null and state the gap."},
        ],
        "as_of": _now_iso(),
    }


def get_compliance_rules() -> Dict[str, Any]:
    """Housing advertising is legally restricted. Read before proposing a change."""
    return {
        "category": "Housing — a Special Ad Category on the major ad platforms",
        "never_propose": [
            "Tightening or shrinking a geographic radius",
            "ZIP or postal-code level targeting",
            "Audience layering, lookalikes, interest or demographic targeting",
            "Audience exclusions of any kind",
            "Retargeting or remarketing audiences",
        ],
        "allowed_optimizations": [
            "Budget level, within authorized amounts",
            "Ad scheduling by hour or day",
            "Keywords, negative keywords and match types",
            "Ad copy and creative, subject to compliance review",
            "Landing page and floor-plan alignment",
            "Bidding strategy",
        ],
        "copy_rules": [
            "Never describe or imply a preferred type of resident.",
            "No language referencing a protected class, including familial status.",
            "Describe the property and the unit, not the person who should live there.",
        ],
        "review": ("Copy that will reach a client or a channel must pass the portal's "
                   "Fair Housing review before publication. An agent may draft it; the "
                   "portal approves it."),
        "as_of": _now_iso(),
    }


# --- tool registry ---------------------------------------------------------
# The description each tool carries IS the instruction the agent inherits, so
# these are written for a reader who knows nothing about this portfolio.

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "find_property",
        "title": "Find a property",
        "description": (
            "Resolve a property by name, address, uuid or company id. Call this first: "
            "every other tool takes the company_id it returns. The response says which "
            "data sources exist for that property, so you can tell what is available "
            "before asking for it."),
        "handler": find_property,
        "inputSchema": {
            "type": "object",
            "properties": {"identifier": {
                "type": "string",
                "description": "Property name, address, uuid or HubSpot company id.",
            }},
            "required": ["identifier"],
        },
    },
    {
        "name": "get_property_brief",
        "title": "Get the community brief",
        "description": (
            "The property's context layer, curated by the marketing team: positioning, "
            "voice, story, amenities, inventory, geography, competitors, goals and "
            "guardrails. Read it before writing any ad copy, content or answer for the "
            "property. Human-edited values are authoritative and must not be "
            "contradicted. Internal-only fields are never returned."),
        "handler": get_property_brief,
        "inputSchema": {
            "type": "object",
            "properties": {
                "company_id": {"type": "string",
                               "description": "From find_property."},
                "section": {"type": "string",
                            "description": ("Optional section slug: identity, "
                                            "voice_and_positioning, brand_and_story, "
                                            "lifecycle, inventory, amenities, geography, "
                                            "competitors, strategy_and_goals, "
                                            "operations_and_tech, guardrails, "
                                            "tracking_and_attribution, documents.")},
            },
            "required": ["company_id"],
        },
    },
    {
        "name": "get_availability",
        "title": "Get current availability",
        "description": (
            "Occupancy, leased percent, exposure and available units for the property — "
            "the demand-side facts no ad platform knows. Use it to decide which floor "
            "plans need promotion: a vacant unit loses revenue every day it stays "
            "vacant, so spend should follow availability."),
        "handler": get_availability,
        "inputSchema": {
            "type": "object",
            "properties": {"company_id": {"type": "string",
                                          "description": "From find_property."}},
            "required": ["company_id"],
        },
    },
    {
        "name": "get_leasing_funnel",
        "title": "Get the leasing funnel",
        "description": (
            "Lead to tour to application to lease, by channel, for one month. This is "
            "the only place the funnel exists; ad platforms stop at the lead. Judge "
            "channels on leases. Defaults to the last full month."),
        "handler": get_leasing_funnel,
        "inputSchema": {
            "type": "object",
            "properties": {
                "company_id": {"type": "string", "description": "From find_property."},
                "month": {"type": "string",
                          "description": "YYYY-MM. Defaults to the last full month."},
            },
            "required": ["company_id"],
        },
    },
    {
        "name": "get_spend_authorization",
        "title": "Get authorized spend",
        "description": (
            "What this property is authorized to spend per month, by service, from the "
            "signed deal. Never proposeor make a change that exceeds these amounts. "
            "These are contracted amounts, not amounts already spent."),
        "handler": get_spend_authorization,
        "inputSchema": {
            "type": "object",
            "properties": {"company_id": {"type": "string",
                                          "description": "From find_property."}},
            "required": ["company_id"],
        },
    },
    {
        "name": "get_open_work",
        "title": "Get work in flight",
        "description": (
            "Open tickets for this property, so you do not propose work that is already "
            "under way. Internal comments and account notes are never included."),
        "handler": get_open_work,
        "inputSchema": {
            "type": "object",
            "properties": {
                "company_id": {"type": "string", "description": "From find_property."},
                "limit": {"type": "integer",
                          "description": "Max items, 1-50. Default 20."},
            },
            "required": ["company_id"],
        },
    },
    {
        "name": "get_metric_rules",
        "title": "Get the counting rules",
        "description": (
            "The counting rules for this portfolio. Apply them before reporting any "
            "number. They are not preferences: each one exists because ignoring it put "
            "a wrong number in front of a client."),
        "handler": get_metric_rules,
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_compliance_rules",
        "title": "Get advertising compliance rules",
        "description": (
            "Housing advertising rules that bind every recommendation for these "
            "properties. Housing is a Special Ad Category, so targeting changes that "
            "are routine elsewhere are prohibited here. Read this before proposing any "
            "targeting, audience or copy change."),
        "handler": get_compliance_rules,
        "inputSchema": {"type": "object", "properties": {}},
    },
]

BY_NAME = {t["name"]: t for t in TOOLS}


def tool_descriptors() -> List[Dict[str, Any]]:
    """The tools/list payload: everything except the Python handler."""
    return [{k: v for k, v in t.items() if k != "handler"} for t in TOOLS]
