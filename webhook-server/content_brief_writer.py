"""Phase 2 — AI content brief generation (Claude Haiku).

Given a topic cluster from content_planner.cluster_keywords, build a full SEO
brief optimized for traditional search AND Generative Engines / AI Overviews.

Brief schema (persisted to rpm_content_briefs HubDB):
    h1, meta_description, outline[{h2, h3_list, target_entities, paa_answered}],
    target_word_count, internal_link_targets, schema_types

Caller flow:
    1. Cluster keywords -> pick a hub
    2. Pull top-10 SERPs for the hub keyword
    3. Parse competitor H1/H2 headings (via on_page)
    4. Extract competitor entities
    5. Send everything to Claude Haiku as JSON-structured user message
    6. Parse response, validate, return
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

CLAUDE_BRIEF_MODEL = "claude-haiku-4-5-20251001"
CLAUDE_BRIEF_MAX_TOKENS = 2000
CLAUDE_BRIEF_TEMPERATURE = 0.3

BRIEF_SYSTEM_PROMPT = """You are an SEO content strategist who writes briefs for apartment marketing teams.

Your briefs target BOTH Google organic rankings AND Generative Engine Optimization (GEO / AEO) — meaning LLM citations in ChatGPT, Perplexity, Gemini, and Google AI Overviews. These two goals share the same tactics: entity coverage, semantic depth, E-E-A-T signals, internal linking, and schema markup.

ALWAYS respond with valid JSON matching this exact shape:

{
  "h1":                 string,
  "meta_description":   string (≤160 chars),
  "outline": [
    {
      "h2":              string,
      "h3_list":         [string],
      "target_entities": [string],   // entities to name-drop in this section
      "paa_answered":    [string]    // People-Also-Ask questions this section should address
    }
  ],
  "target_word_count":      integer (typically 1200-2500),
  "internal_link_targets":  [string],  // anchor text / URLs to link to from this piece
  "schema_types":           [string],  // schema.org types to add (e.g. "FAQPage", "BreadcrumbList")
  "geo_optimization_notes": string     // 1-2 sentences on how this brief specifically targets AI Overviews
}

Prioritize direct answers to PAA questions in early H2s — AI engines favor pages that answer common queries explicitly. Include FAQPage schema when PAA list is non-empty."""


def generate_brief(cluster_data: dict) -> dict:
    """Generate a content brief from a cluster + competitor context.

    Args:
        cluster_data: {
            hub_keyword:       str,
            spokes:            [str],
            property_name:     str,
            property_domain:   str,
            market:            str,
            top_serp_urls:     [str],          # top-10 organic from hub keyword SERP
            competitor_headings: [{url, h1, h2s}],
            paa_questions:     [str],
            related_searches:  [str],
            competitor_entities: [str],        # names pre-extracted by caller
            existing_tracked_keywords: [str],  # for internal-link suggestions
        }

    Returns validated brief dict. Raises ValueError if Claude returns invalid JSON.
    """
    import anthropic
    from config import ANTHROPIC_API_KEY

    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")

    user_message = _build_user_message(cluster_data)

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    logger.info("Generating brief for hub keyword: %s", cluster_data.get("hub_keyword"))
    message = client.messages.create(
        model=CLAUDE_BRIEF_MODEL,
        max_tokens=CLAUDE_BRIEF_MAX_TOKENS,
        temperature=CLAUDE_BRIEF_TEMPERATURE,
        system=BRIEF_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    raw = message.content[0].text.strip()
    brief = _parse_brief_json(raw)
    _validate_brief(brief)
    return brief


def _build_user_message(c: dict) -> str:
    """Assemble the structured prompt Claude sees."""
    lines = [
        f"Property: {c.get('property_name','(unknown)')} ({c.get('property_domain','')})",
        f"Market: {c.get('market','(unknown)')}",
        "",
        f"HUB KEYWORD: {c.get('hub_keyword','')}",
    ]
    spokes = c.get("spokes") or []
    if spokes:
        lines.append("SPOKE KEYWORDS (cover within the same piece):")
        for s in spokes:
            lines.append(f"  - {s}")
    else:
        lines.append("SPOKE KEYWORDS: (none — stand-alone topic)")

    paa = c.get("paa_questions") or []
    if paa:
        lines.append("\nPEOPLE ALSO ASK (must answer several of these):")
        for q in paa[:10]:
            lines.append(f"  - {q}")

    related = c.get("related_searches") or []
    if related:
        lines.append("\nRELATED SEARCHES (use for subheadings / semantic breadth):")
        for r in related[:10]:
            lines.append(f"  - {r}")

    headings = c.get("competitor_headings") or []
    if headings:
        lines.append("\nCOMPETITOR CONTENT STRUCTURE (their H1 + H2s):")
        for h in headings[:5]:
            lines.append(f"  • {h.get('url','')}")
            if h.get("h1"):
                lines.append(f"      H1: {h['h1']}")
            for h2 in (h.get("h2s") or [])[:6]:
                lines.append(f"      H2: {h2}")

    entities = c.get("competitor_entities") or []
    if entities:
        lines.append(f"\nENTITIES COMPETITORS COVER (aim for ≥80% coverage):")
        lines.append(f"  {', '.join(entities[:30])}")

    tracked = c.get("existing_tracked_keywords") or []
    if tracked:
        lines.append("\nEXISTING TRACKED KEYWORDS (suggest internal links when anchor-relevant):")
        lines.append(f"  {', '.join(tracked[:20])}")

    lines.append("\nReturn ONLY the JSON brief object — no preamble, no markdown fences.")
    return "\n".join(lines)


def _parse_brief_json(raw: str) -> dict:
    """Extract valid JSON from Claude's response, tolerating markdown fences."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # Strip markdown code fence if present
    match = re.search(r"\{[\s\S]*\}", raw)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError as e:
            logger.error("Claude returned malformed JSON: %s", raw[:400])
            raise ValueError(f"Content brief generation returned invalid JSON: {e}") from e
    logger.error("Claude returned non-JSON: %s", raw[:400])
    raise ValueError("Content brief generation returned no parseable JSON object")


def _validate_brief(brief: dict) -> None:
    """Raise ValueError if required keys are missing / malformed."""
    required = ["h1", "meta_description", "outline", "target_word_count"]
    for k in required:
        if k not in brief:
            raise ValueError(f"Brief missing required field: {k}")
    if not isinstance(brief["outline"], list) or not brief["outline"]:
        raise ValueError("Brief outline must be a non-empty list")
    for section in brief["outline"]:
        if "h2" not in section:
            raise ValueError("Each outline section must have an 'h2'")
    # Non-fatal nudges — fill in sensible defaults if missing
    brief.setdefault("internal_link_targets", [])
    brief.setdefault("schema_types",          [])
    brief.setdefault("geo_optimization_notes", "")


def persist_brief(property_uuid: str, hub_keyword: str, brief: dict) -> str | None:
    """Write the brief into rpm_content_briefs HubDB. Returns row id or None.

    This is a thin wrapper so callers don't need to know HubDB column names.
    """
    from config import HUBDB_CONTENT_BRIEFS_TABLE_ID
    from hubdb_helpers import insert_row, publish
    import uuid as _uuid
    from datetime import datetime as _dt

    if not HUBDB_CONTENT_BRIEFS_TABLE_ID:
        logger.warning("HUBDB_CONTENT_BRIEFS_TABLE_ID not set — brief not persisted")
        return None

    brief_id = _uuid.uuid4().hex[:12]
    values = {
        "property_uuid":        property_uuid,
        "brief_id":             brief_id,
        "hub_keyword":          hub_keyword,
        "status":               "generated",
        "h1":                   brief.get("h1", ""),
        "meta_description":     brief.get("meta_description", ""),
        "outline_json":         json.dumps(brief.get("outline", [])),
        "target_word_count":    int(brief.get("target_word_count") or 1500),
        "target_entities_json": json.dumps(
            [e for s in (brief.get("outline") or []) for e in (s.get("target_entities") or [])]
        ),
        "internal_links_json":  json.dumps(brief.get("internal_link_targets") or []),
        "schema_types":         ", ".join(brief.get("schema_types") or []),
        "generated_at":         _dt.utcnow().isoformat() + "Z",
    }
    try:
        row_id = insert_row(HUBDB_CONTENT_BRIEFS_TABLE_ID, values)
        publish(HUBDB_CONTENT_BRIEFS_TABLE_ID)
        return row_id
    except Exception as e:
        logger.error("persist_brief failed: %s", e)
        return None
