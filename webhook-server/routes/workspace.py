"""Workspace API — /api/workspace/*.

The HTTP edge of the Workspace. The contract lives in
docs/handoffs/PORTAL_WORKSPACE_BUILD_PLAN.md ("API contract", "Phase 2
contract"). This file does auth and request parsing only; every read and every
action is a call into a `skills/workspace_*.py` module. Routes never call
HubSpot, HubDB, BigQuery or Claude directly.

    GET  /api/workspace/me
    GET  /api/workspace/portfolio?view=needs_me|all&page=     internal only
    GET  /api/workspace/signals?company_id=                   internal only
    POST /api/workspace/signals/<id>/start-work               verified identity
    GET  /api/workspace/work?company_id=&status=
    GET  /api/workspace/work/<id>?company_id=
    POST /api/workspace/work/<id>/decision                    verified identity
    POST /api/workspace/work/<id>/undo                        verified identity, same decider
    GET  /api/workspace/property?company_id=
    GET  /api/workspace/performance?company_id=&range=
    GET  /api/workspace/plan?company_id=
    GET  /api/workspace/client-view?company_id=
    POST /api/workspace/requests/draft
    POST /api/workspace/requests                              verified identity
    GET  /api/workspace/requests?company_id=
    GET  /api/workspace/search?q=&company_id=
    POST /api/internal/workspace/warm                         X-Internal-Key

Gates:
  * every route 404s unless WORKSPACE_ENABLED=true (`_gate`);
  * every user route needs `require_access("workspace")`, and property-scoped
    routes also `require_company_access(company_id)`;
  * actions need a verified identity (Clerk; a signed link only when
    WORKSPACE_SIGNED_LINKS_CAN_DECIDE=true) and are refused in preview mode.

Roles are applied here, server-side. `X-Workspace-Preview-Role: client` from an
internal caller renders every read with client-role filtering ("Preview as
client") and forbids every write. From anyone else the header is ignored.
"""

from __future__ import annotations

import hmac
import logging
import os

from flask import Blueprint, jsonify, make_response, request

from _route_utils import (ALLOWED_ORIGINS, current_portal_email, identity_is_verified,
                          require_access, require_company_access)

logger = logging.getLogger(__name__)

workspace_bp = Blueprint("workspace", __name__)

FEATURE_KEY = "workspace"
API_PREFIX = "/api/workspace"
INTERNAL_PREFIX = "/api/internal/workspace"
PREVIEW_HEADER = "X-Workspace-Preview-Role"


def _preflight():
    """CORS preflight that also allows the workspace headers."""
    resp = make_response("", 204)
    origin = request.headers.get("Origin", "")
    if origin in ALLOWED_ORIGINS:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Credentials"] = "true"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = (
            "Content-Type, X-Portal-Email, Authorization, X-Workspace-Link, " + PREVIEW_HEADER
        )
    return resp


@workspace_bp.before_request
def _gate():
    """Flag check and signed-link handling for every route in this blueprint.

    Runs after the app-level Clerk hook, so a verified Bearer identity is
    already in place by the time anything here looks at the request.
    """
    from skills import workspace_links

    path = request.path
    if not (path.startswith(API_PREFIX) or path.startswith(INTERNAL_PREFIX)):
        return None
    if not workspace_links.workspace_enabled():
        return jsonify({"error": "Not found"}), 404
    if request.method == "OPTIONS":
        return _preflight()
    if path.startswith(API_PREFIX):
        return workspace_links.apply_to_request()
    return None


# ── who is asking ────────────────────────────────────────────────────────────

def _real_internal() -> bool:
    from feature_access import ROLE_INTERNAL, role_for
    return role_for(current_portal_email()) == ROLE_INTERNAL


def _preview_role() -> str | None:
    """"client" when an internal caller asks to preview as a client, else None."""
    if (request.headers.get(PREVIEW_HEADER) or "").strip().lower() == "client" and _real_internal():
        return "client"
    return None


def _is_internal() -> bool:
    """Internal role, and not previewing as a client."""
    return _real_internal() and not _preview_role()


def _write_gate():
    """Actions: never in preview, and only with a verified identity."""
    if _preview_role():
        return jsonify({"error": "preview_read_only",
                        "detail": "Preview as client is read-only."}), 403
    if not identity_is_verified():
        return jsonify({
            "error": "Verified sign-in required",
            "detail": "This action needs a verified session, not an asserted email header "
                      "or a read-only preview link.",
        }), 401
    return None


def _internal_key_ok() -> bool:
    expected = os.getenv("INTERNAL_API_KEY", "")
    provided = request.headers.get("X-Internal-Key", "")
    return bool(expected and provided and hmac.compare_digest(expected, provided))


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


def _refused(exc):
    return jsonify(exc.body()), exc.status


# ── identity and portfolio ───────────────────────────────────────────────────

@workspace_bp.route("/api/workspace/me", methods=["GET", "OPTIONS"])
def workspace_me():
    gate = require_access(FEATURE_KEY)
    if gate:
        return gate
    from skills import workspace_links, workspace_views
    preview = _preview_role()
    try:
        return jsonify(workspace_views.build_me(
            current_portal_email(), verified=identity_is_verified(),
            can_decide=identity_is_verified() and not preview, preview_role=preview,
            signed_link=bool(request.environ.get(workspace_links.SIGNED_LINK_ENVIRON))))
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
    view = (request.args.get("view") or "").strip() or None
    if view and view not in wp.VIEWS:
        return jsonify({"error": "Invalid view", "detail": "needs_me|all"}), 400
    raw_page = (request.args.get("page") or "1").strip()
    if not raw_page.isdigit() or int(raw_page) < 1:
        return jsonify({"error": "Invalid page"}), 400
    try:
        return jsonify(wp.build_portfolio(current_portal_email(), view=view, page=int(raw_page)))
    except Exception as exc:  # noqa: BLE001
        return _failed("portfolio", exc)


# ── signals ──────────────────────────────────────────────────────────────────

@workspace_bp.route("/api/workspace/signals", methods=["GET", "OPTIONS"])
def workspace_signals():
    gate = require_access(FEATURE_KEY)
    if gate:
        return gate
    if not _is_internal():
        return jsonify({"error": "Internal role required"}), 403
    company_id = _company_id() or None
    if company_id:
        gate = require_company_access(company_id)
        if gate:
            return gate
    from skills import workspace_inbox, workspace_signals as ws
    try:
        return jsonify(ws.build_signals(current_portal_email(), company_id))
    except workspace_inbox.PropertyNotFound:
        return jsonify({"error": "Property not found"}), 404
    except Exception as exc:  # noqa: BLE001
        return _failed("signals", exc)


@workspace_bp.route("/api/workspace/signals/<signal_id>/start-work", methods=["POST", "OPTIONS"])
def workspace_signal_start_work(signal_id):
    gate = _write_gate()
    if gate:
        return gate
    body = request.get_json(silent=True) or {}
    company_id = str(body.get("company_id") or "").strip()
    gate = _property_gate(company_id)
    if gate:
        return gate
    if not _is_internal():
        return jsonify({"error": "Internal role required"}), 403
    # The existing portal-ticket gate (domain, pilot roster, feature) applies to
    # anything filed into ClickUp, from here as from the ticket form.
    from routes.portal_tickets import _gate as ticket_gate
    ident, denied = ticket_gate(request)
    if denied:
        return denied
    ctx, err = _load(company_id)
    if err:
        return err
    from skills import workspace_common, workspace_signals as ws
    try:
        return jsonify(ws.start_work(ctx, signal_id, current_portal_email(), ticket_internal=ident[1])), 201
    except workspace_common.WorkspaceError as exc:
        return _refused(exc)
    except Exception as exc:  # noqa: BLE001
        return _failed("start work", exc)


# ── work ─────────────────────────────────────────────────────────────────────

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
        items, gaps = workspace_inbox.collect(ctx)
        return jsonify(workspace_inbox.build_work(items, gaps, status=status, internal=_is_internal()))
    except Exception as exc:  # noqa: BLE001
        return _failed("work", exc)


@workspace_bp.route("/api/workspace/work/<item_id>", methods=["GET", "OPTIONS"])
def workspace_work_item(item_id):
    company_id = _company_id()
    gate = _property_gate(company_id)
    if gate:
        return gate
    from skills import workspace_inbox
    try:
        workspace_inbox.parse_item_id(item_id)
    except ValueError:
        return jsonify({"error": "Item not found"}), 404
    ctx, err = _load(company_id)
    if err:
        return err
    try:
        item, _ = workspace_inbox.find_item(ctx, item_id)
    except Exception as exc:  # noqa: BLE001
        return _failed("item", exc)
    if item is None:
        return jsonify({"error": "Item not found"}), 404
    return jsonify(workspace_inbox.view_item(item, _is_internal()))


@workspace_bp.route("/api/workspace/work/<item_id>/decision", methods=["POST", "OPTIONS"])
def workspace_decision(item_id):
    """Approve or "not now" one item, through the source's existing handler.

    Needs a verified identity whatever PORTAL_STRICT_IDENTITY says, plus
    require_access and require_company_access. An asserted X-Portal-Email or a
    read-only preview link is never enough to act.
    """
    gate = _write_gate()
    if gate:
        return gate
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
        return _refused(exc)

    ctx, err = _load(company_id)
    if err:
        return err
    try:
        result = workspace_decisions.decide(ctx, item_id, action, reason, current_portal_email(),
                                            internal=_is_internal())
    except workspace_decisions.DecisionError as exc:
        return _refused(exc)
    except Exception as exc:  # noqa: BLE001
        return _failed("decision", exc)
    return jsonify(result)


@workspace_bp.route("/api/workspace/work/<item_id>/undo", methods=["POST", "OPTIONS"])
def workspace_undo(item_id):
    """Undo the caller's own decision within 10 minutes, where a safe reverse exists."""
    gate = _write_gate()
    if gate:
        return gate
    body = request.get_json(silent=True) or {}
    company_id = str(body.get("company_id") or "").strip()
    gate = _property_gate(company_id)
    if gate:
        return gate
    ctx, err = _load(company_id)
    if err:
        return err
    from skills import workspace_decisions
    try:
        return jsonify(workspace_decisions.undo(ctx, item_id, current_portal_email(),
                                                internal=_is_internal()))
    except workspace_decisions.DecisionError as exc:
        return _refused(exc)
    except Exception as exc:  # noqa: BLE001
        return _failed("undo", exc)


# ── property screens ─────────────────────────────────────────────────────────

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
        return jsonify(workspace_views.build_performance(ctx, range_days=int(raw),
                                                         internal=_is_internal()))
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
        return jsonify(workspace_views.build_plan(ctx, internal=_is_internal()))
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
        # Always the client-role rendering, whoever asks.
        items, gaps = workspace_inbox.collect(ctx)
        return jsonify(workspace_inbox.build_client_view(items, gaps))
    except Exception as exc:  # noqa: BLE001
        return _failed("client view", exc)


# ── new request ──────────────────────────────────────────────────────────────

@workspace_bp.route("/api/workspace/requests/draft", methods=["POST", "OPTIONS"])
def workspace_request_draft():
    """Draft only; nothing is filed. Refused in preview (it spends a model call)."""
    if _preview_role():
        return jsonify({"error": "preview_read_only", "detail": "Preview as client is read-only."}), 403
    body = request.get_json(silent=True) or {}
    company_id = str(body.get("company_id") or "").strip()
    gate = _property_gate(company_id)
    if gate:
        return gate
    ctx, err = _load(company_id)
    if err:
        return err
    from skills import workspace_common, workspace_requests
    try:
        return jsonify(workspace_requests.draft(ctx, body.get("text") or ""))
    except workspace_common.WorkspaceError as exc:
        return _refused(exc)
    except Exception as exc:  # noqa: BLE001
        return _failed("request draft", exc)


@workspace_bp.route("/api/workspace/requests", methods=["GET", "POST", "OPTIONS"])
def workspace_requests_route():
    from skills import workspace_common, workspace_requests
    if request.method == "GET":
        company_id = _company_id()
        gate = _property_gate(company_id)
        if gate:
            return gate
        ctx, err = _load(company_id)
        if err:
            return err
        try:
            return jsonify(workspace_requests.recent(ctx))
        except Exception as exc:  # noqa: BLE001
            return _failed("requests", exc)

    gate = _write_gate()
    if gate:
        return gate
    body = request.get_json(silent=True) or {}
    company_id = str(body.get("company_id") or "").strip()
    gate = _property_gate(company_id)
    if gate:
        return gate
    from routes.portal_tickets import _gate as ticket_gate
    ident, denied = ticket_gate(request)
    if denied:
        return denied
    ctx, err = _load(company_id)
    if err:
        return err
    try:
        result = workspace_requests.file_requests(ctx, body.get("tickets"), current_portal_email(),
                                                  ticket_internal=ident[1])
    except workspace_common.WorkspaceError as exc:
        return _refused(exc)
    except Exception as exc:  # noqa: BLE001
        return _failed("requests", exc)
    return jsonify(result), (201 if result["created"] else 200)


# ── search ───────────────────────────────────────────────────────────────────

@workspace_bp.route("/api/workspace/search", methods=["GET", "OPTIONS"])
def workspace_search():
    gate = require_access(FEATURE_KEY)
    if gate:
        return gate
    company_id = _company_id() or None
    if company_id:
        gate = require_company_access(company_id)
        if gate:
            return gate
    from skills import workspace_common, workspace_search as wsearch
    try:
        return jsonify(wsearch.search(current_portal_email(), request.args.get("q") or "",
                                      internal=_is_internal(), company_id=company_id))
    except workspace_common.WorkspaceError as exc:
        return _refused(exc)
    except Exception as exc:  # noqa: BLE001
        return _failed("search", exc)


# ── internal ─────────────────────────────────────────────────────────────────

@workspace_bp.route("/api/internal/workspace/warm", methods=["POST", "OPTIONS"])
def workspace_warm():
    """Pre-build the portfolio-wide caches (spend sheet, AptIQ exports, property
    list) before a demo and from cron. X-Internal-Key only."""
    if not _internal_key_ok():
        return jsonify({"error": "Internal key required"}), 401
    from skills import workspace_cache
    try:
        return jsonify(workspace_cache.warm())
    except Exception as exc:  # noqa: BLE001
        return _failed("warm", exc)


# ── v3: dashboard ────────────────────────────────────────────────────────────

@workspace_bp.route("/api/workspace/dashboard", methods=["GET", "OPTIONS"])
def workspace_dashboard():
    gate = require_access(FEATURE_KEY)
    if gate:
        return gate
    from skills import workspace_dashboard as wdash
    lens = (request.args.get("lens") or "").strip() or None
    if lens and lens not in wdash.LENSES:
        return jsonify({"error": "Invalid lens", "detail": "|".join(wdash.LENSES)}), 400
    try:
        return jsonify(wdash.build_dashboard(current_portal_email(), internal=_is_internal(),
                                             scope_internal=_real_internal(), lens=lens))
    except Exception as exc:  # noqa: BLE001
        return _failed("dashboard", exc)


# ── v3: approvals ────────────────────────────────────────────────────────────

@workspace_bp.route("/api/workspace/approvals", methods=["GET", "OPTIONS"])
def workspace_approvals():
    gate = require_access(FEATURE_KEY)
    if gate:
        return gate
    from skills import workspace_approvals as wapp
    category = (request.args.get("category") or "").strip() or None
    if category and category not in wapp.CATEGORIES:
        return jsonify({"error": "Invalid category", "detail": "|".join(wapp.CATEGORIES)}), 400
    try:
        return jsonify(wapp.build_approvals(current_portal_email(), internal=_is_internal(),
                                            scope_internal=_real_internal(), category=category))
    except Exception as exc:  # noqa: BLE001
        return _failed("approvals", exc)
