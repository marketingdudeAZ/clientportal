"""ClickUp webhook → HubSpot company notes blueprint.

POST /webhooks/clickup/task-activity
    Receives ClickUp task webhooks. On a status change, posts a proactive-
    work note to the linked HubSpot company (see clickup_notes.py). The
    signature is verified with CLICKUP_WEBHOOK_SECRET (comma-separated list
    supported — one secret per ClickUp webhook).
"""

from __future__ import annotations

import hashlib
import hmac
import logging

from flask import Blueprint, jsonify, request

from config import CLICKUP_WEBHOOK_SECRET

logger = logging.getLogger(__name__)

clickup_bp = Blueprint("clickup_notes", __name__)


def _verify_signature(raw: bytes, signature: str) -> bool:
    if not CLICKUP_WEBHOOK_SECRET:
        logger.warning("CLICKUP_WEBHOOK_SECRET not set — accepting unsigned webhook")
        return True
    if not signature:
        return False
    for secret in (s.strip() for s in CLICKUP_WEBHOOK_SECRET.split(",") if s.strip()):
        expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        if hmac.compare_digest(expected, signature):
            return True
    return False


@clickup_bp.route("/webhooks/clickup/task-activity", methods=["POST"])
def clickup_task_activity():
    raw = request.get_data()
    sig = request.headers.get("X-Signature", "")
    if not _verify_signature(raw, sig):
        return jsonify({"error": "invalid signature"}), 401

    payload = request.get_json(silent=True) or {}
    try:
        from clickup_notes import handle_event
        result = handle_event(payload)
    except Exception as e:  # never 500 a webhook — ClickUp would retry-storm
        logger.error("clickup task-activity failed: %s", e, exc_info=True)
        return jsonify({"status": "error", "reason": str(e)}), 200
    return jsonify(result), 200
