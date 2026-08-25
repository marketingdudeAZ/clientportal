"""Portal feedback API — /api/feedback.

    GET  /api/feedback/health    is the widget usable right now
    POST /api/feedback           file one report (multipart/form-data)

Gated to internal staff. This writes into a live ClickUp list, and the widget
that calls it is a testing instrument, not a client-facing feature — a client
finding it and filing into the internal QA queue would be confusing for
everyone.

The health endpoint exists so the widget can hide itself rather than appear,
accept a carefully written bug report, and only then admit it has nowhere to
put it.
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from _route_utils import current_portal_email, is_internal_caller, preflight_response

logger = logging.getLogger(__name__)

feedback_bp = Blueprint("feedback", __name__)

MAX_FILES = 4


def _authorize():
    """None when the caller may file. Otherwise a response."""
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
        logger.error("feedback: role lookup failed for %s: %s", email, exc)
        return jsonify({"error": "Authorization unavailable"}), 503
    return jsonify({"error": "Internal access required"}), 403


@feedback_bp.route("/api/feedback/health", methods=["GET", "OPTIONS"])
def feedback_health():
    if request.method == "OPTIONS":
        return preflight_response()
    gate = _authorize()
    if gate:
        return gate

    import portal_feedback
    ready = portal_feedback.is_configured()
    return jsonify({
        "ready": ready,
        "reason": None if ready else (
            "CLICKUP_LIST_PORTAL_FEEDBACK is not set, so reports have nowhere "
            "to go."),
        "severities": [
            {"key": k, "label": v["label"]}
            for k, v in portal_feedback.SEVERITY.items()
        ],
        "max_screenshots": portal_feedback.MAX_SHOTS,
    })


@feedback_bp.route("/api/feedback", methods=["POST", "OPTIONS"])
def feedback_submit():
    if request.method == "OPTIONS":
        return preflight_response()
    gate = _authorize()
    if gate:
        return gate

    import portal_feedback

    note = (request.form.get("note") or "").strip()
    severity = (request.form.get("severity") or "").strip().lower()

    context = {
        "page":          (request.form.get("page") or "").strip(),
        "url":           (request.form.get("url") or "").strip(),
        "company_id":    (request.form.get("company_id") or "").strip(),
        "property_name": (request.form.get("property_name") or "").strip(),
        "viewport":      (request.form.get("viewport") or "").strip(),
        "user_agent":    request.headers.get("User-Agent", "")[:300],
    }

    shots = []
    for storage in request.files.getlist("screenshot")[:MAX_FILES]:
        if not storage or not storage.filename:
            continue
        shots.append((
            storage.filename,
            storage.read(),
            (storage.mimetype or "application/octet-stream"),
        ))

    try:
        result = portal_feedback.submit(
            note=note,
            severity=severity,
            context=context,
            reporter=current_portal_email() or "internal-key",
            screenshots=shots,
        )
    except portal_feedback.FeedbackNotConfigured as exc:
        # 503, not 500: the service is fine, it has not been pointed anywhere.
        return jsonify({"ok": False, "error": str(exc)}), 503
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except portal_feedback.FeedbackFailed as exc:
        # 502: the upstream refused. The tester must retry — nothing was saved.
        return jsonify({"ok": False, "error": str(exc)}), 502
    except Exception as exc:                                    # noqa: BLE001
        logger.error("feedback: unexpected failure: %s", exc, exc_info=True)
        return jsonify({"ok": False,
                        "error": "Something went wrong filing that. Nothing "
                                 "was saved — please try again."}), 500

    return jsonify({"ok": True, **result})
