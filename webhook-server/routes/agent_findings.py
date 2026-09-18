"""Findings posted INTO the portal by an outside agent.

    POST /api/agent/findings     post one finding, or up to 25 in a batch
    GET  /api/agent/findings     read back what is live for a property

WHY A SECOND DOOR
    /mcp lets the vendor's agents READ the portal. This is the return path: an
    agent that noticed something posts it, and it lands in the same Approvals
    queue as every rule the portal runs itself — one card, receipts, a why and
    a for-whom. Without this the only way an agent's finding reaches a person
    is a report someone has to read.

WHY IT REUSES THE MCP TOKENS
    Same consumer, same agreement, same rotation. A caller that can read the
    portal's context is the caller that posts findings about it, so a second
    token table would be two things to rotate and one more to leak. Like /mcp,
    every route here 404s until a token is configured.

WHAT IT WILL NOT DO
    Nothing here executes anything. A finding is stored as a loop event and
    waits for a human in the portal; only an approval fires a webhook. An agent
    cannot mark its own finding as not needing a signed deal, cannot skip the
    Fair Housing check, and cannot post an action with no receipts — see
    skills/agent_findings.py for each refusal and why.

READBACK IS PART OF THE CONTRACT
    GET returns exactly what the queue will show, so an agent can verify its
    post landed instead of assuming a 200 means visible. A warehouse that
    cannot be read comes back as a gap, never as an empty list.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

agent_findings_bp = Blueprint("agent_findings", __name__)

MAX_BATCH = 25


class Unavailable(Exception):
    """Our side broke. Never the caller's fault, so never a 4xx."""


def _caller_label_or_error() -> Tuple[Optional[str], Optional[Tuple[Any, int]]]:
    """(caller label, error response). Mirrors /mcp exactly, on purpose.

    Named for `_caller_label` deliberately: tests/test_auth_coverage.py reads
    each handler's OWN source for an auth marker, so a check one call deeper is
    invisible to it. Keep the name if you move this.
    """
    from routes.mcp import _caller_label, _enabled

    if not _enabled():
        # An unconfigured door is a closed door. 404 so a scanner cannot even
        # tell the feature exists.
        return None, (jsonify({"error": "Not found"}), 404)
    label = _caller_label()
    if not label:
        return None, (jsonify({"error": "Unauthorized"}), 401)
    return label, None


def _resolve(identifier: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """(company_id, uuid, property_name) for whatever the agent called it.

    An agent knows a property by whatever name is in its own platform, so this
    accepts a company id, a uuid, a domain or a name — the same set a person can
    type. An unresolvable property is the agent's error to fix, not a silent
    write to the wrong card.
    """
    from skills import property_resolver as pr

    try:
        identity = pr.resolve(identifier)
    except LookupError:
        # PropertyNotFound / AmbiguousProperty — the agent's to fix, and safe
        # to name back to it.
        raise
    except Exception as exc:  # noqa: BLE001
        # HubSpot down, credentials stale, network gone. Telling the agent its
        # finding was rejected would make it stop retrying something that was
        # never wrong, so this is ours to own.
        logger.error("agent_findings: cannot resolve %r: %s", identifier, exc,
                     exc_info=True)
        raise Unavailable("The property directory could not be reached.")
    data = identity.to_dict()
    return (str(identity.company_id), data.get("uuid"), identity.name)


def _post_one(payload: Dict[str, Any], *, label: str,
              identifier: str) -> Dict[str, Any]:
    from skills import agent_findings as af

    company_id, uuid, name = _resolve(identifier)
    reco = af.normalize(payload, company_id=company_id, property_name=name,
                        posted_by=label)
    stored = af.store(reco, property_uuid=uuid,
                      idempotency_key=payload.get("idempotency_key")
                      or request.headers.get("Idempotency-Key"))
    logger.info("agent finding accepted: %s from %s for %s (%s)",
                reco["rule_key"], label, name, stored["fingerprint"])
    return {"status": "accepted", "id": reco["id"],
            "fingerprint": stored["fingerprint"],
            "property": name, "company_id": company_id,
            "needs_signed_deal": reco["action"]["requires_signed_deal"],
            "needs_fair_housing_review": reco["action"]["fair_housing_review"],
            "next": "It is in the portal's Approvals queue. Nothing runs until "
                    "a person approves it."}


@agent_findings_bp.route("/api/agent/findings", methods=["POST"])
def post_findings():
    label, err = _caller_label_or_error()
    if err:
        return err

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "Send a JSON object."}), 400

    items = body.get("findings")
    batch = isinstance(items, list)
    if not batch:
        items = [body.get("finding") if isinstance(body.get("finding"), dict) else body]
    if not items:
        return jsonify({"error": "No findings in the request."}), 400
    if len(items) > MAX_BATCH:
        return jsonify({"error": "Send at most %d findings per request." % MAX_BATCH}), 413

    default_property = (body.get("property") or body.get("company_id")
                        or body.get("uuid"))
    results: List[Dict[str, Any]] = []
    accepted = 0
    from skills.agent_findings import FindingRejected, FindingUnavailable

    for item in items:
        if not isinstance(item, dict):
            results.append({"status": "rejected",
                            "reason": "Each finding must be a JSON object."})
            continue
        identifier = (item.get("property") or item.get("company_id")
                      or item.get("uuid") or default_property)
        if not identifier:
            results.append({"status": "rejected",
                            "reason": "property is required: name the property "
                                      "this finding is about."})
            continue
        try:
            results.append(_post_one(item, label=label, identifier=str(identifier)))
            accepted += 1
        except FindingRejected as exc:
            logger.info("agent finding rejected from %s: %s", label, exc.reason)
            results.append(dict({"status": "rejected", "reason": exc.reason},
                                **exc.detail))
        except FindingUnavailable as exc:
            # Fair Housing checker down, warehouse unwritable. Nothing was
            # stored and nothing was judged, so the agent should come back.
            logger.error("agent finding could not be accepted from %s: %s",
                         label, exc)
            results.append({"status": "unavailable", "retry": True,
                            "reason": str(exc)})
        except LookupError as exc:
            # PropertyNotFound / AmbiguousProperty: the agent named something we
            # do not have, which only it can fix.
            logger.info("agent finding for unknown property %r from %s: %s",
                        identifier, label, exc)
            results.append({"status": "rejected",
                            "reason": "No single property matches %r. Use the "
                                      "company id from the MCP tools."
                                      % str(identifier)[:120]})
        except Unavailable as exc:
            # Our outage. A retryable status, and no internal detail echoed.
            results.append({"status": "unavailable", "retry": True,
                            "reason": str(exc)})
        except Exception as exc:  # noqa: BLE001
            logger.error("agent finding failed from %s for %r: %s", label,
                         identifier, exc, exc_info=True)
            results.append({"status": "unavailable", "retry": True,
                            "reason": "The portal could not process this "
                                      "finding. Retry shortly."})

    if not batch:
        one = results[0]
        # A single post gets a status code an agent can branch on without
        # parsing the body; a batch is a 200 with per-item outcomes, because
        # some accepted and some not is not one status code.
        #   200 accepted · 422 the finding is wrong · 503 we are broken, retry
        status = {"accepted": 200, "rejected": 422}.get(one["status"], 503)
        return jsonify(one), status
    unavailable = sum(1 for r in results if r["status"] == "unavailable")
    return jsonify({"accepted": accepted,
                    "rejected": len(results) - accepted - unavailable,
                    "unavailable": unavailable,
                    "retry_unavailable": bool(unavailable),
                    "results": results}), 200


@agent_findings_bp.route("/api/agent/findings", methods=["GET"])
def get_findings():
    label, err = _caller_label_or_error()
    if err:
        return err

    identifier = (request.args.get("property") or request.args.get("company_id")
                  or request.args.get("uuid"))
    if not identifier:
        return jsonify({"error": "property is required."}), 400
    try:
        company_id, uuid, name = _resolve(str(identifier))
    except LookupError:
        return jsonify({"error": "No single property matches %r."
                                 % str(identifier)[:120]}), 404
    except Unavailable as exc:
        return jsonify({"error": str(exc), "retry": True}), 503

    from skills import agent_findings as af

    findings, gaps = af.recent(company_id, uuid=uuid)
    mine = [f for f in findings if f.get("posted_by") == label]
    return jsonify({"property": name, "company_id": company_id,
                    "count": len(mine), "findings": mine,
                    "gaps": gaps,
                    "note": "Only findings posted by this caller are listed."}), 200
