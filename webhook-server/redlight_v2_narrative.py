"""Claude-driven narrative generation for Red Light v2 sections 4 & 5.

Section 4: Where you are going  — forward projection from the trailing trend.
Section 5: How you got here     — causal explanation of MoM + YoY deltas.

Both sections receive the structured payload from redlight_v2.build_report_payload
and return a short, plain-language paragraph suitable for a leasing director.

Failure mode: if ANTHROPIC_API_KEY is missing or the API call errors, return
a rule-based fallback string so the PDF still renders.
"""

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)


NARRATIVE_SYSTEM_PROMPT = """You write short, plain-language narrative paragraphs for a multifamily marketing operations report (the "Red Light Report v2").

Your audience: a leasing director or property manager at RPM Living.

Rules:
- Write in second person ("you", "your property") — this is a client deliverable.
- Be specific. Reference real numbers and trends from the data provided.
- One paragraph per section. 3-5 sentences. No headings, no bullets, no markdown.
- Do not invent metrics that are not in the data.
- If a value is missing or null, say "we don't have data for that yet" rather than guessing.
- Never mention specific dollar rent amounts. You may discuss cost per lease and service spend.
- Tone: direct, confident, advisory. Avoid filler ("we are pleased to report").
"""


def _build_user_prompt(section: str, payload: dict) -> str:
    """Prepare a compact JSON brief for Claude."""
    brief = {
        "property_name":    payload.get("property_name"),
        "submarket":        payload.get("current", {}).get("submarket_name"),
        "market":           payload.get("current", {}).get("market_name"),
        "current":          {k: v for k, v in (payload.get("current") or {}).items()
                             if k not in ("_raw", "hubspot_company_id", "aptiq_property_id")},
        "last_month":       payload.get("last_month"),
        "last_month_label": payload.get("last_month_label"),
        "last_year":        payload.get("last_year"),
        "last_year_label":  payload.get("last_year_label"),
        "mom_deltas":       payload.get("mom_comparison"),
        "yoy_deltas":       payload.get("yoy_comparison"),
        "trailing_trend":   payload.get("trailing_trend"),
    }

    if section == "where_going":
        ask = (
            "Write the 'Where you are going' paragraph. Project the next 30-60 "
            "days using the trailing trend and current trajectory. Flag the "
            "single biggest risk or opportunity you see in the numbers."
        )
    elif section == "how_got_here":
        ask = (
            "Write the 'How you got here' paragraph. Explain what drove the "
            "month-over-month and year-over-year changes. Name the metric that "
            "moved the most and connect it to a likely cause."
        )
    else:
        raise ValueError(f"Unknown section: {section}")

    return (
        ask
        + "\n\nData (JSON):\n"
        + json.dumps(brief, default=str, indent=2)
    )


def _fallback_where_going(payload: dict) -> str:
    cur = payload.get("current") or {}
    occ = cur.get("occupancy")
    leases = cur.get("leases_last_30")
    if occ is None and leases is None:
        return ("We don't have enough ApartmentIQ data on this property yet to "
                "project where you're going. Once we have a full month of "
                "snapshots in place, this section will project trajectory.")
    parts = []
    if occ is not None:
        parts.append(f"current occupancy is {occ:.1f}%")
    if leases is not None:
        parts.append(f"you signed {leases} leases in the last 30 days")
    return (
        "Based on the most recent data — " + ", ".join(parts) + " — "
        "we project the next 30-60 days will move in line with the current "
        "trailing run-rate. We'll have a more confident projection once we've "
        "captured a few months of ApartmentIQ snapshots."
    )


def _fallback_how_got_here(payload: dict) -> str:
    mom = payload.get("mom_comparison") or []
    biggest = None
    biggest_mag = 0
    for row in mom:
        d = row.get("delta") or {}
        pct = d.get("pct")
        if pct is not None and abs(pct) > biggest_mag:
            biggest_mag = abs(pct)
            biggest = row
    if not biggest:
        return ("We don't have a prior-month snapshot to compare against yet, "
                "so we can't explain what drove the current numbers. Next "
                "month's report will include this analysis.")
    label = biggest.get("label")
    pct = biggest["delta"]["pct"]
    direction = "up" if pct > 0 else "down"
    return (
        f"The biggest mover month-over-month was {label}, {direction} "
        f"{abs(pct):.1f}% versus {payload.get('last_month_label')}. The other "
        "metrics moved within their normal range. We'll layer richer causation "
        "as additional data sources connect."
    )


def generate_narrative(section: str, payload: dict) -> str:
    """Return the prose for one section. Falls back to a rule-based string on error."""
    from config import ANTHROPIC_API_KEY, CLAUDE_AGENT_MODEL

    if not ANTHROPIC_API_KEY:
        logger.warning("ANTHROPIC_API_KEY missing — using fallback narrative for %s", section)
        return (_fallback_where_going if section == "where_going"
                else _fallback_how_got_here)(payload)

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model=CLAUDE_AGENT_MODEL,
            max_tokens=400,
            system=NARRATIVE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _build_user_prompt(section, payload)}],
        )
        text = message.content[0].text.strip()
        return text or (_fallback_where_going if section == "where_going"
                        else _fallback_how_got_here)(payload)
    except Exception as exc:
        logger.error("Claude narrative for %s failed: %s — using fallback", section, exc)
        return (_fallback_where_going if section == "where_going"
                else _fallback_how_got_here)(payload)


def attach_narratives(payload: dict) -> dict:
    """Add 'where_going' and 'how_got_here' keys to the payload in place."""
    payload["where_going"]   = generate_narrative("where_going",   payload)
    payload["how_got_here"]  = generate_narrative("how_got_here",  payload)
    return payload
