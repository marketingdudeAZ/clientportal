"""Portal meta routes — feature visibility for the logged-in user.

GET /api/portal/features
    Returns the Beta/Prod visibility manifest for the signed-in portal
    user (X-Portal-Email). The CMS template reads this to show/hide
    surfaces. This is a convenience for the UI only — every protected
    endpoint still enforces access server-side via require_access.

    Response:
      {
        "email": "kyle@rpmliving.com",
        "role": "internal",
        "features": {
          "redlight": {"label": "...", "stage": "beta", "visible": true},
          ...
        }
      }
"""

from __future__ import annotations

from flask import Blueprint, jsonify

from _route_utils import current_portal_email, preflight_response

portal_bp = Blueprint("portal", __name__)


@portal_bp.route("/api/portal/features", methods=["GET", "OPTIONS"])
def portal_features():
    from flask import request

    if request.method == "OPTIONS":
        return preflight_response()

    from feature_access import resolve_features, role_for

    email = current_portal_email()
    if not email:
        return jsonify({"error": "Authentication required"}), 401

    return jsonify({
        "email": email,
        "role": role_for(email),
        "features": resolve_features(email),
    })
