"""Workspace API — /api/workspace/*.

The HTTP edge of the simplified client portal. The contract lives in
docs/handoffs/PORTAL_WORKSPACE_BUILD_PLAN.md ("API contract"). This file does
auth and request parsing only; every read and every decision is a call into a
`skills/workspace_*.py` module. Routes never call HubSpot, HubDB, BigQuery or
Claude directly.

    GET  /api/workspace/me
    GET  /api/workspace/portfolio                 internal role only
    GET  /api/workspace/work?company_id=&status=
    GET  /api/workspace/work/<id>?company_id=
    GET  /api/workspace/property?company_id=
    GET  /api/workspace/performance?company_id=&range=
    GET  /api/workspace/plan?company_id=
    GET  /api/workspace/client-view?company_id=

Gates:
  * every route 404s unless WORKSPACE_ENABLED=true (`_gate`);
  * every route needs `require_access("workspace")`;
  * every property-scoped route also needs `require_company_access(company_id)`.

Identity comes from Clerk (Bearer JWT, verified by server.py's before_request)
or, for internal demos, a signed preview link (`X-Workspace-Link`). There is
deliberately no `?email=` identity here.
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, make_response, request

from _route_utils import (ALLOWED_ORIGINS, current_portal_email, identity_is_verified,
                          require_access, require_company_access)

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


# ── helpers ──────────────────────────────────────────────────────────────────

def _is_internal() -> bool:
    from feature_access import ROLE_INTERNAL, role_for
    return role_for(current_portal_email()) == ROLE_INTERNAL


def _company_id() -> str:
    return (request.args.get("company_id") or "").strip()


def _property_gate(company_id: str):
    """require_access + require_company_access. None, or a response to return."""
    gate = require_access(FEATURE_KEY)
    if gate:
        return gate
    return require_company_access(company_id)


def _load(company_id: str):
    """(PropertyContext, None) or (None, error response)."""
    from skills import workspace_inbox
    try:
        return workspace_inbox.load_context(company_id), None
    except workspace_inbox.PropertyNotFound:
        return None, (jsonify({"error": "Property not found"}), 404)
    except Exception as exc:  # noqa: BLE001
        logger.warning("workspace: company %s unreadable: %s", company_id, exc)
        return None, (jsonify({"error": "Could not read the property",
                               "detail": type(exc).__name__}), 502)


def _failed(what: str, exc: Exception):
    logger.error("workspace %s failed: %s", what, exc, exc_info=True)
    return jsonify({"error": f"Could not load {what}"}), 500


# ── reads ────────────────────────────────────────────────────────────────────

@workspace_bp.route("/api/workspace/me", methods=["GET", "OPTIONS"])
def workspace_me():
    gate = require_access(FEATURE_KEY)
    if gate:
        return gate
    from skills import workspace_views
    try:
        return jsonify(workspace_views.build_me(current_portal_email(),
                                                verified=identity_is_verified()))
    except Exception as exc:  # noqa: BLE001
        return _failed("me", exc)


@workspace_bp.route("/api/workspace/portfolio", methods=["GET", "OPTIONS"])
def workspace_portfolio():
    gate = require_access(FEATURE_KEY)
    if gate:
        return gate
    if not _is_internal():
        return jsonify({"error": "Internal role required"}), 403
    from skills import workspace_portfolio as wp
    try:
        return jsonify(wp.build_portfolio(current_portal_email()))
    except Exception as exc:  # noqa: BLE001
        return _failed("portfolio", exc)


_WORK_STATUSES = ("to_do", "in_motion", "done", "all")


@workspace_bp.route("/api/workspace/work", methods=["GET", "OPTIONS"])
def workspace_work():
    company_id = _company_id()
    gate = _property_gate(company_id)
    if gate:
        return gate
    status = (request.args.get("status") or "to_do").strip()
    if status not in _WORK_STATUSES:
        return jsonify({"error": "Invalid status", "detail": "|".join(_WORK_STATUSES)}), 400
    ctx, err = _load(company_id)
    if err:
        return err
    from skills import workspace_inbox
    try:
        items, gaps = workspace_inbox.collect(ctx, internal=_is_internal())
        return jsonify(workspace_inbox.build_work(items, gaps, status=status))
    except Exception as exc:  # noqa: BLE001
        return _failed("work", exc)


@workspace_bp.route("/api/workspace/work/<item_id>", methods=["GET", "OPTIONS"])
def workspace_work_item(item_id):
    company_id = _company_id()
    gate = _property_gate(company_id)
    if gate:
        return gate
    from skills import workspace_common, workspace_inbox
    try:
        workspace_inbox.parse_item_id(item_id)
    except ValueError:
        return jsonify({"error": "Item not found"}), 404
    ctx, err = _load(company_id)
    if err:
        return err
    try:
        item, _ = workspace_inbox.find_item(ctx, item_id, internal=_is_internal())
    except Exception as exc:  # noqa: BLE001
        return _failed("item", exc)
    if item is None:
        return jsonify({"error": "Item not found"}), 404
    return jsonify(workspace_common.public(item))


@workspace_bp.route("/api/workspace/property", methods=["GET", "OPTIONS"])
def workspace_property():
    company_id = _company_id()
    gate = _property_gate(company_id)
    if gate:
        return gate
    ctx, err = _load(company_id)
    if err:
        return err
    from skills import workspace_views
    try:
        return jsonify(workspace_views.build_property(ctx, internal=_is_internal()))
    except Exception as exc:  # noqa: BLE001
        return _failed("property", exc)


@workspace_bp.route("/api/workspace/performance", methods=["GET", "OPTIONS"])
def workspace_performance():
    company_id = _company_id()
    gate = _property_gate(company_id)
    if gate:
        return gate
    from skills import workspace_views
    raw = (request.args.get("range") or "30").strip()
    if not raw.isdigit() or int(raw) not in workspace_views.RANGES:
        return jsonify({"error": "Invalid range", "detail": "30|90|365"}), 400
    ctx, err = _load(company_id)
    if err:
        return err
    try:
        return jsonify(workspace_views.build_performance(ctx, range_days=int(raw)))
    except Exception as exc:  # noqa: BLE001
        return _failed("performance", exc)


@workspace_bp.route("/api/workspace/plan", methods=["GET", "OPTIONS"])
def workspace_plan():
    company_id = _company_id()
    gate = _property_gate(company_id)
    if gate:
        return gate
    ctx, err = _load(company_id)
    if err:
        return err
    from skills import workspace_views
    try:
        return jsonify(workspace_views.build_plan(ctx))
    except Exception as exc:  # noqa: BLE001
        return _failed("plan", exc)


@workspace_bp.route("/api/workspace/client-view", methods=["GET", "OPTIONS"])
def workspace_client_view():
    company_id = _company_id()
    gate = _property_gate(company_id)
    if gate:
        return gate
    ctx, err = _load(company_id)
    if err:
        return err
    from skills import workspace_inbox
    try:
        # What the client sees, whoever is asking: internal-only work never
        # appears here, even for an internal caller previewing the page.
        items, gaps = workspace_inbox.collect(ctx, internal=False)
        return jsonify(workspace_inbox.build_client_view(items, gaps))
    except Exception as exc:  # noqa: BLE001
        return _failed("client view", exc)


# ── decision ─────────────────────────────────────────────────────────────────

@workspace_bp.route("/api/workspace/work/<item_id>/decision", methods=["POST", "OPTIONS"])
def workspace_decision(item_id):
    """Approve or "not now" one item, through the source's existing handler.

    Needs a verified identity (Clerk or a signed preview link) whatever
    PORTAL_STRICT_IDENTITY says, plus require_access and
    require_company_access. An asserted X-Portal-Email is never enough to act.
    """
    if not identity_is_verified():
        return jsonify({
            "error": "Verified sign-in required",
            "detail": "Decisions need a verified session, not an asserted email header.",
        }), 401
    body = request.get_json(silent=True) or {}
    company_id = str(body.get("company_id") or "").strip()
    gate = _property_gate(company_id)
    if gate:
        return gate

    from skills import workspace_decisions
    action, reason = body.get("action"), body.get("reason")
    try:
        workspace_decisions.validate(action, reason)
    except workspace_decisions.DecisionError as exc:
        return jsonify(exc.body()), exc.status

    ctx, err = _load(company_id)
    if err:
        return err
    try:
        result = workspace_decisions.decide(ctx, item_id, action, reason, current_portal_email(),
                                            internal=_is_internal())
    except workspace_decisions.DecisionError as exc:
        return jsonify(exc.body()), exc.status
    except Exception as exc:  # noqa: BLE001
        return _failed("decision", exc)
    return jsonify(result)
