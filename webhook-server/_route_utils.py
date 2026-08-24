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


def identity_is_verified():
    """True if this request's portal email was proven, not merely asserted.

    server.py's `_clerk_identity` before_request sets this after it verifies a
    Clerk Bearer JWT. The flag lives in `request.environ` under a lowercase
    dotted key rather than a header, because anything shaped like `HTTP_*` can
    be injected by the caller — including `X-Portal-User-Id`, which is why
    presence of that header is not proof of anything.
    """
    return bool(request.environ.get("portal.identity_verified"))


def is_internal_caller():
    """True for a server-to-server call carrying the shared internal key."""
    import hmac
    expected = os.environ.get("INTERNAL_API_KEY", "")
    provided = request.headers.get("X-Internal-Key", "")
    return bool(expected and provided and hmac.compare_digest(expected, provided))


def require_company_access(company_id):
    """Reject unless the caller may act on `company_id`. 400/401/403 or None.

        gate = require_company_access(company_id)
        if gate:
            return gate

    What this fixes: every property-scoped endpoint used to take company_id
    straight from the query string and answer for it. `GET /api/portal-tickets
    ?company_id=<anything>` returned another property's tickets to anyone who
    changed the number. Unlike `require_access`, which asks "may this person
    see this FEATURE", this asks "may this person see THIS PROPERTY".

    The rules:
      - internal key            -> allowed (cron, webhooks, internal jobs)
      - no email                -> 401
      - no company_id           -> 400, never a silent portfolio-wide answer
      - internal role           -> allowed, portfolio-wide by design
      - client role             -> allowed only for ids in feature_access
                                   .companies_for(email); otherwise 403

    What this does NOT fix, and must not be described as fixing: with no Bearer
    token, X-Portal-Email is asserted by the caller. Anyone who sends
    `X-Portal-Email: someone@rpmliving.com` is internal as far as this function
    can tell, so this narrows the hole to "you must know an internal address"
    rather than closing it. Closing it needs the identity proven — Clerk covers
    part of the userbase today. Set PORTAL_STRICT_IDENTITY=true to require a
    verified identity here and make the gate real; it is off by default because
    turning it on locks out every user Clerk does not yet cover.
    """
    from feature_access import ROLE_INTERNAL, companies_for, role_for

    if is_internal_caller():
        return None

    email = current_portal_email()
    if not email:
        return jsonify({"error": "Authentication required"}), 401

    if os.environ.get("PORTAL_STRICT_IDENTITY", "").lower() in ("1", "true", "yes"):
        if not identity_is_verified():
            return jsonify({
                "error": "Verified sign-in required",
                "detail": "This endpoint requires a verified session, not an "
                          "asserted email header.",
            }), 401

    company_id = (str(company_id) if company_id is not None else "").strip()
    if not company_id:
        return jsonify({"error": "company_id required"}), 400

    if role_for(email) == ROLE_INTERNAL:
        return None

    if company_id in companies_for(email):
        return None

    # Deliberately does not say whether the company exists. A 403 that
    # distinguishes "not yours" from "no such property" is a directory.
    return jsonify({
        "error": "Not authorized for this property",
        "company_id": company_id,
    }), 403
