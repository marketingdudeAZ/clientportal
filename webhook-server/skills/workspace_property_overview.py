"""Workspace v3 Property detail — `GET /api/workspace/property-overview?company_id=`.

| field                | source                                                              |
|----------------------|---------------------------------------------------------------------|
| health               | Red Light score on the HubSpot company (bands in workspace_scope)   |
| objective            | plan_stages.determine_mode over AptIQ occupancy, target, lease-up   |
| kpis.ai_visibility   | ai_mentions composite index (HubDB)                                 |
| kpis.units_to_lease  | AptIQ advertised available units                                    |
| kpis.renewal_rate    | null: no renewal-rate field on main                                 |
| kpis.lead_to_lease   | null: no lead-to-lease feed outside the Hyly beta tables            |
| exposure_forecast    | AptIQ exposure next 30 / 60 / 90 days × units, per month            |
| vendor_audit         | spend tracker sheet (Zillow, CoStar package); verdict always unknown|
| visibility_by_engine | ai_mentions per-engine citation rates                               |
| findings / actions   | workspace_inbox items with their receipts                           |
| draft_email          | null: no source drafts client emails                                |
| loop                 | workspace_inbox items by lens                                       |

A vendor verdict other than `unknown` needs a market-rate source; there is none.
`forecasting.py` forecasts leases for a 30-day horizon, not units to lease by
month, so the monthly exposure view is AptIQ's own exposure windows.
"""

from __future__ import annotations

import logging
from datetime import date

from skills import workspace_common as wc
from skills import workspace_inbox as wi
from skills import workspace_scope as wscope

logger = logging.getLogger(__name__)

ENGINE_LABELS = {"chatgpt": "ChatGPT", "perplexity": "Perplexity", "gemini": "Gemini",
                 "google_aio": "Google AI Overviews"}
LENSES = ("express", "tailor", "amplify", "evolve")


def ai_snapshot(ctx, gaps: list) -> dict | None:
    from config import HUBDB_AI_MENTIONS_TABLE_ID
    if not HUBDB_AI_MENTIONS_TABLE_ID or not ctx.uuid:
        gaps.append(wc.gap("ai_visibility", "AI visibility audits are not configured for this property",
                           source="ai_mentions"))
        return None
    try:
        import ai_mentions
        snap = ai_mentions.get_latest_snapshot(ctx.uuid)
    except Exception as exc:  # noqa: BLE001
        gaps.append(wc.gap("ai_visibility", f"AI visibility audit could not be read ({type(exc).__name__})",
                           source="ai_mentions"))
        return None
    if snap.get("composite_index") is None:
        gaps.append(wc.gap("ai_visibility", "No AI visibility audit has run for this property", source="ai_mentions"))
        return None
    return snap


def exposure_months(ctx, aptiq: dict | None, as_of: str | None, today: date) -> dict | None:
    """Units coming exposed per month from AptIQ's 30 / 60 / 90-day windows."""
    if not aptiq:
        return None
    raw = aptiq.get("raw") or {}
    units = wc.to_int(ctx.props.get("totalunits"))
    windows = [wc.ratio(raw.get(f"Exposure % (Next {d}d)")) for d in (30, 60, 90)]
    if not units or any(w is None for w in windows):
        return None
    months, prev = [], 0.0
    y, m = today.year, today.month
    for w in windows:
        months.append({"month": f"{y:04d}-{m:02d}", "units_to_lease": max(0, round((w - prev) * units))})
        prev = max(prev, w)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return {"months": months, "source": "aptiq", "as_of": as_of,
            "basis": "AptIQ exposure within the next 30, 60 and 90 days, times unit count"}


def objective(ctx, aptiq: dict | None) -> tuple[str | None, str | None]:
    try:
        import plan_stages
    except Exception:  # noqa: BLE001
        return None, None
    occ = wc.to_float(aptiq.get("occupancy_pct")) if aptiq else None
    if occ is not None and occ <= 1.5:
        occ *= 100
    exposure = wc.to_float((aptiq.get("raw") or {}).get("Exposure % (Next 90d)")) if aptiq else None
    is_lease_up = str(ctx.props.get("occupancy") or "").strip() == "Lease Up"
    if occ is None and not is_lease_up:
        return None, None
    mode = plan_stages.determine_mode(occ, wc.to_float(ctx.props.get("target_occupancy")), is_lease_up, exposure)
    return mode.get("label"), mode.get("reason")


def vendor_audit(ctx, gaps: list) -> list:
    try:
        import sheets_reader
        row = sheets_reader.get_spend_row(ctx.company_id) or {}
    except Exception as exc:  # noqa: BLE001
        logger.debug("spend tracker read failed: %s", exc)
        row = {}
    if not row:
        gaps.append(wc.gap("vendor_audit", "The spend tracker sheet has no row for this property or is not configured",
                           source="spend_tracker_sheet"))
        return []
    out = []
    if row.get("zillow_per_month") is not None:
        out.append({"vendor": "Zillow", "package": None,
                    "monthly": wc.metric(row["zillow_per_month"], "spend_tracker_sheet", None),
                    "verdict": "unknown", "basis": "No market-rate source to compare against"})
    if row.get("costar_package"):
        out.append({"vendor": "CoStar / Apartments.com", "package": row["costar_package"], "monthly": None,
                    "verdict": "unknown", "basis": "No market-rate source to compare against"})
    gaps.append(wc.gap("vendor_audit.verdict",
                       "Vendor verdicts need a market-rate source for ILS packages; there is none, so every "
                       "verdict is unknown"))
    return out


def loop_story(items: list) -> list:
    out = []
    for lens in LENSES:
        mine = [i for i in items if i["lens"] == lens]
        waiting = [i for i in mine if i["status"] == "to_do" and i["needs_approval"]]
        finished = sorted((i for i in mine if i["status"] in ("done", "in_motion")),
                          key=lambda i: str(i.get("_closed") or i.get("_created") or ""), reverse=True)
        if waiting:
            first = sorted(waiting, key=wi._sort_key)[0]
            out.append({"lens": lens, "status": "waiting", "at": first.get("due"), "text": first["title"],
                        "item_id": first["id"]})
        elif finished:
            last = finished[0]
            out.append({"lens": lens, "status": "done",
                        "at": wc.to_iso_ts(last.get("_closed") or last.get("_created")),
                        "text": last["title"], "item_id": last["id"]})
        else:
            out.append({"lens": lens, "status": "upcoming", "at": None, "text": "Nothing open yet",
                        "item_id": None})
    return out


def build_property_overview(ctx, *, internal: bool, today: date | None = None) -> dict:
    from skills import workspace_views

    today = today or date.today()
    gaps: list = []
    aptiq, aptiq_as_of = workspace_views.aptiq_snapshot(ctx, gaps)
    units = wc.to_int(ctx.props.get("totalunits"))

    score = wscope.health_score(ctx.props)
    health = None
    if score is not None:
        health = {"score": score, "band": wscope.health_band(score), "source": "redlight",
                  "as_of": wc.to_iso_ts(ctx.props.get("red_light_run_date"))}
    else:
        gaps.append(wc.gap("health", "No Red Light score on the company record yet", source="redlight"))

    snap = ai_snapshot(ctx, gaps)
    scanned = wc.to_iso_ts(snap.get("scanned_at")) if snap else None
    kpis = {
        "ai_visibility": wc.metric(snap["composite_index"], "ai_mentions", scanned) if snap else None,
        "renewal_rate": None,
        "units_to_lease": wc.metric(aptiq.get("available_units"), "aptiq", aptiq_as_of) if aptiq else None,
        "lead_to_lease": None,
    }
    gaps.append(wc.gap("kpis.renewal_rate", "No renewal-rate field exists on main"))
    gaps.append(wc.gap("kpis.lead_to_lease", "Lead-to-lease needs the leasing funnel feed, which covers only the "
                                             "Hyly beta properties", source="hyly"))

    forecast = exposure_months(ctx, aptiq, aptiq_as_of, today)
    if forecast is None:
        gaps.append(wc.gap("exposure_forecast", "No AptIQ exposure windows for this property", source="aptiq"))

    label, reason = objective(ctx, aptiq)
    if label is None:
        gaps.append(wc.gap("objective", "Needs AptIQ occupancy or a lease-up status"))

    items, item_gaps = wi.collect(ctx, today=today)
    gaps += item_gaps
    open_items = sorted((i for i in items if i["status"] == "to_do" and i["actions"]["approve"]), key=wi._sort_key)
    views = [wi.view_item(i, internal) for i in open_items]
    findings = [{"text": v["found"] or v["title"], "receipts": v["receipts"], "item_id": v["id"]}
                for v in views[:3]]

    engines = []
    if snap:
        for key, data in (snap.get("by_engine") or {}).items():
            rate = wc.to_float((data or {}).get("cited_rate"))
            engines.append({"engine": ENGINE_LABELS.get(key, key),
                            "score": wc.metric(round(rate * 100) if rate is not None else None, "ai_mentions", scanned)})
    gaps.append(wc.gap("draft_email", "No source drafts client emails"))

    cid = ctx.company_id
    return {
        "name": ctx.name or None,
        "city": ctx.props.get("city") or None,
        "state": ctx.props.get("state") or None,
        "units": units,
        "units_source": "hubspot_company",
        "objective": label,
        "objective_reason": reason,
        "health": health,
        "kpis": kpis,
        "exposure_forecast": forecast,
        "vendor_audit": vendor_audit(ctx, gaps),
        "visibility_by_engine": engines,
        "findings": findings,
        "recommended_action": {"item_id": views[0]["id"], "label": views[0]["title"]} if views else None,
        "draft_email": None,
        "loop": loop_story([dict(i, title=wi.view_item(i, internal)["title"]) for i in items]),
        "links": {
            "media_plan": f"#/property/{cid}/media-plan",
            "visibility": f"#/property/{cid}/visibility",
            "content": f"#/property/{cid}/content",
            "creative": f"#/property/{cid}/creative",
            "report": f"#/property/{cid}/report",
        },
        "gaps": wc.gaps_for(gaps, internal),
    }
