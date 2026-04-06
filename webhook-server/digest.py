"""Phase 6, Steps 16-19: AI-curated property digest.

Fetches current + previous month scores and top insights from BigQuery,
generates a Claude digest using the Section 6.1 prompt, and caches the
result for 24 hours per property UUID.

Cache: stored in HubSpot company property `portal_digest_cache` (text)
as JSON with keys: text, cached_at, property_uuid.
Falls back to a static message if BigQuery or Claude fails.
"""

import json
import logging
import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    ANTHROPIC_API_KEY,
    CLAUDE_DIGEST_MODEL,
    CLAUDE_DIGEST_TEMP,
    CLAUDE_DIGEST_MAX_TOKENS,
    DIGEST_CACHE_HOURS,
    HUBSPOT_API_KEY,
)

logger = logging.getLogger(__name__)

# Section 6.1 system prompt
DIGEST_SYSTEM_PROMPT = """You are an AI assistant for RPM Living's client portal. Write a clear, concise
property health digest for the property marketing contact. Plain language only.
No jargon. No internal system names. Maximum 200 words.

Structure as four short sections:
1. What changed this month (vs last month scores and report data)
2. What is working (positive signals from the data)
3. What needs your input (pending recommendations in the feed)
4. What we are handling (recent completions and open tickets)

Always reference specific RPM services by name where relevant.
Never mention NinjaCat, BigQuery, HubSpot, or any internal tool."""

DIGEST_FALLBACK = "Your property summary is being prepared — check back shortly."

HS_BASE = "https://api.hubapi.com"
HS_HEADERS = {
    "Authorization": f"Bearer {HUBSPOT_API_KEY}",
    "Content-Type": "application/json",
}


def _build_user_message(property_name, rpmmarket, scores, insights, open_recs_count):
    """Build the Section 6.1 user message from BigQuery data."""
    current = scores.get("current") or {}
    previous = scores.get("previous") or {}

    overall = current.get("overall_score", "N/A")
    status = current.get("status", "N/A")
    market = current.get("market_score", "N/A")
    marketing = current.get("marketing_score", "N/A")
    funnel = current.get("funnel_score", "N/A")
    experience = current.get("experience_score", "N/A")

    prev_overall = previous.get("overall_score", "N/A") if previous else "N/A"
    prev_status = previous.get("status", "N/A") if previous else "N/A"

    # Format top findings list
    findings_text = ""
    if insights:
        findings = [i.get("finding", "") for i in insights if i.get("finding")]
        findings_text = "; ".join(findings[:5])
    else:
        findings_text = "No findings available for this period"

    return (
        f"Property: {property_name}. Market: {rpmmarket}.\n"
        f"Red Light score this month: {overall} ({status}).\n"
        f"Subscores: Market {market}, Marketing {marketing}, "
        f"Funnel {funnel}, Experience {experience}.\n"
        f"Last month score: {prev_overall} ({prev_status}).\n"
        f"Key findings this month: {findings_text} (from report insights, "
        f"limit 5, ordered by priority DESC).\n"
        f"Open recommendations awaiting client action: {open_recs_count}.\n"
        f"Write the property digest now."
    )


def _call_claude(user_message):
    """Call Claude with the digest prompt. Returns text string."""
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")

    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message = client.messages.create(
        model=CLAUDE_DIGEST_MODEL,
        max_tokens=CLAUDE_DIGEST_MAX_TOKENS,
        temperature=CLAUDE_DIGEST_TEMP,
        system=DIGEST_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    return message.content[0].text.strip()


def _get_cached_digest(company_id):
    """Fetch cached digest from HubSpot company property portal_digest_cache.

    Returns digest text if cache is fresh (< DIGEST_CACHE_HOURS), else None.
    """
    url = f"{HS_BASE}/crm/v3/objects/companies/{company_id}?properties=portal_digest_cache"
    r = requests.get(url, headers=HS_HEADERS)
    if r.status_code != 200:
        return None

    raw = r.json().get("properties", {}).get("portal_digest_cache")
    if not raw:
        return None

    try:
        cached = json.loads(raw)
        cached_at = cached.get("cached_at", 0)
        age_hours = (time.time() - cached_at) / 3600
        if age_hours < DIGEST_CACHE_HOURS:
            logger.info("Digest cache hit for company %s (age=%.1fh)", company_id, age_hours)
            return cached.get("text")
    except (json.JSONDecodeError, KeyError):
        pass
    return None


def _store_cached_digest(company_id, text):
    """Write digest text to HubSpot company property portal_digest_cache."""
    payload = json.dumps({"text": text, "cached_at": time.time()})
    url = f"{HS_BASE}/crm/v3/objects/companies/{company_id}"
    r = requests.patch(url, headers=HS_HEADERS, json={"properties": {"portal_digest_cache": payload}})
    if r.status_code not in (200, 204):
        logger.warning("Could not store digest cache for %s: %s", company_id, r.status_code)


def get_open_recs_count(property_uuid):
    """Count pending recommendations in HubDB for this property."""
    from config import HUBDB_RECOMMENDATIONS_TABLE_ID
    if not HUBDB_RECOMMENDATIONS_TABLE_ID:
        return 0
    url = (
        f"https://api.hubapi.com/cms/v3/hubdb/tables/{HUBDB_RECOMMENDATIONS_TABLE_ID}/rows"
        f"?property_uuid__eq={property_uuid}&status__eq=pending"
    )
    r = requests.get(url, headers=HS_HEADERS)
    if r.status_code == 200:
        return r.json().get("total", 0)
    return 0


def generate_digest(property_uuid, company_id, property_name, rpmmarket):
    """Generate or return cached property digest.

    Returns string — the digest text to render on the portal.
    Never raises. Falls back to DIGEST_FALLBACK on any error.

    Args:
        property_uuid: RPM UUID (used for BigQuery queries + rec count)
        company_id: HubSpot company record ID (used for cache storage)
        property_name: display name
        rpmmarket: RPM market name
    """
    # Check cache first
    try:
        cached = _get_cached_digest(company_id)
        if cached:
            return cached
    except Exception as e:
        logger.warning("Cache check failed: %s", e)

    try:
        from bigquery_client import get_red_light_current_and_prev, get_top_insights

        scores = get_red_light_current_and_prev(property_uuid)
        insights = get_top_insights(property_uuid, limit=5)
    except Exception as e:
        logger.error("BigQuery fetch failed: %s", e)
        scores = {"current": None, "previous": None}
        insights = []

    # Fall back if no data at all
    if not scores.get("current") and not insights:
        logger.info("No BigQuery data for %s — returning fallback", property_uuid)
        return DIGEST_FALLBACK

    open_recs = get_open_recs_count(property_uuid)

    try:
        user_msg = _build_user_message(property_name, rpmmarket, scores, insights, open_recs)
        text = _call_claude(user_msg)
    except Exception as e:
        logger.error("Claude digest generation failed: %s", e)
        return DIGEST_FALLBACK

    # Store in cache
    try:
        _store_cached_digest(company_id, text)
    except Exception as e:
        logger.warning("Cache write failed (non-fatal): %s", e)

    return text
