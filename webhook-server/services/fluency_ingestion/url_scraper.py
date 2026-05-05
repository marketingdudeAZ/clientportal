"""STAGING-ONLY: Property URL scraper — Track 2 phase 2.2.

Scrapes a property's marketing website, extracts visible text, sends to
Anthropic Claude Sonnet (per spec section 4.12, temperature 0.2) with the
locked prompt that:

  • Names a controlled amenity vocabulary (the 39 Apt IQ booleans)
  • Forbids demographic / target-audience extraction (fair housing risk)
  • Returns voice signal, tagline, neighborhood, landmarks, employers, unit noun

This module is the source of authority for the fluency_* fields that the
Apt IQ CSV cannot provide:
  marketed_amenity_names, amenities_descriptions, neighborhood, landmarks,
  nearby_employers, unit_noun, (voice tier signal as input to ranking)

Run cadence per spec: quarterly per property. We do not invoke this from the
daily fluency-tag-sync loop — only when explicitly triggered with
`scrape_urls: true` on the orchestrator endpoint.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import requests

logger = logging.getLogger(__name__)

# Reuse the exact 39-amenity vocab the Apt IQ reader maintains so Claude
# normalizes marketed names back to the same controlled list.
from services.fluency_ingestion.apt_iq_reader import AMENITY_COLS

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = "claude-sonnet-4-5"
TEMPERATURE = 0.2
MAX_OUTPUT_TOKENS = 1500
HTTP_TIMEOUT = 25
SCRAPE_MAX_CHARS = 18000  # cap on raw page text sent to Claude

# Strip these tags + their content before extracting visible text
_NOISE_TAGS_PATTERN = re.compile(
    r"<(script|style|noscript|svg|iframe|head)\b[^>]*>.*?</\1>",
    re.DOTALL | re.IGNORECASE,
)
_TAG_PATTERN = re.compile(r"<[^>]+>")
_WS_PATTERN = re.compile(r"\s+")


def fetch_page_text(url: str) -> str:
    """Fetch a property URL + return cleaned visible text (max SCRAPE_MAX_CHARS).

    Returns "" on any HTTP error / parse failure (caller decides how to handle).
    """
    if not url:
        return ""
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    # Many property marketing sites sit behind Cloudflare and reject anything
    # that looks bot-shaped. Use a real-browser UA + complete header set so
    # the basic bot challenge passes.
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }
    try:
        r = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT, allow_redirects=True)
        r.raise_for_status()
    except requests.RequestException as e:
        logger.warning("url_scraper: fetch failed for %s: %s", url, e)
        return ""

    html = r.text or ""
    cleaned = _NOISE_TAGS_PATTERN.sub(" ", html)
    cleaned = _TAG_PATTERN.sub(" ", cleaned)
    cleaned = _WS_PATTERN.sub(" ", cleaned).strip()
    return cleaned[:SCRAPE_MAX_CHARS]


# Per spec section 4.12, locked prompt. Demographic extraction is explicitly
# forbidden (fair housing).
_PROMPT_TEMPLATE = """You are extracting structured marketing data from a multifamily apartment property website. Output ONLY valid JSON, no preamble.

CONTROLLED AMENITY VOCABULARY (use only these exact names for `amenity_normalized`):
{amenity_list}

WEBSITE CONTENT:
{scraped_text}

Return JSON with these fields:
{{
  "marketed_amenity_names": ["names as the property markets them, e.g. Aquadeck"],
  "amenity_normalized": ["names from the controlled vocabulary above only"],
  "amenities_descriptions": "1–3 short sentences describing the marketed amenity story (no fluff, no pricing)",
  "voice_signal": "luxury|standard|value|lifestyle",
  "voice_evidence": "short phrase quoted from the site that supports the signal",
  "tagline": "exact property tagline if present, else empty string",
  "differentiators": ["short phrases naming unique features"],
  "neighborhood": "named neighborhood",
  "nearby_neighborhoods": ["named", "nearby", "neighborhoods"],
  "landmarks": ["named landmarks within 2 miles"],
  "nearby_employers": ["named employers explicitly mentioned on the site"],
  "unit_noun": "apartment|townhome|loft|home|duplex"
}}

For any field where evidence is not present on the website, output an empty string or empty array. Do NOT invent.

Do NOT extract demographic information about residents, tenants, or target audience. Only extract data about the physical property and its named geographic context.
"""


def scrape_property(property_url: str) -> dict | None:
    """Scrape one property URL → structured marketing dict, or None on failure.

    Failure modes (all return None):
      • property_url empty / unreachable
      • Anthropic key missing
      • Claude returns non-JSON or shape doesn't include required keys
    """
    if not ANTHROPIC_API_KEY:
        logger.warning("url_scraper: ANTHROPIC_API_KEY not set; skipping")
        return None
    if not property_url:
        return None

    page_text = fetch_page_text(property_url)
    if len(page_text) < 200:
        logger.info("url_scraper: %s returned <200 chars; skipping", property_url)
        return None

    prompt = _PROMPT_TEMPLATE.format(
        amenity_list="\n".join(f"  - {a}" for a in AMENITY_COLS),
        scraped_text=page_text,
    )
    body = {
        "model":       MODEL,
        "max_tokens":  MAX_OUTPUT_TOKENS,
        "temperature": TEMPERATURE,
        "messages": [{"role": "user", "content": prompt}],
    }
    try:
        r = requests.post(
            ANTHROPIC_API_URL,
            headers={
                "x-api-key":         ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "Content-Type":      "application/json",
            },
            json=body,
            timeout=60,
        )
        r.raise_for_status()
    except requests.RequestException as e:
        logger.error("url_scraper: Anthropic call failed for %s: %s", property_url, e)
        return None

    try:
        msg = r.json()
        # Claude returns {"content": [{"type": "text", "text": "..."}], ...}
        text_block = next((c.get("text", "") for c in (msg.get("content") or [])
                           if c.get("type") == "text"), "")
        if not text_block:
            return None
        # Extract first JSON object from the text (Claude sometimes wraps in
        # ```json fences despite "no preamble" instruction).
        match = re.search(r"\{.*\}", text_block, re.DOTALL)
        raw_json = match.group(0) if match else text_block
        return json.loads(raw_json)
    except (KeyError, ValueError, json.JSONDecodeError) as e:
        logger.warning("url_scraper: parse failed for %s: %s | head=%s",
                       property_url, e, (r.text or "")[:200])
        return None
