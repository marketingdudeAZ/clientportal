"""Phase 3 — Keyword research (ideas, suggestions, difficulty, gap, save).

Thin business-logic wrappers on top of dataforseo_client. Server routes call
these — the heavy lifting (SERP calls, result normalization) happens here so
the server.py route handlers stay focused on auth + tier gating + I/O.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _normalize_idea(row: dict) -> dict:
    """Reshape a DataForSEO keyword_ideas row into the frontend contract."""
    kw_info = row.get("keyword_info") or {}
    kw_props = row.get("keyword_properties") or {}
    serp_info = row.get("serp_info") or {}
    monthly = kw_info.get("monthly_searches") or []
    features = []
    for f in (serp_info.get("serp_item_types") or []):
        features.append(f)
    intent_category = (row.get("search_intent_info") or {}).get("main_intent") or ""
    return {
        "keyword":        row.get("keyword") or "",
        "volume":         kw_info.get("search_volume") or 0,
        "difficulty":     kw_props.get("keyword_difficulty") or 0,
        "intent":         intent_category,
        "cpc":            round(kw_info.get("cpc") or 0, 2),
        "serp_features":  features,
        "monthly_volumes": [m.get("search_volume") or 0 for m in monthly][-12:],
    }


def expand_seed(seed_keywords: list[str], location_code: int | None = None, limit: int = 200) -> list[dict]:
    """Seed expansion via DataForSEO keyword_ideas."""
    from dataforseo_client import keyword_ideas
    raw = keyword_ideas(seed_keywords, location_code=location_code, limit=limit)
    return [_normalize_idea(r) for r in raw]


def suggest_variations(seed: str, location_code: int | None = None, limit: int = 200) -> list[dict]:
    """Long-tail n-gram variations via keyword_suggestions."""
    from dataforseo_client import keyword_suggestions
    raw = keyword_suggestions(seed, location_code=location_code, limit=limit)
    return [_normalize_idea(r) for r in raw]


def enrich_difficulty(keywords: list[str], location_code: int | None = None, batch_max: int = 1000) -> list[dict]:
    """Bulk KD check. Chunks >batch_max input into multiple API calls."""
    from dataforseo_client import bulk_keyword_difficulty
    if not keywords:
        return []
    out: list[dict] = []
    for i in range(0, len(keywords), batch_max):
        chunk = keywords[i:i + batch_max]
        try:
            rows = bulk_keyword_difficulty(chunk, location_code=location_code)
        except Exception as e:
            logger.warning("bulk_keyword_difficulty failed on chunk starting %d: %s", i, e)
            continue
        for r in rows:
            out.append({
                "keyword":    r.get("keyword") or "",
                "difficulty": r.get("keyword_difficulty"),
            })
    return out


def competitor_gap(
    property_domain: str,
    competitor_domain: str,
    max_difficulty: int = 60,
    min_volume: int = 30,
) -> list[dict]:
    """Interactive sibling of content_planner.semantic_gaps — keywords the
    competitor ranks for that the property doesn't.

    More permissive defaults than semantic_gaps (KD 60, vol 30) since this is
    an on-demand user query, not a filtered auto-surfaced recommendation.
    """
    from dataforseo_client import domain_intersection

    try:
        rows = domain_intersection(target1=property_domain, target2=competitor_domain, exclude_top_domain=True)
    except Exception as e:
        logger.warning("domain_intersection failed: %s", e)
        return []

    out: list[dict] = []
    for row in rows:
        kw_data = row.get("keyword_data") or {}
        kw_info = kw_data.get("keyword_info") or {}
        kw_props = kw_data.get("keyword_properties") or {}
        keyword = row.get("keyword") or kw_data.get("keyword")
        if not keyword:
            continue
        difficulty = kw_props.get("keyword_difficulty") or 0
        volume = kw_info.get("search_volume") or 0
        if difficulty > max_difficulty or volume < min_volume:
            continue
        out.append({
            "keyword":    keyword,
            "volume":     int(volume),
            "difficulty": int(difficulty),
            "cpc":        round(kw_info.get("cpc") or 0, 2),
        })
    out.sort(key=lambda r: r["volume"], reverse=True)
    return out


def seeds_from_brief(
    neighborhoods: list[str],
    landmarks: list[str],
    units: list[str],
    competitors: list[str],
    city: str = "",
) -> list[str]:
    """Compose local-intent keyword seeds from confirmed brief fields.

    Produces combinations like "midtown 1 bedroom apartments", "studio
    apartments near central park", "property name alternative". Caller
    feeds these into expand_seed() for volume/KD enrichment and
    keyword_planner_lookup() for Paid metrics.
    """
    seeds: list[str] = []
    units_clean = [u.strip().lower() for u in units if u and u.strip()]
    neighborhoods_clean = [n.strip() for n in neighborhoods if n and n.strip()]
    landmarks_clean = [l.strip() for l in landmarks if l and l.strip()]
    competitors_clean = [c.strip() for c in competitors if c and c.strip()]

    # Neighborhood × unit
    for n in neighborhoods_clean:
        seeds.append(f"{n} apartments")
        for u in units_clean:
            seeds.append(f"{n} {u} apartments")
            seeds.append(f"{u} in {n}")

    # Landmark-adjacent searches
    for l in landmarks_clean:
        seeds.append(f"apartments near {l}")

    # City-level fallback if we have a city but no neighborhoods
    if city and not neighborhoods_clean:
        seeds.append(f"{city} apartments")
        for u in units_clean:
            seeds.append(f"{city} {u} apartments")

    # Competitor-branded seeds (classified as paid_only downstream)
    for c in competitors_clean:
        seeds.append(f"{c} alternative")
        seeds.append(f"{c} reviews")

    # Dedupe preserving order
    seen: set[str] = set()
    out: list[str] = []
    for s in seeds:
        s = " ".join(s.split()).lower()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def save_to_tracked(property_uuid: str, keywords: list[dict]) -> int:
    """Bulk-save picked keywords into rpm_seo_keywords HubDB.

    Args:
        property_uuid: RPM property UUID
        keywords: list of {keyword, priority?, intent?, tag?, branded?, target_position?}

    Returns count of successfully inserted rows. Publishes table once at end.
    Also invalidates the SEO dashboard cache so the new keywords show up.
    """
    from config import HUBDB_SEO_KEYWORDS_TABLE_ID
    from hubdb_helpers import insert_row, publish

    if not HUBDB_SEO_KEYWORDS_TABLE_ID:
        return 0

    inserted = 0
    for k in keywords:
        kw = (k.get("keyword") or "").strip()
        if not kw:
            continue
        values = {
            "property_uuid":   property_uuid,
            "keyword":         kw,
            "priority":        k.get("priority") or "medium",
            "intent":          k.get("intent") or "",
            "tag":             k.get("tag") or "",
            "branded":         bool(k.get("branded", False)),
            "target_position": k.get("target_position"),
        }
        try:
            insert_row(HUBDB_SEO_KEYWORDS_TABLE_ID, values)
            inserted += 1
        except Exception as e:
            logger.warning("save_to_tracked: insert failed for %s: %s", kw, e)

    if inserted:
        try:
            publish(HUBDB_SEO_KEYWORDS_TABLE_ID)
        except Exception as e:
            logger.warning("save_to_tracked: publish failed: %s", e)
        # Invalidate the Phase 1 SEO dashboard cache so new keywords appear
        try:
            from seo_dashboard import invalidate
            invalidate(property_uuid)
        except Exception:
            pass

    return inserted
