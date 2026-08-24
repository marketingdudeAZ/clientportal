"""Ask — orchestration: resolve → assemble → narrate → cache.

Shape copied from `swot._assemble_context` / `generate_swot`: gather one
property's context, hand it to Claude, cache the result for 24h on the HubSpot
company record, and dedupe concurrent generation for the same property. Three
deliberate departures:

* SYNCHRONOUS. SWOT is a page-load side effect, so it generates in a daemon
  thread and the client polls. Ask is a click on a question — the person is
  waiting for that specific answer, so the POST returns it. The in-flight set
  is still here: a second click while the first is generating gets `generating`
  back rather than a second LLM call.

* EVIDENCE IS COMPUTED, NOT WRITTEN. Every number in the answer is formatted in
  `ask_context` before the model sees it, and a finding that cites no evidence
  index is DROPPED in `_coerce_findings`. The model chooses which receipts to
  quote; it never produces one. That is the mechanism behind "never a bare
  percentage" — a claim with no numerator and denominator cannot survive.

* IT DEGRADES INTO A REAL ANSWER. With no ANTHROPIC_API_KEY, or on any API
  failure, `_fallback` writes the narrative from the same signals. The client
  gets the same shape with `narrator: "rules"`, never an error page. The
  answer's floor is the arithmetic; the model only adds prose.

R1: this module reads `uuid` through the Property Resolver and never writes it.
Cache writes go through `hubspot_client.patch_company`, which raises
R1Violation at the boundary if a payload ever touches an immutable property.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

from skills import ask_context, question_registry

logger = logging.getLogger(__name__)

CACHE_HOURS = float(os.environ.get("ASK_CACHE_HOURS", "24"))
CACHE_PROP = "portal_ask_cache"
CACHE_TO_HUBSPOT = os.environ.get("ASK_CACHE_HUBSPOT", "true").lower() != "false"
MAX_TOKENS = int(os.environ.get("ASK_MAX_TOKENS", "1400"))

_INFLIGHT = set()                       # (company_id, question_key) generating now
_INFLIGHT_LOCK = threading.Lock()
_MEMO: Dict[str, Dict[str, Any]] = {}   # company_id -> {question_key: entry}
_HUBSPOT_CACHE_BROKEN = False           # stop retrying a PATCH that 400s

SYSTEM_PROMPT = (
    "You are a senior multifamily digital-marketing analyst answering ONE preset "
    "question about ONE property for RPM Living's client portal.\n"
    "\n"
    "You are given a numbered EVIDENCE list. Every line already contains its "
    "numerator, its denominator and its source. These are the only numbers that "
    "exist for you.\n"
    "\n"
    "Rules:\n"
    "1. Never state a number, percentage or direction that is not in the "
    "evidence list. Do not compute new figures from the ones given.\n"
    "2. Never write a bare percentage. When you cite a change, carry the two "
    "raw values with it, exactly as the evidence line does "
    "(\"leads fell 170 to 114, down 32.9%\").\n"
    "3. Every finding must cite at least one evidence index. A finding you "
    "cannot support with an index must not be written.\n"
    "4. If an input was missing, say which one and what it would have told us. "
    "Do not fill the gap with an assumption, and do not imply the data is zero.\n"
    "5. Write to a leasing director in second person. Direct, specific, no "
    "filler, no marketing language. Do not promise lease counts.\n"
    "6. If the honest answer is 'not much' or 'we cannot tell from this', say "
    "that.\n"
    "\n"
    "Return ONLY valid JSON, no markdown fence, in exactly this shape:\n"
    '{"headline": "one sentence", "summary": "2-4 sentences", '
    '"findings": [{"title": "short", "detail": "1-3 sentences", '
    '"evidence": [0, 2]}], "next_step": "one concrete action or null", '
    '"not_evidenced": ["what you could not answer and why"]}\n'
    "2 to 5 findings, ordered most important first."
)


# ── the ONE Claude call ────────────────────────────────────────────────────

def _complete(system: str, user: str, max_tokens: int = MAX_TOKENS) -> str:
    """The only place this workstream talks to a model.

    Everything above it deals in dicts, so swapping the transport is a change
    to this function body and nothing else.

    # TODO(workstream-A): route through skills.llm_gateway
    """
    from config import ANTHROPIC_API_KEY, CLAUDE_AGENT_MODEL
    import anthropic

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    msg = client.messages.create(
        model=CLAUDE_AGENT_MODEL,
        max_tokens=max_tokens,
        temperature=0.2,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return msg.content[0].text.strip()


def _llm_available() -> bool:
    try:
        from config import ANTHROPIC_API_KEY
    except Exception:                                           # noqa: BLE001
        return False
    return bool(ANTHROPIC_API_KEY)


# ── prompt assembly ────────────────────────────────────────────────────────

def _relevant_signals(ctx: ask_context.AskContext, focus: str) -> List[Dict[str, Any]]:
    signals = ctx.signals()
    if focus == question_registry.FOCUS_POSITIVE:
        signals = [s for s in signals if s.get("sentiment") == "positive"]
    elif focus == question_registry.FOCUS_NEGATIVE:
        signals = [s for s in signals if s.get("sentiment") == "negative"]
    return signals


def _identity_block(ident: Dict[str, Any]) -> str:
    bits = [
        "name: %s" % (ident.get("name") or "unknown"),
        "market: %s" % (ident.get("market") or "unknown"),
        "units: %s" % (ident.get("unit_count") or "unknown"),
        "leasing status: %s" % (ident.get("occupancy") or "unknown"),
    ]
    return "\n".join("  " + b for b in bits)


def build_prompt(question, ctx: ask_context.AskContext,
                 evidence: List[str]) -> str:
    lines = ["PROPERTY", _identity_block(ctx.identity), "",
             "QUESTION", "  " + question.label, "",
             "HOW TO ANSWER THIS ONE", "  " + question.instruction, "",
             "EVIDENCE (the only numbers that exist)"]
    if evidence:
        for i, e in enumerate(evidence):
            lines.append("  [%d] %s" % (i, e))
    else:
        lines.append("  (none — every input for this question was unavailable)")

    caveats = ctx.caveats()
    if caveats:
        lines += ["", "DATA-QUALITY CAVEATS (must reach the reader)"]
        lines += ["  - " + c for c in caveats]

    missing = ctx.missing_inputs()
    if missing:
        lines += ["", "INPUTS THAT WERE NOT AVAILABLE (name these; do not guess past them)"]
        lines += ["  - %s (%s): %s" % (m["input"], m["source"], m["reason"]) for m in missing]

    lines += ["", "Answer the question now as JSON."]
    return "\n".join(lines)


# ── response handling ──────────────────────────────────────────────────────

def _parse_json(text: str) -> Optional[dict]:
    t = (text or "").strip()
    if t.startswith("```"):
        parts = t.split("```")
        t = parts[1] if len(parts) > 1 else t
        if t.lstrip().lower().startswith("json"):
            t = t.lstrip()[4:]
    try:
        obj = json.loads(t)
    except (ValueError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


def _coerce_findings(raw: Any, evidence: List[str]) -> List[Dict[str, Any]]:
    """Keep only findings that cite a real evidence index.

    This is the enforcement point for "every claim needs its numerator,
    denominator and source": an unsupported finding is dropped rather than
    printed with a hedge, because a hedged unsupported claim still reads as a
    claim to the client it is shown to.
    """
    out: List[Dict[str, Any]] = []
    for item in (raw or []):
        if not isinstance(item, dict):
            continue
        idxs = []
        for i in (item.get("evidence") or []):
            try:
                i = int(i)
            except (TypeError, ValueError):
                continue
            if 0 <= i < len(evidence):
                idxs.append(i)
        if not idxs:
            logger.info("ask: dropped an unsupported finding %r", item.get("title"))
            continue
        out.append({
            "title": str(item.get("title") or "").strip(),
            "detail": str(item.get("detail") or "").strip(),
            "evidence": [evidence[i] for i in sorted(set(idxs))],
        })
    return out


# ── deterministic fallback ─────────────────────────────────────────────────

_DIRECTION_WORD = {"up": "rose", "down": "fell", "flat": "held roughly flat"}


def _fallback(question, ctx: ask_context.AskContext,
              signals: List[Dict[str, Any]], evidence: List[str]) -> Dict[str, Any]:
    """Answer from arithmetic alone. Same shape, no model."""
    findings = []
    for s in signals[:4]:
        idx = evidence.index(s["evidence"]) if s.get("evidence") in evidence else None
        if idx is None:
            continue
        findings.append({
            "title": "%s %s" % (s.get("metric", "metric").capitalize(),
                                _DIRECTION_WORD.get(s.get("direction"), "moved")),
            "detail": s["evidence"],
            "evidence": [evidence[idx]],
        })
    if not findings and evidence:
        findings = [{"title": "What the data shows", "detail": evidence[0],
                     "evidence": [evidence[0]]}]

    missing = ctx.missing_inputs()
    if findings:
        headline = findings[0]["detail"]
        summary = ("Answered from the property's own figures without a written "
                   "narrative. %d measured change(s) are listed below, each with "
                   "its own numbers." % len(findings))
    else:
        headline = "There is not enough data on file to answer this question."
        summary = ("None of the inputs this question needs returned usable data, "
                   "so there is nothing to report yet.")
    return {
        "headline": headline,
        "summary": summary,
        "findings": findings,
        "next_step": None,
        "not_evidenced": ["%s: %s" % (m["input"], m["reason"]) for m in missing],
    }


# ── cache ──────────────────────────────────────────────────────────────────

def _cache_read(company_id: str) -> Dict[str, Any]:
    memo = _MEMO.get(str(company_id))
    if memo is not None:
        return memo
    if not CACHE_TO_HUBSPOT:
        return {}
    try:
        import hubspot_client
        props = hubspot_client.get_company(str(company_id), [CACHE_PROP]) or {}
        raw = props.get(CACHE_PROP)
        data = json.loads(raw) if raw else {}
        if not isinstance(data, dict):
            data = {}
    except Exception as exc:                                    # noqa: BLE001
        logger.debug("ask: cache read failed for %s: %s", company_id, exc)
        data = {}
    _MEMO[str(company_id)] = data
    return data


def _cache_get(company_id: str, key: str) -> Optional[Dict[str, Any]]:
    entry = _cache_read(company_id).get(key)
    if not isinstance(entry, dict):
        return None
    if (time.time() - float(entry.get("cached_at") or 0)) / 3600.0 >= CACHE_HOURS:
        return None
    return entry


def _cache_put(company_id: str, key: str, answer: Dict[str, Any]) -> None:
    global _HUBSPOT_CACHE_BROKEN
    store = _cache_read(company_id)
    store[key] = {"answer": answer, "cached_at": time.time()}
    _MEMO[str(company_id)] = store
    if not CACHE_TO_HUBSPOT or _HUBSPOT_CACHE_BROKEN:
        return
    try:
        import hubspot_client
        # R1: patch_company rejects any payload touching an immutable property,
        # `uuid` included. The only key here is the cache blob.
        hubspot_client.patch_company(str(company_id), {CACHE_PROP: json.dumps(store)})
    except Exception as exc:                                    # noqa: BLE001
        _HUBSPOT_CACHE_BROKEN = True
        logger.warning("ask: durable cache disabled for this process — "
                       "PATCH %s failed (%s)", CACHE_PROP, exc)


def clear_cache() -> None:
    """Drop the process-local cache. For tests and for `force=True` callers."""
    global _HUBSPOT_CACHE_BROKEN
    _MEMO.clear()
    _HUBSPOT_CACHE_BROKEN = False
    with _INFLIGHT_LOCK:
        _INFLIGHT.clear()


# ── the public entry point ─────────────────────────────────────────────────

def answer(identifier: str, question_key: str, *, force: bool = False) -> Dict[str, Any]:
    """Answer one preset question about one property.

    Raises `question_registry.UnknownQuestion` for an unknown key and
    `property_resolver.PropertyNotFound` / `AmbiguousProperty` for an
    unresolvable property — the route maps those to 404/409. Everything else
    degrades into an answer that names what it could not see.
    """
    question = question_registry.get(question_key)

    from skills import property_resolver
    identity = property_resolver.resolve(identifier)
    company_id = str(identity.company_id)

    if not force:
        hit = _cache_get(company_id, question.key)
        if hit:
            out = dict(hit["answer"])
            out["cached"] = True
            out["cached_at"] = hit.get("cached_at")
            return out

    inflight_key = (company_id, question.key)
    with _INFLIGHT_LOCK:
        if inflight_key in _INFLIGHT:
            return {"question": question.key, "label": question.label,
                    "company_id": company_id, "generating": True, "answered": False,
                    "message": "This answer is already being generated."}
        _INFLIGHT.add(inflight_key)
    try:
        result = _generate(question, identity)
    finally:
        with _INFLIGHT_LOCK:
            _INFLIGHT.discard(inflight_key)

    if result.get("answered"):
        _cache_put(company_id, question.key, result)
    return result


def _generate(question, identity) -> Dict[str, Any]:
    ctx = ask_context.assemble(identity, question.pulls)

    base = {
        "question": question.key,
        "label": question.label,
        "company_id": str(identity.company_id),
        "property_uuid": identity.uuid,
        "property_name": identity.name,
        "viz": question.viz.to_dict() if question.viz else None,
        "viz_data": (ctx.pulls[question.viz.pull].data
                     if question.viz and question.viz.pull in ctx.pulls
                     and ctx.pulls[question.viz.pull].available else None),
        "evidence": ctx.evidence(),
        "caveats": ctx.caveats(),
        "missing_inputs": ctx.missing_inputs(),
        "inputs": {k: {"available": p.available, "source": p.source,
                       "caveat": p.caveat, "missing_reason": p.missing_reason,
                       "quality": p.quality}
                   for k, p in ctx.pulls.items()},
        "generated_at": time.time(),
        "cached": False,
    }

    unmet = [r for r in question.required if not ctx.pulls.get(r) or not ctx.pulls[r].available]
    if unmet:
        reasons = [ctx.pulls[r].missing_reason for r in unmet
                   if ctx.pulls.get(r) and ctx.pulls[r].missing_reason]
        base.update({
            "answered": False,
            "narrator": "none",
            "headline": "We can't answer this one for %s yet."
                        % (identity.name or "this property"),
            "summary": " ".join(reasons) or
                       "A required input for this question was not available.",
            "findings": [],
            "next_step": None,
            "not_evidenced": ["%s: %s" % (m["input"], m["reason"])
                              for m in ctx.missing_inputs()],
        })
        return base

    signals = _relevant_signals(ctx, question.focus)
    evidence = base["evidence"]
    narrative, narrator = None, "rules"

    if _llm_available():
        try:
            raw = _complete(SYSTEM_PROMPT, build_prompt(question, ctx, evidence))
            parsed = _parse_json(raw)
            if parsed:
                findings = _coerce_findings(parsed.get("findings"), evidence)
                if findings:
                    narrative = {
                        "headline": str(parsed.get("headline") or "").strip(),
                        "summary": str(parsed.get("summary") or "").strip(),
                        "findings": findings,
                        "next_step": (str(parsed["next_step"]).strip()
                                      if parsed.get("next_step") else None),
                        "not_evidenced": [str(x) for x in (parsed.get("not_evidenced") or [])],
                    }
                    narrator = "claude"
                else:
                    logger.warning("ask: every finding for %s was unsupported — "
                                   "falling back to rules", question.key)
            else:
                logger.warning("ask: unparseable model output for %s", question.key)
        except Exception as exc:                                # noqa: BLE001
            logger.error("ask: narrative failed for %s: %s", question.key, exc)

    if narrative is None:
        narrative = _fallback(question, ctx, signals, evidence)

    # A dark input is always visible, whatever the narrator chose to mention.
    stated = set(narrative.get("not_evidenced") or [])
    for m in ctx.missing_inputs():
        line = "%s: %s" % (m["input"], m["reason"])
        if line not in stated:
            narrative.setdefault("not_evidenced", []).append(line)

    base.update(narrative)
    base["answered"] = True
    base["narrator"] = narrator
    base["signals"] = signals
    return base
