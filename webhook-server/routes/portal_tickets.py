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
    )
    return jsonify(body_out), status


# ── POST /api/portal-tickets/resolve-profile ─────────────────────────────────

@portal_tickets_bp.route("/api/portal-tickets/resolve-profile", methods=["POST", "OPTIONS"])
def ticket_resolve_profile():
    """Apply a requester's conflict decisions to the property profile.

    Body: {company_id, resolutions: [{key, value}]}. Only the fields the user
    chose to overwrite are sent; "keep current" needs no write."""
    if request.method == "OPTIONS":
        return preflight_response()
    if not _is_authorized(request):
        return jsonify({"error": "auth required"}), 401
    body = request.get_json(silent=True) or {}
    company_id = (body.get("company_id") or "").strip()
    resolutions = body.get("resolutions") or []
    edited_by = request.headers.get("X-Portal-Email", "").strip()
    if not company_id or not isinstance(resolutions, list):
        return jsonify({"ok": False, "error": "company_id and resolutions required"}), 400

    import portal_ticket_profile
    applied, failed = [], []
    for r in resolutions:
        key = (r or {}).get("key")
        value = (r or {}).get("value")
        if not key or value in (None, ""):
            continue
        try:
            ok, msg = portal_ticket_profile.resolve_conflict(company_id, key, value, edited_by=edited_by)
        except Exception as e:  # noqa: BLE001
            ok, msg = False, str(e)
        (applied if ok else failed).append({"key": key, "message": msg if not ok else "ok"})
    return jsonify({"ok": True, "applied": applied, "failed": failed})



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
