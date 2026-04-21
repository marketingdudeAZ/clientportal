"""Phase 3 — Google Trends via DataForSEO.

Three capabilities:
- explore(keywords, timeframe) — time series per keyword
- seasonal_peaks(keywords) — peak-month detection from 3yr data
- related_rising(seed) — breakout queries related to a seed
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

logger = logging.getLogger(__name__)

MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]


def explore(keywords: list[str], timeframe: str = "past_12_months", location_code: int | None = None) -> dict:
    """Google Trends interest-over-time for up to 5 keywords.

    Returns {
        series: [{keyword, values:[int,...]}],
        timeframe: str,
    }
    """
    from dataforseo_client import trends_explore

    if not keywords:
        return {"series": [], "timeframe": timeframe}
    # DataForSEO caps at 5 keywords per call
    kws = keywords[:5]
    try:
        raw = trends_explore(kws, timeframe=timeframe)
    except Exception as e:
        logger.warning("trends_explore failed: %s", e)
        return {"series": [], "timeframe": timeframe, "error": str(e)}

    # Result shape varies slightly by endpoint version.
    # Normalize to a simple series-of-values-per-keyword.
    series: list[dict] = []
    items = raw.get("items") or []
    # Most responses put the "graph" under items[0].data with per-keyword values.
    graph_item = next((it for it in items if it.get("type") == "google_trends_graph"), items[0] if items else {})
    data_points = graph_item.get("data") or []
    # data_points is list of {date_from, date_to, values:[int per-keyword]}
    for idx, kw in enumerate(kws):
        values = [int(dp.get("values", [0]*len(kws))[idx] or 0) for dp in data_points]
        series.append({"keyword": kw, "values": values})

    return {
        "series":     series,
        "timeframe":  timeframe,
        "keywords":   kws,
    }


def seasonal_peaks(keywords: list[str], location_code: int | None = None) -> dict:
    """Identify peak month per keyword using a 5-year horizon.

    Returns {peaks: [{keyword, peak_month, peak_avg_value}]}
    """
    if not keywords:
        return {"peaks": []}

    data = explore(keywords, timeframe="past_5_years", location_code=location_code)
    series = data.get("series") or []

    peaks: list[dict] = []
    for s in series:
        values = s.get("values") or []
        if not values:
            continue
        # Aggregate month-of-year averages. Assumes weekly or monthly granularity
        # over 5 years → collapse to 12 buckets by index modulo.
        # For weekly data (260 points), each month ~= 21-22 weeks.
        n_per_month = max(1, len(values) // 60)  # 60 months in 5 years
        monthly = defaultdict(list)
        for i, v in enumerate(values):
            month_bucket = (i // n_per_month) % 12
            monthly[month_bucket].append(v)
        month_avgs = {m: (sum(vs) / len(vs) if vs else 0) for m, vs in monthly.items()}
        if not month_avgs:
            continue
        peak_month_idx = max(month_avgs, key=lambda m: month_avgs[m])
        peaks.append({
            "keyword":          s["keyword"],
            "peak_month":       MONTHS[peak_month_idx],
            "peak_avg_value":   round(month_avgs[peak_month_idx], 1),
        })
    return {"peaks": peaks}


def related_rising(seed: str, location_code: int | None = None) -> dict:
    """Rising related queries for a seed keyword (Google Trends "breakout" list).

    Returns {rising: [str]} — best-effort; DataForSEO surface varies.
    """
    if not seed:
        return {"rising": []}

    try:
        from dataforseo_client import _post, _first_result, DATAFORSEO_DEFAULT_LANGUAGE, DATAFORSEO_DEFAULT_LOCATION
    except ImportError:
        return {"rising": []}

    # trends_explore doesn't expose rising queries directly — use related_queries endpoint
    payload = {
        "keywords":      [seed],
        "time_range":    "past_12_months",
        "location_code": location_code or DATAFORSEO_DEFAULT_LOCATION,
        "language_code": DATAFORSEO_DEFAULT_LANGUAGE,
    }
    try:
        raw = _first_result(_post("/v3/keywords_data/google_trends/related_queries/live", payload))
    except Exception as e:
        logger.warning("related_queries failed: %s", e)
        return {"rising": []}

    items = raw.get("items") or []
    rising: list[str] = []
    for it in items:
        if it.get("type") != "google_trends_related_queries":
            continue
        # Rising queries list
        for r in (it.get("data") or {}).get("rising", []) or []:
            q = r.get("query")
            if q:
                rising.append(q)
    return {"rising": rising[:25]}
