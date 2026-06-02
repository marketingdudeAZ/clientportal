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
  - ILS LISTING RESEARCH — auto-extracted from public ILS profiles
    (apartments.com, zillow, etc.) including REAL RESIDENT REVIEW EXCERPTS

WEIGHT THE ILS REVIEWS HEAVILY when drafting voice/tone, unique selling
points, and overarching goals. Resident reviews are the most reliable signal
in the input — much more so than website copy (often AI-generated) or pitch
decks (sales-coded). When the website says "luxurious lifestyle" but reviews
mention "the elevator is always broken," the brief should reflect both: the
property's positioning AND a flag that operations may need to align with it.

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


# Subpage path keywords we ALWAYS try (even when not linked from home).
# Most apartment marketing sites use these or very close variants. Each
# variant gets a single HEAD-then-GET attempt; failures are silent.
_SUBPAGE_HINTS = (
    "amenities", "amenity", "features", "floorplans", "floor-plans", "floor_plans",
    "neighborhood", "location", "lifestyle", "community", "about",
)
_SCRAPE_HEADERS = {"User-Agent": "RPMClientPortal-BriefDrafter/1.0"}


def _strip_html(html: str) -> str:
    html = re.sub(r"<script\b[^>]*>.*?</script>", " ", html, flags=re.I | re.S)
    html = re.sub(r"<style\b[^>]*>.*?</style>", " ", html, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()


def _extract_internal_paths(html: str, host: str) -> list[str]:
    """Pull internal URL paths from anchor tags on the homepage."""
    paths: list[str] = []
    seen: set[str] = set()
    # Find every href; cheap regex is fine for our purposes.
    for m in re.finditer(r'href\s*=\s*["\']([^"\']+)["\']', html, flags=re.I):
        raw = m.group(1).strip()
        if not raw or raw.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        # Same-host absolute -> reduce to path; relative -> use as-is.
        if raw.startswith("http://") or raw.startswith("https://"):
            try:
                from urllib.parse import urlparse
                u = urlparse(raw)
                if u.netloc.lower().lstrip("www.") != host.lower().lstrip("www."):
                    continue
                path = u.path or "/"
            except Exception:
                continue
        else:
            path = raw if raw.startswith("/") else "/" + raw
        # Keep it on the same host, drop query/fragment.
        path = path.split("?", 1)[0].split("#", 1)[0].rstrip("/") or "/"
        if path in seen:
            continue
        seen.add(path)
        paths.append(path)
    return paths


def scrape_site_text(domain: str, max_chars: int = 20000) -> str:
    """Fetch the homepage AND a handful of high-signal subpages, return
    concatenated visible text. Best-effort; never raises.

    Why crawl: the homepage rarely lists every amenity/feature. Amenity
    grids, floorplans, neighborhood blurbs, and community pages all
    live behind their own URL. Without them the LLM has to guess.
    Strategy:
      1) Fetch homepage.
      2) Pull internal links from the homepage; keep ones whose path
         hints at amenities/features/floorplans/neighborhood/etc.
      3) Also try a small set of canonical paths even if unlinked
         (covers sites where these pages are in a nav that's
         JavaScript-rendered).
      4) Fetch up to 5 additional pages, cap each at ~2,500 chars,
         and concatenate with `--- PAGE: <path> ---` markers so the
         LLM knows the provenance.
    """
    import requests
    from urllib.parse import urlparse

    base = f"https://{domain}"
    try:
        r = requests.get(base, timeout=15, headers=_SCRAPE_HEADERS, allow_redirects=True)
        r.raise_for_status()
    except Exception as e:
        logger.warning("brief_ai_drafter: site scrape failed for %s: %s", domain, e)
        return ""

    home_html = r.text or ""
    host = urlparse(r.url).netloc or domain
    home_text = _strip_html(home_html)[:6000]

    # Discover subpage candidates.
    linked = _extract_internal_paths(home_html, host)
    keep = []
    for path in linked:
        low = path.lower()
        if any(hint in low for hint in _SUBPAGE_HINTS):
            keep.append(path)
    # Always-try canonical paths (in case nav is JS-rendered).
    for hint in _SUBPAGE_HINTS:
        for candidate in (f"/{hint}", f"/{hint}.html"):
            if candidate not in keep:
                keep.append(candidate)

    # Crawl up to 5 subpages, ~2,500 chars each.
    chunks: list[str] = [f"--- PAGE: / ---\n{home_text}"]
    fetched: set[str] = {"/"}
    for path in keep:
        if len(chunks) > 5 or path in fetched:
            continue
        try:
            rr = requests.get(base + path, timeout=10, headers=_SCRAPE_HEADERS, allow_redirects=True)
            if rr.status_code != 200:
                continue
            sub_text = _strip_html(rr.text or "")[:2500]
            if not sub_text:
                continue
            chunks.append(f"--- PAGE: {path} ---\n{sub_text}")
            fetched.add(path)
        except Exception:
            continue

    full = "\n\n".join(chunks)
    return full[:max_chars]


def draft_brief(
    domain: str,
    deck_pdf_bytes: bytes | None = None,
    rfp_pdf_bytes: bytes | None = None,
    site_text: str | None = None,
    ils_urls: dict[str, str] | list[str] | None = None,
) -> dict:
    """Call Claude Sonnet and return the drafted brief JSON.

    Args:
        domain: normalized domain (use normalize_domain).
        deck_pdf_bytes: raw bytes of the pitch deck PDF (optional).
        rfp_pdf_bytes: raw bytes of the RFP PDF (optional).
        site_text: pre-scraped homepage text. If None, scrape_site_text is
            called here.
        ils_urls: optional dict ({"apartments_com": url, "zillow": url, ...})
            or list of URLs to also research. Real resident reviews from
            ILS profiles are a strong anti-slop signal — much harder for
            a PMA to AI-fabricate than website copy. See ils_research.py.

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

    # ILS research — best-effort, never raises. Adds resident review quotes
    # and structured amenity/unit data that grounds the brief in something
    # the PMA can't have AI-generated.
    ils_text = ""
    if ils_urls:
        try:
            from ils_research import format_for_prompt, research_ils_listings
            ils_data = research_ils_listings(ils_urls)
            ils_text = format_for_prompt(ils_data)
            logger.info("brief_ai_drafter: ILS research found %d providers, %d quotes",
                        len(ils_data.get("providers") or []),
                        len(ils_data.get("merged_review_quotes") or []))
        except Exception as e:
            logger.warning("brief_ai_drafter: ILS research failed: %s", e)

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

    # ILS research — placed AFTER website text so the website is the cache
    # boundary; ILS data changes more often than website content.
    if ils_text:
        user_content.append({
            "type": "text",
            "text": ils_text,
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


# ─────────────────────────────────────────────────────────────────────────
# Community Brief v2 structured extractor
# ─────────────────────────────────────────────────────────────────────────
#
# Aligned to community_brief.FIELDS — emits values keyed by the logical
# field key so the caller can pass each one straight into
# community_brief.write_field(company_id, key, value). One LLM round-trip
# per property; intentionally focused (only the fields a website can
# plausibly support).

# Logical key → human-readable extraction hint for the LLM. Driven by
# what community_brief.write_field accepts as an editable key. Order is
# meaningful — it's the order Claude sees the fields, which makes the
# higher-value extractions come first in the JSON.
CB_DRAFTABLE = [
    ("property_amenities",
     "Community-level amenities (pool, fitness center, clubhouse, dog park, etc.). "
     "ONE PER LINE. Look hard at /amenities and /features pages — these are the "
     "highest-value extractions. Do NOT include in-unit features here."),
    ("unit_features",
     "In-unit features (stainless appliances, walk-in closets, quartz counters, "
     "in-unit washer/dryer, etc.). ONE PER LINE. Distinct from property amenities."),
    ("marketed_amenity_names",
     "Specific/marketed names of amenities as they appear on the marketing site, "
     "more descriptive than generic categories. Examples: instead of 'pool', use "
     "'Resort-Style Pool with Cabanas'; instead of 'gym', use '24-Hour Fitness "
     "Center with Free Weights'; branded names like 'The Loft' or 'Bark Park' "
     "also belong here. Pull the exact marketing phrases. ONE PER LINE. 5-12 items "
     "when possible."),
    ("amenities_descriptions",
     "Short prose descriptions of the amenity experience that Fluency can pull "
     "from for ad copy — 2-4 sentences total. Capture the property's marketing "
     "voice (e.g. 'A resort-style pool surrounded by lounge cabanas and outdoor "
     "kitchens — your weekend's headquarters.'). Plain prose, NOT a list."),
    ("taglines",
     "Marketing taglines / slogans the property uses in its own copy. ONE PER LINE. "
     "Maximum 3. Skip if no clear tagline appears."),
    ("brand_adjectives",
     "3-5 adjectives that best describe this community's brand voice based on the site. "
     "ONE PER LINE."),
    ("differentiators",
     "What makes this property different from competitors. ONE PER LINE. 2-4 items."),
    ("selling_points",
     "Additional value propositions worth emphasizing in marketing. ONE PER LINE."),
    ("residents_love",
     "What residents would love about living here, grounded in the source material. "
     "ONE PER LINE."),
    ("neighborhood",
     "The single neighborhood the property sits IN (e.g. 'South Congress', not "
     "'Austin'). One short string."),
    ("nearby_neighborhoods",
     "Adjacent / nearby neighborhoods worth name-dropping in marketing. ONE PER LINE."),
    ("landmarks",
     "Landmarks / POIs the property is close to (parks, downtowns, attractions). "
     "ONE PER LINE."),
    ("neighborhood_highlights",
     "Why this location matters — food scene, transit access, vibe, walkability. "
     "ONE PER LINE."),
    ("nearby_employers",
     "Major employers near this property's location that commuters from this "
     "community would plausibly work for. ONE PER LINE. 4-8 items. When the "
     "website explicitly mentions employers, prefer those; otherwise pull "
     "from established general knowledge of the area's major employers given "
     "the confirmed city/neighborhood (e.g. Atlanta → Delta, Coca-Cola, UPS, "
     "Home Depot HQ; Fort Lauderdale → AutoNation, Citrix, Sheridan Healthcare). "
     "Only use this knowledge path when the city is unambiguous from the source."),
    ("advertised_name",
     "The full property name as used in headlines / titles. One short string."),
    ("short_name",
     "A short / abbreviated property name for tight UI. One short string."),
]

_CB_SYSTEM = """You are a marketing analyst extracting structured brief fields from an apartment community's website.

The website text you receive is multi-page: the homepage AND a handful of subpages
(amenities, floorplans, neighborhood, etc.), each prefixed with a `--- PAGE: <path> ---`
marker. PAY ATTENTION to which page each fact came from — amenity pages are
much more reliable than the homepage for amenity lists.

For each field below, return a JSON value (or null) PLUS a confidence score
0.0-1.0. Use null when the source does NOT clearly support an answer — never
guess, never invent specific phone numbers, addresses, or rental prices.

Confidence rubric:
  0.9-1.0  Explicitly stated in the sources (exact phrase or direct claim).
  0.6-0.8  Strongly implied (multiple consistent signals).
  0.3-0.5  Weakly inferred (one soft signal); flag for review.
  0.0-0.2  Not supported — return null for the value.

For LIST-type fields (amenities, taglines, adjectives, etc.) return a SINGLE STRING
with one item per line — NOT a JSON array. Empty string means none found.

For SINGLE STRING fields (neighborhood, advertised_name, short_name) return a short
plain string or null.

Response shape (return ONLY this JSON — no preamble, no code fences):

{
  "<field_key>": {"value": <string-or-null>, "confidence": <0.0-1.0>},
  ...
}

The fields to extract, IN ORDER:

"""


def draft_community_brief_overrides(*, domain: str, property_name: str,
                                     site_text: str | None = None,
                                     min_confidence: float = 0.55) -> dict:
    """Run the LLM to extract structured override values from a property's website.

    Returns a dict keyed by community_brief logical keys — only fields whose
    confidence meets `min_confidence` are included. Values are normalized
    strings ready to PATCH directly via community_brief.write_field.

    Costs one Anthropic round-trip (~$0.02 with caching). Falls back to {}
    on any error so the caller's main flow continues.
    """
    import anthropic
    from config import ANTHROPIC_API_KEY, CLAUDE_AGENT_MODEL

    if not ANTHROPIC_API_KEY:
        logger.info("draft_community_brief_overrides: no anthropic key; skipping")
        return {}

    if site_text is None:
        site_text = scrape_site_text(domain) if domain else ""
    if not site_text:
        logger.info("draft_community_brief_overrides: no site text for %s", domain)
        return {}

    field_lines = [f"  {key}\n    {hint}" for key, hint in CB_DRAFTABLE]
    system = _CB_SYSTEM + "\n".join(field_lines)

    user_content: list[dict] = [
        {"type": "text",
         "text": f"WEBSITE CONTENT from https://{domain} (multi-page, trimmed):\n\n{site_text}",
         "cache_control": {"type": "ephemeral"}},
        {"type": "text",
         "text": f"Property name: {property_name}\n\nReturn ONLY the JSON object."},
    ]

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        resp = client.messages.create(
            model=CLAUDE_AGENT_MODEL,
            max_tokens=2200,
            temperature=0.2,
            system=system,
            messages=[{"role": "user", "content": user_content}],
        )
        raw = "".join(b.text for b in resp.content if hasattr(b, "text"))
    except Exception as e:
        logger.warning("draft_community_brief_overrides: API call failed: %s", e)
        return {}

    # Strip possible code fences and parse.
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```\s*$", "", raw)
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError) as e:
        logger.warning("draft_community_brief_overrides: bad JSON from LLM: %s", e)
        return {}

    out: dict[str, str] = {}
    for key, _hint in CB_DRAFTABLE:
        entry = parsed.get(key)
        if not isinstance(entry, dict):
            continue
        val = entry.get("value")
        conf = entry.get("confidence")
        try:
            conf = float(conf) if conf is not None else 0.0
        except (TypeError, ValueError):
            conf = 0.0
        if val is None or conf < min_confidence:
            continue
        if isinstance(val, list):
            val = "\n".join(str(v).strip() for v in val if str(v).strip())
        val = str(val).strip()
        if val:
            out[key] = val
    logger.info("draft_community_brief_overrides: %s produced %d fields above %.2f",
                domain, len(out), min_confidence)
    return out
