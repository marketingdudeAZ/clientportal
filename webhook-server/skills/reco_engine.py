"""Layer 2 — one ranked queue out of every recommendation rule.

WHY THIS EXISTS
    Rules are cheap and attention is not. 109 managed properties times a dozen
    rules is over a thousand findings a week, which is the same as none: nobody
    reads past Wednesday. This module's job is SUPPRESSION as much as merging —
    it decides the handful that reach a person, and keeps the rest visible but
    quiet.

WHAT IT DOES
    * Calls each registered producer (`reco_digital`, `reco_seo`) and validates
      every recommendation against the shared contract. A malformed one is
      dropped and counted, never rendered half-built.
    * Ranks by consequence: severity and confidence, weighted by how much rent
      is actually exposed at that property, so a 300-unit lease-up outranks a
      stable 80-unit property with the same rule firing.
    * Dedupes: one live recommendation per (rule_key, property). A rule that
      fires every night does not become a queue of identical cards.
    * Drops anything already decided, and anything a person said "not now" to
      inside its cool-off window.
    * Caps what surfaces — per property and portfolio-wide — and reports what it
      held back rather than hiding it.

WHAT IT REFUSES
    * A recommendation whose action would change targeting. Housing is a Special
      Ad Category; that is a compliance failure, not a ranking question, so it
      is dropped loudly (counted in `refused`) even if a producer emits one.
    * A spend-increasing action without `requires_signed_deal` set. Money moves
      on a signature, never on a card.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Producers, in the order their output is considered when scores tie.
# `workspace_signals` is not a new rule set — it is the one that already ships
# (occupancy drop, stale inventory, lease wave, lead drop, spend pacing, data
# stale). It is adapted here rather than re-implemented, so the portal has ONE
# queue instead of a signals list beside a recommendations list.
PRODUCERS: Tuple[str, ...] = ("workspace_signals", "reco_digital", "reco_seo")

CATEGORIES = ("cost", "vendors", "content", "creative", "compliance")
SEVERITIES = {"high": 3.0, "medium": 2.0, "low": 1.0}
ACTION_KINDS = {
    "budget_change", "new_ad_group", "pause", "creative_refresh", "tracking_fix",
    "landing_page", "content_brief", "page_fix", "schema", "listing_change",
    "internal_link", "none",
}
SPEND_INCREASING_KINDS = {"budget_change", "new_ad_group"}

# Words that only appear in an action that steers by audience or geography.
# Housing advertising cannot do either, so a producer emitting one is a bug.
FORBIDDEN_ACTION_TERMS = (
    "radius", "zip", "postal", "audience", "lookalike", "look-alike",
    "remarketing", "retarget", "demographic", "age range", "gender",
    "income target", "geo target", "geotarget", "location target",
)

# How many surface at once. Past this, attention is the bottleneck, not insight.
MAX_PER_PROPERTY = 3
MAX_PORTFOLIO = 20

# A "not now" is respected for this long before the rule may ask again.
NOT_NOW_COOLOFF_DAYS = 30


def _today(today: Optional[date] = None) -> date:
    return today or date.today()


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _gap(field: str, source: str, message: str) -> Dict[str, str]:
    return {"field": field, "source": source, "message": message}


# --- validation ------------------------------------------------------------

def validate(reco: Any) -> Optional[str]:
    """None when the recommendation is well-formed, else why it was dropped."""
    if not isinstance(reco, dict):
        return "not an object"
    for key in ("id", "company_id", "rule_key", "category", "severity",
                "confidence", "found", "action"):
        if not reco.get(key):
            return "missing %s" % key
    if reco["category"] not in CATEGORIES:
        return "unknown category %r" % reco["category"]
    if reco["severity"] not in SEVERITIES:
        return "unknown severity %r" % reco["severity"]
    try:
        confidence = int(reco["confidence"])
    except (TypeError, ValueError):
        return "confidence is not a number"
    if not 1 <= confidence <= 10:
        return "confidence %s out of range" % confidence
    action = reco["action"]
    if not isinstance(action, dict):
        return "action is not an object"
    if action.get("kind") not in ACTION_KINDS:
        return "unknown action kind %r" % action.get("kind")
    receipts = reco.get("receipts")
    if action["kind"] != "none" and not receipts:
        return "an action with no receipts"
    if receipts is not None and not isinstance(receipts, list):
        return "receipts is not a list"
    for receipt in receipts or []:
        if not isinstance(receipt, dict) or not receipt.get("source"):
            return "a receipt with no source"
    return None


def compliance_refusal(reco: Dict[str, Any]) -> Optional[str]:
    """Why this recommendation must never be shown, or None."""
    action = reco.get("action") or {}
    haystack = " ".join(str(v).lower() for v in (
        [action.get("kind", ""), reco.get("found", ""), reco.get("expect", "")]
        + [str(x) for x in (action.get("params") or {}).keys()]
        + [str(x) for x in (action.get("params") or {}).values()]))
    for term in FORBIDDEN_ACTION_TERMS:
        if term in haystack:
            return ("would change audience or geographic targeting (%r); housing "
                    "is a Special Ad Category" % term)
    if action.get("kind") in SPEND_INCREASING_KINDS and not action.get("requires_signed_deal"):
        return "would change spend without requiring a signed deal"
    return None


# --- ranking ---------------------------------------------------------------

def exposure_weight(context: Optional[Dict[str, Any]]) -> float:
    """How much is riding on this property, as a multiplier from 1.0 to ~3.0.

    Rent at risk is the honest weight: units times how many are unleased. With
    no availability data the weight is 1.0, so a property we know less about is
    never ranked above one we can measure.
    """
    if not context:
        return 1.0
    units = context.get("units")
    exposure = context.get("exposure")          # share unleased, 0-1
    try:
        units = float(units) if units not in (None, "") else None
    except (TypeError, ValueError):
        units = None
    try:
        exposure = float(exposure) if exposure not in (None, "") else None
    except (TypeError, ValueError):
        exposure = None
    if units is None:
        return 1.0
    scale = min(units / 250.0, 2.0)             # 250 units ≈ one full step
    if exposure is None:
        return 1.0 + (scale * 0.25)
    return 1.0 + scale * (0.5 + min(exposure, 0.5) * 3.0)


def score(reco: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> float:
    base = SEVERITIES.get(reco.get("severity"), 1.0)
    confidence = max(1, min(int(reco.get("confidence") or 1), 10)) / 10.0
    return round(base * confidence * exposure_weight(context), 4)


# --- suppression -----------------------------------------------------------

def _decided_keys(decisions: Optional[Dict[str, Any]], today: date) -> set:
    """rule keys that are settled, or inside a "not now" cool-off.

    `decisions` is {item_id: [{at, action, ...}]} — the same shape
    workspace_inbox.decision_history returns.
    """
    out = set()
    if not decisions:
        return out
    cutoff = today - timedelta(days=NOT_NOW_COOLOFF_DAYS)
    for item_id, history in (decisions or {}).items():
        if not history:
            continue
        latest = history[-1]
        if latest.get("undone"):
            continue
        rule_key = str(item_id).split(":")[0] if item_id else ""
        if not rule_key:
            continue
        action = (latest.get("action") or "").lower()
        if action in ("approve", "approved"):
            out.add(rule_key)
        elif action in ("not_now", "reject", "rejected", "dismiss"):
            at = str(latest.get("at") or "")[:10]
            try:
                when = date.fromisoformat(at) if at else None
            except ValueError:
                when = None
            if when is None or when >= cutoff:
                out.add(rule_key)
    return out


def rank_and_suppress(recos: List[Dict[str, Any]],
                      *, context: Optional[Dict[str, Any]] = None,
                      decisions: Optional[Dict[str, Any]] = None,
                      today: Optional[date] = None,
                      max_per_property: int = MAX_PER_PROPERTY) -> Dict[str, Any]:
    """Rank one property's recommendations and decide which ones surface."""
    today = _today(today)
    dropped: List[Dict[str, str]] = []
    refused: List[Dict[str, str]] = []
    kept: List[Dict[str, Any]] = []

    for reco in recos or []:
        problem = validate(reco)
        if problem:
            dropped.append({"id": str((reco or {}).get("id") or "?"), "reason": problem})
            continue
        refusal = compliance_refusal(reco)
        if refusal:
            logger.error("reco_engine refused %s: %s", reco.get("id"), refusal)
            refused.append({"id": reco["id"], "rule_key": reco.get("rule_key"),
                            "reason": refusal})
            continue
        kept.append(reco)

    # One live card per (rule_key, property): keep the strongest.
    by_rule: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for reco in kept:
        key = (str(reco.get("company_id")), str(reco.get("rule_key")))
        reco["_score"] = score(reco, context)
        current = by_rule.get(key)
        if current is None or reco["_score"] > current["_score"]:
            by_rule[key] = reco
    deduped = len(kept) - len(by_rule)

    settled = _decided_keys(decisions, today)
    live = [r for r in by_rule.values() if str(r.get("rule_key")) not in settled]
    already_decided = len(by_rule) - len(live)

    live.sort(key=lambda r: (-r["_score"], r.get("rule_key") or ""))
    surfaced, quiet = live[:max_per_property], live[max_per_property:]
    return {
        "surfaced": surfaced,
        "quiet": quiet,
        "counts": {"produced": len(recos or []), "surfaced": len(surfaced),
                   "quiet": len(quiet), "deduped": deduped,
                   "already_decided": already_decided,
                   "dropped": len(dropped), "refused": len(refused)},
        "dropped": dropped,
        "refused": refused,
    }


# --- producers -------------------------------------------------------------

# Severity words the existing signals use, mapped onto this contract's.
_SIGNAL_SEVERITY = {"critical": "high", "high": "high", "warning": "medium",
                    "medium": "medium", "info": "low", "low": "low"}

# What the shipped signals are really telling you to do. Anything unmapped
# becomes an observation rather than a guess at an action.
_SIGNAL_ACTION = {
    "stale_inventory": ("new_ad_group", "cost", ["paid_search", "ils"]),
    "lease_wave": ("budget_change", "cost", ["paid_search", "pmax"]),
    "lead_drop": ("tracking_fix", "cost", ["paid_search", "website"]),
    "occupancy_drop": ("none", "cost", ["paid_search"]),
    "spend_pacing": ("none", "cost", ["paid_search"]),
    "data_stale": ("none", "compliance", ["website"]),
}


def signals_producer(company_id: str, today: Optional[date] = None) -> Dict[str, Any]:
    """Adapt the signals that already ship into the shared contract.

    Deliberately an ADAPTER, not a rewrite: `skills/workspace_signals.py` is
    live, tested and tuned, and a second implementation of "is this property
    slipping" would drift from it within a month.
    """
    out: Dict[str, Any] = {"company_id": company_id, "recommendations": [],
                           "gaps": [], "rules_run": [], "rules_skipped": []}
    try:
        from skills import workspace_signals as ws
    except Exception as exc:  # noqa: BLE001
        out["gaps"].append(_gap("signals", "workspace_signals",
                                "The signals module is not available here."))
        logger.info("reco_engine: workspace_signals unavailable (%s)", exc)
        return out
    try:
        built = ws.build_signals("", company_id=company_id, today=today) or {}
    except Exception as exc:  # noqa: BLE001
        logger.error("reco_engine: signals failed for %s: %s", company_id, exc,
                     exc_info=True)
        out["gaps"].append(_gap("signals", "workspace_signals",
                                "Signals could not be built for this property."))
        return out

    out["gaps"].extend(built.get("gaps") or [])
    for signal in built.get("signals") or []:
        kind = signal.get("kind") or "signal"
        action_kind, category, channels = _SIGNAL_ACTION.get(
            kind, ("none", "cost", []))
        metric = signal.get("metric") or {}
        receipts = []
        if metric.get("source"):
            receipts.append({"label": signal.get("title") or kind,
                             "value": metric.get("value"),
                             "source": metric.get("source"),
                             "as_of": metric.get("as_of")})
        out["rules_run"].append(kind)
        out["recommendations"].append({
            "id": signal.get("id") or "%s:%s" % (kind, company_id),
            "company_id": company_id,
            "property_name": signal.get("property_name"),
            "rule_key": kind,
            "category": category,
            "channels": channels,
            "severity": _SIGNAL_SEVERITY.get(
                str(signal.get("severity") or "").lower(), "medium"),
            "confidence": 8 if receipts else 5,
            "found": signal.get("detail") or signal.get("title") or "",
            "receipts": receipts,
            "expect": None,
            "if_skip": None,
            "action": {"kind": action_kind if receipts else "none", "params": {},
                       "executor": "portal",
                       # A budget move still needs a signature, same as any other.
                       "requires_signed_deal": action_kind in SPEND_INCREASING_KINDS,
                       "fair_housing_review": False},
            "start_by": None,
            "verify": None,
        })
    return out


def _producer(name: str) -> Optional[Callable]:
    """Import a producer's `run`, or None when that module is not present.

    Producers are developed independently; a missing one is a gap, not a crash.
    """
    if name == "workspace_signals":
        return signals_producer
    try:
        module = __import__("skills.%s" % name, fromlist=["run"])
    except Exception as exc:  # noqa: BLE001
        logger.info("reco_engine: producer %s unavailable (%s)", name, exc)
        return None
    run = getattr(module, "run", None)
    return run if callable(run) else None


def for_property(company_id: str, *, context: Optional[Dict[str, Any]] = None,
                 decisions: Optional[Dict[str, Any]] = None,
                 producers: Optional[Tuple[str, ...]] = None,
                 today: Optional[date] = None,
                 max_per_property: int = MAX_PER_PROPERTY) -> Dict[str, Any]:
    """Every rule, one property, ranked and suppressed."""
    today = _today(today)
    recos: List[Dict[str, Any]] = []
    gaps: List[Dict[str, str]] = []
    rules_run: List[str] = []
    rules_skipped: List[Dict[str, Any]] = []

    for name in (producers or PRODUCERS):
        run = _producer(name)
        if run is None:
            gaps.append(_gap("recommendations", name,
                             "This rule set is not installed on this server yet."))
            continue
        try:
            result = run(company_id, today=today) or {}
        except Exception as exc:  # noqa: BLE001 — one bad producer must not kill the queue
            logger.error("reco_engine: producer %s failed: %s", name, exc, exc_info=True)
            gaps.append(_gap("recommendations", name,
                             "This rule set could not complete its run."))
            continue
        recos.extend(result.get("recommendations") or [])
        gaps.extend(result.get("gaps") or [])
        rules_run.extend(result.get("rules_run") or [])
        rules_skipped.extend(result.get("rules_skipped") or [])

    outcome = rank_and_suppress(recos, context=context, decisions=decisions,
                                today=today, max_per_property=max_per_property)
    outcome.update({"company_id": company_id, "gaps": gaps,
                    "rules_run": rules_run, "rules_skipped": rules_skipped,
                    "as_of": _now_iso()})
    return outcome


def for_portfolio(properties: List[Dict[str, Any]], *,
                  producers: Optional[Tuple[str, ...]] = None,
                  today: Optional[date] = None,
                  max_portfolio: int = MAX_PORTFOLIO,
                  max_per_property: int = MAX_PER_PROPERTY) -> Dict[str, Any]:
    """The portfolio queue: what needs a person this week, across properties.

    `properties` is [{company_id, name, units, exposure, decisions?}, …] —
    whatever the caller already knows, so this does no lookups of its own.
    """
    today = _today(today)
    per_property: Dict[str, Dict[str, Any]] = {}
    everything: List[Dict[str, Any]] = []
    gaps: List[Dict[str, str]] = []
    totals = {"produced": 0, "surfaced": 0, "quiet": 0, "deduped": 0,
              "already_decided": 0, "dropped": 0, "refused": 0}

    for prop in properties or []:
        company_id = str(prop.get("company_id") or "").strip()
        if not company_id:
            continue
        outcome = for_property(
            company_id, context=prop, decisions=prop.get("decisions"),
            producers=producers, today=today, max_per_property=max_per_property)
        per_property[company_id] = outcome
        for reco in outcome["surfaced"]:
            reco.setdefault("property_name", prop.get("name"))
            everything.append(reco)
        for key in totals:
            totals[key] += outcome["counts"].get(key, 0)
        for gap in outcome["gaps"]:
            if gap not in gaps:
                gaps.append(gap)

    everything.sort(key=lambda r: (-(r.get("_score") or 0), r.get("rule_key") or ""))
    queue, held = everything[:max_portfolio], everything[max_portfolio:]
    return {
        "queue": queue,
        "held_back": len(held),
        "properties": per_property,
        "property_count": len(per_property),
        "totals": totals,
        "gaps": gaps,
        "as_of": _now_iso(),
        "note": ("%d recommendations surfaced across %d properties; %d more are "
                 "visible per property but held out of the queue so the list "
                 "stays readable." % (len(queue), len(per_property), len(held))),
    }


def to_work_item(reco: Dict[str, Any]) -> Dict[str, Any]:
    """Shape a recommendation like a workspace inbox item, so the existing
    Approvals UI renders it without a new screen."""
    action = reco.get("action") or {}
    return {
        "id": "reco:%s" % reco.get("id"),
        "source": "recommendation",
        "source_id": reco.get("id"),
        "title": reco.get("found"),
        "found": reco.get("found"),
        "expect": reco.get("expect"),
        "if_skip": reco.get("if_skip"),
        "receipts": reco.get("receipts") or [],
        "channels": reco.get("channels") or [],
        "category": reco.get("category"),
        "lens": "amplify" if action.get("kind") not in ("none",) else "evolve",
        "start_by": reco.get("start_by"),
        "status": "to_do",
        "needs_approval": action.get("kind") != "none",
        "client_visible": reco.get("category") != "cost" or bool(
            action.get("requires_signed_deal")),
        "internal_only": False,
        "cost_note": None,
        "steps": [],
        "trail": [],
        "actions": {"approve": action.get("kind") != "none", "not_now": True},
        "verify": reco.get("verify"),
        "executor": action.get("executor"),
        "fair_housing_review": bool(action.get("fair_housing_review")),
    }
