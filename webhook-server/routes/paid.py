"""Paid Media routes — /api/paid/*.

First blueprint extracted from the server.py monolith. Pattern to copy for
future extractions:
  1. All `@app.route` decorators become `@paid_bp.route`.
  2. Shared helpers (`preflight_response`, `require_feature`) come from
     `_route_utils`, not from server.py directly — avoids circular imports.
  3. Feature-specific helpers (`_resolve_paid_context`) live in the blueprint.
  4. Business modules (`paid_media`) are still imported lazily inside the
     handlers for Flask boot speed parity with the rest of server.py.
"""

import logging

from flask import Blueprint, jsonify, request

from _route_utils import preflight_response, require_feature

logger = logging.getLogger(__name__)

paid_bp = Blueprint("paid", __name__)


def _resolve_paid_context():
    """Return (email, company_id, tier) or a Flask response on reject.

    Same shape as server.py's `_resolve_seo_context` but with paid_* feature
    keys. Kept here because only Paid routes use it.
    """
    email = request.headers.get("X-Portal-Email", "").lower().strip()
    if not email:
        return jsonify({"error": "Authentication required"}), 401
    company_id = request.args.get("company_id") or (request.get_json(silent=True) or {}).get("company_id")
    if not company_id:
        return jsonify({"error": "company_id is required"}), 400
    from seo_entitlement import get_seo_tier
    tier = get_seo_tier(str(company_id))
    return email, str(company_id), tier


@paid_bp.route("/api/paid/targeting", methods=["GET", "OPTIONS"])
def paid_targeting():
    if request.method == "OPTIONS":
        return preflight_response()
    ctx = _resolve_paid_context()
    if not isinstance(ctx, tuple):
        return ctx
    _, company_id, tier = ctx
    gate = require_feature(tier, "paid_targeting")
    if gate:
        return gate
    platform = request.args.get("platform", "meta").lower()
    try:
        from paid_media import targeting_coverage
        return jsonify({"tier": tier, **targeting_coverage(company_id, platform=platform)})
    except Exception as e:
        logger.error("paid targeting failed: %s", e, exc_info=True)
        return jsonify({"error": "Failed to load targeting"}), 500


@paid_bp.route("/api/paid/audiences", methods=["GET", "OPTIONS"])
def paid_audiences():
    if request.method == "OPTIONS":
        return preflight_response()
    ctx = _resolve_paid_context()
    if not isinstance(ctx, tuple):
        return ctx
    _, company_id, tier = ctx
    gate = require_feature(tier, "paid_audiences")
    if gate:
        return gate
    try:
        from paid_media import audience_narrative
        return jsonify({"tier": tier, **audience_narrative(company_id)})
    except Exception as e:
        logger.error("paid audiences failed: %s", e, exc_info=True)
        return jsonify({"error": "Failed to load audiences"}), 500


@paid_bp.route("/api/paid/creative", methods=["GET", "OPTIONS"])
def paid_creative():
    if request.method == "OPTIONS":
        return preflight_response()
    ctx = _resolve_paid_context()
    if not isinstance(ctx, tuple):
        return ctx
    _, company_id, tier = ctx
    gate = require_feature(tier, "paid_creative")
    if gate:
        return gate
    try:
        from paid_media import creative_and_offers
        return jsonify({"tier": tier, **creative_and_offers(company_id)})
    except Exception as e:
        logger.error("paid creative failed: %s", e, exc_info=True)
        return jsonify({"error": "Failed to load creative"}), 500


@paid_bp.route("/api/paid/trust-signal", methods=["POST", "OPTIONS"])
def paid_trust_signal():
    """Silent log when a client drills into keyword-level detail in Paid.

    v1: log-only, no notifications — want volume data before wiring routing.
    Paid JS fires this on keyword-like searches/filters inside Paid surface.
    """
    if request.method == "OPTIONS":
        return preflight_response()
    email = request.headers.get("X-Portal-Email", "").lower().strip()
    if not email:
        return jsonify({"error": "Authentication required"}), 401
    payload = request.get_json(silent=True) or {}
    company_id = str(payload.get("company_id") or "").strip()
    if not company_id:
        return jsonify({"error": "company_id is required"}), 400
    signal_type = (payload.get("signal_type") or "paid_keyword_drilldown").strip()
    detail = (payload.get("detail") or "").strip()
    try:
        from paid_media import log_trust_signal
        log_trust_signal(company_id, email, signal_type, detail)
    except Exception as e:
        logger.warning("paid trust signal log failed: %s", e)
    # Always 204 — don't want the client to retry or learn whether logging
    # succeeded; best-effort observation channel.
    return ("", 204)
