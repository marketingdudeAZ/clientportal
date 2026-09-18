"""Layer 2 — findings posted by an outside agent, turned into portal cards.

WHY THIS EXISTS
    The vendor's agents can see the ad accounts and their own warehouse, and
    they will notice things worth doing. Today the only way that reaches a
    person is a report someone reads. This is the other direction: an agent
    posts what it found, and it lands in the same Approvals queue as every
    other recommendation — one card, with receipts, with a why and a
    for-whom, and with the same gate in front of any action.

WHAT IT REFUSES
    * A finding with an action but no receipts. A card asking someone to change
      live spend without showing the numbers is worse than no card.
    * Copy that fails the Fair Housing check. Housing is a Special Ad Category
      and an agent's prose reaches a client or a channel, so it is checked
      BEFORE storage, not at publish time.
    * Anything whose action would change audience or geographic targeting, or
      move spend without a signed deal. Those are compliance failures whoever
      produced them, so they are refused and counted, never queued.
    * A duplicate. The same finding posted nightly is one card, not thirty.

WHAT IT DOES NOT DO
    Nothing here executes anything, and nothing here decides. Storage is a
    loop event; a human still approves in the portal, and only then does the
    portal fire a webhook. That ordering is the whole design: the agent
    proposes, the portal authorizes.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

EVENT_TYPE = "agent_finding_posted"
LOOP_STAGE = "optimize"

# A finding is kept for this long before the queue stops offering it. An agent
# that stops reporting something has, in effect, withdrawn it.
FINDING_TTL_DAYS = 30

MAX_TEXT = 2000
MAX_RECEIPTS = 12
MAX_PARAMS_BYTES = 4000


class FindingRejected(ValueError):
    """The finding is wrong and will never be accepted as posted.

    The reason is safe to hand back verbatim, because it names something the
    caller can change.
    """

    def __init__(self, reason: str, detail: Optional[Dict[str, Any]] = None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail or {}


class FindingUnavailable(RuntimeError):
    """The finding may be fine; WE could not accept it right now.

    Kept separate from FindingRejected on purpose. An agent told "rejected"
    stops posting that finding, so reporting our own outage as a rejection
    would silently drop real work — the caller must retry instead.
    """


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _clip(value: Any, limit: int = MAX_TEXT) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] if text else None


def fingerprint(company_id: str, rule_key: str, found: str,
                idempotency_key: Optional[str] = None) -> str:
    """Stable id for "the same finding".

    An explicit idempotency key wins. Without one, the property, the rule and
    the finding text are what make two posts the same thing — deliberately NOT
    the numbers, so a value drifting by a dollar overnight does not produce a
    second card.
    """
    basis = idempotency_key or "|".join([str(company_id), str(rule_key),
                                         (found or "").strip().lower()])
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]


def fair_housing_check(texts: List[str]) -> Tuple[Optional[str], bool]:
    """(refusal reason or None, needs_human_review).

    `workspace_common.fair_housing_review` already fails closed — a checker
    outage comes back as high severity — which is the right trade here: this is
    an unattended machine writing copy that can reach a renter.

    High severity is refused outright. Low severity is protected-class
    vocabulary on its own, which is often innocent ("a single page"), so the
    finding is accepted and flagged for a human rather than thrown away.
    """
    body = [t for t in texts if t]
    if not body:
        return None, False
    from skills import workspace_common as wc

    review = wc.fair_housing_review(*body)
    if not review:
        return None, False
    terms = [str(t) for t in (review.get("terms") or [])][:5]
    if review.get("severity") == "high":
        if terms == ["fair_housing_check_unavailable"]:
            # The helper failed closed. Nothing is stored — but the copy was
            # never judged, so this is our outage, not the agent's bad wording.
            raise FindingUnavailable(
                "The Fair Housing check could not run, so this finding was not "
                "accepted. Retry shortly.")
        return ("The wording includes language Fair Housing rules do not allow "
                "in housing marketing%s."
                % (": %s" % ", ".join(terms) if terms else "")), False
    return None, True


def normalize(payload: Dict[str, Any], *, company_id: str, property_name: Optional[str],
              posted_by: str, today: Optional[date] = None) -> Dict[str, Any]:
    """Payload in, a recommendation in the shared contract out.

    Raises FindingRejected with a reason the caller can hand back verbatim.
    """
    today = today or date.today()
    if not isinstance(payload, dict):
        raise FindingRejected("The finding must be a JSON object.")

    rule_key = _clip(payload.get("rule_key") or payload.get("kind"), 80)
    found = _clip(payload.get("found") or payload.get("summary"))
    if not rule_key:
        raise FindingRejected("rule_key is required: name the check that fired.")
    if not found:
        raise FindingRejected("found is required: one sentence naming what you found.")

    action_in = payload.get("action") or {}
    if not isinstance(action_in, dict):
        raise FindingRejected("action must be an object.")
    kind = _clip(action_in.get("kind"), 40) or "none"

    receipts_in = payload.get("receipts") or []
    if not isinstance(receipts_in, list):
        raise FindingRejected("receipts must be a list.")
    receipts = []
    for item in receipts_in[:MAX_RECEIPTS]:
        if not isinstance(item, dict):
            continue
        source = _clip(item.get("source"), 80)
        if not source:
            continue                      # a receipt with no source is not a receipt
        receipts.append({"label": _clip(item.get("label"), 160),
                         "value": item.get("value"),
                         "source": source,
                         "as_of": _clip(item.get("as_of"), 40)})
    if kind != "none" and not receipts:
        raise FindingRejected(
            "An action needs at least one receipt with a source. A card asking "
            "someone to change live spend without showing the numbers is worse "
            "than no card.")

    params = action_in.get("params") or {}
    if not isinstance(params, dict):
        raise FindingRejected("action.params must be an object.")
    if len(json.dumps(params, default=str)) > MAX_PARAMS_BYTES:
        raise FindingRejected("action.params is too large.")

    expect = _clip(payload.get("expect"))
    if_skip = _clip(payload.get("if_skip"))

    problem, needs_review = fair_housing_check(
        [found, expect, if_skip] + [str(v) for v in params.values()])
    if problem:
        raise FindingRejected(problem, {"check": "fair_housing"})

    severity = str(payload.get("severity") or "medium").lower()
    try:
        confidence = int(payload.get("confidence") or 6)
    except (TypeError, ValueError):
        confidence = 6
    confidence = max(1, min(confidence, 10))

    channels = payload.get("channels") or []
    if isinstance(channels, str):
        channels = [channels]
    channels = [_clip(c, 40) for c in channels if _clip(c, 40)][:8]

    reco = {
        "id": "%s:%s:%s" % (rule_key, company_id, today.isoformat()),
        "company_id": str(company_id),
        "property_name": property_name,
        "rule_key": rule_key,
        "category": str(payload.get("category") or "cost").lower(),
        "channels": channels,
        "severity": severity if severity in ("high", "medium", "low") else "medium",
        "confidence": confidence,
        "found": found,
        "receipts": receipts,
        "expect": expect,
        "if_skip": if_skip,
        "action": {
            "kind": kind,
            "params": params,
            "executor": _clip(action_in.get("executor"), 60) or "ninjacat",
            # An agent does not get to decide either of these.
            "requires_signed_deal": kind in ("budget_change", "new_ad_group"),
            "fair_housing_review": bool(action_in.get("fair_housing_review"))
            or needs_review
            or kind in ("creative_refresh", "content_brief", "page_fix"),
        },
        "start_by": _clip(payload.get("start_by"), 40),
        "verify": payload.get("verify") if isinstance(payload.get("verify"), dict) else None,
        "posted_by": posted_by,
        "posted_at": _now_iso(),
    }

    from skills import reco_engine

    problem = reco_engine.validate(reco)
    if problem:
        raise FindingRejected("The finding is not usable: %s." % problem)
    refusal = reco_engine.compliance_refusal(reco)
    if refusal:
        logger.error("agent_findings refused %s from %s: %s", reco["id"], posted_by,
                     refusal)
        raise FindingRejected(
            "Refused: %s. Housing advertising cannot be targeted by audience or "
            "geography, and spend moves on a signed deal." % refusal,
            {"check": "compliance"})
    return reco


def store(reco: Dict[str, Any], *, property_uuid: Optional[str],
          idempotency_key: Optional[str] = None) -> Dict[str, Any]:
    """Write the finding as a loop event. Raises when it cannot be persisted.

    Deliberately loud: an agent told "accepted" for something that was dropped
    would keep reporting it while nobody ever sees it.
    """
    from loop_writer import record

    key = fingerprint(reco["company_id"], reco["rule_key"], reco["found"],
                      idempotency_key)
    payload = dict(reco)
    payload["fingerprint"] = key
    try:
        event_id = record(LOOP_STAGE, EVENT_TYPE, property_uuid=property_uuid,
                          payload=payload)
    except Exception as exc:  # noqa: BLE001
        logger.error("agent_findings: could not store %s: %s", reco["id"], exc,
                     exc_info=True)
        raise FindingUnavailable(
            "The finding could not be recorded, so it was not accepted. Retry "
            "shortly.")
    return {"fingerprint": key, "event_id": event_id}


def recent(company_id: str, *, uuid: Optional[str] = None,
           days: int = FINDING_TTL_DAYS,
           today: Optional[date] = None) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """Findings still live for one property, newest first, deduped.

    Returns (recommendations, gaps). A warehouse that cannot be read is a gap,
    never an empty list pretending nothing was posted.
    """
    today = today or date.today()
    unreadable = [{"field": "agent_findings", "source": "loop_events",
                   "message": "Findings posted by agents could not be read, so "
                              "any that exist are not shown."}]
    key_uuid = str(uuid or company_id)
    try:
        from skills import workspace_history
        by_property = workspace_history.property_events([key_uuid],
                                                        types=(EVENT_TYPE,))
    except Exception as exc:  # noqa: BLE001
        logger.info("agent_findings: history unavailable for %s: %s", company_id, exc)
        return [], unreadable
    if by_property is None:          # warehouse unreadable, NOT "nothing posted"
        return [], unreadable

    events = by_property.get(key_uuid) or []
    cutoff = (today - timedelta(days=days)).isoformat()
    gaps: List[Dict[str, str]] = []
    seen = set()
    out: List[Dict[str, Any]] = []
    for event in events:            # property_events returns newest first
        when = str(event.get("occurred_at") or "")[:10]
        if when and when < cutoff:
            continue
        payload = event.get("payload") or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                continue
        key = payload.get("fingerprint")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(payload)
    return out, gaps


def run(company_id: str, *, today: Optional[date] = None,
        uuid: Optional[str] = None) -> Dict[str, Any]:
    """Producer entry point, so posted findings rank in the same queue.

    Resolves the uuid itself because loop events are uuid-keyed and a company id
    is not always the same string. Reading by the wrong key would return an
    empty list — a posted finding silently missing from the queue — so a failed
    resolve is reported as a gap instead.
    """
    empty = {"company_id": company_id, "recommendations": [], "rules_run": [],
             "rules_skipped": []}
    if not uuid:
        try:
            from skills import property_resolver as pr
            uuid = pr.resolve(str(company_id)).to_dict().get("uuid")
        except Exception as exc:  # noqa: BLE001
            logger.info("agent_findings: cannot resolve %s: %s", company_id, exc)
            return dict(empty, gaps=[{
                "field": "agent_findings", "source": "property_resolver",
                "message": "This property could not be resolved, so findings "
                           "posted by agents were not looked up."}])
    if not uuid:
        return dict(empty, gaps=[{
            "field": "agent_findings", "source": "hubspot",
            "message": "This property has no uuid, so findings posted by agents "
                       "cannot be stored against it."}])

    recos, gaps = recent(company_id, uuid=uuid, today=today)
    return {"company_id": company_id, "recommendations": recos, "gaps": gaps,
            "rules_run": sorted({r.get("rule_key") for r in recos if r.get("rule_key")}),
            "rules_skipped": []}
