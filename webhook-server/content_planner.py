"""Phase 2 — Content Planner (iPullRank / GEO methodology).

Three capabilities for Standard+ tier properties:

1. Topic clusters — hub-and-spoke grouping of tracked keywords by SERP overlap.
   Two keywords cluster together if their top-10 SERPs share >= 4 URLs.
2. Semantic gaps — keywords competitors rank for but the property doesn't,
   filtered to low-difficulty (<40) and sorted by search volume desc.
3. Decay detection — flag URLs whose tracked keywords have dropped >= N
   positions over the last 30 days. Pulls history from BigQuery.

All three are used by the weekly cron to populate the content planner UI and
the refresh queue. Cached in-memory per run to avoid re-fetching SERPs.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

logger = logging.getLogger(__name__)


# ── Topic clustering ─────────────────────────────────────────────────────────

def _extract_top_urls(serp_result: dict, depth: int = 10) -> list[str]:
    """Pull the top-N organic result URLs from a serp_organic_advanced response."""
    urls: list[str] = []
    for item in (serp_result.get("items") or [])[:depth]:
        if item.get("type") == "organic" and item.get("url"):
            urls.append(item["url"].lower())
    return urls


def cluster_keywords(
    property_uuid: str,
    overlap_threshold: int = 4,
    _serp_cache: dict[str, dict] | None = None,
) -> list[dict]:
    """Group a property's tracked keywords by SERP overlap.

    Args:
        property_uuid: RPM property UUID
        overlap_threshold: min shared top-10 URLs to cluster two keywords
        _serp_cache: optional prefilled {keyword: serp_dict} for tests

    Returns list of cluster dicts:
        [{
            hub_keyword:    str,          # highest-volume keyword in cluster
            spokes:         [str, ...],   # other keywords in the same cluster
            total_volume:   int,          # sum of monthly search volumes
            current_coverage_pct: float,  # fraction of cluster KWs ranking in top-10
            avg_difficulty: float,        # mean keyword_difficulty across spokes
        }, ...]

    Unclustered keywords become their own single-keyword "cluster" (hub=itself,
    spokes=[]).
    """
    from config import HUBDB_SEO_KEYWORDS_TABLE_ID
    from hubdb_helpers import read_rows
    from dataforseo_client import serp_organic_advanced

    keywords = read_rows(HUBDB_SEO_KEYWORDS_TABLE_ID, filters={"property_uuid": property_uuid})
    if not keywords:
        return []

    serp_cache = dict(_serp_cache or {})

    # Collect SERPs and top-URL sets per keyword
    url_sets: dict[str, set[str]] = {}
    kw_meta: dict[str, dict] = {}
    for kw in keywords:
        k = (kw.get("keyword") or "").strip()
        if not k:
            continue
        kw_meta[k] = {
            "volume":     kw.get("volume") or 0,
            "difficulty": kw.get("difficulty") or 0,
            "position":   kw.get("position"),  # optional — from latest rank snapshot
        }
        if k not in serp_cache:
            try:
                serp_cache[k] = serp_organic_advanced(k)
            except Exception as e:
                logger.warning("SERP fetch failed for %s: %s", k, e)
                serp_cache[k] = {}
        url_sets[k] = set(_extract_top_urls(serp_cache[k]))

    # Union-find: cluster keywords whose URL sets overlap >= threshold
    parent: dict[str, str] = {k: k for k in url_sets}

    def find(k: str) -> str:
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    kws = list(url_sets.keys())
    for i, a in enumerate(kws):
        for b in kws[i + 1:]:
            if len(url_sets[a] & url_sets[b]) >= overlap_threshold:
                union(a, b)

    # Bucket by root
    buckets: dict[str, list[str]] = defaultdict(list)
    for k in kws:
        buckets[find(k)].append(k)

    # Build output — pick hub = highest-volume keyword in each bucket
    out: list[dict] = []
    for members in buckets.values():
        members_sorted = sorted(members, key=lambda k: kw_meta[k].get("volume", 0), reverse=True)
        hub = members_sorted[0]
        spokes = members_sorted[1:]
        total_volume = sum(kw_meta[k].get("volume", 0) or 0 for k in members)
        ranking = [k for k in members if (kw_meta[k].get("position") or 999) <= 10]
        coverage = round(len(ranking) / len(members), 3) if members else 0.0
        diffs = [kw_meta[k].get("difficulty", 0) or 0 for k in members]
        avg_diff = round(sum(diffs) / len(diffs), 1) if diffs else 0.0
        out.append({
            "hub_keyword":          hub,
            "spokes":               spokes,
            "total_volume":         int(total_volume),
            "current_coverage_pct": coverage,
            "avg_difficulty":       avg_diff,
        })

    # Sort clusters by total volume desc
    out.sort(key=lambda c: c["total_volume"], reverse=True)
    return out


# ── Semantic gap analysis ────────────────────────────────────────────────────

def semantic_gaps(
    property_domain: str,
    competitor_domains: list[str],
    max_difficulty: int = 40,
    min_volume: int = 50,
) -> list[dict]:
    """Keywords competitors rank for but the property doesn't.

    Fans out to dataforseo domain_intersection across each competitor,
    filters to low-KD opportunities, sorts by search volume desc.

    Returns [{keyword, volume, difficulty, competitor, cpc}]
    """
    from dataforseo_client import domain_intersection

    seen: dict[str, dict] = {}
    for comp in competitor_domains:
        try:
            rows = domain_intersection(target1=property_domain, target2=comp, exclude_top_domain=True)
        except Exception as e:
            logger.warning("domain_intersection failed for %s vs %s: %s", property_domain, comp, e)
            continue
        for row in rows:
            kw_info = row.get("keyword_data", {}).get("keyword_info") or {}
            keyword = row.get("keyword") or row.get("keyword_data", {}).get("keyword")
            if not keyword:
                continue
            difficulty = (row.get("keyword_data", {}).get("keyword_properties") or {}).get("keyword_difficulty") or 0
            volume = kw_info.get("search_volume") or 0
            if difficulty > max_difficulty or volume < min_volume:
                continue
            # Keep the one with highest volume if we see the keyword across multiple competitors
            if keyword not in seen or (seen[keyword]["volume"] < volume):
                seen[keyword] = {
                    "keyword":    keyword,
                    "volume":     int(volume),
                    "difficulty": int(difficulty),
                    "competitor": comp,
                    "cpc":        round(kw_info.get("cpc") or 0, 2),
                }
    return sorted(seen.values(), key=lambda r: r["volume"], reverse=True)


# ── Content decay detection ──────────────────────────────────────────────────

def detect_decay(
    property_uuid: str,
    threshold: int = 5,
    min_affected: int = 3,
    lookback_days: int = 30,
) -> list[dict]:
    """Flag URLs whose tracked keywords have dropped N+ positions.

    Pulls rank history from BigQuery (seo_ranks_daily), groups by URL, computes
    30-day delta per keyword. A URL is flagged if it has >= min_affected keywords
    whose positions dropped by >= threshold.

    Returns [{
        url, avg_drop, affected_keywords_count, affected_keywords: [...],
        priority: 'high' | 'medium' | 'low'
    }] sorted by avg_drop desc.
    """
    from bigquery_client import get_seo_rank_history, is_bigquery_configured

    if not is_bigquery_configured():
        logger.info("detect_decay: BQ not configured, returning []")
        return []

    try:
        history = get_seo_rank_history(property_uuid, days=lookback_days)
    except Exception as e:
        logger.warning("detect_decay: BQ query failed for %s: %s", property_uuid, e)
        return []
    if not history:
        return []

    # Group rows by (url, keyword), pick earliest and latest within the window
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in history:
        url = row.get("url")
        kw = row.get("keyword")
        if not (url and kw):
            continue
        grouped[(url, kw)].append(row)

    # Per-URL: collect keyword deltas where position worsened (increased rank number)
    per_url: dict[str, list[dict]] = defaultdict(list)
    for (url, kw), rows in grouped.items():
        rows_sorted = sorted(rows, key=lambda r: r.get("fetched_at") or "")
        if len(rows_sorted) < 2:
            continue
        earliest = rows_sorted[0].get("position")
        latest = rows_sorted[-1].get("position")
        if earliest is None or latest is None:
            continue
        # Higher position number = worse rank. Drop = latest - earliest
        drop = int(latest) - int(earliest)
        if drop >= threshold:
            per_url[url].append({
                "keyword":      kw,
                "from":         int(earliest),
                "to":           int(latest),
                "drop":         drop,
            })

    results: list[dict] = []
    for url, affected in per_url.items():
        if len(affected) < min_affected:
            continue
        drops = [a["drop"] for a in affected]
        avg_drop = round(sum(drops) / len(drops), 1)
        # Priority buckets: >= 15 avg drop = high, 10-14 = medium, else low
        if avg_drop >= 15:
            pri = "high"
        elif avg_drop >= 10:
            pri = "medium"
        else:
            pri = "low"
        results.append({
            "url":                      url,
            "avg_drop":                 avg_drop,
            "affected_keywords_count":  len(affected),
            "affected_keywords":        affected,
            "priority":                 pri,
        })

    return sorted(results, key=lambda r: r["avg_drop"], reverse=True)
