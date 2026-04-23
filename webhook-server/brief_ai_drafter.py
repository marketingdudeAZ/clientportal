"""AI-drafted Client Brief — website + pitch deck + RFP → HubSpot field suggestions.

Flow:
    1. Resolve the HubSpot company from a domain/URL (clients don't know their
       company_id — they know the site URL).
    2. Scrape the site homepage for lightweight context.
    3. Pass website text + uploaded PDFs (deck, RFP) as Claude content blocks.
    4. Claude drafts JSON keyed by existing HubSpot property names so the
       portal UI can diff it against the current brief.
    5. A confidence score per field tells the UI how strongly to flag it for
       the client to confirm.

Only the Sonnet API call is here. Route handlers in server.py wrap this in
a background thread + HubDB status row (same async pattern as content briefs).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Existing HubSpot property names the brief maps to — MUST match the keys
# used by /api/client-brief PATCH in server.py so the accept route can
# pass them through unchanged.
DRAFTABLE_FIELDS = [
    "property_voice_and_tone",
    "neighborhoods_to_target",
    "landmarks_near_the_property",
    "property_tag_lines",
    "what_makes_this_property_unique_",
    "brand_adjectives",
    "additional_selling_points",
    "overarching_goals",
    "primary_competitors",
    "units_offered",
]

# Fields that require human knowledge — never draft these.
HUMAN_ONLY_FIELDS = [
    "budget_finalized",
    "property_management_system",
    "website_cms",
    "bot_on_website",
    "challenges_in_the_next_6_8_months_",
    "onsite_upcoming_events",
    "tracking___for_search",
    "tracking___for_social_ads",
    "tracking___for_facebook",
    "tracking___for_display_ads",
    "tracking___for_apple",
]

_DRAFTER_SYSTEM_PROMPT = """You are drafting a Client Brief for an apartment property onboarding to a marketing agency.

You will receive one or more of:
  - Text scraped from the property's website
  - A pitch deck (PDF)
  - An RFP document (PDF)

Produce a JSON object with a draft value + a confidence score (0.0-1.0) for each field below. Use `null` for the value when the source material does not support a confident answer — do not guess or hallucinate.

Confidence rubric:
  0.9-1.0  Explicitly stated in the sources (exact phrase or direct claim).
  0.6-0.8  Strongly implied (multiple consistent signals).
  0.3-0.5  Weakly inferred (one soft signal); flag for client review.
  0.0-0.2  Not supported — return `null` for the value.

Fields (use these EXACT keys):

  property_voice_and_tone            Brand voice descriptor. 1-2 sentences.
  neighborhoods_to_target            Comma-separated list of target neighborhoods.
  landmarks_near_the_property        Comma-separated nearby POIs / landmarks.
  property_tag_lines                 Newline-separated taglines (≤ 3).
  what_makes_this_property_unique_   Differentiators. 1-3 sentences.
  brand_adjectives                   Comma-separated adjectives (3-6).
  additional_selling_points          Extra value props. 1-3 sentences.
  overarching_goals                  Strategic objectives inferred from the material. 1-2 sentences.
  primary_competitors                Comma-separated competitor property names.
  units_offered                      Comma-separated unit types (e.g. "Studio, 1 Bed, 2 Bed, 3 Bed").

Response shape (return ONLY this JSON — no preamble, no markdown fences):

{
  "property_voice_and_tone":           { "value": string | null, "confidence": number },
  "neighborhoods_to_target":           { "value": string | null, "confidence": number },
  ... (one entry per field above)
}

Rules:
  - Prefer specifics to generalities. "Midtown, East Village" beats "Manhattan".
  - For taglines, extract what's on the site — do NOT invent new ones.
  - Competitors: only list properties explicitly named, or clearly positioned as comparisons in the deck.
  - Neighborhoods: only list ones the sources reference; leave null if none are named.
  - Never fabricate addresses, phone numbers, or stats. If it's not in the source, leave it out."""


class BriefDrafterError(Exception):
    """Raised when the Sonnet draft step cannot produce a valid JSON object."""


def normalize_domain(raw: str) -> str:
    """Accept a URL or bare domain; return the lowercase host.

    Clients will paste anything — "https://www.example.com/apartments" or
    "example.com" — strip the scheme, path, and any leading 'www.' so the
    HubSpot company search can match on the `domain` property.
    """
    if not raw:
        return ""
    s = raw.strip().lower()
    s = re.sub(r"^https?://", "", s)
    s = s.split("/", 1)[0]
    s = s.split("?", 1)[0]
    if s.startswith("www."):
        s = s[4:]
    return s


def scrape_site_text(domain: str, max_chars: int = 12000) -> str:
    """Fetch the homepage and return visible text. Best-effort; never raises.

    Keeping this dependency-light and synchronous — the caller is already
    running in a background thread.
    """
    import requests

    url = f"https://{domain}"
    try:
        r = requests.get(
            url,
            timeout=15,
            headers={"User-Agent": "RPMClientPortal-BriefDrafter/1.0"},
            allow_redirects=True,
        )
        r.raise_for_status()
    except Exception as e:
        logger.warning("brief_ai_drafter: site scrape failed for %s: %s", domain, e)
        return ""

    html = r.text or ""
    # Drop script/style blocks, then strip tags — crude but sufficient for
    # the small amount of context Claude needs.
    html = re.sub(r"<script\b[^>]*>.*?</script>", " ", html, flags=re.I | re.S)
    html = re.sub(r"<style\b[^>]*>.*?</style>", " ", html, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


def draft_brief(
    domain: str,
    deck_pdf_bytes: bytes | None = None,
    rfp_pdf_bytes: bytes | None = None,
    site_text: str | None = None,
) -> dict:
    """Call Claude Sonnet and return the drafted brief JSON.

    Args:
        domain: normalized domain (use normalize_domain).
        deck_pdf_bytes: raw bytes of the pitch deck PDF (optional).
        rfp_pdf_bytes: raw bytes of the RFP PDF (optional).
        site_text: pre-scraped homepage text. If None, scrape_site_text is
            called here.

    Returns a dict keyed by DRAFTABLE_FIELDS with {value, confidence} entries.
    Raises BriefDrafterError if Sonnet returns unparseable JSON.
    """
    import anthropic
    import base64
    from config import ANTHROPIC_API_KEY, CLAUDE_AGENT_MODEL

    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")

    if site_text is None:
        site_text = scrape_site_text(domain)

    # Build Claude content blocks. Put stable/large inputs first (system prompt
    # is already cached; site text is reusable across drafts of the same
    # property), and volatile per-request asks last so the cache prefix stays
    # hot for follow-ups.
    user_content: list[dict] = []

    # PDF document blocks — Claude parses them natively.
    if deck_pdf_bytes:
        user_content.append({
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": base64.standard_b64encode(deck_pdf_bytes).decode("ascii"),
            },
            "title": "Pitch Deck",
        })
    if rfp_pdf_bytes:
        user_content.append({
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": base64.standard_b64encode(rfp_pdf_bytes).decode("ascii"),
            },
            "title": "RFP",
        })

    # Website context.
    if site_text:
        user_content.append({
            "type": "text",
            "text": (
                f"WEBSITE CONTENT from https://{domain} (trimmed):\n\n"
                f"{site_text}"
            ),
            "cache_control": {"type": "ephemeral"},
        })
    else:
        user_content.append({
            "type": "text",
            "text": f"(No website text available for {domain}.)",
        })

    user_content.append({
        "type": "text",
        "text": "Draft the client brief JSON now. Return ONLY the JSON object.",
    })

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    logger.info("brief_ai_drafter: drafting for domain=%s (deck=%s rfp=%s site_chars=%d)",
                domain, bool(deck_pdf_bytes), bool(rfp_pdf_bytes), len(site_text or ""))

    message = client.messages.create(
        model=CLAUDE_AGENT_MODEL,
        max_tokens=2500,
        system=[{
            "type": "text",
            "text": _DRAFTER_SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": user_content}],
    )

    raw = next((b.text for b in message.content if b.type == "text"), "").strip()
    draft = _parse_draft_json(raw)
    return _coerce_and_validate(draft)


def _parse_draft_json(raw: str) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", raw)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError as e:
            logger.error("brief_ai_drafter: malformed JSON: %s", raw[:400])
            raise BriefDrafterError(f"Drafter returned invalid JSON: {e}") from e
    raise BriefDrafterError("Drafter returned no parseable JSON object")


def _coerce_and_validate(draft: dict) -> dict:
    """Ensure every expected field is present with a {value, confidence} shape.

    Missing fields default to a null value with 0 confidence, so the UI can
    render the section uniformly without having to special-case "not drafted".
    """
    out: dict[str, dict[str, Any]] = {}
    for field in DRAFTABLE_FIELDS:
        entry = draft.get(field) or {}
        if not isinstance(entry, dict):
            # Model may have returned a bare string; wrap it.
            entry = {"value": entry, "confidence": 0.5}
        value = entry.get("value")
        try:
            confidence = float(entry.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        out[field] = {"value": value, "confidence": round(confidence, 2)}
    return out


# ─── API-key resolver ───────────────────────────────────────────────────────

def resolve_company_by_domain(domain: str) -> dict | None:
    """Look up a HubSpot company by its `domain` property.

    Falls back to searching the `website` URL property if the exact-match on
    `domain` returns nothing (HubSpot portals sometimes only have `website`
    populated).

    Returns {id, name, domain, uuid, rpmmarket, city, state} or None if no
    match. Raises for non-auth HubSpot errors so the caller surfaces a real
    5xx rather than a silent empty draft.
    """
    import requests
    from config import HUBSPOT_API_KEY

    if not domain:
        return None
    domain = normalize_domain(domain)
    if not HUBSPOT_API_KEY:
        logger.warning("brief_ai_drafter: HUBSPOT_API_KEY not set — lookup skipped")
        return None

    hdrs = {
        "Authorization": f"Bearer {HUBSPOT_API_KEY}",
        "Content-Type": "application/json",
    }
    props = ["name", "domain", "website", "uuid", "rpmmarket", "city", "state"]

    def _search(prop: str, value: str) -> dict | None:
        body = {
            "filterGroups": [{
                "filters": [{"propertyName": prop, "operator": "EQ", "value": value}],
            }],
            "properties": props,
            "limit": 5,
        }
        r = requests.post(
            "https://api.hubapi.com/crm/v3/objects/companies/search",
            headers=hdrs, json=body, timeout=10,
        )
        r.raise_for_status()
        results = r.json().get("results") or []
        if not results:
            return None
        # If >1 match, return the first — rare enough that we don't block on
        # disambiguation for v1. Log so we can revisit if it becomes an issue.
        if len(results) > 1:
            logger.info("brief_ai_drafter: %d companies match %s=%s; using first",
                        len(results), prop, value)
        first = results[0]
        p = first.get("properties") or {}
        return {
            "id":        first.get("id"),
            "name":      p.get("name"),
            "domain":    p.get("domain"),
            "website":   p.get("website"),
            "uuid":      p.get("uuid"),
            "rpmmarket": p.get("rpmmarket"),
            "city":      p.get("city"),
            "state":     p.get("state"),
        }

    # Exact domain match first.
    found = _search("domain", domain)
    if found:
        return found

    # Fallback: website URL contains the domain.
    try:
        body = {
            "filterGroups": [{
                "filters": [{"propertyName": "website", "operator": "CONTAINS_TOKEN", "value": domain}],
            }],
            "properties": props,
            "limit": 5,
        }
        r = requests.post(
            "https://api.hubapi.com/crm/v3/objects/companies/search",
            headers=hdrs, json=body, timeout=10,
        )
        r.raise_for_status()
        results = r.json().get("results") or []
        if results:
            first = results[0]
            p = first.get("properties") or {}
            return {
                "id":        first.get("id"),
                "name":      p.get("name"),
                "domain":    p.get("domain"),
                "website":   p.get("website"),
                "uuid":      p.get("uuid"),
                "rpmmarket": p.get("rpmmarket"),
                "city":      p.get("city"),
                "state":     p.get("state"),
            }
    except Exception as e:
        logger.warning("brief_ai_drafter: website-fallback lookup failed: %s", e)

    return None
