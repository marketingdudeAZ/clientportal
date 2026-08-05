"""Portal ticket API blueprint — /api/portal-tickets/* (docs/ticket-page-scope.md).

Per-type ticket forms backed by ClickUp. Namespaced under /api/portal-tickets
so it runs ALONGSIDE the existing HubSpot Service Hub ticket flow (/api/ticket)
rather than replacing it — the Service Hub → ClickUp consolidation is a later,
separately-owned change.

Endpoints:
  GET  /api/portal-tickets/types            Available ticket types + live form schema
  POST /api/portal-tickets/create           Create a ClickUp task from a portal ticket
  GET  /api/portal-tickets?company_id=X      This property's open + recent tickets
  GET  /api/portal-tickets/admin/discover    (internal) map ClickUp lists → env list ids

Auth — dual, mirroring the Loop blueprint:
  * Portal user via X-Portal-Email
  * Internal/server via X-Internal-Key (required for the admin discover route)
"""

from __future__ import annotations

import logging
import os

from flask import Blueprint, jsonify, request

import portal_tickets
from _route_utils import preflight_response

logger = logging.getLogger(__name__)

portal_tickets_bp = Blueprint("portal_tickets", __name__)


def _is_authorized(req) -> bool:
    if req.headers.get("X-Portal-Email", "").strip():
        return True
    key = req.headers.get("X-Internal-Key", "")
    return bool(key and key == os.environ.get("INTERNAL_API_KEY", ""))


def _is_internal(req) -> bool:
    key = req.headers.get("X-Internal-Key", "")
    return bool(key and key == os.environ.get("INTERNAL_API_KEY", ""))


# ── GET /api/portal-tickets/types ────────────────────────────────────────────

@portal_tickets_bp.route("/api/portal-tickets/types", methods=["GET", "OPTIONS"])
def ticket_types():
    if request.method == "OPTIONS":
        return preflight_response()
    if not _is_authorized(request):
        return jsonify({"error": "auth required"}), 401
    include_internal = _is_internal(request)
    try:
        types = portal_tickets.types_with_schema(include_internal=include_internal)
    except Exception as e:  # noqa: BLE001
        logger.warning("portal ticket types failed: %s", e)
        types = []
    return jsonify({"types": types})


# ── POST /api/portal-tickets/create ──────────────────────────────────────────

@portal_tickets_bp.route("/api/portal-tickets/create", methods=["POST", "OPTIONS"])
def ticket_create():
    if request.method == "OPTIONS":
        return preflight_response()
    if not _is_authorized(request):
        return jsonify({"error": "auth required"}), 401

    body = request.get_json(silent=True) or {}
    company_id = (body.get("company_id") or "").strip()
    type_key = (body.get("ticket_type") or body.get("type") or "").strip()
    subject = (body.get("subject") or "").strip()
    fields = body.get("fields") or {}
    property_uuid = (body.get("uuid") or "").strip()
    # Optional property-profile gap answers. A malformed value is ignored
    # rather than rejected — these are never required, so they must never be
    # able to fail a ticket.
    brief_answers = body.get("brief_answers")
    if not isinstance(brief_answers, dict):
        brief_answers = None
    submitted_by = request.headers.get("X-Portal-Email", "").strip()

    if not company_id:
        return jsonify({"ok": False, "error": "company_id required"}), 400
    if not type_key:
        return jsonify({"ok": False, "error": "ticket_type required"}), 400
    if not isinstance(fields, dict):
        return jsonify({"ok": False, "error": "fields must be an object"}), 400

    body_out, status = portal_tickets.create_ticket(
        company_id,
        type_key,
        subject=subject,
        fields=fields,
        submitted_by=submitted_by,
        property_uuid=property_uuid,
        brief_answers=brief_answers,
    )
    return jsonify(body_out), status


# ── GET /api/portal-tickets/brief-gaps ───────────────────────────────────────

@portal_tickets_bp.route("/api/portal-tickets/brief-gaps", methods=["GET", "OPTIONS"])
def ticket_brief_gaps():
    """What this ticket type still needs from the property profile.

    Feeds the ticket form's profile block: up to PORTAL_TICKET_BRIEF_MAX_ASK
    optional questions for fields that are still empty, plus what we already
    know (shown back, never re-asked). See docs/ticket-brief-gaps-scope.md.

    This endpoint must never break the ticket form, so beyond the argument
    checks it does not fail: an unreadable profile, a disabled flag, or an
    unexpected error all return 200 with an empty ask.
    """
    if request.method == "OPTIONS":
        return preflight_response()
    if not _is_authorized(request):
        return jsonify({"error": "auth required"}), 401

    company_id = (request.args.get("company_id") or "").strip()
    type_key = (request.args.get("ticket_type") or "").strip()
    if not company_id:
        return jsonify({"ok": False, "error": "company_id required"}), 400
    if not type_key:
        return jsonify({"ok": False, "error": "ticket_type required"}), 400
    if not portal_tickets._type_by_key(type_key):
        return jsonify({"ok": False, "error": "Unknown ticket type."}), 400

    if not portal_tickets._gaps_enabled():
        body = portal_tickets._empty_gaps(company_id, type_key)
        body.update({"ok": True, "enabled": False})
        return jsonify(body)

    try:
        body = portal_tickets.brief_gaps(company_id, type_key)
    except Exception as e:  # noqa: BLE001 — a gap read must never cost a ticket
        logger.warning("brief gaps failed for %s/%s: %s", company_id, type_key, e)
        body = portal_tickets._empty_gaps(company_id, type_key)
        body["degraded"] = True
    body.update({"ok": True, "enabled": True})
    return jsonify(body)


# ── GET /api/portal-tickets ──────────────────────────────────────────────────

@portal_tickets_bp.route("/api/portal-tickets", methods=["GET", "OPTIONS"])
def ticket_list():
    if request.method == "OPTIONS":
        return preflight_response()
    if not _is_authorized(request):
        return jsonify({"error": "auth required"}), 401
    company_id = (request.args.get("company_id") or "").strip()
    property_uuid = (request.args.get("uuid") or "").strip()
    if not company_id and not property_uuid:
        return jsonify({"error": "company_id or uuid required"}), 400
    try:
        tickets = portal_tickets.list_tickets(company_id, property_uuid=property_uuid)
    except Exception as e:  # noqa: BLE001
        logger.warning("portal ticket list failed for %s: %s", company_id, e)
        tickets = []
    return jsonify({"tickets": tickets})


# ── GET /api/portal-tickets/admin/discover (internal) ────────────────────────

@portal_tickets_bp.route("/api/portal-tickets/admin/discover", methods=["GET", "OPTIONS"])
def ticket_discover():
    """Pull the real ClickUp list ids and match them to ticket types by name.
    Internal only — returns a paste-ready env block for the list-id vars."""
    if request.method == "OPTIONS":
        return preflight_response()
    if not _is_internal(request):
        return jsonify({"error": "internal key required"}), 401
    try:
        return jsonify(portal_tickets.discover_list_ids())
    except Exception as e:  # noqa: BLE001
        logger.warning("portal ticket discover failed: %s", e)
        return jsonify({"error": "discovery failed"}), 502
