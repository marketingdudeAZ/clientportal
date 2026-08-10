"""Shared utilities used by both server.py and routes/* blueprints.

Keep this file tiny — it's the foundation the blueprint split builds on.
Anything with business logic belongs in a feature module, not here.
"""

import os

from flask import jsonify, make_response, request


# CORS origins allowed for the portal. Adding a new origin here + the
# @app.after_request add_cors handler in server.py applies to all blueprints.
ALLOWED_ORIGINS = [
    "https://go.rpmliving.com",
    "https://www.rpmliving.com",
    "https://digital.rpmliving.com",
]
if os.getenv("FLASK_ENV") == "development":
    ALLOWED_ORIGINS.append("http://localhost:3000")


def preflight_response():
    """Build a CORS preflight 204 response.

    Routes that accept OPTIONS should call this at the top of the handler.
    Public because blueprints need it too.
    """
    resp = make_response("", 204)
    origin = request.headers.get("Origin", "")
    if origin in ALLOWED_ORIGINS:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Credentials"] = "true"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, DELETE, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = (
            "Content-Type, X-Portal-Email, X-Internal-Key, X-Hub-Signature-256, Authorization"
        )
    return resp


def require_feature(tier, feature):
    """Reject with 403 if the given SEO tier doesn't include `feature`.

    Returns a Flask response on reject, or None to continue. Callers do:
        gate = require_feature(tier, "keywords_write")
        if gate:
            return gate
    """
    from seo_entitlement import has_feature
    if not has_feature(tier, feature):
        return jsonify({
            "error": "Feature not available on current SEO tier",
            "feature": feature,
            "tier": tier,
        }), 403
    return None


def current_portal_email():
    """The logged-in portal user's email, lowercased, or "" if absent.

    Single source for reading X-Portal-Email so every route normalizes it
    the same way.
    """
    return request.headers.get("X-Portal-Email", "").lower().strip()


def require_access(feature_key, email=None):
    """Reject unless the logged-in user may see `feature_key` (Beta/Prod).

    Returns a Flask response on reject (401 if not signed in, 403 if the
    feature isn't released to them), or None to continue. Usage mirrors
    require_feature:

        gate = require_access("redlight")
        if gate:
            return gate

    Server-to-server callers authenticate with X-Internal-Key and should
    be let through before this check — this gate is for portal users.
    """
    from feature_access import can_access, stage_for

    email = current_portal_email() if email is None else (email or "").lower().strip()
    if not email:
        return jsonify({"error": "Authentication required"}), 401
    if not can_access(email, feature_key):
        return jsonify({
            "error": "Feature not available for this user yet",
            "feature": feature_key,
            "stage": stage_for(feature_key),
        }), 403
    return None
