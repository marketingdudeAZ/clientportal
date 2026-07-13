"""Full-funnel media-mix forecasting engine (v1 — funnel-sufficiency model).

Reframes forecasting from "predict my leads" to a GOAL-BASED media planner that
works backwards from the leases a property needs to fill, then checks whether
each stage of the funnel is fed well enough to get there.

The model (per property, trailing window):

    GOAL (units to lease)
      -> required conversions = units x leads_per_lease
      -> required sessions    = conversions / session->conv rate
      -> required impressions = sessions   / impression->session rate

Overlay ACTUAL impressions / sessions / conversions (from NinjaCat) and the
earliest stage where actual < required is where the funnel is *starved*. Because
properties jam budget into bottom-funnel search (demand capture), the binding
constraint is almost always the top (demand creation) — which is the case the
tool exists to make.

Measurement convention: conversions are anchored on GA4 site conversions (not
Google Ads' separately-counted conversions), so the funnel is one coherent
measurement system. Google Ads conversions are a channel diagnostic only.

No BigQuery / HubSpot deps here — pure functions over plain dicts, so the whole
model is unit-testable. The endpoint assembles the inputs and calls run_funnel_forecast().
"""

from __future__ import annotations

DEFAULT_LEADS_PER_LEASE = 32.0

# A site conversion rate below this reads as a mid/bottom-funnel problem
# (site/offer/landing), not a traffic problem. Multifamily GA4 site CVR
# typically lands ~1.5-3%.
HEALTHY_SITE_CVR = 0.015

# Context multipliers on required TOP-funnel (impressions). A cold audience
# needs disproportionately more reach to manufacture the same demand.
CONTEXT_TOPFUNNEL_MULTIPLIER = {
    "lease_up":   1.5,   # new, nobody knows the property yet
    "new_supply": 1.4,   # new-to-market / heavy new competition
    "btr":        1.3,   # build-to-rent — richer story to tell
    "stabilized": 1.0,
}

# Channels that create demand (top/mid) vs capture it (bottom). Used to steer
# the fill recommendation toward the funnel stage that's actually empty.
DEMAND_CREATION_CHANNELS = {
    "seo", "paid_social", "display", "ctv", "geofence", "social_posting",
}
DEMAND_CAPTURE_CHANNELS = {"search", "pmax", "retargeting"}


def _safe_div(n, d):
    try:
        return n / d if d else None
    except (TypeError, ZeroDivisionError):
        return None


def compute_actual_funnel(channel_rows: list[dict]) -> dict:
    """Collapse per-channel NinjaCat rows into one site funnel + per-channel reach.

    channel_rows: [{channel_bucket, impressions, clicks, sessions, leads, spend}, ...]
    for a single trailing period.

    Conversions/sessions are taken from the GA4 site total (the 'Website / Traffic'
    bucket) so we don't double-count Google Ads vs GA4 conversions. Impressions and
    clicks aggregate the reach channels (paid search + organic).
    """
    site_sessions = 0.0
    site_conversions = 0.0
    reach_impressions = 0.0
    reach_clicks = 0.0
    per_channel = {}

    for r in channel_rows:
        bucket = (r.get("channel_bucket") or "").strip()
        impr = float(r.get("impressions") or 0)
        clk = float(r.get("clicks") or 0)
        sess = float(r.get("sessions") or 0)
        leads = float(r.get("leads") or 0)
        spend = float(r.get("spend") or 0)
        per_channel[bucket] = {
            "impressions": impr, "clicks": clk, "sessions": sess,
            "leads": leads, "spend": spend,
        }
        # GA4 site total = the single source of truth for traffic + conversions
        if bucket == "Website / Traffic":
            site_sessions += sess
            site_conversions += leads
        else:
            # reach comes from the paid/organic search buckets
            reach_impressions += impr
            reach_clicks += clk
        # organic sessions still count toward site traffic if GA4 total is absent
    # If there's no explicit GA4 total bucket, fall back to summing channel sessions.
    if site_sessions == 0:
        site_sessions = sum(c["sessions"] for c in per_channel.values())
    if site_conversions == 0:
        site_conversions = sum(c["leads"] for c in per_channel.values())

    return {
        "impressions": reach_impressions,
        "clicks": reach_clicks,
        "sessions": site_sessions,
        "conversions": site_conversions,
        "ctr": _safe_div(reach_clicks, reach_impressions),
        "session_rate": _safe_div(site_sessions, reach_impressions),  # impr -> session
        "conv_rate": _safe_div(site_conversions, site_sessions),      # session -> conv
        "per_channel": per_channel,
    }


def run_funnel_forecast(
    *,
    goal_leases: float,
    channel_rows: list[dict],
    leads_per_lease: float = DEFAULT_LEADS_PER_LEASE,
    context: str = "stabilized",
) -> dict:
    """Backward funnel-sufficiency forecast for one property.

    Returns the required-vs-actual at each funnel stage, the binding constraint
    (where the funnel is empty), a plain-English diagnosis, and a reallocate-vs-
    spend-more recommendation.
    """
    actual = compute_actual_funnel(channel_rows)
    conv_rate = actual["conv_rate"] or HEALTHY_SITE_CVR
    session_rate = actual["session_rate"] or 0.10  # fallback impr->session

    topfunnel_mult = CONTEXT_TOPFUNNEL_MULTIPLIER.get(context, 1.0)

    # ---- backward pass: what the goal requires at each stage ----
    req_conversions = goal_leases * leads_per_lease
    req_sessions = _safe_div(req_conversions, conv_rate) or 0
    req_impressions = (_safe_div(req_sessions, session_rate) or 0) * topfunnel_mult

    # ---- stage gaps (actual / required) ----
    def stage(actual_v, req_v):
        ratio = _safe_div(actual_v, req_v)
        return {
            "actual": round(actual_v),
            "required": round(req_v),
            "ratio": round(ratio, 2) if ratio is not None else None,
            "shortfall": round(max(0.0, req_v - actual_v)),
        }

    stages = {
        "impressions": stage(actual["impressions"], req_impressions),
        "sessions":    stage(actual["sessions"], req_sessions),
        "conversions": stage(actual["conversions"], req_conversions),
    }

    # ---- find the binding constraint: earliest (top-most) starved stage ----
    order = ["impressions", "sessions", "conversions"]
    starved = [s for s in order if (stages[s]["ratio"] or 0) < 1.0]
    bottleneck = starved[0] if starved else None

    # conversion-rate health: distinguishes "not enough traffic" from "site leaks"
    cvr_healthy = (actual["conv_rate"] or 0) >= HEALTHY_SITE_CVR

    # ---- diagnosis ----
    diagnosis = []
    achievable_leases = _safe_div(actual["conversions"], leads_per_lease) or 0
    if bottleneck is None:
        diagnosis.append(f"On track — the funnel is fed to support ~{goal_leases:g} leases.")
    else:
        need_x = stages[bottleneck]["ratio"]
        need_x = round(1 / need_x, 1) if need_x else None
        if bottleneck in ("impressions", "sessions") and cvr_healthy:
            diagnosis.append(
                f"The site converts fine ({(actual['conv_rate'] or 0)*100:.1f}%). "
                f"The funnel is starved at the TOP — {bottleneck} are "
                f"{need_x}x short of the goal. This is a demand-creation gap, "
                f"not a conversion problem."
            )
        elif not cvr_healthy:
            diagnosis.append(
                f"Conversion rate is low ({(actual['conv_rate'] or 0)*100:.1f}%) — "
                f"fixing the site/offer lifts every dollar already being spent."
            )
        diagnosis.append(
            f"At current volume you can support ~{achievable_leases:.1f} leases "
            f"vs a goal of {goal_leases:g}."
        )

    # ---- reallocate vs spend-more ----
    spend_by_channel = {b: c["spend"] for b, c in actual["per_channel"].items()}
    total_spend = sum(spend_by_channel.values())
    # demand-capture vs demand-creation split of current spend
    capture_spend = sum(
        c["spend"] for b, c in actual["per_channel"].items()
        if b in ("Paid Search",)  # bottom-funnel today
    )
    creation_spend = total_spend - capture_spend
    capture_share = _safe_div(capture_spend, total_spend)

    if bottleneck is None:
        mode = "on_track"
    elif bottleneck in ("impressions", "sessions"):
        # top starved -> need more demand creation; if budget is bottom-heavy,
        # a reallocation may partly close it, but a big shortfall needs more $.
        mode = "spend_more" if (stages[bottleneck]["ratio"] or 1) < 0.7 else "reallocate"
    else:
        mode = "fix_conversion"

    recommendation = {
        "mode": mode,
        "current_spend": round(total_spend, 2),
        "capture_share": round(capture_share, 2) if capture_share is not None else None,
        "note": _recommendation_note(mode, capture_share, bottleneck),
    }

    return {
        "goal_leases": goal_leases,
        "leads_per_lease": leads_per_lease,
        "context": context,
        "topfunnel_multiplier": topfunnel_mult,
        "actual": actual,
        "stages": stages,
        "bottleneck": bottleneck,
        "cvr_healthy": cvr_healthy,
        "achievable_leases": round(achievable_leases, 1),
        "diagnosis": diagnosis,
        "recommendation": recommendation,
    }


def _recommendation_note(mode, capture_share, bottleneck):
    if mode == "on_track":
        return "Funnel is fed for the goal — hold the mix and optimize."
    if mode == "fix_conversion":
        return "Traffic is sufficient; the leak is on-site. Fix landing/offer before adding spend."
    cap = f"{(capture_share or 0)*100:.0f}%"
    if mode == "reallocate":
        return (f"{cap} of spend is bottom-funnel demand capture. Shift some into "
                f"demand creation (SEO, social, awareness) to feed the top.")
    return (f"{cap} of spend is bottom-funnel, and the top is well short of the goal. "
            f"This needs a bigger, full-funnel budget — search alone can't manufacture "
            f"the demand the goal requires.")
