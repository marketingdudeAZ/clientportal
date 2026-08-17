"""Ticket → property-profile loop API — /api/ticket-profile/* .

The review surface for proposals generated when a ClickUp ticket completes
(docs/ticket-to-profile-loop.md). A proposal is a *suggestion*: nothing reaches
the property profile until a person accepts it here, and accepting routes
through community_brief.write_field() so override-wins and the audit log are
preserved.

Endpoints:
  GET  /api/ticket-profile/proposals?company_id=X   Pending proposals for a property
  POST /api/ticket-profile/proposals/accept         Apply one to the profile
  POST /api/ticket-profile/proposals/reject         Dismiss one
  GET  /api/ticket-profile/history?company_id=X     Tickets that shaped this profile

Every route 404s unless TICKET_PROFILE_LOOP_ENABLED=true, so this blueprint is
inert on main until the flag is flipped.

Auth — dual, mirroring the portal-tickets blueprint:
  * Portal user via X-Portal-Email
  * Internal/server via X-Internal-Key
"""

from __future__ import annotations

import logging
import os

from flask import Blueprint, jsonify, request

import ticket_profile_sync
from _route_utils import preflight_response

logger = logging.getLogger(__name__)

ticket_profile_bp = Blueprint("ticket_profile", __name__)


def _enabled() -> bool:
    return ticket_profile_sync.enabled()


def _actor(req) -> str:
    """The person on the hook for this action — recorded on the audit row."""
    return req.headers.get("X-Portal-Email", "").strip()


def _is_authorized(req) -> bool:
    if _actor(req):
        return True
    key = req.headers.get("X-Internal-Key", "")
    return bool(key and key == os.environ.get("INTERNAL_API_KEY", ""))


def _guard(req):
    """(response, status) to return early, or None to proceed."""
    if not _enabled():
        return jsonify({"error": "ticket-profile loop disabled"}), 404
    if not _is_authorized(req):
        return jsonify({"error": "auth required"}), 401
    return None


# ── GET /api/ticket-profile/proposals ────────────────────────────────────────

@ticket_profile_bp.route("/api/ticket-profile/proposals", methods=["GET", "OPTIONS"])
def proposals():
    if request.method == "OPTIONS":
        return preflight_response()
    blocked = _guard(request)
    if blocked:
        return blocked

    company_id = (request.args.get("company_id") or "").strip()
    property_uuid = (request.args.get("uuid") or "").strip()
    if not company_id and not property_uuid:
        return jsonify({"error": "company_id or uuid required"}), 400
    try:
        rows = ticket_profile_sync.list_proposals(company_id, property_uuid)
    except Exception as e:  # noqa: BLE001 — the profile page must still render
        logger.warning("ticket-profile proposals failed for %s: %s", company_id, e)
        rows = []
    return jsonify({"proposals": rows})


# ── POST /api/ticket-profile/proposals/accept|reject ─────────────────────────

def _action(fn):
    if not _enabled():
        return jsonify({"error": "ticket-profile loop disabled"}), 404
    if not _is_authorized(request):
        return jsonify({"error": "auth required"}), 401
    actor = _actor(request)
    if not actor:
        # An internal key can read, but a profile write must name a person —
        # community_brief.write_field records it on the audit row.
        return jsonify({"error": "X-Portal-Email required to action a proposal"}), 401

    body = request.get_json(silent=True) or {}
    pid = (body.get("proposal_id") or "").strip()
    if not pid:
        return jsonify({"error": "proposal_id required"}), 400
    try:
        result = fn(pid, actor, body)
    except ticket_profile_sync.ProposalError as e:
        return jsonify({"error": e.message}), e.status
    except Exception as e:  # noqa: BLE001
        logger.warning("ticket-profile action failed for %s: %s", pid, e)
        return jsonify({"error": "could not action this proposal"}), 502
    return jsonify({"ok": True, "proposal": result}), 200


@ticket_profile_bp.route("/api/ticket-profile/proposals/accept",
                         methods=["POST", "OPTIONS"])
def accept_proposal():
    """Apply a proposal to the profile. Auth: _is_authorized + X-Portal-Email."""
    if request.method == "OPTIONS":
        return preflight_response()
    return _action(lambda pid, actor, body: ticket_profile_sync.accept(pid, actor))


@ticket_profile_bp.route("/api/ticket-profile/proposals/reject",
                         methods=["POST", "OPTIONS"])
def reject_proposal():
    """Dismiss a proposal. Auth: _is_authorized + X-Portal-Email."""
    if request.method == "OPTIONS":
        return preflight_response()
    return _action(lambda pid, actor, body: ticket_profile_sync.reject(
        pid, actor, (body.get("note") or "").strip()))


# ── GET /api/ticket-profile/history ──────────────────────────────────────────

@ticket_profile_bp.route("/api/ticket-profile/history", methods=["GET", "OPTIONS"])
def history():
    """Tickets that touched this property + which profile fields each changed.

    Exact `task_id ↔ company_id` lookup from the portal_tickets mapping — the
    same rule the ticket list uses. Never fuzzy-matched.
    """
    if request.method == "OPTIONS":
        return preflight_response()
    blocked = _guard(request)
    if blocked:
        return blocked

    company_id = (request.args.get("company_id") or "").strip()
    property_uuid = (request.args.get("uuid") or "").strip()
    if not company_id and not property_uuid:
        return jsonify({"error": "company_id or uuid required"}), 400
    try:
        limit = max(1, min(int(request.args.get("limit") or 25), 100))
    except (TypeError, ValueError):
        limit = 25
    try:
        rows = ticket_profile_sync.ticket_history(company_id, property_uuid, limit)
    except Exception as e:  # noqa: BLE001
        logger.warning("ticket-profile history failed for %s: %s", company_id, e)
        rows = []
    return jsonify({"tickets": rows})
