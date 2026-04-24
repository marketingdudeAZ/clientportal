"""Onboarding keyword generator — from confirmed brief to SEO + Paid HubDBs.

One public entry point, `generate_for_property`, orchestrating:
  1. Read brief fields from HubSpot company.
  2. Compose local-intent seeds (keyword_research.seeds_from_brief).
  3. Expand + enrich with DataForSEO Labs (SEO metrics).
  4. Fetch Google Ads Keyword Planner metrics (Paid metrics).
  5. Merge rows by keyword string.
  6. Classify each row as seo_target / paid_only / both.
  7. Persist to rpm_seo_keywords and rpm_paid_keywords HubDBs.

Called from the new POST /api/onboarding/keywords/generate Flask route.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


def _split_csv(value: str | None) -> list[str]:
    """HubSpot stores pill-style fields as `a, b; c` — handle both separators."""
    if not value:
        return []
    parts: list[str] = []
    for chunk in value.replace(";", ",").split(","):
        s = chunk.strip()
        if s:
            parts.append(s)
    return parts


def read_brief_context(company_id: str) -> dict:
    """Pull the brief fields we need to seed keyword research."""
    import requests
    from config import HUBSPOT_API_KEY

    props = [
        "name", "domain", "uuid", "rpmmarket", "city", "state",
        "neighborhoods_to_target", "landmarks_near_the_property",
        "units_offered", "primary_competitors",
    ]
    props_param = "&".join(f"properties={p}" for p in props)
    r = requests.get(
        f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}?{props_param}",
        headers={"Authorization": f"Bearer {HUBSPOT_API_KEY}"},
        timeout=10,
    )
    r.raise_for_status()
    p = r.json().get("properties") or {}
    return {
        "name":          p.get("name", ""),
        "domain":        p.get("domain", ""),
        "property_uuid": p.get("uuid", ""),
        "market":        p.get("rpmmarket", ""),
        "city":          p.get("city", ""),
        "state":         p.get("state", ""),
        "neighborhoods": _split_csv(p.get("neighborhoods_to_target")),
        "landmarks":     _split_csv(p.get("landmarks_near_the_property")),
        "units":         _split_csv(p.get("units_offered")),
        "competitors":   _split_csv(p.get("primary_competitors")),
    }


def _merge_seo_and_paid(seo_rows: list[dict], paid_rows: list[dict]) -> list[dict]:
    """Join SEO + Paid rows on keyword string (case-insensitive)."""
    paid_by_kw = {(r.get("keyword") or "").lower(): r for r in paid_rows}
    merged: list[dict] = []
    for s in seo_rows:
        kw_lc = (s.get("keyword") or "").lower()
        p = paid_by_kw.get(kw_lc, {})
        merged.append({
            **s,
            "competition":       p.get("competition", ""),
            "competition_index": p.get("competition_index", 0),
            "cpc_low":           p.get("cpc_low", 0),
            "cpc_high":          p.get("cpc_high", 0),
        })
    return merged


def _persist_seo(property_uuid: str, rows: list[dict]) -> int:
    from keyword_research import save_to_tracked
    formatted = [
        {
            "keyword":         r.get("keyword"),
            "priority":        r.get("priority", "medium"),
            "intent":          r.get("intent", ""),
            "tag":             r.get("source_neighborhood") or "local",
            "branded":         False,
            "target_position": 10,
        }
        for r in rows
        if r.get("keyword")
    ]
    return save_to_tracked(property_uuid, formatted)


def _persist_paid(property_uuid: str, rows: list[dict]) -> int:
    from config import HUBDB_PAID_KEYWORDS_TABLE_ID
    from hubdb_helpers import insert_row, publish

    if not HUBDB_PAID_KEYWORDS_TABLE_ID:
        logger.warning("onboarding_keywords: HUBDB_PAID_KEYWORDS_TABLE_ID not set; skipping Paid persist")
        return 0

    # HubDB DATETIME columns expect epoch milliseconds, not ISO strings —
    # sending ISO silently 400s every row (same bug we fixed in ai_mentions.py).
    now_ms = int(datetime.utcnow().timestamp() * 1000)
    inserted = 0
    for r in rows:
        kw = (r.get("keyword") or "").strip()
        if not kw:
            continue
        values = {
            "property_uuid":     property_uuid,
            "keyword":           kw,
            "match_type":        "phrase",  # default; refinable later in Paid UI
            "priority":          r.get("priority", "medium"),
            "neighborhood":      r.get("source_neighborhood", ""),
            "intent":            r.get("intent", ""),
            "reason":            r.get("reason", ""),
            "cpc_low":           r.get("cpc_low", 0),
            "cpc_high":          r.get("cpc_high", 0),
            "competition_index": r.get("competition_index", 0),
            "generated_at":      now_ms,
            "approved":          False,
        }
        try:
            if insert_row(HUBDB_PAID_KEYWORDS_TABLE_ID, values):
                inserted += 1
        except Exception as e:
            logger.warning("onboarding_keywords: insert failed for %s: %s", kw, e)
    if inserted:
        try:
            publish(HUBDB_PAID_KEYWORDS_TABLE_ID)
        except Exception as e:
            logger.warning("onboarding_keywords: HubDB publish failed: %s", e)
    return inserted


def generate_for_property(
    company_id: str,
    refine_with_claude: bool = False,
) -> dict:
    """Main orchestration. Returns a summary dict for the HTTP response.

    Idempotent-ish: running it twice will insert duplicate rows in HubDB.
    Callers should dedupe upstream (e.g. check if keywords already exist for
    this property before calling) if they care.
    """
    from config import KEYWORD_RESEARCH_MAX_RESULTS
    from dataforseo_client import is_configured, keyword_planner_lookup
    from keyword_classifier import (
        LABEL_BOTH, LABEL_PAID, LABEL_SEO, classify,
    )
    from keyword_research import expand_seed, seeds_from_brief

    if not is_configured():
        return {"error": "DataForSEO is not configured"}

    ctx = read_brief_context(company_id)
    property_uuid = ctx["property_uuid"]
    if not property_uuid:
        return {"error": "Property UUID missing on HubSpot company record"}

    # 1. Build seeds.
    seeds = seeds_from_brief(
        neighborhoods=ctx["neighborhoods"],
        landmarks=ctx["landmarks"],
        units=ctx["units"],
        competitors=ctx["competitors"],
        city=ctx["city"],
    )
    if not seeds:
        return {"error": "Brief has no neighborhoods, landmarks, or competitors — cannot generate seeds"}

    # 2. Expand via DataForSEO Labs (SEO metrics: volume + KD + intent).
    seo_rows = expand_seed(seeds, limit=KEYWORD_RESEARCH_MAX_RESULTS)

    # Attach source neighborhood (best-effort — the seed that generated each
    # keyword isn't tracked by expand_seed, but we can match back on substring).
    for r in seo_rows:
        kw_lc = (r.get("keyword") or "").lower()
        for n in ctx["neighborhoods"]:
            if n.lower() in kw_lc:
                r["source_neighborhood"] = n
                break

    # 3. Paid metrics via Google Ads Keyword Planner (through DataForSEO).
    kw_strings = [r["keyword"] for r in seo_rows if r.get("keyword")]
    paid_rows = keyword_planner_lookup(kw_strings)

    # 4. Merge.
    merged = _merge_seo_and_paid(seo_rows, paid_rows)

    # 5. Classify.
    classified = classify(
        merged,
        competitor_brands=ctx["competitors"],
        property_brand=ctx["name"],
        refine_with_claude=refine_with_claude,
    )

    # 6. Route.
    seo_bucket = [r for r in classified if r.get("label") in (LABEL_SEO, LABEL_BOTH)]
    paid_bucket = [r for r in classified if r.get("label") in (LABEL_PAID, LABEL_BOTH)]

    seo_inserted = _persist_seo(property_uuid, seo_bucket)
    paid_inserted = _persist_paid(property_uuid, paid_bucket)

    return {
        "status":         "ok",
        "property_uuid":  property_uuid,
        "seeds_count":    len(seeds),
        "keywords_found": len(classified),
        "seo_inserted":   seo_inserted,
        "paid_inserted":  paid_inserted,
        "label_counts": {
            LABEL_SEO:  sum(1 for r in classified if r.get("label") == LABEL_SEO),
            LABEL_PAID: sum(1 for r in classified if r.get("label") == LABEL_PAID),
            LABEL_BOTH: sum(1 for r in classified if r.get("label") == LABEL_BOTH),
        },
    }
