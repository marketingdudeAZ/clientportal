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

    Dispositioning properties are intentionally excluded — they are on their
    way out of the portfolio and should not surface in the dashboard, KPIs,
    or spend.
    """
    return [
        {
            "filters": [
                {
                    "propertyName": "plestatus",
                    "operator": "IN",
                    "values": ["RPM Managed", "Onboarding"],
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

    # LIST endpoint enumeration (2026-07-06): the /search endpoint's
    # pagination stops short non-deterministically at portfolio size — the
    # Properties tab showed 874 while the true 2-status count is much
    # higher. Same flakiness spend_sheet + triage already worked around.
    # We enumerate every company via the fully-consistent LIST endpoint and
    # filter plestatus client-side.
    _ = _build_filter_groups(email, role)  # kept for back-compat/tests
    allowed = {"RPM Managed", "Onboarding"}

    all_companies = []
    seen_ids = set()
    after = None
    for _ in range(100):  # Safety cap: max 10,000 companies
        params = {"limit": 100, "properties": ",".join(COMPANY_PROPS)}
        if after:
            params["after"] = after
        resp = requests.get(
            f"{API_BASE}/crm/v3/objects/companies",
            headers=HEADERS, params=params, timeout=25,
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        after = data.get("paging", {}).get("next", {}).get("after")
        for company in results:
            cid = company["id"]
            if (company.get("properties", {}).get("plestatus") or "").strip() not in allowed:
                continue
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


def _spend_by_company():
    """{company_id: monthly $} from the deal-line-item engine (spend_sheet).

    The authoritative monthly spend lives on deal line items — the legacy
    company-level *_monthly_spend fields this module used to sum are only
    sparsely populated, which undercounted the dashboard KPI (showed $339k
    portfolio-wide). spend_sheet keeps its own 30-min cache, so this join
    is cheap after the first call. Empty dict on any failure — callers fall
    back to the legacy per-property computation.
    """
    try:
        from spend_sheet import get_spend_sheet_data, _SPEND_COLUMN_KEYS
        out = {}
        for row in get_spend_sheet_data():
            total = 0.0
            for k in _SPEND_COLUMN_KEYS:
                v = row.get(k)
                if v:
                    try:
                        total += float(v)
                    except (TypeError, ValueError):
                        pass
            out[str(row.get("company_id"))] = total
        return out
    except Exception as e:
        logger.warning("portfolio: spend_sheet join unavailable (%s) — legacy spend fields", e)
        return {}


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


# Minimum group size before per-city/market spend averages are exposed. Below
# this, an "average" is effectively one property's spend — suppress the money
# fields (count is still shown so the UI can say "only 2 in this city").
BENCHMARK_MIN_N = 3


def _median(vals):
    s = sorted(vals)
    n = len(s)
    if n == 0:
        return None
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def compute_benchmarks(companies, spend_map=None):
    """Spend benchmarks grouped by city and by RPM market, across the full
    managed portfolio (all RPM Managed + Onboarding — not role-scoped).

    Powers the "10 properties in Tempe spend ~$7K/mo, you're at $4K" story.
    Per group we return count, mean + median monthly spend, and spend-per-unit
    (sum spend / sum units — an honest size-normalized figure that a raw mean
    hides). Money fields are null when count < BENCHMARK_MIN_N.
    """
    if spend_map is None:
        spend_map = _spend_by_company()

    def _blank():
        return {"count": 0, "units": 0, "spend_total": 0.0, "spends": []}

    by_city = {}
    by_market = {}
    for props in companies:
        cid = str(props.get("hubspot_company_id") or "")
        monthly = spend_map.get(cid) if cid in spend_map else _compute_monthly_spend(props)
        units = _safe_int(props.get("totalunits"))

        city = (props.get("city") or "").strip()
        state = (props.get("state") or "").strip()
        if city:
            key = f"{city}, {state}" if state else city
            g = by_city.setdefault(key, _blank())
            g["count"] += 1
            g["units"] += units
            g["spend_total"] += monthly
            g["spends"].append(monthly)

        market = (props.get("rpmmarket") or "").strip()
        if market:
            g = by_market.setdefault(market, _blank())
            g["count"] += 1
            g["units"] += units
            g["spend_total"] += monthly
            g["spends"].append(monthly)

    def _finalize(groups):
        out = {}
        for key, g in groups.items():
            n = g["count"]
            entry = {"count": n, "total_units": g["units"]}
            if n >= BENCHMARK_MIN_N:
                entry["avg_spend"] = round(g["spend_total"] / n, 2)
                entry["median_spend"] = round(_median(g["spends"]), 2)
                entry["avg_spend_per_unit"] = (
                    round(g["spend_total"] / g["units"], 2) if g["units"] else None
                )
            else:
                entry["avg_spend"] = None
                entry["median_spend"] = None
                entry["avg_spend_per_unit"] = None
            out[key] = entry
        return out

    return {"by_city": _finalize(by_city), "by_market": _finalize(by_market)}


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

    spend_map = _spend_by_company()  # deal-line-item truth; {} on failure

    for props in companies:
        # Units
        total_units += _safe_int(props.get("totalunits"))

        # Flags
        total_flags += _safe_int(props.get("redlight_flag_count"))

        # Spend — deal line items when available, legacy fields otherwise
        cid = str(props.get("hubspot_company_id") or "")
        total_spend += spend_map.get(cid) if cid in spend_map else _compute_monthly_spend(props)

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
    _smap = _spend_by_company()
    benchmarks = compute_benchmarks(companies, _smap)
    for props in companies:
        _cid = str(props.get("hubspot_company_id") or "")
        monthly = _smap.get(_cid) if _cid in _smap else _compute_monthly_spend(props)
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
            # HubSpot company id — lets the portfolio table drill into the
            # property detail view (which loads by company_id).
            "company_id": props.get("hubspot_company_id", ""),
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
        "benchmarks": benchmarks,
    }
