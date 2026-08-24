"""Ask routes — /api/ask/*.

    GET  /api/ask/questions      the registry manifest (what a client may ask)
    POST /api/ask/<key>          answer one preset question for one property

There is no free-text endpoint, and there is deliberately no `?q=` fallback.
v1 answers preset questions only; the manifest publishes `free_text: false` so
a client reads that as a decision rather than as a missing feature. An
unrecognized key is a 404, not a best-effort answer.

Extraction recipe is `routes/paid.py`'s: `@ask_bp.route` instead of
`@app.route`, shared helpers from `_route_utils`, feature-specific helpers
local to the blueprint, business modules imported lazily inside handlers.
Nothing here goes in server.py.

Auth mirrors `routes/redlight.py` — a portal user gated on the `ask` feature,
or a server-to-server caller with X-Internal-Key which skips the gate.
"""

from __future__ import annotations

import hmac
import logging
import os

from flask import Blueprint, jsonify, request

from _route_utils import current_portal_email, preflight_response, require_access

logger = logging.getLogger(__name__)

ask_bp = Blueprint("ask", __name__)

FEATURE_KEY = "ask"


def _internal_key_ok() -> bool:
    expected = os.getenv("INTERNAL_API_KEY", "")
    provided = request.headers.get("X-Internal-Key", "")
    return bool(expected and provided and hmac.compare_digest(expected, provided))


def _authorize():
    """None when the caller may proceed, else a Flask response."""
    if _internal_key_ok():
        return None
    email = current_portal_email()
    if not email:
        return jsonify({"error": "Authentication required"}), 401
    return require_access(FEATURE_KEY, email=email)


def _identifier():
    """The property the question is about, from the body or the query string.

    Accepts company_id / uuid / property. All three go to the Property
    Resolver, which is the one thing allowed to decide what an identifier is —
    the route must never branch on the shape of the string itself.
    """
    payload = request.get_json(silent=True) or {}
    for key in ("company_id", "uuid", "property", "property_id", "identifier"):
        value = payload.get(key) or request.args.get(key)
        if value:
            return str(value).strip()
    return ""


@ask_bp.route("/api/ask/questions", methods=["GET", "OPTIONS"])
def ask_questions():
    if request.method == "OPTIONS":
        return preflight_response()
    gate = _authorize()
    if gate:
        return gate
    from skills import question_registry
    return jsonify(question_registry.manifest())


@ask_bp.route("/api/ask/<key>", methods=["POST", "OPTIONS"])
def ask_answer(key):
    if request.method == "OPTIONS":
        return preflight_response()
    gate = _authorize()
    if gate:
        return gate

    identifier = _identifier()
    if not identifier:
        return jsonify({"error": "company_id is required"}), 400

    from skills import ask_engine, property_resolver, question_registry

    force = str(request.args.get("force", "")).lower() in ("1", "true", "yes")
    try:
        result = ask_engine.answer(identifier, key, force=force)
    except question_registry.UnknownQuestion:
        return jsonify({
            "error": "Unknown question",
            "question": key,
            "available": question_registry.keys(),
        }), 404
    except property_resolver.AmbiguousProperty as exc:
        return jsonify({"error": "Ambiguous property", "detail": str(exc)}), 409
    except property_resolver.PropertyNotFound as exc:
        return jsonify({"error": "Property not found", "detail": str(exc)}), 404
    except Exception as exc:                                    # noqa: BLE001
        logger.error("ask %s failed for %s: %s", key, identifier, exc, exc_info=True)
        return jsonify({"error": "Failed to answer question"}), 500

    if result.get("generating"):
        return jsonify(result), 202
    return jsonify(result)
