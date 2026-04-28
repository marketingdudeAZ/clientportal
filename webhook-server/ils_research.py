"""ILS (Internet Listing Service) research — apartments.com, zillow, etc.

Fetches the property's official ILS profile pages and extracts structured
data (amenities, unit mix, pricing, ratings, real resident reviews) to
feed into the AI brief drafter as additional context.

WHY THIS MATTERS for anti-slop:
  Resident reviews on apartments.com are real tenant voice — the strongest
  signal we have that isn't either marketing copy or AI-generated. A brief
  grounded in actual review excerpts ("the pool was closed three times in
  February") beats a brief grounded in the property's website (which is
  often itself ChatGPT'd these days).

DESIGN:
  - Each ILS provider gets a fetcher class with a `fetch(url) → dict` method
  - We pass the fetched HTML to Claude Haiku for structured extraction —
    avoids brittle CSS-selector code that breaks every time apartments.com
    redesigns their listing template
  - Failures are graceful: if the fetch is blocked or parse fails, we return
    an empty dict and the brief drafter proceeds with whatever else it has
  - Caching: results are stored in HubDB rpm_brief_drafts so repeat draft
    requests don't re-hit ILS sites (also kinder to their rate limits)

CALLED BY:
  brief_ai_drafter.draft_brief() — receives an `ils_urls` dict mapping
  provider name → URL, and the extracted data lands in the prompt as
  cache-eligible context.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)


# Realistic browser UA — the BriefDrafter UA gets blocked aggressively by
# apartments.com's bot detection. A proper User-Agent with Accept headers
# resembling a normal browser request gets through more often (we still get
# blocked sometimes — that's why fail-graceful is the contract).
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.5 Safari/605.1.15"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/webp,image/avif,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Upgrade-Insecure-Requests": "1",
}

_FETCH_TIMEOUT = 15
_MAX_HTML_CHARS = 80_000  # cap before sending to Claude (~20k tokens)


# ── Provider detection ─────────────────────────────────────────────────────


SUPPORTED_PROVIDERS: dict[str, list[str]] = {
    "apartments_com":  ["apartments.com"],
    "zillow":          ["zillow.com"],
    "rent_com":        ["rent.com"],
    "apartmentlist":   ["apartmentlist.com"],
    "realtor":         ["realtor.com"],
    "forrent":         ["forrent.com"],
    "trulia":          ["trulia.com"],
    "hotpads":         ["hotpads.com"],
}


def detect_provider(url: str) -> str | None:
    """Return the provider key for a URL, or None if unsupported."""
    if not url:
        return None
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return None
    host = host.lower().lstrip("www.")
    for key, domains in SUPPORTED_PROVIDERS.items():
        if any(host == d or host.endswith("." + d) for d in domains):
            return key
    return None


# ── HTML fetch + sanitization ─────────────────────────────────────────────


def _strip_html(html: str) -> str:
    """Remove script/style and collapse tags. Crude but enough for Claude
    to see the visible content + some structural attributes."""
    html = re.sub(r"<script\b[^>]*>.*?</script>", " ", html, flags=re.I | re.S)
    html = re.sub(r"<style\b[^>]*>.*?</style>", " ", html, flags=re.I | re.S)
    html = re.sub(r"<noscript\b[^>]*>.*?</noscript>", " ", html, flags=re.I | re.S)
    # Keep text content of meaningful tags but drop the rest. Inline JSON-LD
    # blocks (which apartments.com uses heavily) are great signal — preserve
    # script[type="application/ld+json"] if any exist.
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_jsonld(html: str) -> list[dict]:
    """Pull JSON-LD structured-data blocks out of the page. Apartments.com,
    Zillow, and most ILS providers embed Product/ApartmentComplex schemas
    that are far more reliable than scraping rendered HTML."""
    blocks = []
    pattern = re.compile(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        re.I | re.S,
    )
    for m in pattern.finditer(html or ""):
        raw = m.group(1).strip()
        try:
            blocks.append(json.loads(raw))
        except json.JSONDecodeError:
            # Some pages double-encode or include trailing commas — try to
            # repair the most common offender (trailing commas).
            try:
                cleaned = re.sub(r",\s*([}\]])", r"\1", raw)
                blocks.append(json.loads(cleaned))
            except Exception:
                continue
    return blocks


def _fetch_html(url: str) -> str:
    """Best-effort GET. Returns empty string on any failure."""
    try:
        r = requests.get(url, headers=_BROWSER_HEADERS, timeout=_FETCH_TIMEOUT, allow_redirects=True)
        if r.status_code == 200:
            return r.text or ""
        logger.info("ils_research: %s returned HTTP %s", url, r.status_code)
    except requests.RequestException as e:
        logger.info("ils_research: fetch failed for %s: %s", url, e)
    return ""


# ── Claude-assisted structured extraction ─────────────────────────────────


_EXTRACTION_PROMPT = """\
You're extracting structured property data from an Internet Listing Service \
(ILS) page like apartments.com or zillow. Return ONLY a JSON object with \
the following fields. Use null for any field the page does not support.

{
  "property_name": string | null,
  "address": string | null,
  "amenities": [string, ...],
  "unit_mix": [{"type": string, "beds": number, "baths": number, "rent_low": number, "rent_high": number, "sqft_low": number, "sqft_high": number}],
  "pet_policy": string | null,
  "rating": number | null,                 // out of 5; null if not shown
  "review_count": number | null,
  "review_excerpts": [                     // up to 5 specific resident-voice quotes
    {"text": string, "rating": number | null, "date": string | null}
  ],
  "highlighted_features": [string, ...],   // ILS-platform "highlights" / badges
  "neighborhood_descriptors": [string, ...],
  "concession_text": string | null,        // current promo from the listing if any
  "school_assignments": [string, ...],     // zillow-specific
  "walk_score": number | null,
  "transit_score": number | null
}

EXTRACTION RULES:
  - For review_excerpts, prefer SPECIFIC quotes ("the gym treadmills are old", \
"my car was towed") over generic ones ("great place"). Specific reviews are \
gold for our marketing brief.
  - amenities: only list amenities the page actually lists; do not infer.
  - unit_mix: parse pricing tables; null fields if a value is missing.
  - Return ONLY the JSON object — no preamble, no markdown fences.

PAGE CONTENT (provider: {provider}, url: {url}):

JSON-LD blocks (most reliable):
{jsonld}

Text content:
{text}
"""


def _claude_extract(provider: str, url: str, jsonld: list[dict], text: str) -> dict[str, Any]:
    """Send sanitized page to Claude Haiku for structured extraction."""
    try:
        import anthropic

        from config import ANTHROPIC_API_KEY, CLAUDE_BRIEF_MODEL
        if not ANTHROPIC_API_KEY:
            return {}
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        prompt = _EXTRACTION_PROMPT.format(
            provider=provider,
            url=url[:500],
            jsonld=json.dumps(jsonld)[:20_000] if jsonld else "(none)",
            text=text[:_MAX_HTML_CHARS],
        )
        msg = client.messages.create(
            model=CLAUDE_BRIEF_MODEL,
            max_tokens=2500,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = "".join(b.text for b in msg.content if hasattr(b, "text")).strip()
        # Strip code fences if Claude added them
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I | re.M).strip()
        return json.loads(raw)
    except Exception as e:
        logger.warning("ils_research: Claude extraction failed for %s: %s", provider, e)
        return {}


# ── Public entry point ────────────────────────────────────────────────────


def research_ils_listings(ils_urls: dict[str, str] | list[str]) -> dict[str, Any]:
    """Fetch + extract structured data from one or more ILS URLs.

    Args:
      ils_urls: either {"apartments_com": "https://...", "zillow": "https://..."}
                or a flat list of URLs (provider auto-detected).

    Returns:
      {
        "providers":      ["apartments_com", "zillow", ...],
        "by_provider":    {provider: extracted_dict},
        "merged_amenities":      [str, ...],   # union across providers
        "merged_review_quotes":  [str, ...],   # specific tenant voice
        "ratings":               {provider: rating},
        "errors":         {url: reason},
      }

    Never raises — failures land in `errors` and the brief drafter degrades
    gracefully.
    """
    # Normalize input
    items: list[tuple[str, str]] = []   # (provider, url)
    if isinstance(ils_urls, dict):
        for prov, url in ils_urls.items():
            if url:
                items.append((prov, url))
    else:
        for url in (ils_urls or []):
            prov = detect_provider(url)
            if prov:
                items.append((prov, url))

    if not items:
        return {"providers": [], "by_provider": {}, "merged_amenities": [],
                "merged_review_quotes": [], "ratings": {}, "errors": {}}

    by_provider: dict[str, dict] = {}
    errors: dict[str, str] = {}
    for prov, url in items:
        html = _fetch_html(url)
        if not html:
            errors[url] = "fetch_blocked_or_failed"
            continue
        jsonld = _extract_jsonld(html)
        text = _strip_html(html)
        if not text and not jsonld:
            errors[url] = "no_extractable_content"
            continue
        extracted = _claude_extract(prov, url, jsonld, text)
        if not extracted:
            errors[url] = "extraction_returned_empty"
            continue
        by_provider[prov] = extracted

    # Merge across providers
    merged_amenities: list[str] = []
    seen_amen: set[str] = set()
    merged_quotes: list[str] = []
    seen_q: set[str] = set()
    ratings: dict[str, float] = {}

    for prov, data in by_provider.items():
        for a in (data.get("amenities") or []):
            key = (a or "").strip().lower()
            if key and key not in seen_amen:
                seen_amen.add(key)
                merged_amenities.append(a)
        for q in (data.get("review_excerpts") or []):
            text = (q.get("text") if isinstance(q, dict) else str(q)) or ""
            text = text.strip()
            if text and len(text) > 20:
                k = text[:80].lower()
                if k not in seen_q:
                    seen_q.add(k)
                    merged_quotes.append(text)
        if data.get("rating") is not None:
            try:
                ratings[prov] = float(data["rating"])
            except (TypeError, ValueError):
                pass

    return {
        "providers":            list(by_provider.keys()),
        "by_provider":          by_provider,
        "merged_amenities":     merged_amenities,
        "merged_review_quotes": merged_quotes[:10],
        "ratings":              ratings,
        "errors":               errors,
    }


def format_for_prompt(ils_data: dict[str, Any], max_chars: int = 6000) -> str:
    """Format ILS research output as a text block for the brief drafter prompt.

    Kept short and structured so it caches well and doesn't drown out the
    primary website content. Real review quotes get prioritized — they are
    the most valuable signal.
    """
    if not ils_data or not ils_data.get("providers"):
        return ""

    lines: list[str] = ["ILS LISTING RESEARCH (auto-extracted from public ILS profiles):"]

    if ils_data.get("ratings"):
        rating_parts = [f"{p}: {r:.1f}/5" for p, r in ils_data["ratings"].items()]
        lines.append(f"  Ratings: {', '.join(rating_parts)}")

    if ils_data.get("merged_amenities"):
        lines.append(f"  Amenities (union across ILS): {', '.join(ils_data['merged_amenities'][:30])}")

    quotes = ils_data.get("merged_review_quotes") or []
    if quotes:
        lines.append("  Real resident review excerpts (use these to ground 'voice' and 'unique selling points'):")
        for q in quotes[:8]:
            lines.append(f'    - "{q[:300]}"')

    # Provider-specific extras
    for prov, data in (ils_data.get("by_provider") or {}).items():
        unit_mix = data.get("unit_mix") or []
        if unit_mix:
            mix_strs = []
            for u in unit_mix[:8]:
                t = u.get("type") or f"{u.get('beds', '?')}BR"
                lo = u.get("rent_low")
                hi = u.get("rent_high")
                if lo and hi:
                    mix_strs.append(f"{t}: ${lo}-${hi}")
                elif lo:
                    mix_strs.append(f"{t}: from ${lo}")
                else:
                    mix_strs.append(t)
            lines.append(f"  Unit mix ({prov}): {'; '.join(mix_strs)}")
        if data.get("concession_text"):
            lines.append(f"  Current concession on {prov}: {data['concession_text'][:200]}")
        if data.get("walk_score"):
            lines.append(f"  Walk Score ({prov}): {data['walk_score']}")
        if data.get("transit_score"):
            lines.append(f"  Transit Score ({prov}): {data['transit_score']}")

    text = "\n".join(lines)
    return text[:max_chars]
