"""Need-aware, budget-bounded plan builder for the portal Plan & Spend page.

Reframes the funnel from "hit N impressions" to "are you represented at each
stage, with the right services activated?" — and sizes Good/Better/Best to what
the property actually needs (its MODE) inside a budget ENVELOPE, so there are no
$75k runaway recommendations.

Mode (from occupancy vs target + lease-up + exposure):
  grow      — occupancy below target, or lease-up: add coverage, NO trim option
  optimize  — stabilized + well-leased: trim/efficiency is on the table
  rebalance — balanced: hold + modest moves

Fixed-cost services (SEO, Social Content, Reputation) use real HubSpot SKU tiers
from config. Paid media is variable: active channels flex around CURRENT spend by
mode; new channels enter at their real monthly MINIMUM. The plan total is capped
at a leasing-season flex ceiling (current x FLEX_CEILING_MULT).
"""
from __future__ import annotations

import logging
import requests

from config import (
    HUBSPOT_API_KEY,
    SEO_TIERS, SEO_TIER_ORDER,
    SOCIAL_POSTING_TIERS, REPUTATION_TIERS,
)
import funnel_forecast as _ff

logger = logging.getLogger(__name__)
_HB = "https://api.hubapi.com"

# ── stage -> channels that feed it (order = funnel order) ──
STAGES = [
    {"key": "awareness", "name": "Awareness",
     "goal": "Get discovered by renters who don't know you yet",
     "channels": ["pmax", "meta", "tiktok", "display", "ctv", "social_content"]},
    {"key": "consideration", "name": "Consideration",
     "goal": "Capture people actively searching for a home",
     "channels": ["paid_search", "seo", "pmax", "retargeting"]},
    {"key": "conversion", "name": "Conversion",
     "goal": "Turn interest into tours & signed leases",
     "channels": ["seo", "retargeting", "reputation"]},
]

CHANNEL_NAME = {
    "paid_search": "Paid Search", "pmax": "Performance Max", "seo": "SEO",
    "meta": "Meta (FB / IG)", "tiktok": "TikTok", "display": "Display",
    "ctv": "CTV", "retargeting": "Retargeting",
    "social_content": "Social Content", "reputation": "Reputation",
}

# fixed-cost services -> real SKU tiers (label, price); recommended index
FIXED = {
    "seo": {"tiers": [[k, SEO_TIERS[k]] for k in SEO_TIER_ORDER], "rec": 2},
    "social_content": {"tiers": [[k, v] for k, v in SOCIAL_POSTING_TIERS.items()], "rec": 0},
    "reputation": {"tiers": [[k, v] for k, v in REPUTATION_TIERS.items()], "rec": 1,
                   "note": "3rd (premium) package pending pricing"},
}
FIXED_KEYS = set(FIXED)

# paid media real monthly minimums
CHANNEL_MIN = {
    "meta": 500, "tiktok": 300, "display": 500, "ctv": 1200, "retargeting": 300,
    "paid_search": 1000, "pmax": 1000,
}
NEW_REC_MULT = 2.0    # a new channel's "Recommended" = 2x its minimum
NEW_PUSH_MULT = 3.0   # "Push" = 3x its minimum
FLEX_CEILING_MULT = 1.35   # leasing-season flex ceiling = current + 35%


def get_current_channel_spend(company_id: str) -> dict:
    """{channel: monthly $} from the property's primary deal line items.

    Same source as Enrolled Services + the funnel 'Current', so everything agrees.
    Reputation isn't a media channel to lineitem_to_channel(), so we detect it here.
    """
    hh = {"Authorization": f"Bearer {HUBSPOT_API_KEY}", "Content-Type": "application/json"}
    out = {}
    try:
        da = requests.get(f"{_HB}/crm/v3/objects/companies/{company_id}/associations/deals",
                          headers=hh, timeout=10)
        dids = [a["id"] for a in da.json().get("results", [])] if da.ok else []
        if not dids:
            return out
        db = requests.post(f"{_HB}/crm/v3/objects/deals/batch/read", headers=hh,
                           json={"inputs": [{"id": d} for d in dids[:100]],
                                 "properties": ["dealstage", "closedate"]}, timeout=10)
        deals = db.json().get("results", []) if db.ok else []
        deals.sort(key=lambda d: (1 if "won" in (d.get("properties", {}).get("dealstage") or "").lower() else 0,
                                  d.get("properties", {}).get("closedate") or ""), reverse=True)
        if not deals:
            return out
        did = deals[0]["id"]
        li = requests.get(f"{_HB}/crm/v3/objects/deals/{did}/associations/line_items",
                          headers=hh, timeout=10)
        lids = [a["id"] for a in li.json().get("results", [])] if li.ok else []
        if not lids:
            return out
        lb = requests.post(f"{_HB}/crm/v3/objects/line_items/batch/read", headers=hh,
                           json={"inputs": [{"id": x} for x in lids],
                                 "properties": ["hs_sku", "name", "amount", "price"]}, timeout=10)
        for l in (lb.json().get("results", []) if lb.ok else []):
            lp = l.get("properties", {})
            sku = lp.get("hs_sku") or ""
            name = lp.get("name") or ""
            try:
                amt = float(str(lp.get("amount") or lp.get("price") or 0).replace(",", ""))
            except (ValueError, TypeError):
                amt = 0.0
            if amt <= 0:
                continue
            ch = _ff.lineitem_to_channel(sku, name)
            if not ch and "reputation" in (sku + " " + name).lower():
                ch = "reputation"
            if ch:
                out[ch] = out.get(ch, 0) + amt
    except Exception as e:
        logger.warning("plan_stages: current channel fetch failed for %s: %s", company_id, e)
    return out


def determine_mode(occ, target_occ, is_lease_up, exposure):
    """Pick the recommendation posture from the property's leasing situation."""
    target_occ = target_occ or 95.0
    if is_lease_up:
        return {"key": "grow", "label": "Grow mode",
                "reason": "This is a lease-up still filling units — the plan favors adding coverage."}
    if occ is not None and occ < target_occ - 2:
        gap = round(target_occ - occ)
        return {"key": "grow", "label": "Grow mode",
                "reason": f"Occupancy {occ:.0f}% is ~{gap} pts below target ({target_occ:.0f}%) — favor adding coverage, not trimming."}
    if occ is not None and occ >= target_occ and (exposure is None or exposure <= 5):
        return {"key": "optimize", "label": "Optimize mode",
                "reason": f"Occupancy {occ:.0f}% is at/above target with little to lease — efficiency and trims are on the table."}
    return {"key": "rebalance", "label": "Rebalance mode",
            "reason": "Occupancy is roughly on target — hold the core and make modest, targeted moves."}


def _fixed_current_tier(key, amt):
    """Nearest tier index for a fixed service's current spend (None if no match)."""
    tiers = FIXED[key]["tiers"]
    if not amt:
        return None
    best_i, best_d = 0, float("inf")
    for i, (_, price) in enumerate(tiers):
        d = abs(price - amt)
        if d < best_d:
            best_i, best_d = i, d
    return best_i


def _money(n):
    return "$" + format(int(round(n)), ",")


def _media_opts(key, mode_key, current):
    """Need-aware option set for a paid-media channel. Returns (opts, rec, selected)."""
    if current and current > 0:  # active channel — flex around current
        if mode_key == "grow":
            opts = [["Maintain", round(current), "current"],
                    ["Grow +10%", round(current * 1.10), "+" + _money(current * 0.10)],
                    ["Push +20%", round(current * 1.20), "+" + _money(current * 0.20)]]
            rec, sel = 0, 0
        elif mode_key == "optimize":
            opts = [["Trim −15%", round(current * 0.85), "−" + _money(current * 0.15)],
                    ["Maintain", round(current), "current"],
                    ["Grow +10%", round(current * 1.10), "+" + _money(current * 0.10)]]
            rec, sel = 0, 1
        else:  # rebalance
            opts = [["Trim −10%", round(current * 0.90), "−" + _money(current * 0.10)],
                    ["Maintain", round(current), "current"],
                    ["Grow +10%", round(current * 1.10), "+" + _money(current * 0.10)]]
            rec, sel = 1, 1
        return opts, rec, sel
    # new channel — enter at the real minimum, bounded steps
    lo = CHANNEL_MIN.get(key, 500)
    opts = [["Minimum", lo, "entry"],
            ["Recommended", round(lo * NEW_REC_MULT), "balanced"],
            ["Push", round(lo * NEW_PUSH_MULT), "aggressive"]]
    rec = 1 if mode_key == "grow" else 0
    return opts, rec, None  # not selected until toggled on


def build_plan(company_id, hs_props, current_channels, occ=None, exposure=None):
    """Assemble the full stage-diagnosis + guarded G/B/B payload for one property."""
    units = float(hs_props.get("totalunits") or 0)
    try:
        target_occ = float(hs_props.get("target_occupancy") or 0) or 95.0
    except (ValueError, TypeError):
        target_occ = 95.0
    occ_status = (hs_props.get("occupancy_status") or "").lower()
    is_lease_up = "lease" in occ_status and "up" in occ_status
    if occ is None:
        try:
            occ = float(hs_props.get("occupancy__") or 0) or None
        except (ValueError, TypeError):
            occ = None

    mode = determine_mode(occ, target_occ, is_lease_up, exposure)

    # ── services ──
    services = {}
    for key in CHANNEL_NAME:
        cur = current_channels.get(key, 0)
        active = cur > 0
        if key in FIXED_KEYS:
            f = FIXED[key]
            sel = _fixed_current_tier(key, cur) if active else None
            services[key] = {
                "name": CHANNEL_NAME[key], "type": "fixed",
                "tiers": f["tiers"], "rec": f["rec"], "note": f.get("note"),
                "active": active, "selected": sel, "current": round(cur),
            }
        else:
            opts, rec, sel = _media_opts(key, mode["key"], cur)
            services[key] = {
                "name": CHANNEL_NAME[key], "type": "media",
                "opts": opts, "rec": rec, "active": active,
                "selected": sel, "current": round(cur),
                "min": CHANNEL_MIN.get(key),
            }

    # ── stages: coverage + templated story ──
    stages = []
    for st in STAGES:
        active_ch = [c for c in st["channels"] if services[c]["active"]]
        gap_ch = [c for c in st["channels"] if not services[c]["active"]]
        pct = round(len(active_ch) / len(st["channels"]) * 100) if st["channels"] else 0
        stages.append({
            "key": st["key"], "name": st["name"], "goal": st["goal"],
            "channels": st["channels"], "active": active_ch, "gap": gap_ch,
            "pct": pct,
            "story": _stage_story(st["key"], active_ch, gap_ch, pct, mode["key"], is_lease_up),
        })

    current_total = round(sum(current_channels.values()))
    ceiling = round(current_total * FLEX_CEILING_MULT) if current_total else None

    weakest = min(stages, key=lambda s: s["pct"]) if stages else None
    diagnosis = _diagnosis(stages, mode["key"])

    return {
        "available": True,
        "property": {"name": hs_props.get("name"), "units": units},
        "mode": mode,
        "occupancy": occ, "target_occupancy": target_occ, "is_lease_up": is_lease_up,
        "envelope": {"current": current_total, "ceiling": ceiling,
                     "flex_pct": round((FLEX_CEILING_MULT - 1) * 100)},
        "current_total": current_total,
        "stages": stages,
        "services": services,
        "weakest_stage": weakest["key"] if weakest else None,
        "diagnosis": diagnosis,
    }


def _stage_story(key, active, gap, pct, mode_key, is_lease_up):
    names = lambda ks: ", ".join(CHANNEL_NAME[k] for k in ks)
    season = " In leasing season" if mode_key == "grow" else ""
    if pct == 0:
        return f"<b>You're not represented here at all.</b> {names(gap)} all feed this stage and none are active — renters at this step never encounter you."
    if pct < 40:
        only = names(active)
        return f"<b>Barely represented — only {only} touches this stage.</b>{season}, this is the gap to close first, with right-sized additions rather than a budget blowout."
    if pct < 75:
        return f"<b>Partly covered.</b> {names(active)} live; adding {names(gap)} would round out this stage."
    if gap:
        return f"<b>Well represented.</b> {names(active)} are all live — {names(gap)} is the only optional add-on left."
    return f"<b>Fully covered.</b> Every channel that feeds this stage is active."


def _diagnosis(stages, mode_key):
    weak = [s for s in stages if s["pct"] < 40]
    strong = [s for s in stages if s["pct"] >= 75]
    if not weak:
        return "You're represented across the funnel — the moves here are fine-tuning."
    wnames = " and ".join(s["name"].lower() for s in weak)
    if strong:
        return (f"You're strong at {strong[0]['name'].lower()} but thin at {wnames}. "
                "Balance the funnel by adding coverage where it's missing — not by spending more where you're already covered.")
    return f"You're thin at {wnames}. Add right-sized coverage there to balance the funnel."
