"""Leadership API blueprint — /api/leadership.

    GET /api/leadership?since_days=90    the whole view
    GET /api/leadership/revenue          just the revenue snapshot

Auth is deliberately stricter than the rest of the portal. Every other surface
answers questions about ONE property, and a client seeing their own property is
the point. This one aggregates contracted revenue across all 751 managed
properties — what each service line earns, and what the whole book is worth.
A client must never reach it, and neither should an internal user who only has
portal access because they manage a building.

So: X-Internal-Key for server-to-server, or a portal user whose
`feature_access.role_for()` is internal. There is no client path, no allowlist,
and no company_id parameter to scope it with — `require_company_access` is the
wrong tool here, because the answer is the portfolio.
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from _route_utils import current_portal_email, is_internal_caller, preflight_response

logger = logging.getLogger(__name__)

leadership_bp = Blueprint("leadership", __name__)

MAX_WINDOW_DAYS = 365


def _authorize():
    """None when the caller may see portfolio-wide money. Otherwise a response."""
    if is_internal_caller():
        return None

    email = current_portal_email()
    if not email:
        return jsonify({"error": "Authentication required"}), 401

    try:
        from feature_access import ROLE_INTERNAL, role_for
        if role_for(email) == ROLE_INTERNAL:
            return None
    except Exception as exc:                                    # noqa: BLE001
        # Fail closed. If we cannot establish that someone is internal, they
        # are not — an identity service being down is not a reason to hand out
        # the revenue of the entire portfolio.
        logger.error("leadership: role lookup failed for %s: %s", email, exc)
        return jsonify({"error": "Authorization unavailable"}), 503

    return jsonify({"error": "Internal access required"}), 403


def _window() -> int:
    try:
        days = int(request.args.get("since_days", 90))
    except (TypeError, ValueError):
        days = 90
    return max(1, min(days, MAX_WINDOW_DAYS))


@leadership_bp.route("/api/leadership", methods=["GET", "OPTIONS"])
def leadership_view():
    if request.method == "OPTIONS":
        return preflight_response()
    gate = _authorize()
    if gate:
        return gate

    import leadership

    force = str(request.args.get("force", "")).lower() in ("1", "true", "yes")
    try:
        payload = leadership.build(since_days=_window(), force=force)
    except Exception as exc:                                    # noqa: BLE001
        logger.error("leadership: build failed: %s", exc, exc_info=True)
        return jsonify({"error": "Could not assemble the leadership view"}), 500

    # A partly-degraded view is still worth returning — the sections that read
    # cleanly are the ones someone came for. `degraded` names what did not, so
    # a missing section reads as missing rather than as zero.
    return jsonify(payload)


@leadership_bp.route("/api/leadership/revenue", methods=["GET", "OPTIONS"])
def leadership_revenue():
    if request.method == "OPTIONS":
        return preflight_response()
    gate = _authorize()
    if gate:
        return gate

    import leadership

    force = str(request.args.get("force", "")).lower() in ("1", "true", "yes")
    try:
        return jsonify(leadership.revenue(force=force))
    except Exception as exc:                                    # noqa: BLE001
        logger.error("leadership: revenue failed: %s", exc, exc_info=True)
        return jsonify({"error": "Could not read the revenue snapshot"}), 500
