"""ApartmentIQ API Client — Fetch comp data for video script generation.

Base URL: https://data.apartmentiq.io/apartmentiq/api/v1
Auth: Bearer token via Authorization header
Rate limit: 100 requests / 5 minutes

Key endpoints used:
- /properties/bulk_details   → property performance + physical details
- /comp_sets/{id}/market_survey → comp set rent/occupancy data
- /markets/narratives         → market narrative summary
"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://data.apartmentiq.io/apartmentiq/api/v1"
APTIQ_TOKEN = os.getenv("ApartmentIQ_Token", "")


def _headers() -> dict:
    return {"Authorization": f"Bearer {APTIQ_TOKEN}"}


# ─── Property Details ────────────────────────────────────────────────────────

def get_property_details(property_id: str) -> dict | None:
    """Fetch full property details from ApartmentIQ.

    Returns dict with: address, year_built, total_units, property_class,
    asking_rent, ner, occupancy, exposure, available_units_count, etc.
    """
    if not APTIQ_TOKEN:
        logger.warning("ApartmentIQ_Token not configured")
        return None

    url = f"{BASE_URL}/properties/bulk_details"
    try:
        resp = requests.get(
            url,
            headers=_headers(),
            params={"property_ids": property_id},
            timeout=15,
        )
        if resp.status_code == 429:
            logger.warning("ApartmentIQ rate limit hit")
            return None
        resp.raise_for_status()
        data = resp.json()
        # bulk_details returns a list — get first result
        results = data if isinstance(data, list) else data.get("data", data.get("properties", []))
        if isinstance(results, list) and results:
            return results[0]
        elif isinstance(results, dict):
            return results
        return data
    except Exception as exc:
        logger.error("ApartmentIQ property details failed: %s", exc)
        return None


# ─── Market Survey (Comp Set) ────────────────────────────────────────────────

def get_market_survey(comp_set_id: str, bedroom_count: int | None = None) -> list[dict]:
    """Fetch market survey data for a comp set.

    Returns list of comp properties with rent, occupancy, unit details.
    """
    if not APTIQ_TOKEN:
        return []

    url = f"{BASE_URL}/comp_sets/{comp_set_id}/market_survey"
    params = {}
    if bedroom_count is not None:
        params["filter[bedroom_count]"] = bedroom_count

    try:
        resp = requests.get(url, headers=_headers(), params=params, timeout=15)
        if resp.status_code in (403, 404):
            logger.info("Comp set %s not accessible", comp_set_id)
            return []
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else data.get("data", [])
    except Exception as exc:
        logger.error("ApartmentIQ market survey failed: %s", exc)
        return []


# ─── Market Narrative ────────────────────────────────────────────────────────

def get_market_narrative(market_id: str) -> dict | None:
    """Fetch structured market analysis narrative.

    Returns dict with sections: overview, rent_performance, occupancy,
    pipeline, transactions, demographics.
    """
    if not APTIQ_TOKEN:
        return None

    url = f"{BASE_URL}/markets/narratives"
    try:
        resp = requests.get(
            url,
            headers=_headers(),
            params={"geo_boundary_id": market_id},
            timeout=15,
        )
        if resp.status_code in (403, 404):
            logger.info("Market narrative not available for %s", market_id)
            return None
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.error("ApartmentIQ market narrative failed: %s", exc)
        return None


# ─── Aggregated context for script generation ────────────────────────────────

def get_comp_context(aptiq_property_id: str, aptiq_market_id: str) -> dict:
    """Fetch all relevant ApartmentIQ data for a property and its market.

    Returns a structured dict with property performance, comp positioning,
    and market narrative — ready to feed into Claude for script generation.
    """
    context = {
        "property": None,
        "market_narrative": None,
        "comp_summary": None,
    }

    # 1. Property details
    if aptiq_property_id:
        prop = get_property_details(aptiq_property_id)
        if prop:
            context["property"] = {
                "asking_rent":       prop.get("asking_rent"),
                "ner":               prop.get("ner"),
                "rent_psf":          prop.get("rent_psf"),
                "occupancy":         prop.get("occupancy"),
                "exposure":          prop.get("exposure"),
                "leased_percent":    prop.get("leased_percent"),
                "total_units":       prop.get("total_units"),
                "year_built":        prop.get("year_built"),
                "property_class":    prop.get("property_class"),
                "property_type":     prop.get("property_type"),
                "available_units":   prop.get("available_units_count"),
                "market_name":       prop.get("market_name"),
                "submarket_name":    prop.get("submarket_name"),
            }

    # 2. Market narrative
    if aptiq_market_id:
        narrative = get_market_narrative(aptiq_market_id)
        if narrative:
            context["market_narrative"] = narrative

    return context


def format_comp_context_for_prompt(context: dict) -> str:
    """Format ApartmentIQ data into a readable prompt section for Claude.

    IMPORTANT: Strips all pricing/rent data before output — the video script
    must NEVER mention pricing, but Claude can use occupancy, demand signals,
    and market positioning to write a more targeted script.
    """
    lines = []

    prop = context.get("property")
    if prop:
        lines.append("MARKET INTELLIGENCE (from ApartmentIQ):")
        # Occupancy & demand — safe to reference in scripts
        if prop.get("occupancy") is not None:
            lines.append(f"  Property occupancy: {prop['occupancy']}%")
        if prop.get("leased_percent") is not None:
            lines.append(f"  Leased: {prop['leased_percent']}%")
        if prop.get("exposure") is not None:
            lines.append(f"  Exposure rate: {prop['exposure']}%")
        if prop.get("available_units") is not None:
            lines.append(f"  Available units: {prop['available_units']}")
        # Property characteristics
        if prop.get("year_built"):
            lines.append(f"  Year built: {prop['year_built']}")
        if prop.get("property_class"):
            lines.append(f"  Class: {prop['property_class']}")
        if prop.get("submarket_name"):
            lines.append(f"  Submarket: {prop['submarket_name']}")
        if prop.get("market_name"):
            lines.append(f"  Market: {prop['market_name']}")
        # Rent POSITIONING only (not actual amounts) — tell Claude how we compare
        # We include this as context but remind Claude it cannot mention pricing
        lines.append("")
        lines.append("  NOTE: Use occupancy/demand signals to convey urgency.")
        lines.append("  High occupancy → 'homes are going fast', 'limited availability'")
        lines.append("  High leased % → 'join a thriving community'")
        lines.append("  Low exposure → 'don't miss your chance'")

    narrative = context.get("market_narrative")
    if narrative:
        lines.append("")
        lines.append("MARKET NARRATIVE:")
        # Extract key sections if structured
        if isinstance(narrative, dict):
            for section in ["overview", "occupancy", "demographics"]:
                val = narrative.get(section)
                if val:
                    lines.append(f"  {section.title()}: {str(val)[:300]}")
        elif isinstance(narrative, str):
            lines.append(f"  {narrative[:600]}")

    if not lines:
        return ""

    return "\n".join(lines)
