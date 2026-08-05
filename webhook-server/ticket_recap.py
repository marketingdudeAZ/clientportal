"""Ticket → client-facing recap generator (ClickUp complete → HubSpot note).

Turns an internal ClickUp ticket (description + work-thread comments) into a
short, CLIENT-FACING activity note, run through a positioning layer that strips
internal coaching / self-blame and reframes problems appropriately. GENERATES
ONLY — posting is the caller's decision (shadow mode posts nothing).

See docs/clickup-ticket-recap-plan.md for the framing policy this encodes.
"""
from __future__ import annotations

import json
import logging
import re

import rec_voice

logger = logging.getLogger(__name__)

# Ticket-type → a short client framing hint. dispo_cancel is intentionally
# absent — the caller must NOT generate a client note for off-boarding tickets.
TYPE_FRAMING = {
    "new_account_build": "onboarding complete — campaigns are live",
    "budget_update":     "budget change is live and pacing",
    "general":           "the request has been handled",
    "creative_update":   "creative / ad copy refreshed",
    "performance_review": "a performance review and the optimizations we made",
    "rebrand":           "rebrand rollout complete",
}
EXCLUDED_TYPES = {"dispo_cancel"}  # never write a client note for these

# The house rules live in rec_voice.CLIENT_VOICE_RULES and are shared with every
# other client-facing generator. They are pulled in here as NAMED FRAGMENTS
# rather than as the assembled block because the recap's bullet list interleaves
# shared rules with recap-specific ones, and the original order is preserved
# byte-for-byte (tests/test_ticket_recap_prompt.py pins it). Editing a shared
# rule in rec_voice.py moves the recap and the Loop recs together.
#
# Recap-specific and deliberately NOT shared:
#   - the ticket-shaped preamble and the 2-4 sentence contract
#   - "Say what we did and the outcome" (the recap is retrospective; a Loop rec
#     is prospective, and this clause would actively mislead it)
#   - the surface-problems / client-input framing rules
#   - the forward-looking close
#   - the JSON output schema
SYSTEM_PROMPT = (
    "You write SHORT, client-facing activity notes for a multifamily digital-"
    "marketing agency (RPM). The input is an INTERNAL support ticket — a "
    "description plus the team's work-thread comments. Turn it into 2–4 sentences "
    "that a client (property owner / property-marketing team) can read on their "
    "account record.\n\n"
    "RULES:\n"
    f"{rec_voice.RULE_VOICE_WE} Say what we did and the outcome.\n"
    f"{rec_voice.RULE_STRIP_INTERNAL}\n"
    "- Do NOT hide problems — surface them, but reframe how we speak about them.\n"
    "- When something was missing or wrong because of a client / property-marketing "
    "input (e.g. not provided at onboarding), frame it as that input being needed, "
    "not as an RPM error.\n"
    f"{rec_voice.RULE_INTEGRITY}\n"
    "- End forward-looking when it reads naturally.\n"
    f"{rec_voice.RULE_PLAIN_PUNCTUATION}\n"
    f"{rec_voice.numbers_rule('the ticket data provided below')}\n\n"
    f"{rec_voice.BLOCK_PRODUCT_ACCURACY}\n"
    f"{rec_voice.RULE_TARGETING_LOCATION}\n\n"
    "Return ONLY JSON: {\"note\":\"…\", \"surfaced_problem\":true|false, "
    "\"attribution\":\"none|property_marketing|external|internal\", "
    "\"needs_review\":true|false, \"review_reason\":\"…\"}. Set needs_review=true if "
    "the ticket is sensitive or ambiguous, or you are unsure the framing is safe to "
    "show a client."
)

# Deterministic backstop: if any of these survive into the note, force review —
# even if the model thought the note was clean.
#
# The coaching / self-blame patterns are recap-specific: those terms arrive from
# manager↔specialist work threads, an input only the recap has. The internal-tool
# and targeting patterns back shared rules, so they come from rec_voice.
_RECAP_REDACT_TERMS = (
    r"\bspecialist\b", r"\bmanager\b", r"\bcoach(?:ed|ing)?\b",
    r"\bmisconfigured\b", r"\bwe messed up\b", r"\bour (?:mistake|error|fault)\b",
    r"\bwasn'?t configured\b", r"\bset ?up (?:wrong|incorrectly)\b",
)
_REDACT = [re.compile(p, re.I) for p in (
    _RECAP_REDACT_TERMS + rec_voice.INTERNAL_TERMS + rec_voice.TARGETING_TERMS
)]


def infer_ticket_type(task: dict) -> str:
    """Map a ClickUp task's list name to a ticket type (robust to id churn)."""
    name = ((task.get("list") or {}).get("name") or "").lower()
    if "account build" in name or "new account" in name:
        return "new_account_build"
    if "budget" in name:
        return "budget_update"
    if "dispo" in name or "cancel" in name:
        return "dispo_cancel"
    if "creative" in name or "ad copy" in name:
        return "creative_update"
    if "performance" in name:
        return "performance_review"
    if "rebrand" in name:
        return "rebrand"
    return "general"


# ClickUp field names that are noise/internal — never feed them to the recap.
_FIELD_SKIP = {"qa status", "task progress", "comment count", "priority", "market",
               "account manager", "submitter email", "property url", "property domain",
               "website", "property code",
               # positioning/aspirational input — NOT a confirmed action; feeding it
               # makes the recap claim targeting/locations we may not have configured.
               "key messages", "key message", "target audience", "positioning"}


def _internal_narrative(task: dict, comments: list) -> str:
    parts = []
    if task.get("name"):
        parts.append("Title: " + task["name"])
    desc = (task.get("text_content") or task.get("description") or "").strip()
    if desc:
        parts.append("Description:\n" + desc)
    # Structured ticket fields (real budgets/scope) so the model uses ACTUAL
    # values instead of inventing numbers. Matching/internal fields are skipped.
    try:
        from clickup_client import _resolve_field_value
        detail = []
        for f in (task.get("custom_fields") or []):
            nm = (f.get("name") or "").strip()
            if not nm or nm.lower() in _FIELD_SKIP:
                continue
            val = _resolve_field_value(f)
            if val in (None, "", []):
                continue
            if f.get("type") == "currency":
                try:
                    val = "$" + format(int(round(float(val))), ",")
                except (TypeError, ValueError):
                    pass
            if isinstance(val, list):
                val = ", ".join(str(x) for x in val)
            detail.append(f"- {nm}: {val}")
        if detail:
            parts.append("Ticket details (use these exact values; do not invent others):\n"
                         + "\n".join(detail))
    except Exception:
        pass
    if comments:
        parts.append("Work thread (chronological):")
        for c in comments:
            t = (c.get("text") or "").strip()
            if t:
                parts.append("- " + t)
    return "\n".join(parts)


def generate_recap(task: dict, comments: list, ticket_type: str = "general") -> dict:
    """Return the client-facing draft + review metadata. Never posts.

    {note, needs_review, review_reason, flags, attribution, surfaced_problem}
    """
    result = {"note": "", "needs_review": True, "review_reason": "not generated",
              "flags": [], "attribution": "none", "surfaced_problem": False}
    narrative = _internal_narrative(task, comments)
    if not narrative.strip():
        result["review_reason"] = "empty ticket — nothing to summarize"
        return result

    hint = TYPE_FRAMING.get(ticket_type, "the request has been handled")
    try:
        from config import ANTHROPIC_API_KEY, CLAUDE_DIGEST_MODEL
        if not ANTHROPIC_API_KEY:
            result["review_reason"] = "LLM unavailable"
            return result
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        extra = ""
        if ticket_type in ("budget_update", "new_account_build"):
            extra = ("\n\nThis is a " + ("budget update" if ticket_type == "budget_update"
                     else "new account build") + ": DO state the specific per-channel "
                     "budget figures shown in the ticket data above (e.g. 'increased Paid "
                     "Search to $4,000 and Performance Max to $1,500'). Use ONLY figures "
                     "that appear in the data — never invent or estimate any. "
                     "Keep it high level: budgets and channels turned on, what is running, "
                     "tracking set up, and deliverables. Do NOT mention any location, "
                     "neighborhood, district, market, audience, or positioning, and do NOT "
                     "describe who or where the campaigns target.")
        user = f"Ticket type framing hint: {hint}\n\n{narrative}{extra}"
        msg = client.messages.create(
            model=CLAUDE_DIGEST_MODEL, max_tokens=500, temperature=0,
            system=SYSTEM_PROMPT, messages=[{"role": "user", "content": user}],
        )
        raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
        m = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(m.group(0)) if m else {}
        result.update({
            "note": (data.get("note") or "").strip(),
            "needs_review": bool(data.get("needs_review", False)),
            "review_reason": data.get("review_reason") or "",
            "attribution": data.get("attribution") or "none",
            "surfaced_problem": bool(data.get("surfaced_problem", False)),
        })
    except Exception as e:
        logger.warning("ticket_recap generation failed: %s", e)
        result["review_reason"] = f"generation error: {e}"
        return result

    # deterministic redaction backstop — any internal term forces review
    hits = []
    for pat in _REDACT:
        m = pat.search(result["note"] or "")
        if m:
            hits.append(m.group(0))
    if hits:
        result["flags"] = sorted(set(hits))
        result["needs_review"] = True
        if not result["review_reason"]:
            result["review_reason"] = "internal terms detected: " + ", ".join(result["flags"])
    if not (result["note"] or "").strip():
        result["needs_review"] = True
        result["review_reason"] = result["review_reason"] or "empty note returned"
    return result
