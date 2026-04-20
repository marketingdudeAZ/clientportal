"""Assemble the /api/seo/dashboard payload.

Reads the keyword universe from HubDB, latest ranks + 30-day deltas from
BigQuery, and cached on-page + backlinks summaries from HubDB. Never hits
DataForSEO in the request path — that work lives in seo_refresh_cron.py.
"""

import logging
import time
from typing import Any

from config import (
    HUBDB_SEO_COMPETITORS_TABLE_ID,
    HUBDB_SEO_KEYWORDS_TABLE_ID,
)
from hubdb_helpers import read_rows

logger = logging.getLogger(__name__)

_cache: dict = {}
CACHE_TTL = 1800  # 30 minutes


def _rank_deltas(history: list[dict], keyword: str) -> dict:
    """Given per-keyword history rows (newest first), return deltas over 7/30 days."""
    rows = [r for r in history if r["keyword"] == keyword and r.get("position")]
    if not rows:
        return {"current": None, "delta_7d": None, "delta_30d": None, "url": None}
    current = rows[0]
    cur_pos = current["position"]
    cur_time = current["fetched_at"]

    def _delta(window_days):
        for r in rows[1:]:
            age_days = (cur_time - r["fetched_at"]).total_seconds() / 86400
            if age_days >= window_days:
                return (r["position"] or 0) - cur_pos  # positive = improved
        return None

    return {
        "current": cur_pos,
        "delta_7d": _delta(7),
        "delta_30d": _delta(30),
        "url": current.get("url"),
    }


def build_dashboard(property_uuid: str) -> dict:
    """Return the complete dashboard payload for a property."""
    if not property_uuid:
        return {}

    now = time.time()
    cached = _cache.get(property_uuid)
    if cached and (now - cached[0]) < CACHE_TTL:
        return cached[1]

    keywords_rows = read_rows(
        HUBDB_SEO_KEYWORDS_TABLE_ID,
        filters={"property_uuid": property_uuid},
    )
    competitors = read_rows(
        HUBDB_SEO_COMPETITORS_TABLE_ID,
        filters={"property_uuid": property_uuid},
    )

    try:
        from bigquery_client import get_seo_rank_history

        history = get_seo_rank_history(property_uuid, days=90)
    except Exception as e:  # BigQuery unavailable — still render the keyword table
        logger.warning("BigQuery rank history unavailable: %s", e)
        history = []

    keywords: list[dict[str, Any]] = []
    for kw in keywords_rows:
        deltas = _rank_deltas(history, kw.get("keyword"))
        keywords.append({
            "id": kw.get("id"),
            "keyword": kw.get("keyword"),
            "priority": kw.get("priority"),
            "tag": kw.get("tag"),
            "intent": kw.get("intent"),
            "branded": bool(kw.get("branded")),
            "target_position": kw.get("target_position"),
            "position": deltas["current"],
            "delta_7d": deltas["delta_7d"],
            "delta_30d": deltas["delta_30d"],
            "url": deltas["url"],
        })

    # Dates in history are TIMESTAMPs from BigQuery; aggregate daily points.
    organic_trend = _trend_from_history(history)

    payload = {
        "property_uuid": property_uuid,
        "keywords": keywords,
        "competitors": [
            {"id": c.get("id"), "domain": c.get("competitor_domain"), "label": c.get("label")}
            for c in competitors
        ],
        "organic_trend": organic_trend,
        "summary": {
            "total_keywords": len(keywords),
            "ranking_top_3": sum(1 for k in keywords if (k["position"] or 999) <= 3),
            "ranking_top_10": sum(1 for k in keywords if (k["position"] or 999) <= 10),
            "improving_30d": sum(1 for k in keywords if (k["delta_30d"] or 0) > 0),
        },
    }
    _cache[property_uuid] = (now, payload)
    return payload


def _trend_from_history(history: list[dict]) -> list[dict]:
    """Collapse rank history into per-day 'visibility' score.

    Visibility = sum of (101 - position) over tracked keywords per day.
    Clients see this as a single-line trendline.
    """
    if not history:
        return []
    by_day: dict[str, int] = {}
    for r in history:
        pos = r.get("position")
        if not pos:
            continue
        day = r["fetched_at"].strftime("%Y-%m-%d")
        by_day[day] = by_day.get(day, 0) + max(0, 101 - pos)
    return [{"date": d, "visibility": v} for d, v in sorted(by_day.items())]


def invalidate(property_uuid: str | None = None):
    if property_uuid:
        _cache.pop(property_uuid, None)
    else:
        _cache.clear()
