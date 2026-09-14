"""Workspace v3 Dashboard — `GET /api/workspace/dashboard?lens=`.

| field                     | source                                                        |
|---------------------------|---------------------------------------------------------------|
| greeting_name             | HubSpot owner record matching the caller's email               |
| kpis.ai_visibility        | mean `ai_mentions` composite index across scope (HubDB)        |
| kpis.portfolio_occupancy  | unit-weighted AptIQ advertised occupancy (daily export)        |
| kpis.identified_savings   | null: no recommendation source records a savings amount        |
| kpis.waiting_on_you       | items needing a decision (workspace_inbox, cheap sources)      |
| health_tiles / properties | Red Light score on the HubSpot company (workspace_scope bands) |
| properties.to_lease_90d   | AptIQ 90-day exposure × unit count                            |
| properties.overspend      | null: no market-rate source                                    |
| activity                  | BigQuery loop_events for the scope, last 30 days               |
| waiting                   | workspace_inbox items needing a decision                       |
| loop_status               | latest loop event across the scope                             |

`lens` changes only `kpi_order`; the data is the same for both.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from skills import workspace_common as wc
from skills import workspace_inbox as wi
from skills import workspace_scope as wscope

logger = logging.getLogger(__name__)

LENSES = ("asset_manager", "marketing_manager")
KPI_ORDER = {
    "asset_manager": ["portfolio_occupancy", "identified_savings", "waiting_on_you", "ai_visibility"],
    "marketing_manager": ["ai_visibility", "waiting_on_you", "portfolio_occupancy", "identified_savings"],
}
ITEM_SOURCES = ("hubdb_rec", "call_prep", "video_variant", "content_brief", "onboarding_gap")
MAX_TILES = 50
MAX_VISIBILITY_READS = 25

# event_type → (kind, sentence, visibility). Sentences are fixed; no model writes them.
ACTIVITY = {
    "forecast_run": ("forecast", "Ran the leasing forecast", "client"),
    "recommendation_proposed": ("draft", "Drafted a budget recommendation", "internal"),
    "recommendation_approved": ("decision", "Recommendation approved", "client"),
    "recommendation_rejected": ("decision", "Recommendation set aside", "client"),
    "workspace_decision": ("decision", "Decision recorded", "client"),
    "workspace_request_filed": ("draft", "Request filed with the team", "client"),
    "portal_ticket_filed": ("draft", "Request filed through the portal", "client"),
    "portal_ticket_recap_matched": ("publish", "Completed request recapped", "client"),
    "profile_update_proposed": ("draft", "Proposed a property profile update", "internal"),
    "profile_update_accepted": ("decision", "Property profile updated", "client"),
    "community_brief_published": ("publish", "Published the community brief", "client"),
    "property_brief_published": ("publish", "Published the property brief", "client"),
    "aeo_content_generated": ("draft", "Drafted answer-engine content", "client"),
    "ad_variant_published": ("publish", "Published an ad variant", "client"),
    "ai_mention_index_changed": ("audit", "AI visibility audit updated", "client"),
    "keyword_rank_changed": ("check", "Search rankings changed", "client"),
    "page_health_changed": ("flag", "Website health changed", "client"),
    "seo_refresh": ("check", "Refreshed search data", "internal"),
    "aptiq_history_backfill": ("check", "Refreshed AptIQ history", "internal"),
    "hyly_pull": ("check", "Refreshed the leasing funnel", "internal"),
    "cron_completed": ("check", "Scheduled check completed", "internal"),
}

CATEGORY_BY_REC_TYPE = {"budget_change": "cost", "package_upgrade": "vendor", "strategy_change": "content"}
CATEGORY_BY_SOURCE = {"loop_rec": "cost", "call_prep": "content", "content_brief": "content",
                      "video_variant": "creative", "ticket_profile": "content",
                      "onboarding_gap": "compliance", "portal_ticket": "content", "service_ticket": "content"}


def category_for(item: dict) -> str:
    if item["source"] == "hubdb_rec":
        return CATEGORY_BY_REC_TYPE.get((item.get("_raw") or {}).get("rec_type"), "cost")
    if item.get("fair_housing_review") and item["fair_housing_review"].get("severity") == "high":
        return "compliance"
    return CATEGORY_BY_SOURCE.get(item["source"], "content")


def greeting_name(email: str, gaps: list) -> str | None:
    try:
        import hubspot_client
        r = hubspot_client._request("GET", f"{hubspot_client.API_BASE}/crm/v3/owners",
                                    params={"email": email, "limit": 1})
        results = r.json().get("results") or []
        name = (results[0].get("firstName") or "").strip() if results else ""
    except Exception as exc:  # noqa: BLE001
        logger.debug("workspace greeting lookup failed: %s", exc)
        name = ""
    if not name:
        gaps.append(wc.gap("greeting_name", "No HubSpot user record matches this email", source="hubspot"))
    return name or None


def scope_items(props: list, today: date, gaps: list, sources=ITEM_SOURCES) -> list:
    """[(props, items)] for the worst-health properties in scope, cheap sources only."""
    chosen = wscope.worst_first(props)[:wscope.MAX_ITEM_PROPERTIES]
    if len(props) > len(chosen):
        gaps.append(wc.gap("items", f"Work items were read for the {len(chosen)} properties with the "
                                    f"lowest health of {len(props)} in scope"))

    def _one(p):
        try:
            ctx = wi.load_context(str(p.get("hubspot_company_id") or ""))
            items, _ = wi.collect(ctx, sources=sources, today=today, with_history=False)
            return p, items
        except Exception as exc:  # noqa: BLE001
            logger.warning("workspace scope items: %s failed: %s", p.get("hubspot_company_id"), exc)
            return p, []

    with ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(_one, chosen))


def _aptiq(gaps: list) -> tuple[dict, str | None]:
    from skills import workspace_cache
    try:
        return workspace_cache.aptiq_daily()
    except Exception as exc:  # noqa: BLE001
        gaps.append(wc.gap("portfolio_occupancy", f"AptIQ daily export unavailable ({exc})", source="aptiq"))
        return {}, None


def _aptiq_row(p: dict, rows: dict) -> dict | None:
    return rows.get(str(p.get("aptiq_property_id") or "").strip()) if rows else None


def occupancy_kpi(props: list, rows: dict, loaded: str | None) -> dict | None:
    from services.fluency_ingestion import apt_iq_reader
    weighted = units_total = 0.0
    as_of = None
    for p in props:
        row = _aptiq_row(p, rows)
        units = wc.to_float(p.get("totalunits"))
        occ = wc.ratio(apt_iq_reader._resolve_col(row, "occupancy_pct")) if row else None
        if occ is None or not units:
            continue
        weighted += occ * units
        units_total += units
        stamp = wc.to_iso_ts(row.get("Report Generation Date"))
        as_of = max(as_of, stamp) if (as_of and stamp) else (stamp or as_of)
    if not units_total:
        return None
    return wc.metric(round(weighted / units_total, 4), "aptiq", as_of or loaded, units=int(units_total))


def visibility_kpi(props: list, gaps: list) -> dict | None:
    import ai_mentions
    from config import HUBDB_AI_MENTIONS_TABLE_ID
    if not HUBDB_AI_MENTIONS_TABLE_ID:
        gaps.append(wc.gap("kpis.ai_visibility", "HUBDB_AI_MENTIONS_TABLE_ID is not configured", source="ai_mentions"))
        return None
    scores, as_of = [], None
    for p in props[:MAX_VISIBILITY_READS]:
        uuid = str(p.get("uuid") or "").strip()
        if not uuid:
            continue
        try:
            snap = ai_mentions.get_latest_snapshot(uuid)
        except Exception as exc:  # noqa: BLE001
            logger.debug("ai mentions read failed for %s: %s", uuid, exc)
            continue
        if snap.get("composite_index") is not None:
            scores.append(float(snap["composite_index"]))
            stamp = wc.to_iso_ts(snap.get("scanned_at"))
            as_of = max(as_of, stamp) if (as_of and stamp) else (stamp or as_of)
    if len(props) > MAX_VISIBILITY_READS:
        gaps.append(wc.gap("kpis.ai_visibility", f"Averaged over the first {MAX_VISIBILITY_READS} properties"))
    if not scores:
        gaps.append(wc.gap("kpis.ai_visibility", "No AI visibility audit has run for these properties",
                           source="ai_mentions"))
        return None
    return wc.metric(round(sum(scores) / len(scores)), "ai_mentions", as_of, properties=len(scores))


def activity_rows(props: list, internal: bool, gaps: list) -> tuple[list, str | None]:
    from skills import workspace_history
    names = {str(p.get("uuid") or ""): (p.get("name"), str(p.get("hubspot_company_id") or "")) for p in props}
    try:
        events = workspace_history.recent_events([u for u in names if u][:200], limit=40)
    except Exception as exc:  # noqa: BLE001
        gaps.append(wc.gap("activity", f"Loop events could not be read ({type(exc).__name__})", source="loop_events"))
        return [], None
    if events is None:
        gaps.append(wc.gap("activity", "Activity needs BigQuery loop events, which are not configured here",
                           source="loop_events"))
        return [], None
    rows, latest = [], None
    for ev in events:
        at = wc.to_iso_ts(ev.get("occurred_at"))
        latest = max(latest, at) if (latest and at) else (at or latest)
        spec = ACTIVITY.get(ev.get("event_type"))
        if not spec:
            continue
        kind, sentence, visibility = spec
        if ev.get("event_type") == "workspace_decision":
            action = (ev.get("payload") or {}).get("action")
            sentence = "Approved" if action == "approve" else "Set aside for now"
            title = (ev.get("payload") or {}).get("title")
            if title:
                sentence = f"{sentence}: {title}"
        if visibility == "internal" and not internal:
            continue
        name, cid = names.get(str(ev.get("property_uuid") or ""), (None, ev.get("company_id")))
        rows.append({"at": at, "text": f"{sentence} — {name}" if name else sentence,
                     "company_id": cid or None, "kind": kind, "visibility": visibility})
    return rows[:20], latest


def build_dashboard(email: str, *, internal: bool, lens: str | None = None,
                    today: date | None = None, scope_internal: bool | None = None) -> dict:
    today = today or date.today()
    lens = lens if lens in LENSES else "marketing_manager"
    # Scope follows the real role, filtering the effective one: an internal
    # "Preview as client" sees their own properties rendered as a client would.
    scope = wscope.properties_in_scope(email, internal if scope_internal is None else scope_internal)
    props = scope["properties"]
    gaps: list = list(scope["gaps"])

    rows, loaded = _aptiq(gaps) if props else ({}, None)
    per_property = scope_items(props, today, gaps) if props else []
    waiting_items = [(p, i) for p, items in per_property for i in items
                     if i["status"] == "to_do" and i["needs_approval"]]
    waiting_items.sort(key=lambda pi: (wscope.health_score(pi[0]) or 0, wi._sort_key(pi[1])))

    tiles, prop_rows = [], []
    for p in wscope.worst_first(props)[:MAX_TILES]:
        score = wscope.health_score(p)
        cid = str(p.get("hubspot_company_id") or "")
        units = wc.to_int(p.get("totalunits"))
        row = _aptiq_row(p, rows)
        e90 = wc.ratio(row.get("Exposure % (Next 90d)")) if row else None
        stamp = (wc.to_iso_ts(row.get("Report Generation Date")) if row else None) or loaded
        tiles.append({"company_id": cid, "name": p.get("name") or None, "score": score,
                      "band": wscope.health_band(score), "source": "redlight"})
        prop_rows.append({
            "company_id": cid, "name": p.get("name") or None,
            "units": units, "units_source": "hubspot_company",
            "to_lease_90d": wc.metric(round(e90 * units) if (e90 is not None and units) else None,
                                      "aptiq", stamp, exposure_90d=e90),
            "overspend_per_year": None,
            "health": score, "health_source": "redlight", "band": wscope.health_band(score),
        })
    if len(props) > MAX_TILES:
        gaps.append(wc.gap("health_tiles", f"Showing the {MAX_TILES} lowest-health properties of {len(props)}"))
    if prop_rows:
        gaps.append(wc.gap("properties.overspend_per_year",
                           "Overspend needs a market-rate source for vendor packages; there is none"))
    if any(t["score"] is None for t in tiles):
        gaps.append(wc.gap("health_tiles.score", "Some properties have no Red Light score yet", source="redlight"))

    activity, latest = activity_rows(props, internal, gaps) if props else ([], None)
    running = None
    if latest:
        last = wc.to_datetime(latest)
        running = bool(last and wc.utc_now() - last <= timedelta(hours=48))

    kpis = {
        "ai_visibility": visibility_kpi(props, gaps) if props else None,
        "portfolio_occupancy": occupancy_kpi(props, rows, loaded),
        "identified_savings": None,
        "waiting_on_you": wc.metric(len(waiting_items), "workspace_inbox", wc.now_iso()),
    }
    gaps.append(wc.gap("kpis.identified_savings",
                       "No recommendation source records a savings amount yet"))
    if props and kpis["portfolio_occupancy"] is None:
        gaps.append(wc.gap("kpis.portfolio_occupancy", "No AptIQ occupancy for these properties", source="aptiq"))

    return {
        "greeting_name": greeting_name(email, gaps),
        "as_of": wc.now_iso(),
        "lens": lens,
        "kpi_order": KPI_ORDER[lens],
        "scope_label": scope["label"],
        "kpis": kpis,
        "health_tiles": tiles,
        "properties": prop_rows,
        "activity": activity,
        "waiting": [{
            "item_id": i["id"], "company_id": str(p.get("hubspot_company_id") or ""),
            "title": wi.view_item(i, internal)["title"],
            "subtitle": f"{p.get('name') or 'Property'} · {wi.SOURCE_LABELS[i['source']]}",
            "category": category_for(i),
        } for p, i in waiting_items[:8]],
        "loop_status": {"running": running, "property_count": len(props), "last_pass": latest},
        "gaps": wc.gaps_for(gaps, internal),
    }
