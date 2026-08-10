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
  * Portal user via a VERIFIED Clerk session JWT (Authorization: Bearer …)
  * Internal/server via X-Internal-Key (required for the admin discover route)

These routes file work into ClickUp and read a property's request history, so
a bare X-Portal-Email header is not accepted as identity — see `_identity()`.
Portal users are additionally gated behind the `portal_tickets` feature, which
is what keeps the pilot to named people rather than the whole portal.
"""

from __future__ import annotations

import logging
import os

from flask import Blueprint, jsonify, request

import portal_tickets
from _route_utils import (
    current_portal_email,
    preflight_response,
    require_access,
    verified_portal_email,
)

logger = logging.getLogger(__name__)

portal_tickets_bp = Blueprint("portal_tickets", __name__)

FEATURE_KEY = "portal_tickets"


def _is_internal(req) -> bool:
    key = req.headers.get("X-Internal-Key", "")
    return bool(key and key == os.environ.get("INTERNAL_API_KEY", ""))


def _dev_mode() -> bool:
    """Local development only — never true on Render."""
    return os.environ.get("FLASK_ENV") == "development"


def _identity(req):
    """Resolve the caller. Returns (email, is_internal) or None if unauthorized.

    Fails CLOSED: a caller-supplied X-Portal-Email is not identity here. The
    old check accepted any non-empty header, so `curl -H 'X-Portal-Email: x'`
    could file tickets against, and enumerate the request history of, any
    property in the portfolio. Only two things authenticate now:

      * X-Internal-Key  — server-to-server (also grants internal audience)
      * a verified Clerk Bearer token — the portal frontend already attaches
        one to every fetch (client-portal.html)

    The header fallback survives only under FLASK_ENV=development so local work
    doesn't need a live Clerk session.
    """
    if _is_internal(req):
        return (current_portal_email() or "internal@rpmliving.com", True)
    email = verified_portal_email()
    if email:
        return (email, False)
    if _dev_mode() and current_portal_email():
        return (current_portal_email(), False)
    return None


def _gate(req):
    """Authenticate + feature-gate. Returns (identity, None) or (None, response)."""
    ident = _identity(req)
    if not ident:
        return None, (jsonify({"error": "auth required"}), 401)
    email, is_internal = ident
    if not is_internal:
        denied = require_access(FEATURE_KEY, email)
        if denied:
            return None, denied
    return ident, None


# ── GET /api/portal-tickets/types ────────────────────────────────────────────

@portal_tickets_bp.route("/api/portal-tickets/types", methods=["GET", "OPTIONS"])
def ticket_types():
    if request.method == "OPTIONS":
        return preflight_response()
    ident, denied = _gate(request)
    if denied:
        return denied
    _, is_internal = ident
    try:
        types = portal_tickets.types_with_schema(include_internal=is_internal)
    except Exception as e:  # noqa: BLE001
        logger.warning("portal ticket types failed: %s", e)
        types = []
    return jsonify({"types": types})


# ── POST /api/portal-tickets/create ──────────────────────────────────────────

@portal_tickets_bp.route("/api/portal-tickets/create", methods=["POST", "OPTIONS"])
def ticket_create():
    if request.method == "OPTIONS":
        return preflight_response()
    ident, denied = _gate(request)
    if denied:
        return denied
    submitted_by, is_internal = ident

    body = request.get_json(silent=True) or {}
    company_id = (body.get("company_id") or "").strip()
    type_key = (body.get("ticket_type") or body.get("type") or "").strip()
    subject = (body.get("subject") or "").strip()
    fields = body.get("fields") or {}
    property_uuid = (body.get("uuid") or "").strip()

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
        internal=is_internal,
    )
    return jsonify(body_out), status


# ── GET /api/portal-tickets ──────────────────────────────────────────────────

@portal_tickets_bp.route("/api/portal-tickets", methods=["GET", "OPTIONS"])
def ticket_list():
    if request.method == "OPTIONS":
        return preflight_response()
    _, denied = _gate(request)
    if denied:
        return denied
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
