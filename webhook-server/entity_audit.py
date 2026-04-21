"""Phase 2 — Entity audit + schema.org recommendations.

iPullRank methodology: entities are the primary signal Google uses for topical
authority, and entity coverage drives LLM citation (GEO/AEO). This module:

1. Extracts entities from a URL via DataForSEO on_page content_parsing.
2. Diffs a target page's entities against top competitors — surfaces the
   "entities my competitors mention that I don't".
3. Recommends missing schema.org types for the property's page.

Called from content_brief_writer (to enrich the brief with entity targets) and
from the /api/content/clusters route when the AM wants an entity-gap report.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Schema types that matter most for apartments / multifamily pages.
APARTMENT_SCHEMA_TYPES = [
    "ApartmentComplex",
    "Apartment",
    "LocalBusiness",
    "Place",
    "FAQPage",
    "BreadcrumbList",
    "PostalAddress",
    "ImageGallery",
]


def extract_entities(url: str) -> list[dict]:
    """Return entities array from DataForSEO on_page content_parsing.

    Shape: [{name, type, salience, mentions}] where salience 0-1.
    Returns empty list on error so callers can keep going.
    """
    from dataforseo_client import onpage_content_parsing

    try:
        result = onpage_content_parsing(url)
    except Exception as e:
        logger.warning("onpage_content_parsing failed for %s: %s", url, e)
        return []

    # Result structure: {items: [{page_content: {..., entities: [...]}}]}
    items = result.get("items") or []
    if not items:
        return []
    page_content = items[0].get("page_content") or {}
    entities = page_content.get("entities") or []

    # Normalize the field names we care about
    normalized: list[dict] = []
    for e in entities:
        name = (e.get("name") or e.get("text") or "").strip()
        if not name:
            continue
        normalized.append({
            "name":     name,
            "type":     e.get("type") or e.get("entity_type") or "UNKNOWN",
            "salience": round(e.get("salience") or 0.0, 3),
            "mentions": int(e.get("mentions") or e.get("count") or 1),
        })
    return normalized


def audit_page(url: str, competitor_urls: list[str]) -> dict:
    """Return entity-gap analysis for `url` vs up to N competitor URLs.

    Returns:
        {
            target_entities:  [{name, type, salience, mentions}, ...],
            competitor_entities: {comp_url: [entities]},
            gaps: [{name, type, avg_salience, appears_in_n_competitors}, ...]  # sorted by salience desc
        }

    A "gap" is an entity that appears on 2+ competitor pages but NOT on the
    target page. Sorted by average competitor salience (descending).
    """
    from collections import defaultdict

    target_entities = extract_entities(url)
    target_names_lower = {e["name"].lower() for e in target_entities}

    comp_map: dict[str, list[dict]] = {}
    for comp_url in competitor_urls:
        comp_map[comp_url] = extract_entities(comp_url)

    # Aggregate competitor entities
    by_name: dict[str, dict] = defaultdict(lambda: {"saliences": [], "types": [], "count": 0})
    for comp_url, ents in comp_map.items():
        seen_here: set[str] = set()
        for e in ents:
            key = e["name"].lower()
            if key in seen_here:
                continue
            seen_here.add(key)
            by_name[key]["saliences"].append(e["salience"])
            by_name[key]["types"].append(e["type"])
            by_name[key]["count"] += 1
            by_name[key]["display_name"] = e["name"]

    gaps: list[dict] = []
    for key, agg in by_name.items():
        if key in target_names_lower:
            continue
        if agg["count"] < 2:
            continue  # only flag if 2+ competitors mention it
        gaps.append({
            "name":                       agg.get("display_name", key),
            "type":                       max(set(agg["types"]), key=agg["types"].count),  # modal type
            "avg_salience":               round(sum(agg["saliences"]) / len(agg["saliences"]), 3),
            "appears_in_n_competitors":   agg["count"],
        })
    gaps.sort(key=lambda g: g["avg_salience"], reverse=True)

    return {
        "target_entities":      target_entities,
        "competitor_entities":  comp_map,
        "gaps":                 gaps,
    }


def recommend_schema(url: str, property_type: str = "ApartmentComplex") -> dict:
    """Check which schema.org types are present on the URL; recommend missing.

    Returns:
        {
            present:      [str],
            missing:      [str],
            templates:    {type: suggested_jsonld_dict}
        }
    """
    from dataforseo_client import onpage_content_parsing

    try:
        result = onpage_content_parsing(url)
    except Exception as e:
        logger.warning("schema recommend: onpage_content_parsing failed for %s: %s", url, e)
        return {"present": [], "missing": APARTMENT_SCHEMA_TYPES, "templates": _schema_templates(url, property_type)}

    items = result.get("items") or []
    page_meta = (items[0].get("meta") or {}) if items else {}
    # DataForSEO surfaces detected schema under meta.schema or meta.htags.schema_org
    detected: list[str] = []
    for s in (page_meta.get("schema") or []):
        t = s.get("@type") or s.get("type")
        if t:
            if isinstance(t, list):
                detected.extend(t)
            else:
                detected.append(t)
    # Also peek at raw JSON-LD blocks if present
    for block in (page_meta.get("json_ld") or []):
        t = block.get("@type") or block.get("type")
        if t:
            if isinstance(t, list):
                detected.extend(t)
            else:
                detected.append(t)

    present = sorted(set(detected))
    missing = [t for t in APARTMENT_SCHEMA_TYPES if t not in present]

    return {
        "present":   present,
        "missing":   missing,
        "templates": _schema_templates(url, property_type, missing_only=missing),
    }


def _schema_templates(url: str, property_type: str, missing_only: list[str] | None = None) -> dict:
    """Starter JSON-LD blocks the AM can paste into the page's <head>.

    These are templates — the AM fills in real values (address, name, etc).
    """
    all_templates = {
        "ApartmentComplex": {
            "@context":      "https://schema.org",
            "@type":         "ApartmentComplex",
            "name":          "{{ property_name }}",
            "url":           url,
            "address":       {
                "@type":           "PostalAddress",
                "streetAddress":   "{{ street }}",
                "addressLocality": "{{ city }}",
                "addressRegion":   "{{ state }}",
                "postalCode":      "{{ zip }}",
            },
            "telephone":        "{{ phone }}",
            "numberOfUnits":    "{{ total_units }}",
            "petsAllowed":      True,
        },
        "LocalBusiness": {
            "@context":      "https://schema.org",
            "@type":         "LocalBusiness",
            "name":          "{{ property_name }}",
            "url":           url,
            "priceRange":    "{{ price_range }}",
        },
        "Place": {
            "@context":      "https://schema.org",
            "@type":         "Place",
            "name":          "{{ property_name }}",
            "hasMap":        "{{ google_maps_url }}",
        },
        "FAQPage": {
            "@context":   "https://schema.org",
            "@type":      "FAQPage",
            "mainEntity": [
                {"@type": "Question", "name": "{{ question }}",
                 "acceptedAnswer": {"@type": "Answer", "text": "{{ answer }}"}}
            ],
        },
        "BreadcrumbList": {
            "@context":        "https://schema.org",
            "@type":           "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "Home",   "item": "{{ home_url }}"},
                {"@type": "ListItem", "position": 2, "name": "{{ city }}", "item": "{{ city_url }}"},
                {"@type": "ListItem", "position": 3, "name": "{{ property_name }}"},
            ],
        },
    }
    if missing_only is not None:
        return {t: v for t, v in all_templates.items() if t in missing_only}
    return all_templates
