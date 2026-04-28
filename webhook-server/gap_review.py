"""Gap-review engine for onboarding intake forms.

After a PMA submits the intake form, this module:
  1. Scores per-field completeness (required fields present? forced-fact
     fields parseable as expected types?)
  2. Detects typos in free-text via a heuristic + extended dictionary
  3. Runs Claude Haiku as an "AI-slop classifier" — does this read as
     specific human voice or as generic ChatGPT marketing copy?
  4. Synthesizes a list of gap questions to send the Community Manager
  5. Updates the company's HubSpot properties to trigger the gap-review
     workflow (the workflow itself is owned by HubSpot — see
     docs/handoffs/HUBSPOT_GAP_REVIEW_WORKFLOW.md)

Design tenet: we never delete or auto-rewrite a PMA's free-text answer.
We surface gaps as structured questions for the CM to answer in the portal
response form. Their structured answers are the trusted ground truth.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


# ── Field-level rules ───────────────────────────────────────────────────────
# Each form field declares its trust tag (so downstream consumers know what
# to weight) and any forced-fact validation. Trust tags:
#   structured     — dropdown / multi-select; can't be typo'd
#   forced_fact    — must parse as a specific shape (number, date, etc.)
#   ai_assisted    — AI strawman the PMA accepted via this/that
#   free_text      — open prose (highest slop risk; gets classifier)
#   verified_by_cm — set after Community Manager confirms via response form

# Required fields on the intake form. If missing or empty, the gap engine
# generates a question for them.
REQUIRED_FIELDS: dict[str, dict[str, Any]] = {
    "property_name":          {"trust": "structured",  "label": "Property name"},
    "neighborhoods":          {"trust": "structured",  "label": "Target neighborhoods"},
    "unit_types":             {"trust": "structured",  "label": "Unit types offered"},
    "primary_competitors":    {"trust": "structured",  "label": "Primary competitors"},
    "current_concession":     {"trust": "forced_fact", "label": "Current concession amount",
                               "validate": "concession"},
    "current_occupancy_pct":  {"trust": "forced_fact", "label": "Current occupancy %",
                               "validate": "percent"},
    "top_resident_complaint": {"trust": "free_text",   "label": "Top recent resident complaint",
                               "max_chars": 280},
    "brand_archetype":        {"trust": "ai_assisted", "label": "Brand archetype"},
    "community_manager_email":{"trust": "structured",  "label": "Community Manager email",
                               "validate": "rpm_email"},
    "regional_manager_email": {"trust": "structured",  "label": "Regional Manager email",
                               "validate": "rpm_email"},
}

# Forced-fact validators — return (ok, normalized_value, reason).
def _validate_concession(raw: Any) -> tuple[bool, str | None, str]:
    """Concession should be a dollar amount or '0' or 'none'. Free prose
    like 'two months free' won't parse and triggers a gap question."""
    if raw is None:
        return False, None, "missing"
    s = str(raw).strip().lower()
    if s in ("0", "none", "no concession", "n/a"):
        return True, "0", ""
    # Allow "$1500", "$1,500", "1500"
    m = re.search(r"\$?\s*([\d,]+)(?:\.\d+)?", s)
    if m:
        normalized = m.group(1).replace(",", "")
        if normalized.isdigit():
            return True, normalized, ""
    return False, None, "not parseable as a dollar amount"


def _validate_percent(raw: Any) -> tuple[bool, str | None, str]:
    """Occupancy must be a number 0–100."""
    if raw is None:
        return False, None, "missing"
    m = re.search(r"(\d{1,3}(?:\.\d+)?)\s*%?", str(raw))
    if not m:
        return False, None, "not numeric"
    val = float(m.group(1))
    if not (0 <= val <= 100):
        return False, None, "out of range 0-100"
    return True, f"{val:g}", ""


def _validate_rpm_email(raw: Any) -> tuple[bool, str | None, str]:
    """RPM convention is first.last@rpmliving.com. Other domains rejected."""
    if raw is None:
        return False, None, "missing"
    s = str(raw).strip().lower()
    if not re.match(r"^[a-z]+\.[a-z]+(?:-[a-z]+)?@rpmliving\.com$", s):
        return False, None, "must match first.last@rpmliving.com"
    return True, s, ""


_VALIDATORS = {
    "concession": _validate_concession,
    "percent":    _validate_percent,
    "rpm_email":  _validate_rpm_email,
}


# ── Typo / spell-check ──────────────────────────────────────────────────────
# Lightweight heuristic — full pyspellchecker integration is overkill given
# free-text is narrowly scoped to a 280-char field. We catch obvious patterns
# and let the AI-slop classifier do the heavy lift on prose quality.

_TYPO_PATTERNS = [
    (re.compile(r"\b(\w+)\s+\1\b", re.IGNORECASE), "duplicated word"),
    (re.compile(r"\b\w*?(\w)\1{3,}\w*?\b"),         "letter repeated 4+ times"),
    (re.compile(r"\s{3,}"),                          "excessive whitespace"),
    (re.compile(r"[^\x00-\x7F]"),                    "non-ASCII character"),
]


def detect_typos(text: str) -> list[dict[str, str]]:
    """Return a list of typo flags found in `text`. Empty list = clean."""
    if not text:
        return []
    flags: list[dict[str, str]] = []
    for pattern, reason in _TYPO_PATTERNS:
        m = pattern.search(text)
        if m:
            flags.append({"reason": reason, "snippet": m.group(0)[:60]})
    return flags


# ── AI-slop classifier ─────────────────────────────────────────────────────


_SLOP_CLASSIFIER_PROMPT = """\
Score the following property-marketing free-text answer for AI-slop risk.

AI-slop = generic, hedged, marketing-fluff prose that reads like a ChatGPT \
output. Specific human voice = concrete details, named places, real \
numbers, observable specifics.

Return ONLY a JSON object with this exact shape:
{"slop_score": <float 0.0-1.0>, "reason": "<one short sentence>"}

A score above 0.7 means the field reads as generic AI copy and should be \
verified with the Community Manager. A score below 0.3 means it's clearly \
specific to this property.

Field name: {field_name}
Field text:
\"\"\"{text}\"\"\"
"""


def score_slop(field_name: str, text: str) -> dict[str, Any]:
    """Run Claude Haiku to score a free-text field for AI-slop risk.

    Returns {slop_score, reason}. On any error, returns a neutral score of
    0.5 so the gap engine still surfaces the field for CM review (failing
    open is the right default — slop slipping through is worse than a few
    extra gap questions).
    """
    if not text or len(text.strip()) < 10:
        return {"slop_score": 0.0, "reason": "text too short to classify"}

    try:
        import anthropic
        from config import ANTHROPIC_API_KEY, CLAUDE_BRIEF_MODEL

        if not ANTHROPIC_API_KEY:
            return {"slop_score": 0.5, "reason": "ANTHROPIC_API_KEY not set"}

        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model=CLAUDE_BRIEF_MODEL,
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": _SLOP_CLASSIFIER_PROMPT.format(field_name=field_name, text=text[:2000]),
            }],
        )
        raw = "".join(b.text for b in message.content if hasattr(b, "text")).strip()
        # Strip code-fence if Claude wrapped the JSON
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE | re.MULTILINE).strip()
        parsed = json.loads(raw)
        score = float(parsed.get("slop_score", 0.5))
        reason = str(parsed.get("reason", ""))[:200]
        return {"slop_score": max(0.0, min(1.0, score)), "reason": reason}
    except Exception as e:
        logger.warning("slop classifier failed for %s: %s", field_name, e)
        return {"slop_score": 0.5, "reason": f"classifier error: {type(e).__name__}"}


# ── Main entry: review_intake ───────────────────────────────────────────────


def review_intake(payload: dict[str, Any]) -> dict[str, Any]:
    """Score an intake submission and synthesize gap questions.

    Returns:
      {
        "field_trust":    {field: trust_tag},  # one per field
        "completeness":   float 0-1,
        "ai_slop_score":  float 0-1,           # weighted avg over free-text fields
        "typo_flags":     {field: [{reason, snippet}, ...]},
        "validation_errors": {field: reason},
        "gap_questions":  [{field, label, prompt, type, options?}, ...]
      }
    """
    from config import AI_SLOP_FLAG_THRESHOLD

    field_trust: dict[str, str] = {}
    typo_flags: dict[str, list[dict[str, str]]] = {}
    validation_errors: dict[str, str] = {}
    gap_questions: list[dict[str, Any]] = []
    slop_scores: list[float] = []

    present_count = 0
    for field, rule in REQUIRED_FIELDS.items():
        raw = payload.get(field)
        present = bool(raw) and (not isinstance(raw, list) or len(raw) > 0)
        if present:
            present_count += 1
        field_trust[field] = rule["trust"]

        if not present:
            gap_questions.append({
                "field":  field,
                "label":  rule["label"],
                "prompt": f"We didn't get {rule['label'].lower()} on the intake form. Can you provide it?",
                "type":   "text",
            })
            continue

        # Forced-fact validation
        if rule.get("validate"):
            validator = _VALIDATORS.get(rule["validate"])
            if validator:
                ok, _normalized, reason = validator(raw)
                if not ok:
                    validation_errors[field] = reason
                    gap_questions.append({
                        "field":  field,
                        "label":  rule["label"],
                        "prompt": f"The value for {rule['label']} ({raw!r}) didn't parse — {reason}. Please re-enter.",
                        "type":   "text",
                    })

        # Typo + slop detection on free-text
        if rule["trust"] == "free_text" and isinstance(raw, str):
            t_flags = detect_typos(raw)
            if t_flags:
                typo_flags[field] = t_flags
            slop = score_slop(field, raw)
            slop_scores.append(slop["slop_score"])
            if slop["slop_score"] >= AI_SLOP_FLAG_THRESHOLD:
                gap_questions.append({
                    "field":  field,
                    "label":  rule["label"],
                    "prompt": (
                        f"The intake answer for '{rule['label']}' reads as generic "
                        f"marketing copy ({slop['reason']}). Can you give a more "
                        f"specific, on-the-ground answer?"
                    ),
                    "type":   "text",
                    "current_value": raw,
                })

    completeness = present_count / len(REQUIRED_FIELDS) if REQUIRED_FIELDS else 1.0
    avg_slop = sum(slop_scores) / len(slop_scores) if slop_scores else 0.0

    # Anti-slop nudge: if the PMA didn't supply a single ILS URL, surface a
    # gentle gap question. ILS reviews are the strongest non-fabricable
    # signal we can ground the brief in.
    ils_present = any(
        payload.get(k) for k in ("ils_apartments_com", "ils_zillow", "ils_other")
    )
    if not ils_present:
        gap_questions.append({
            "field":  "ils_apartments_com",
            "label":  "Apartments.com / Zillow URL",
            "prompt": (
                "We didn't get an ILS profile URL (apartments.com, zillow, etc.). "
                "Real resident reviews from those pages are our strongest signal — "
                "can you share the property's ILS profile URLs?"
            ),
            "type":   "text",
        })

    return {
        "field_trust":       field_trust,
        "completeness":      round(completeness, 3),
        "ai_slop_score":     round(avg_slop, 3),
        "typo_flags":        typo_flags,
        "validation_errors": validation_errors,
        "gap_questions":     gap_questions,
    }


# ── Trigger the HubSpot workflow ────────────────────────────────────────────


def _now_ms() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)


def trigger_gap_email_workflow(
    company_id: str,
    gap_questions: list[dict[str, Any]],
    *,
    target: str = "send_cm_email",
) -> dict[str, Any]:
    """Set the company properties HubSpot watches, including a single-use
    response token for the /onboarding/gap-response/<token> link.

    Returns {token, action, expires_at}.
    """
    import requests
    from config import GAP_REVIEW_TOKEN_TTL_DAYS, HUBSPOT_API_KEY

    token = secrets.token_urlsafe(24)
    expires_at_ms = _now_ms() + GAP_REVIEW_TOKEN_TTL_DAYS * 24 * 3600 * 1000

    questions_json = json.dumps(gap_questions)[:65000]  # HubSpot textarea limit

    payload = {
        "properties": {
            "rpm_gap_review_action":    target,
            "rpm_gap_review_token":     token,
            "rpm_gap_review_questions": questions_json,
            "rpm_gap_review_status":    "none",  # workflow flips to 'sent' after task creation
        }
    }
    url = f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}"
    r = requests.patch(
        url,
        headers={"Authorization": f"Bearer {HUBSPOT_API_KEY}", "Content-Type": "application/json"},
        json=payload,
        timeout=10,
    )
    r.raise_for_status()
    logger.info("gap_review: triggered %s for company=%s (token=%s)", target, company_id, token[:8])
    return {"token": token, "action": target, "expires_at": expires_at_ms}


def mark_response_received(company_id: str) -> None:
    """Stamp rpm_gap_review_response_at when the CM submits the response form.

    The HubSpot workflow watches this property and closes out the open tasks
    + flips rpm_gap_review_status = 'responded'.
    """
    import requests
    from config import HUBSPOT_API_KEY

    payload = {"properties": {"rpm_gap_review_response_at": _now_ms()}}
    url = f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}"
    requests.patch(
        url,
        headers={"Authorization": f"Bearer {HUBSPOT_API_KEY}", "Content-Type": "application/json"},
        json=payload,
        timeout=10,
    ).raise_for_status()
