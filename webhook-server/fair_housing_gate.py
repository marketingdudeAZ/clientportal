"""Fair Housing compliance GATE for user-entered marketing copy.

Distinct from `fair_housing.py` (which guards paid-media *targeting* — radius
minimums + audience descriptors). This module screens the free *prose* a
property team types that can feed ads — Property Profile overrides and Call
Prep answers — and blocks the save when it isn't compliant.

Two layers:
  1. HARD_PATTERNS — a tight, high-precision blocklist that always blocks, even
     if the LLM is unavailable (familial status, explicit protected-class
     steering, source-of-income refusals). Kept narrow so ordinary amenity /
     neighborhood copy never trips it (e.g. "single-family", "White Street").
  2. An LLM pass (Claude Haiku) for nuance — discriminatory preferences,
     limitations, or steering that isn't a fixed phrase.

`check_fair_housing()` returns {"compliant": bool, "violations": [...]}.
Fail-OPEN on LLM/infra errors (never block a save because the checker is down) —
HARD_PATTERNS still apply. Protected classes follow the federal Fair Housing Act
plus commonly-protected state/local classes (source of income).
"""
from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

# High-confidence, unambiguous violations — always block. Narrow by design.
HARD_PATTERNS = [
    (re.compile(r"\bno\s+(kids|children)\b", re.I), "familial status", "no kids/children"),
    (re.compile(r"\b(adults?[\s-]only|no\s+families|families\s+not\s+welcome)\b", re.I), "familial status", "adults-only / no families"),
    (re.compile(r"\bperfect\s+for\s+(a\s+)?(single|singles|couples?|young\s+professionals?|bachelors?|empty[\s-]*nesters?)\b", re.I), "familial status / steering", "'perfect for [singles/couples/young professionals]'"),
    (re.compile(r"\b(christian|catholic|jewish|muslim|hindu)\s+(community|building|residents?|preferred)\b", re.I), "religion", "religious preference"),
    (re.compile(r"\b(whites?|blacks?|hispanics?|asians?|caucasians?)\s+(only|preferred|community|residents?)\b", re.I), "race / national origin", "racial preference"),
    (re.compile(r"\bno\s+(section\s*8|housing\s+vouchers?|vouchers?)\b", re.I), "source of income", "'no Section 8 / vouchers'"),
    (re.compile(r"\b(able[\s-]bodied\s+(residents?|only)|no\s+(disabled|handicapped))\b", re.I), "disability", "excluding people with disabilities"),
    (re.compile(r"\b(exclusive|restricted)\s+(community|neighborhood|clientele)\b", re.I), "steering", "'exclusive/restricted community'"),
]

SYSTEM_PROMPT = (
    "You are a Fair Housing compliance reviewer for U.S. multifamily apartment "
    "marketing. Review the supplied text for language that violates the Fair "
    "Housing Act or commonly-protected state/local classes. Protected classes: "
    "race, color, religion, sex/gender, national origin, familial status "
    "(children/family size), disability, and — where relevant — source of income "
    "and sexual orientation. Flag discriminatory PREFERENCES, LIMITATIONS, or "
    "STEERING (e.g. 'great for young professionals', 'ideal for a quiet mature "
    "crowd', 'safe Christian neighborhood', 'walk to the synagogue', 'no kids', "
    "'able-bodied'). Do NOT flag legitimate, non-targeting descriptions of the "
    "property, amenities, unit features, accessibility features offered, or "
    "neutral neighborhood landmarks. Be precise — false positives frustrate "
    "users. Return ONLY JSON: {\"violations\":[{\"field\":\"...\",\"phrase\":"
    "\"...\",\"protected_class\":\"...\",\"issue\":\"...\",\"suggestion\":\"...\"}]}. "
    "An empty violations array means the copy is compliant."
)


def _hard_scan(items):
    out = []
    for it in items:
        text = it.get("text") or ""
        for pat, cls, label in HARD_PATTERNS:
            m = pat.search(text)
            if m:
                out.append({
                    "field": it.get("field", ""),
                    "phrase": m.group(0).strip(),
                    "protected_class": cls,
                    "issue": f"References {cls} ({label}) — not allowed in housing marketing.",
                    "suggestion": "Describe the property, amenities, or lifestyle features instead of who should (or shouldn't) live there.",
                })
    return out


def check_fair_housing(items):
    """Screen a list of {field, text} dicts for Fair Housing violations.

    Returns {"compliant": bool, "violations": [...], "checked": True}. Fail-open
    on LLM errors; HARD_PATTERNS are always enforced.
    """
    items = [it for it in (items or []) if (it.get("text") or "").strip()]
    if not items:
        return {"compliant": True, "violations": [], "checked": True}

    violations = _hard_scan(items)

    try:
        from config import ANTHROPIC_API_KEY, CLAUDE_BRIEF_MODEL
        if ANTHROPIC_API_KEY:
            import anthropic
            client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            blob = "\n\n".join(f"[{it.get('field', 'text')}]\n{it['text']}" for it in items)
            msg = client.messages.create(
                model=CLAUDE_BRIEF_MODEL, max_tokens=900, temperature=0,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": "Review this marketing copy:\n\n" + blob}],
            )
            raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
            # Robust: pull the first {...} object out of fences / extra prose.
            m = re.search(r"\{.*\}", raw, re.S)
            data = json.loads(m.group(0)) if m else {"violations": []}
            for v in (data.get("violations") or []):
                if not isinstance(v, dict) or not v.get("phrase"):
                    continue
                if any(h["phrase"].lower() == str(v.get("phrase", "")).lower() for h in violations):
                    continue
                violations.append({
                    "field": v.get("field", ""),
                    "phrase": v.get("phrase", ""),
                    "protected_class": v.get("protected_class", ""),
                    "issue": v.get("issue", ""),
                    "suggestion": v.get("suggestion", ""),
                })
    except Exception as e:
        logger.warning("fair_housing_gate: LLM pass unavailable, hard-scan only (%s)", e)

    return {"compliant": len(violations) == 0, "violations": violations, "checked": True}
