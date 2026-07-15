"""Onboarding completeness for the 'What needs you' pipeline.

Lists properties in Onboarding status with a completeness checklist — brief
filled, budget set, creative/photos uploaded, Google Business Profile connected.
Feeds the onboarding-wizard review AND the product-market-fit measurement
(% of properties completing the value-driving events over time — the E/T
leading indicator).
"""
from __future__ import annotations

import logging

import requests

from config import HUBSPOT_API_KEY

logger = logging.getLogger(__name__)
HS = "https://api.hubapi.com"

# Core brief fields — "brief done" if a healthy share of these are filled.
_BRIEF_FIELDS = ["property_voice_and_tone", "property_tag_lines",
                 "what_makes_this_property_unique_", "primary_competitors",
                 "overarching_goals", "units_offered"]
_GBP_FIELDS = ["gbp_resource_name", "google_business_profile_url", "property_s_gbp_link"]
_PROPS = (["name", "rpmmarket", "city", "state", "uuid", "totalunits", "plestatus",
           "managementstart", "hubspot_owner_id"] + _BRIEF_FIELDS + _GBP_FIELDS)


def _hdrs():
    return {"Authorization": f"Bearer {HUBSPOT_API_KEY}", "Content-Type": "application/json"}


def _has(v):
    return bool(str(v or "").strip())


def list_onboarding(max_asset_checks=60):
    """Companies in Onboarding status, each with a completeness checklist.

    checklist = {brief, budget, creative, gbp} (bools). Sorted incomplete-first.
    """
    companies = []
    body = {
        "filterGroups": [{"filters": [{"propertyName": "plestatus", "operator": "EQ", "value": "Onboarding"}]}],
        "properties": _PROPS, "limit": 100,
        "sorts": [{"propertyName": "managementstart", "direction": "DESCENDING"}],
    }
    after = None
    for _ in range(20):
        if after:
            body["after"] = after
        try:
            r = requests.post(f"{HS}/crm/v3/objects/companies/search", headers=_hdrs(), json=body, timeout=20)
        except requests.RequestException as e:
            logger.warning("onboarding search error: %s", e)
            break
        if not r.ok:
            logger.warning("onboarding search failed: %s %s", r.status_code, r.text[:200])
            break
        data = r.json()
        companies += data.get("results", [])
        after = (data.get("paging", {}).get("next", {}) or {}).get("after")
        if not after:
            break

    # Budget = digital spend (media/SEO/reputation) > 0 — reuse the deal engine.
    try:
        from portfolio import _digital_spend_by_company
        spend = _digital_spend_by_company()
    except Exception as e:
        logger.warning("onboarding: digital spend map unavailable (%s)", e)
        spend = {}

    out = []
    asset_checks = 0
    for c in companies:
        cid = c["id"]
        p = c.get("properties", {})
        uuid = str(p.get("uuid") or "").strip()
        if not uuid:
            continue  # never surface a uuid=null record
        brief_done = sum(1 for f in _BRIEF_FIELDS if _has(p.get(f))) >= 3
        budget_done = (spend.get(cid, 0) or 0) > 0
        gbp_done = any(_has(p.get(f)) for f in _GBP_FIELDS)
        creative_done = False
        if asset_checks < max_asset_checks:
            asset_checks += 1
            try:
                from video_generator import fetch_property_assets
                creative_done = len(fetch_property_assets(uuid)) > 0
            except Exception:
                creative_done = False
        checklist = {"brief": brief_done, "budget": budget_done,
                     "creative": creative_done, "gbp": gbp_done}
        done = sum(1 for v in checklist.values() if v)
        out.append({
            "company_id": cid, "uuid": uuid, "name": p.get("name") or "Unknown",
            "market": p.get("rpmmarket") or "", "checklist": checklist,
            "done": done, "total": 4, "owner_id": p.get("hubspot_owner_id") or "",
        })
    out.sort(key=lambda r: r["done"])  # least-complete first
    return out
