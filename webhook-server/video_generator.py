"""Video Generator — Brief → Script → Asset-Matched Creatify Submission

Orchestrates the full video creation flow:
1. Fetch property-specific assets from HubDB (isolated by property_uuid)
2. Call Claude to generate a voiceover script + visual asset plan
3. Submit to Creatify with the script and matched media URLs
4. Store variant data on the HubSpot company record
"""

import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
import anthropic

from config import (
    ANTHROPIC_API_KEY,
    CLAUDE_AGENT_MODEL,
    HUBDB_ASSET_TABLE_ID,
    HUBSPOT_API_KEY,
)
from video_pipeline_config import (
    SCRIPT_WITH_ASSETS_SYSTEM_PROMPT,
    build_script_prompt,
    validate_script,
)
from creatify_client import build_variants_for_brief

logger = logging.getLogger(__name__)

# Media file types we send to Creatify
_VISUAL_FILE_TYPES = {"jpg", "jpeg", "png", "webp", "mp4", "mov"}

HS_HEADERS = {"Authorization": f"Bearer {HUBSPOT_API_KEY}"}


# ─── 1. Fetch property assets from HubDB ────────────────────────────────────

def fetch_property_assets(company_id: str) -> list[dict]:
    """Return all live visual assets for a property from HubDB.

    Each asset dict has: file_url, asset_name, category, subcategory,
    file_type, description.  Only images and videos are returned.
    Results are isolated to the given company_id (property_uuid).
    """
    if not HUBDB_ASSET_TABLE_ID:
        logger.warning("HUBDB_ASSET_TABLE_ID not configured — no assets available")
        return []

    url = (
        f"https://api.hubapi.com/cms/v3/hubdb/tables/{HUBDB_ASSET_TABLE_ID}/rows"
        f"?property_uuid__eq={company_id}&status__eq=live&limit=100"
    )
    try:
        resp = requests.get(url, headers=HS_HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as exc:
        logger.error("Failed to fetch assets for %s: %s", company_id, exc)
        return []

    assets = []
    for row in resp.json().get("results", []):
        vals = row.get("values", {})
        file_type = (vals.get("file_type") or "").lower().strip(".")
        if file_type not in _VISUAL_FILE_TYPES:
            continue
        assets.append({
            "file_url":    vals.get("file_url", ""),
            "asset_name":  vals.get("asset_name", ""),
            "category":    vals.get("category", ""),
            "subcategory": vals.get("subcategory", ""),
            "file_type":   file_type,
            "description": vals.get("description", ""),
        })

    logger.info("Fetched %d visual assets for property %s", len(assets), company_id)
    return assets


# ─── 2. Build asset inventory for Claude ─────────────────────────────────────

def _build_asset_inventory(assets: list[dict]) -> str:
    """Format assets into a numbered inventory string for the Claude prompt."""
    if not assets:
        return "No assets available. Creatify will use property website imagery."

    lines = ["ASSET INVENTORY (use these exact URLs):"]
    for i, a in enumerate(assets, 1):
        label = a["asset_name"] or "Untitled"
        sub = a["subcategory"] or a["category"] or ""
        desc = a["description"] or ""
        ftype = a["file_type"].upper()
        parts = [f"{i}. [{ftype}] {label}"]
        if sub:
            parts.append(f"({sub})")
        if desc:
            parts.append(f"— {desc}")
        parts.append(f"\n   URL: {a['file_url']}")
        lines.append(" ".join(parts))

    return "\n".join(lines)


# ─── 3. Generate script + asset plan via Claude ─────────────────────────────

def generate_script_with_assets(
    brief: dict,
    property_name: str,
    units: int = 0,
    assets: list[dict] | None = None,
    comp_context: str = "",
) -> dict:
    """Call Claude to generate a voiceover script and select matching assets.

    Args:
        brief: Creative brief dict from HubSpot.
        property_name: Display name of the property.
        units: Unit count.
        assets: Property-specific visual assets from HubDB.
        comp_context: Formatted ApartmentIQ market intelligence string.

    Returns:
        {
            "script": "The voiceover text...",
            "media_urls": ["https://...", ...],
            "media_plan": [{"asset_url": "...", "reason": "..."}, ...],
        }
    """
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")

    # Build the user prompt: brief info + market intelligence + asset inventory
    brief_prompt = build_script_prompt(brief, property_name, units)
    asset_inventory = _build_asset_inventory(assets or [])
    parts = [brief_prompt]
    if comp_context:
        parts.append(comp_context)
    parts.append(asset_inventory)
    user_message = "\n\n".join(parts)

    logger.info("Generating script for %s (%d assets available)", property_name, len(assets or []))

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message = client.messages.create(
        model=CLAUDE_AGENT_MODEL,
        max_tokens=1000,
        temperature=0.4,
        system=SCRIPT_WITH_ASSETS_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    raw = message.content[0].text.strip()

    # Parse JSON response
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        # Try to extract JSON from possible markdown fences
        import re
        match = re.search(r"\{[\s\S]*\}", raw)
        if match:
            result = json.loads(match.group())
        else:
            logger.error("Claude returned non-JSON: %s", raw[:300])
            raise ValueError("Script generation returned invalid format")

    script = result.get("script", "")
    media_plan = result.get("media_plan", [])

    # Validate script (pricing check, length check)
    validation = validate_script(script)
    if not validation["ok"]:
        logger.warning("Script validation issues: %s", validation["errors"])
        script = validation["cleaned_script"]

    # Extract ordered media URLs from the plan
    # Only include URLs that are actually in our asset inventory (safety check)
    valid_urls = {a["file_url"] for a in (assets or [])}
    media_urls = []
    for entry in media_plan:
        url = entry.get("asset_url", "")
        if url in valid_urls:
            media_urls.append(url)
        else:
            logger.warning("Claude suggested unknown asset URL: %s", url[:80])

    logger.info("Script generated: %d words, %d matched assets", len(script.split()), len(media_urls))

    return {
        "script": script,
        "media_urls": media_urls,
        "media_plan": media_plan,
    }


# ─── 4. Orchestrator: full generation pipeline ──────────────────────────────

def generate_videos(
    company_id: str,
    property_name: str,
    tier: str,
    brief: dict,
    property_url: str,
    units: int = 0,
    aptiq_property_id: str = "",
    aptiq_market_id: str = "",
) -> list[dict]:
    """End-to-end: brief → AptIQ context → script → asset matching → Creatify.

    Returns list of variant dicts ready to store on HubSpot.
    """
    # 1. Fetch property-specific assets (isolated by property UUID)
    assets = fetch_property_assets(company_id)

    # 2. Fetch ApartmentIQ market intelligence
    comp_context_str = ""
    if aptiq_property_id or aptiq_market_id:
        try:
            from apartmentiq_client import get_comp_context, format_comp_context_for_prompt
            comp_data = get_comp_context(aptiq_property_id, aptiq_market_id)
            comp_context_str = format_comp_context_for_prompt(comp_data)
            if comp_context_str:
                logger.info("ApartmentIQ data loaded for script generation (%d chars)", len(comp_context_str))
        except Exception as exc:
            logger.warning("ApartmentIQ fetch failed (continuing without): %s", exc)

    # 3. Generate script + asset plan via Claude (with market intelligence)
    script_result = generate_script_with_assets(
        brief=brief,
        property_name=property_name,
        units=units,
        assets=assets,
        comp_context=comp_context_str,
    )

    # 3. Prepare brief for Creatify variant builder
    creatify_brief = {
        "script":   script_result["script"],
        "duration": brief.get("duration", 15),
    }

    # 4. Submit to Creatify
    media_urls = script_result["media_urls"] or None
    variants = build_variants_for_brief(
        brief=creatify_brief,
        property_url=property_url,
        tier=tier,
        media_urls=media_urls,
    )

    # Attach the media plan to each variant for transparency
    for v in variants:
        v["media_plan"] = script_result["media_plan"]
        v["script"] = script_result["script"]

    # 5. Store on HubSpot
    import datetime
    cycle_month = datetime.date.today().strftime("%Y-%m")

    try:
        requests.patch(
            f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}",
            headers={**HS_HEADERS, "Content-Type": "application/json"},
            json={"properties": {
                "video_variants_json":     json.dumps(variants),
                "video_cycle_status":      "Processing",
                "video_cycle_month":       cycle_month,
                "video_pipeline_enrolled": "true",
            }},
            timeout=10,
        )
        logger.info("Stored %d variants on company %s", len(variants), company_id)
    except Exception as exc:
        logger.error("Failed to store variants on HubSpot: %s", exc)

    return variants
