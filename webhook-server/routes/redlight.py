"""Red Light Report — Lite blueprint (the walk-before-run rollout surface).

POST /api/red-light-lite/report
    Body: JSON {"properties": [ {metric row}, ... ]}  OR  raw CSV
          (Content-Type text/csv, or any body when ?format=csv-in).
    Query: format=json (default) | html
    Returns the scored portfolio report as JSON, or a brand-styled HTML
    table when format=html (or Accept: text/html).

Auth — dual, matching the full red-light endpoints:
    * Portal user (X-Portal-Email), gated on the `redlight_lite` feature.
    * Server-to-server (X-Internal-Key) bypasses the feature gate.
"""

from __future__ import annotations

import hmac
import logging
import os

from flask import Blueprint, Response, jsonify, request

from _route_utils import current_portal_email, preflight_response, require_access

logger = logging.getLogger(__name__)

redlight_lite_bp = Blueprint("redlight_lite", __name__)


def _internal_key_ok() -> bool:
    expected = os.getenv("INTERNAL_API_KEY", "")
    provided = request.headers.get("X-Internal-Key", "")
    return bool(expected and provided and hmac.compare_digest(expected, provided))


def _wants_html() -> bool:
    if request.args.get("format", "").lower() == "html":
        return True
    if request.args.get("format", "").lower() == "json":
        return False
    return "text/html" in request.headers.get("Accept", "")


def _extract_rows():
    """Pull property rows from a JSON body or a raw CSV upload."""
    ctype = request.headers.get("Content-Type", "")
    if "csv" in ctype or request.args.get("format") == "csv-in":
        from redlight_lite import parse_csv
        return parse_csv(request.get_data(as_text=True))
    payload = request.get_json(silent=True) or {}
    if isinstance(payload, list):
        return payload
    rows = payload.get("properties")
    return rows if isinstance(rows, list) else None


@redlight_lite_bp.route("/api/red-light-lite/report", methods=["POST", "OPTIONS"])
def red_light_lite_report():
    if request.method == "OPTIONS":
        return preflight_response()

    key_ok = _internal_key_ok()
    email = current_portal_email()
    if not email and not key_ok:
        return jsonify({"error": "Authentication required"}), 401
    if email and not key_ok:
        gate = require_access("redlight_lite", email=email)
        if gate:
            return gate

    rows = _extract_rows()
    if not rows:
        return jsonify({"error": "Provide a non-empty 'properties' array or CSV body"}), 400

    from redlight_lite import build_report, render_html

    report = build_report(rows)
    if _wants_html():
        return Response(render_html(report), mimetype="text/html")
    return jsonify(report)
