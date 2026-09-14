"""Workspace API — /api/workspace/*.

The HTTP edge of the simplified client portal. The contract lives in
docs/handoffs/PORTAL_WORKSPACE_BUILD_PLAN.md ("API contract"). This file does
auth and request parsing only; every read and every decision is a call into a
`skills/workspace_*.py` module. Routes never call HubSpot, HubDB, BigQuery or
Claude directly.

Every route 404s unless WORKSPACE_ENABLED=true (see `_gate`), so registering
this blueprint is inert until the flag flips.
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, make_response, request

from _route_utils import ALLOWED_ORIGINS

logger = logging.getLogger(__name__)

workspace_bp = Blueprint("workspace", __name__)

FEATURE_KEY = "workspace"
API_PREFIX = "/api/workspace"


def _preflight():
    """CORS preflight that also allows the signed-link header."""
    resp = make_response("", 204)
    origin = request.headers.get("Origin", "")
    if origin in ALLOWED_ORIGINS:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Credentials"] = "true"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = (
            "Content-Type, X-Portal-Email, Authorization, X-Workspace-Link"
        )
    return resp


@workspace_bp.before_request
def _gate():
    """Flag check for every route in this blueprint.

    Runs after the app-level Clerk hook, so a verified Bearer identity is
    already in place by the time anything here looks at the request.
    """
    from skills import workspace_links

    if not request.path.startswith(API_PREFIX):
        return None
    if not workspace_links.workspace_enabled():
        return jsonify({"error": "Not found"}), 404
    if request.method == "OPTIONS":
        return _preflight()
    return None
