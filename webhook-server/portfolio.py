"""Portfolio data: fetch all properties for a user and compute rollup KPIs."""

import logging
import time

import requests

from config import (
    HUBSPOT_API_KEY,
    SEO_TIERS,
    SOCIAL_POSTING_TIERS,
    REPUTATION_TIERS,
)

logger = logging.getLogger(__name__)

API_BASE = "https://api.hubapi.com"
HEADERS = {
    "Authorization": f"Bearer {HUBSPOT_API_KEY}",
    "Content-Type": "application/json",
}

# In-memory cache: email -> (timestamp, data)
_portfolio_cache = {}
CACHE_TTL_SECONDS = 900  # 15 minutes — large query, expensive to re-fetch

# Properties to fetch for each company
COMPANY_PROPS = [
    "name", "address", "city", "state", "zip", "uuid", "domain",
    "rpmmarket", "plestatus", "totalunits",
    "redlight_report_score", "redlight_flag_count",
    "redlight_digital_flags", "redlight_pm_flags",
    "redlight_ops_flags", "redlight_operations_flags",
    "seo_budget", "social_posting_tier", "reputation_tier",
    "paid_search_monthly_spend", "paid_social_monthly_spend",
    "website_hosting_type",
    "marketing_manager_email", "marketing_director_email", "marketing_rvp_email",
    # Leasing health — pulled from Yardi/ops system synced to HubSpot
    "occupancy__",                       # Physical occupancy %
    "atr__",                             # Available to rent %
    "trending_120_days_lease_expiration", # Leases expiring in 120-day window
    "brf___renewal_leases_120_trend",     # Renewal lease 120-day trend
    "occupancy_status",                   # Lease-Up / Stabilized / In-Transition / Renovation
    # Red Light score fields
    "red_light_report_score", "red_light_report_status",
    "red_light_market_score", "red_light_marketing_score",
    "red_light_funnel_score", "red_light_experience_score",
]

# Role -> which email fields to match
ROLE_EMAIL_FIELDS = {
    "marketing_manager": ["marketing_manager_email"],
    "marketing_director": ["marketing_director_email", "marketing_manager_email"],
    "marketing_rvp": ["marketing_rvp_email", "marketing_director_email", "marketing_manager_email"],
}


def _build_filter_groups(email=None, role=None):
    """Return a single filter group matching all active RPM properties.

    All authenticated portal members see all properties — no per-user filtering.
    The email/role params are kept for backward compatibility but are unused.
    """
    return [
        {
            "filters": [
                {
                    "propertyName": "plestatus",
                    "operator": "IN",
                    "values": ["RPM Managed", "Dispositioning", "Onboarding"],
                }
            ]
        }
    ]


def _search_companies(filter_groups, after=None):
    """Execute a single CRM search request. Returns (results, next_after)."""
    body = {
        "filterGroups": filter_groups,
        "properties": COMPANY_PROPS,
        "limit": 100,
        "sorts": [{"propertyName": "name", "direction": "ASCENDING"}],
    }
    if after:
        body["after"] = after

    resp = requests.post(
        f"{API_BASE}/crm/v3/objects/companies/search",
        headers=HEADERS,
        json=body,
    )
    resp.raise_for_status()
    data = resp.json()
    results = data.get("results", [])
    paging = data.get("paging", {})
    next_after = paging.get("next", {}).get("after")
    return results, next_after


def fetch_portfolio(email, role):
    """Fetch all active RPM properties. Returns list of company dicts.

    All authenticated portal members see all properties.
    Uses a shared in-memory cache with 5-minute TTL.
    """
    cache_key = "all_properties"
    now = time.time()

    cached = _portfolio_cache.get(cache_key)
    if cached and (now - cached[0]) < CACHE_TTL_SECONDS:
        logger.debug("Cache hit for %s", cache_key)
        return cached[1]

    filter_groups = _build_filter_groups(email, role)

    # Paginate through all results
    all_companies = []
    seen_ids = set()
    after = None

    for _ in range(100):  # Safety cap: max 10,000 companies (100 pages × 100 per page)
        results, after = _search_companies(filter_groups, after)
        for company in results:
            cid = company["id"]
            if cid not in seen_ids:
                seen_ids.add(cid)
                all_companies.append(company)
        if not after:
            break

    # Flatten to simple dicts
    properties_list = []
    for company in all_companies:
        props = company.get("properties", {})
        props["hubspot_company_id"] = company["id"]
        properties_list.append(props)

    # Cache the result
    _portfolio_cache[cache_key] = (now, properties_list)

    logger.info("Fetched %d properties for %s (%s)", len(properties_list), email, role)
    return properties_list


def _safe_float(val, default=0.0):
    """Safely convert a value to float."""
    if val is None or val == "":
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _safe_int(val, default=0):
    """Safely convert a value to int."""
    if val is None or val == "":
        return default
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return default


def _compute_monthly_spend(props):
    """Compute total monthly marketing spend for a property."""
    total = 0.0

    # SEO budget
    seo = props.get("seo_budget")
    if seo:
        total += _safe_float(seo)

    # Social posting tier
    social_tier = props.get("social_posting_tier")
    if social_tier and social_tier in SOCIAL_POSTING_TIERS:
        total += SOCIAL_POSTING_TIERS[social_tier]

    # Reputation tier
    rep_tier = props.get("reputation_tier")
    if rep_tier and rep_tier in REPUTATION_TIERS:
        total += REPUTATION_TIERS[rep_tier]

    # Paid search
    paid_search = props.get("paid_search_monthly_spend")
    if paid_search:
        total += _safe_float(paid_search)

    # Paid social
    paid_social = props.get("paid_social_monthly_spend")
    if paid_social:
        total += _safe_float(paid_social)

    return total


def compute_rollups(companies):
    """Compute portfolio-level rollup KPIs from a list of company property dicts."""
    total_properties = len(companies)
    total_units = 0
    total_flags = 0
    total_spend = 0.0
    health_scores = []
    health_distribution = {"healthy": 0, "warning": 0, "critical": 0, "no_data": 0}
    market_breakdown = {}
    occupancy_scores = []
    atr_scores = []
    lease_trend_total = 0

    for props in companies:
        # Units
        total_units += _safe_int(props.get("totalunits"))

        # Flags
        total_flags += _safe_int(props.get("redlight_flag_count"))

        # Spend
        total_spend += _compute_monthly_spend(props)

        # Health score — prefer pipeline score, fall back to leasing score
        rl = props.get("redlight_report_score")
        if rl is not None and rl != "":
            s = _safe_float(rl)
        else:
            ls_r = _compute_leasing_score(props)
            s = ls_r["score"] if ls_r else None
        if s is not None:
            health_scores.append(s)
            if s >= 75:
                health_distribution["healthy"] += 1
            elif s >= 50:
                health_distribution["warning"] += 1
            else:
                health_distribution["critical"] += 1
        else:
            health_distribution["no_data"] += 1

        # Market breakdown
        market = props.get("rpmmarket", "Unknown")
        if market:
            market_breakdown[market] = market_breakdown.get(market, 0) + 1

        # Leasing health
        occ = props.get("occupancy__")
        if occ is not None and occ != "":
            occupancy_scores.append(_safe_float(occ))
        atr = props.get("atr__")
        if atr is not None and atr != "":
            atr_scores.append(_safe_float(atr))
        trend = props.get("trending_120_days_lease_expiration")
        if trend is not None and trend != "":
            lease_trend_total += _safe_int(trend)

    avg_health = round(sum(health_scores) / len(health_scores), 1) if health_scores else None
    avg_occupancy = round(sum(occupancy_scores) / len(occupancy_scores), 1) if occupancy_scores else None
    avg_atr = round(sum(atr_scores) / len(atr_scores), 1) if atr_scores else None

    return {
        "total_properties": total_properties,
        "total_units": total_units,
        "avg_health_score": avg_health,
        "total_monthly_spend": round(total_spend, 2),
        "total_flags": total_flags,
        "health_distribution": health_distribution,
        "market_breakdown": market_breakdown,
        # Leasing rollups
        "avg_occupancy": avg_occupancy,
        "avg_atr": avg_atr,
        "total_leases_expiring_120d": lease_trend_total,
    }


def _compute_leasing_score(props):
    """Compute a lead-gen health score from Occupancy, ATR%, and 120-day trend.

    Mirrors server.py's _compute_leasing_score — keep in sync.
    Returns None if both occupancy and ATR are missing.
    """
    def _fv(k):
        v = props.get(k)
        try:
            return float(v) if v not in (None, "") else None
        except (ValueError, TypeError):
            return None

    occ   = _fv("occupancy__")
    atr   = _fv("atr__")
    trend = _fv("trending_120_days_lease_expiration")
    units = _fv("totalunits") or 0

    if occ is None and atr is None:
        return None

    occ_status    = (props.get("occupancy_status") or "").strip()
    is_lease_up   = occ_status in ("Lease-Up", "In-Transition")
    is_renovation = occ_status == "Renovation"

    if is_renovation:
        return {"score": None, "status": "Renovation", "is_lease_up": False}

    def _occ_score(o):
        if o is None: return 75
        if is_lease_up:
            if o >= 88: return 90
            if o >= 75: return 75
            if o >= 60: return 60
            if o >= 45: return 45
            return 30
        else:
            if o >= 95: return 100
            if o >= 93: return 85
            if o >= 90: return 70
            if o >= 87: return 55
            return 35

    def _atr_score(a):
        if a is None: return 75
        if is_lease_up:
            if a <= 10: return 90
            if a <= 20: return 75
            if a <= 35: return 55
            if a <= 50: return 35
            return 20
        else:
            if a <= 4:  return 100
            if a <= 6:  return 80
            if a <= 9:  return 60
            if a <= 13: return 40
            return 20

    def _exposure_score(t, u):
        if t is None or u == 0: return 75
        pct = (t / u) * 100
        if pct <= 8:  return 100
        if pct <= 15: return 75
        if pct <= 22: return 50
        return 25

    o_score = _occ_score(occ)
    a_score = _atr_score(atr)
    e_score = _exposure_score(trend, units)

    if is_lease_up:
        overall = round(o_score * 0.60 + a_score * 0.40)
    else:
        overall = round(o_score * 0.50 + a_score * 0.30 + e_score * 0.20)

    if overall >= 75:
        status = "ON TRACK"
    elif overall >= 50:
        status = "WATCH"
    else:
        status = "NEEDS ATTENTION"

    return {"score": overall, "status": status, "is_lease_up": is_lease_up}


def format_portfolio_response(companies):
    """Format companies into a clean JSON response with rollups."""
    # Compute rollups
    rollups = compute_rollups(companies)

    # Format individual properties for the table
    properties = []
    for props in companies:
        monthly = _compute_monthly_spend(props)
        rl_score = props.get("redlight_report_score")

        occ_raw = props.get("occupancy__")
        atr_raw = props.get("atr__")
        trend_raw = props.get("trending_120_days_lease_expiration")

        # Prefer pipeline score; fall back to computed leasing score
        ls = _compute_leasing_score(props)
        if rl_score not in (None, ""):
            health_score = _safe_float(rl_score)
            health_status = props.get("red_light_report_status", "")
        elif ls:
            health_score = ls["score"]
            health_status = ls["status"]
        else:
            health_score = None
            health_status = ""

        properties.append({
            "uuid": props.get("uuid", ""),
            "name": props.get("name", "Unknown"),
            "address": props.get("address", ""),
            "city": props.get("city", ""),
            "state": props.get("state", ""),
            "market": props.get("rpmmarket", ""),
            "units": _safe_int(props.get("totalunits")),
            "health_score": health_score,
            "health_status": health_status,
            "market_score": _safe_float(props.get("red_light_market_score")) if props.get("red_light_market_score") else None,
            "marketing_score": _safe_float(props.get("red_light_marketing_score")) if props.get("red_light_marketing_score") else None,
            "funnel_score": _safe_float(props.get("red_light_funnel_score")) if props.get("red_light_funnel_score") else None,
            "experience_score": _safe_float(props.get("red_light_experience_score")) if props.get("red_light_experience_score") else None,
            "monthly_spend": round(monthly, 2),
            "flags": _safe_int(props.get("redlight_flag_count")),
            "status": props.get("plestatus", ""),
            # Leasing health
            "occupancy": _safe_float(occ_raw) if occ_raw else None,
            "atr": _safe_float(atr_raw) if atr_raw else None,
            "lease_trend_120": _safe_int(trend_raw) if trend_raw else None,
            "renewal_trend_120": _safe_int(props.get("brf___renewal_leases_120_trend")) if props.get("brf___renewal_leases_120_trend") else None,
        })

    return {
        "rollups": rollups,
        "properties": properties,
    }
