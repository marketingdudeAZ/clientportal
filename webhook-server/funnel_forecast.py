"""Full-funnel media-mix forecasting engine (v1 — funnel-sufficiency model).

Reframes forecasting from "predict my leads" to a GOAL-BASED media planner that
works backwards from the leases a property needs to fill, then checks whether
each stage of the funnel is fed well enough to get there.

    GOAL (units to lease)
      -> required conversions = units x leads_per_lease
      -> required sessions    = conversions / session->conv rate
      -> required impressions = sessions   / impression->session rate

Two modes, one assembly (_assemble):
  - run_funnel_forecast(): ACTUAL funnel from observed NinjaCat performance.
  - project_funnel_from_budget(): PROJECTED funnel from a scenario budget, using
    per-channel efficiency + diminishing returns. The "required" thresholds stay
    fixed (the property's observed conversion shape) so the bars fill as budget
    grows. This powers the reactive scenario panel on Plan & Spend.

Spend is authoritative from HubSpot SKUs (management fee is its own SKU, so it's
clean media spend). Conversions are anchored on GA4 site conversions.

Pure functions over plain dicts — no BigQuery / HubSpot deps, fully unit-testable.
"""

from __future__ import annotations

DEFAULT_LEADS_PER_LEASE = 32.0
HEALTHY_SITE_CVR = 0.015

CONTEXT_TOPFUNNEL_MULTIPLIER = {
    "lease_up":   1.5,
    "new_supply": 1.4,
    "btr":        1.3,
    "stabilized": 1.0,
}

# ── Channel efficiency model (tunable) ───────────────────────────────────────
# Projections are ANCHORED to the property's observed funnel (so the current
# budget reproduces the current numbers), then scaled by a per-stage "capacity"
# ratio. Each channel has a relative per-dollar strength at each stage, plus a
# saturation cap. Search converts best per $ but saturates HARD (demand capture
# is bounded by existing search volume); awareness scales far (demand creation).
# This is what makes full-funnel beat all-search once search is maxed.
CHANNEL_STAGE_MULT = {
    # channel:        (impr_mult, sess_mult, conv_mult)
    "paid_search":    (0.30, 0.55, 1.00),
    "pmax":           (0.55, 0.50, 0.85),   # PMax spans search+display+YouTube; strong convert
    "seo":            (0.55, 1.00, 0.60),
    "meta":           (1.00, 0.55, 0.38),
    "tiktok":         (1.10, 0.45, 0.30),
    "display":        (1.20, 0.18, 0.22),
    "ctv":            (1.25, 0.10, 0.15),
    "retargeting":    (0.80, 0.45, 0.85),
    "social_content": (0.60, 0.40, 0.25),
}
# Headroom before diminishing returns, as a multiple of CURRENT channel spend.
# Search/PMax are near their ceiling (1.2-1.6x); awareness has lots of room (4-5x).
CHANNEL_SAT_MULT = {
    "paid_search": 1.2, "pmax": 1.6, "seo": 3.0, "meta": 4.0, "tiktok": 4.5,
    "display": 5.0, "ctv": 5.0, "retargeting": 1.8, "social_content": 3.0,
}
OVER_CAP_EFFICIENCY = 0.12   # dollars past the cap only count this much
NEW_CHANNEL_FLOOR = 1500.0   # a not-currently-run channel gets this much headroom

# HubSpot spend-sheet SKU columns -> funnel channel
SKU_COLS = ["search", "pmax", "paid_social", "geofence", "display", "retargeting",
            "ctv", "seo", "social_posting", "eblast", "email_drip"]
SKU_TO_CHANNEL = {
    "search": "paid_search", "pmax": "pmax",
    "paid_social": "meta",
    "display": "display", "geofence": "display", "ctv": "ctv",
    "retargeting": "retargeting",
    "seo": "seo", "social_posting": "social_content",
}
DEMAND_CAPTURE_CHANNELS = {"paid_search", "pmax", "retargeting"}


def lineitem_to_channel(sku, name=""):
    """Map a HubSpot deal line-item (sku + name) to a funnel channel, or None for
    management/package fees that aren't a media channel. This is the same source
    the Enrolled Services table reads, so the funnel's Current view matches it.

    Note: Performance Max already spans Display + retargeting-style inventory, so
    display/retargeting stay their own channels only for STANDALONE line items.
    """
    s = f"{sku} {name}".lower()
    if "performance max" in s or "pmax" in s or "p-max" in s: return "pmax"
    if "search" in s:                                          return "paid_search"
    if "tiktok" in s:                                          return "tiktok"
    if "meta" in s or "facebook" in s or "instagram" in s or ("paid" in s and "social" in s): return "meta"
    if "retarget" in s:                                        return "retargeting"
    if "ctv" in s or "ott" in s or "youtube" in s:             return "ctv"
    if "display" in s or "geofence" in s or "programmatic" in s or "demand gen" in s or "demand_gen" in s: return "display"
    if "seo" in s:                                             return "seo"
    if "social posting" in s or "social_posting" in s or "social content" in s: return "social_content"
    return None  # management fee, reputation, hosting, etc. — not a media channel


def _safe_div(n, d):
    try:
        return n / d if d else None
    except (TypeError, ZeroDivisionError):
        return None


def skus_to_channels(sku_budget: dict) -> dict:
    """Collapse HubSpot SKU dollars into per-channel budget."""
    out = {}
    for sku, amt in (sku_budget or {}).items():
        ch = SKU_TO_CHANNEL.get(sku)
        if not ch:
            continue
        out[ch] = out.get(ch, 0.0) + float(amt or 0)
    return out


def compute_actual_funnel(channel_rows: list[dict]) -> dict:
    """Collapse per-channel NinjaCat rows into one site funnel + per-channel reach.

    Conversions/sessions from the GA4 site total ('Website / Traffic') to avoid
    double-counting Google Ads vs GA4. Impressions/clicks aggregate reach channels.
    """
    site_sessions = site_conversions = reach_impressions = reach_clicks = 0.0
    per_channel = {}
    for r in channel_rows:
        bucket = (r.get("channel_bucket") or "").strip()
        impr = float(r.get("impressions") or 0)
        clk = float(r.get("clicks") or 0)
        sess = float(r.get("sessions") or 0)
        leads = float(r.get("leads") or 0)
        spend = float(r.get("spend") or 0)
        per_channel[bucket] = {"impressions": impr, "clicks": clk, "sessions": sess,
                               "leads": leads, "spend": spend}
        if bucket == "Website / Traffic":
            site_sessions += sess
            site_conversions += leads
        else:
            reach_impressions += impr
            reach_clicks += clk
    if site_sessions == 0:
        site_sessions = sum(c["sessions"] for c in per_channel.values())
    if site_conversions == 0:
        site_conversions = sum(c["leads"] for c in per_channel.values())
    return {
        "impressions": reach_impressions, "clicks": reach_clicks,
        "sessions": site_sessions, "conversions": site_conversions,
        "ctr": _safe_div(reach_clicks, reach_impressions),
        "session_rate": _safe_div(site_sessions, reach_impressions),
        "conv_rate": _safe_div(site_conversions, site_sessions),
        "per_channel": per_channel,
    }


def _assemble(*, impr, sess, conv, req_conv_rate, req_session_rate,
              goal_leases, leads_per_lease, context, spend_by_channel):
    """Shared: build stages/bottleneck/diagnosis/recommendation from a funnel
    (actual or projected). Required thresholds use the passed observed rates."""
    conv_rate = req_conv_rate or HEALTHY_SITE_CVR
    session_rate = req_session_rate or 0.10
    topfunnel_mult = CONTEXT_TOPFUNNEL_MULTIPLIER.get(context, 1.0)

    req_conversions = goal_leases * leads_per_lease
    req_sessions = (_safe_div(req_conversions, conv_rate) or 0)
    req_impressions = (_safe_div(req_sessions, session_rate) or 0) * topfunnel_mult

    def stage(a, r):
        ratio = _safe_div(a, r)
        return {"actual": round(a), "required": round(r),
                "ratio": round(ratio, 2) if ratio is not None else None,
                "shortfall": round(max(0.0, r - a))}

    stages = {
        "impressions": stage(impr, req_impressions),
        "sessions":    stage(sess, req_sessions),
        "conversions": stage(conv, req_conversions),
    }
    order = ["impressions", "sessions", "conversions"]
    starved = [s for s in order if (stages[s]["ratio"] or 0) < 1.0]
    bottleneck = starved[0] if starved else None
    cvr_healthy = (conv_rate or 0) >= HEALTHY_SITE_CVR
    achievable = _safe_div(conv, leads_per_lease) or 0

    diagnosis = []
    if bottleneck is None:
        diagnosis.append(f"On track — the funnel supports ~{achievable:.1f} leases (goal {goal_leases:g}).")
    else:
        r = stages[bottleneck]["ratio"]
        need_x = round(1 / r, 1) if r else None
        if bottleneck in ("impressions", "sessions") and cvr_healthy:
            diagnosis.append(
                f"The site converts fine ({conv_rate*100:.1f}%). The funnel is starved at "
                f"the TOP — {bottleneck} are {need_x}x short of the goal. This is a "
                f"demand-creation gap, not a conversion problem.")
        elif not cvr_healthy:
            diagnosis.append(
                f"Conversion rate is low ({conv_rate*100:.1f}%) — fix the site/offer to "
                f"lift every dollar already being spent.")
        diagnosis.append(f"At this budget you can support ~{achievable:.1f} leases vs a goal of {goal_leases:g}.")

    total_spend = sum(spend_by_channel.values()) if spend_by_channel else 0
    capture_spend = sum(v for k, v in (spend_by_channel or {}).items() if k in DEMAND_CAPTURE_CHANNELS)
    capture_share = _safe_div(capture_spend, total_spend)

    if bottleneck is None:
        mode = "on_track"
    elif bottleneck in ("impressions", "sessions"):
        mode = "spend_more" if (stages[bottleneck]["ratio"] or 1) < 0.7 else "reallocate"
    else:
        mode = "fix_conversion"

    return {
        "goal_leases": goal_leases, "leads_per_lease": leads_per_lease, "context": context,
        "topfunnel_multiplier": topfunnel_mult,
        "actual": {"impressions": round(impr), "sessions": round(sess), "conversions": round(conv),
                   "conv_rate": conv_rate, "session_rate": session_rate},
        "stages": stages, "bottleneck": bottleneck, "cvr_healthy": cvr_healthy,
        "achievable_leases": round(achievable, 1),
        "diagnosis": diagnosis,
        "recommendation": {
            "mode": mode, "current_spend": round(total_spend, 2),
            "capture_share": round(capture_share, 2) if capture_share is not None else None,
            "note": _recommendation_note(mode, capture_share),
        },
    }


def run_funnel_forecast(*, goal_leases, channel_rows, leads_per_lease=DEFAULT_LEADS_PER_LEASE,
                        context="stabilized", spend_by_channel=None):
    """Actual funnel from observed NinjaCat performance."""
    actual = compute_actual_funnel(channel_rows)
    if spend_by_channel is None:
        # fall back to NinjaCat spend if HubSpot budget wasn't supplied
        spend_by_channel = {}
        for b, c in actual["per_channel"].items():
            ch = "paid_search" if b == "Paid Search" else ("meta" if b == "Paid Social" else b)
            if c["spend"]:
                spend_by_channel[ch] = spend_by_channel.get(ch, 0) + c["spend"]
    return _assemble(
        impr=actual["impressions"], sess=actual["sessions"], conv=actual["conversions"],
        req_conv_rate=actual["conv_rate"], req_session_rate=actual["session_rate"],
        goal_leases=goal_leases, leads_per_lease=leads_per_lease, context=context,
        spend_by_channel=spend_by_channel)


def _eff_spend(spend, current, ch):
    """Spend after diminishing returns past the channel's headroom cap."""
    sat = CHANNEL_SAT_MULT.get(ch, 2.0)
    cap = (current * sat) if current and current > 0 else NEW_CHANNEL_FLOOR
    if spend <= cap:
        return spend
    return cap + (spend - cap) * OVER_CAP_EFFICIENCY


def _stage_capacity(budget, current_budget, stage_idx):
    """Sum eff_spend x channel stage-strength across a budget (one funnel stage)."""
    total = 0.0
    for ch, spend in (budget or {}).items():
        mult = CHANNEL_STAGE_MULT.get(ch)
        if not mult or spend <= 0:
            continue
        total += _eff_spend(spend, (current_budget or {}).get(ch), ch) * mult[stage_idx]
    return total


def project_funnel_from_budget(*, goal_leases, budget_by_channel, observed,
                               leads_per_lease=DEFAULT_LEADS_PER_LEASE, context="stabilized",
                               current_budget_by_channel=None):
    """Project the funnel for a scenario budget, ANCHORED to observed reality.

    Each funnel stage scales by capacity(scenario) / capacity(current), so the
    current budget reproduces the observed funnel exactly (ratio 1.0) and changes
    move each stage by the relative per-dollar strength of the channels touched —
    with search saturating hard so a maxed search budget can't fake the goal.
    `observed` is compute_actual_funnel() output.
    """
    cur = current_budget_by_channel or {}
    # per-stage capacity ratio vs current; guard against zero current capacity
    def ratio(idx):
        c = _stage_capacity(cur, cur, idx)
        s = _stage_capacity(budget_by_channel, cur, idx)
        if c > 0:
            return s / c
        # no current spend at this stage: scale gently off any new spend
        return 1.0 + (s / max(NEW_CHANNEL_FLOOR, 1.0))

    r_impr, r_sess, r_conv = ratio(0), ratio(1), ratio(2)
    proj_impr = (observed["impressions"] or 0) * r_impr
    proj_sess = (observed["sessions"] or 0) * r_sess
    proj_conv = (observed["conversions"] or 0) * r_conv
    return _assemble(
        impr=proj_impr, sess=proj_sess, conv=proj_conv,
        req_conv_rate=(observed["conv_rate"] or HEALTHY_SITE_CVR),
        req_session_rate=(observed["session_rate"] or 0.10),
        goal_leases=goal_leases, leads_per_lease=leads_per_lease, context=context,
        spend_by_channel=budget_by_channel)


def _recommendation_note(mode, capture_share):
    if mode == "on_track":
        return "Funnel is fed for the goal — hold the mix and optimize."
    if mode == "fix_conversion":
        return "Traffic is sufficient; the leak is on-site. Fix landing/offer before adding spend."
    cap = f"{(capture_share or 0)*100:.0f}%"
    if mode == "reallocate":
        return (f"{cap} of spend is bottom-funnel demand capture. Shift some into demand "
                f"creation (SEO, social, awareness) to feed the top.")
    return (f"{cap} of spend is bottom-funnel, and the top is well short of the goal. This "
            f"needs a bigger, full-funnel budget — search alone can't manufacture the demand.")
