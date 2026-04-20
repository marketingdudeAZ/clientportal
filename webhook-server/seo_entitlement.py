"""Tier gating for SEO portal features.

Source of truth for 'does this company have SEO, and at which tier'. Checks:
  1. Line item SKU `SEO_Package` on the company's latest deal → use monthly_price → tier.
  2. Fallback to company property `seo_budget` → nearest tier.
  3. Returns None if no signal (customer not on SEO).
"""

import logging

import requests

from config import (
    HUBSPOT_API_KEY,
    SEO_FEATURE_MIN_TIER,
    SEO_TIER_ORDER,
    SEO_TIERS,
)

logger = logging.getLogger(__name__)

_HS_HDRS = {
    "Authorization": f"Bearer {HUBSPOT_API_KEY}",
    "Content-Type": "application/json",
}

SEO_PACKAGE_SKUS = ("SEO_Package", "SEO Package")


def _amount_to_tier(amount: float) -> str | None:
    """Map a monthly amount to the nearest SEO tier by closest price."""
    if amount <= 0:
        return None
    tiers = list(SEO_TIERS.items())
    tiers.sort(key=lambda kv: abs(kv[1] - amount))
    return tiers[0][0]


def _latest_deal_id(company_id: str) -> str | None:
    url = (
        f"https://api.hubapi.com/crm/v4/objects/companies/{company_id}"
        f"/associations/deals?limit=100"
    )
    try:
        r = requests.get(url, headers=_HS_HDRS, timeout=10)
        r.raise_for_status()
    except requests.RequestException as e:
        logger.warning("Failed to list deals for %s: %s", company_id, e)
        return None
    assocs = r.json().get("results", [])
    if not assocs:
        return None
    deal_ids = [a["toObjectId"] for a in assocs]
    return _most_recent_deal(deal_ids)


def _most_recent_deal(deal_ids: list[str]) -> str | None:
    if not deal_ids:
        return None
    body = {
        "inputs": [{"id": d} for d in deal_ids],
        "properties": ["createdate", "dealstage"],
    }
    try:
        r = requests.post(
            "https://api.hubapi.com/crm/v3/objects/deals/batch/read",
            headers=_HS_HDRS,
            json=body,
            timeout=15,
        )
        r.raise_for_status()
    except requests.RequestException as e:
        logger.warning("Failed to batch-read deals: %s", e)
        return None
    deals = r.json().get("results", [])
    deals.sort(key=lambda d: d.get("properties", {}).get("createdate", ""), reverse=True)
    return deals[0]["id"] if deals else None


def _seo_line_item_amount(deal_id: str) -> float | None:
    assoc_url = (
        f"https://api.hubapi.com/crm/v4/objects/deals/{deal_id}"
        f"/associations/line_items?limit=100"
    )
    try:
        r = requests.get(assoc_url, headers=_HS_HDRS, timeout=10)
        r.raise_for_status()
    except requests.RequestException:
        return None
    li_ids = [a["toObjectId"] for a in r.json().get("results", [])]
    if not li_ids:
        return None

    body = {
        "inputs": [{"id": lid} for lid in li_ids],
        "properties": ["hs_sku", "name", "price", "amount"],
    }
    try:
        r = requests.post(
            "https://api.hubapi.com/crm/v3/objects/line_items/batch/read",
            headers=_HS_HDRS,
            json=body,
            timeout=15,
        )
        r.raise_for_status()
    except requests.RequestException:
        return None
    for li in r.json().get("results", []):
        props = li.get("properties", {})
        sku = (props.get("hs_sku") or props.get("name") or "").strip()
        if sku in SEO_PACKAGE_SKUS:
            try:
                return float(props.get("price") or props.get("amount") or 0)
            except (TypeError, ValueError):
                return None
    return None


def _company_seo_budget(company_id: str) -> float | None:
    url = (
        f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}"
        f"?properties=seo_budget"
    )
    try:
        r = requests.get(url, headers=_HS_HDRS, timeout=10)
        r.raise_for_status()
    except requests.RequestException:
        return None
    val = r.json().get("properties", {}).get("seo_budget")
    try:
        return float(val) if val else None
    except (TypeError, ValueError):
        return None


def get_seo_tier(company_id: str) -> str | None:
    """Return tier name ('Local'..'Premium') or None if no SEO package."""
    if not company_id:
        return None

    deal_id = _latest_deal_id(company_id)
    if deal_id:
        amount = _seo_line_item_amount(deal_id)
        if amount:
            return _amount_to_tier(amount)

    budget = _company_seo_budget(company_id)
    if budget:
        return _amount_to_tier(budget)

    return None


def meets_tier(current: str | None, minimum: str) -> bool:
    """Ordered compare: is `current` >= `minimum` in SEO_TIER_ORDER?"""
    if not current:
        return False
    try:
        return SEO_TIER_ORDER.index(current) >= SEO_TIER_ORDER.index(minimum)
    except ValueError:
        return False


def has_feature(tier: str | None, feature: str) -> bool:
    """Whether `tier` entitles the client to a named SEO feature."""
    min_tier = SEO_FEATURE_MIN_TIER.get(feature)
    if not min_tier:
        return False
    return meets_tier(tier, min_tier)
