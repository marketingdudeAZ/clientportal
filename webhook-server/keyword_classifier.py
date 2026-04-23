"""Classify each keyword as seo_target, paid_only, or both.

Called from the onboarding keyword-generation route after an SEO expansion
(via keyword_research.expand_seed) has been enriched with Paid metrics from
dataforseo_client.keyword_planner_lookup. The classifier layers cheap
deterministic heuristics first, then optionally adds a single batched Claude
Haiku pass to produce a short, client-facing "reason" string per keyword.

The label drives routing:
    seo_target  → rpm_seo_keywords HubDB
    paid_only   → rpm_paid_keywords HubDB (Fluency feed)
    both        → written to both
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

LABEL_SEO = "seo_target"
LABEL_PAID = "paid_only"
LABEL_BOTH = "both"

# Heuristic thresholds — tune from data, not lore.
_HIGH_KD = 70            # SERP difficulty considered unrankable for young sites
_HIGH_COMPETITION = 70   # Google Ads competition_index 0-100
_LOW_VOLUME = 10         # noise floor — skip unless branded/hyper-local
_DECENT_VOLUME = 30      # minimum real search interest
_COMMERCIAL_CPC = 1.50   # CPC ≥ $1.50 signals commercial intent


def _token_match(keyword: str, needles: list[str]) -> bool:
    kw = keyword.lower()
    return any(n.lower() in kw for n in needles if n)


def _classify_one(
    row: dict,
    competitor_brands: list[str],
    property_brand: str,
) -> dict:
    """Heuristic label for a single enriched keyword row.

    Expects merged fields: keyword, volume, difficulty (KD 0-100),
    competition_index (0-100), cpc_low, cpc_high, intent, source_neighborhood.
    """
    keyword = (row.get("keyword") or "").strip()
    if not keyword:
        return {"label": LABEL_PAID, "reason": "Empty keyword", "priority": "low"}

    volume = int(row.get("volume") or 0)
    kd = int(row.get("difficulty") or 0)
    competition = int(row.get("competition_index") or 0)
    cpc_high = float(row.get("cpc_high") or 0)
    intent = (row.get("intent") or "").lower()

    # Competitor-brand searches — Paid-only. Ranking for a competitor's brand
    # organically is both unrealistic and would look hostile; bidding on it is
    # the standard play.
    if competitor_brands and _token_match(keyword, competitor_brands):
        return {
            "label": LABEL_PAID,
            "reason": "Competitor-brand search — capture intent via paid only.",
            "priority": "high",
        }

    # Property-brand searches — SEO-target (we own this SERP already).
    if property_brand and property_brand.lower() in keyword.lower():
        return {
            "label": LABEL_SEO,
            "reason": "Branded query — protect organic position.",
            "priority": "high",
        }

    # Very low volume — probably noise. Skip by defaulting to SEO-target with
    # low priority so it doesn't burn Paid budget but still shows up for
    # long-tail content opportunity.
    if volume < _LOW_VOLUME:
        return {
            "label": LABEL_SEO,
            "reason": "Low volume — long-tail SEO candidate, not worth bidding on.",
            "priority": "low",
        }

    # Commercial + hard to rank → Paid only.
    if (kd >= _HIGH_KD or competition >= _HIGH_COMPETITION) and cpc_high >= _COMMERCIAL_CPC:
        return {
            "label": LABEL_PAID,
            "reason": (
                f"High competition (KD {kd}, CI {competition}) with commercial "
                f"CPC ${cpc_high:.2f} — paid is the faster path."
            ),
            "priority": "high",
        }

    # Informational long-tail, reachable KD → SEO-target.
    if kd < 50 and intent in ("", "informational", "navigational"):
        return {
            "label": LABEL_SEO,
            "reason": "Reachable organically — informational intent, moderate KD.",
            "priority": "medium" if volume >= _DECENT_VOLUME else "low",
        }

    # Default: pursue on both sides with moderate priority.
    return {
        "label": LABEL_BOTH,
        "reason": "Mixed signal — pursue organically while bidding defensively.",
        "priority": "medium",
    }


def classify(
    keywords: list[dict],
    competitor_brands: list[str] | None = None,
    property_brand: str | None = None,
    refine_with_claude: bool = False,
) -> list[dict]:
    """Label a list of enriched keywords.

    Args:
        keywords: rows from keyword_research merged with keyword_planner_lookup.
            Each row should include at least `keyword`; the more of {volume,
            difficulty, competition_index, cpc_low, cpc_high, intent,
            source_neighborhood} it has, the better the heuristic works.
        competitor_brands: list of competitor brand names from the client brief.
        property_brand: the property's own brand name (to mark protectable terms).
        refine_with_claude: if True, run a single batched Haiku call to rewrite
            the `reason` field into client-friendly prose. Label + priority are
            not modified — heuristics stay authoritative.

    Returns a new list; does not mutate inputs.
    """
    competitor_brands = competitor_brands or []
    out: list[dict] = []
    for row in keywords:
        label = _classify_one(row, competitor_brands, property_brand or "")
        merged = dict(row)
        merged.update(label)
        out.append(merged)

    if refine_with_claude and out:
        try:
            _refine_reasons(out)
        except Exception as e:
            # Never block the pipeline on the refinement step.
            logger.warning("keyword_classifier: Claude refinement failed: %s", e)

    return out


# ─── Optional Claude Haiku refinement ───────────────────────────────────────

_REFINE_SYSTEM_PROMPT = """You are helping an apartment marketing team explain to clients why each keyword is targeted via SEO, Paid, or both.

You will receive a JSON array of keyword objects with an existing `label` and a short heuristic `reason`. Rewrite each `reason` as one clear sentence (≤ 20 words) that a client could read in a report. Do NOT change the `label` or `priority` fields. Return ONLY a JSON array of objects with `keyword` and `reason` — no preamble, no markdown fences.

Style rules:
- Concrete and specific; no jargon like "KD" or "CI".
- Client-friendly; avoid passive voice.
- If the label is paid_only, explain why organic ranking is impractical.
- If the label is seo_target, explain the organic opportunity.
- If the label is both, explain the defensive-bid + organic-pursuit logic."""


def _refine_reasons(classified: list[dict]) -> None:
    """Batch a single Claude Haiku call to rewrite reasons in-place."""
    import anthropic
    from config import ANTHROPIC_API_KEY, CLAUDE_BRIEF_MODEL

    if not ANTHROPIC_API_KEY:
        return

    # Keep the payload minimal — just what Claude needs to reason about label fit.
    payload = [
        {
            "keyword": r.get("keyword", ""),
            "label":   r.get("label", ""),
            "volume":  r.get("volume", 0),
            "difficulty": r.get("difficulty", 0),
            "competition_index": r.get("competition_index", 0),
            "cpc_high": r.get("cpc_high", 0),
            "heuristic_reason": r.get("reason", ""),
        }
        for r in classified
    ]

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message = client.messages.create(
        model=CLAUDE_BRIEF_MODEL,
        max_tokens=4000,
        system=[{
            "type": "text",
            "text": _REFINE_SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{
            "role": "user",
            "content": json.dumps(payload, separators=(",", ":")),
        }],
    )
    raw = next((b.text for b in message.content if b.type == "text"), "").strip()
    rewrites = _parse_reason_json(raw)
    if not rewrites:
        return

    lookup = {r.get("keyword", ""): r.get("reason", "") for r in rewrites}
    for row in classified:
        new = lookup.get(row.get("keyword", ""))
        if new:
            row["reason"] = new


def _parse_reason_json(raw: str) -> list[dict]:
    """Best-effort parse of Claude's JSON array response."""
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass
    match = re.search(r"\[[\s\S]*\]", raw)
    if match:
        try:
            data = json.loads(match.group())
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            logger.warning("keyword_classifier: Haiku returned malformed JSON")
    return []
