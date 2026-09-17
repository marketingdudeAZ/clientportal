"""MCP endpoint — portal context for an external agent platform.

WHY IT LIVES IN THIS SERVICE
    One Render service, one deploy, one set of credentials. The protocol is
    JSON-RPC 2.0 over a single POST, so it needs no ASGI server and no SDK: the
    portal keeps running under waitress exactly as it does today, and this file
    is the only new request path.

TRANSPORT
    POST /mcp            the whole protocol (initialize, tools/list, tools/call)
    GET  /mcp            405 — we never push server-initiated messages
    GET  /mcp/health     liveness, no token, so a monitor can watch it

    A client that accepts text/event-stream gets its reply as a single SSE
    `message` event, which is what the reference server does; anything else
    gets plain JSON. Both are valid and clients accept either.

AUTH
    Authorization: Bearer <token>, matched in constant time against
    MCP_BEARER_TOKEN, or MCP_TOKENS ("label:token,label:token") when you want
    one token per consumer so a single one can be rotated. With neither set the
    endpoint 404s: an unconfigured door is a closed door, not an open one.

    This path deliberately does NOT use portal identity. There is no user here,
    no Clerk session and no company scope — just a machine token that grants
    read access to the tools in skills/mcp_context.py. That module is read-only,
    which is what makes a single shared token acceptable.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import time
import uuid as _uuid
from typing import Any, Dict, Optional, Tuple

from flask import Blueprint, Response, jsonify, request

logger = logging.getLogger(__name__)

mcp_bp = Blueprint("mcp", __name__)

SERVER_NAME = "rpm-portal-context"
SERVER_VERSION = "1.0.0"

# Versions of the MCP spec this endpoint implements. The newest is returned when
# a client asks for something we do not recognize.
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")

# JSON-RPC 2.0 error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


# --- auth ------------------------------------------------------------------

def _token_table() -> Dict[str, str]:
    """token -> label."""
    table: Dict[str, str] = {}
    for pair in (os.environ.get("MCP_TOKENS") or "").split(","):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        label, token = pair.split(":", 1)
        token = token.strip()
        if token:
            table[token] = label.strip() or "unlabeled"
    single = (os.environ.get("MCP_BEARER_TOKEN") or "").strip()
    if single:
        table.setdefault(single, "default")
    return table


def _enabled() -> bool:
    if (os.environ.get("MCP_ENABLED") or "").strip().lower() in ("0", "false", "no"):
        return False
    return bool(_token_table())


def _caller_label() -> Optional[str]:
    header = request.headers.get("Authorization") or ""
    if not header.lower().startswith("bearer "):
        return None
    presented = header[7:].strip()
    if not presented:
        return None
    for token, label in _token_table().items():
        if hmac.compare_digest(presented, token):
            return label
    return None


# --- protocol plumbing -----------------------------------------------------

def _wants_sse() -> bool:
    return "text/event-stream" in (request.headers.get("Accept") or "").lower()


def _envelope(payload: Dict[str, Any], status: int = 200,
              session_id: Optional[str] = None) -> Response:
    """One JSON-RPC response, as SSE or JSON depending on what the client accepts."""
    if _wants_sse():
        body = "event: message\ndata: %s\n\n" % json.dumps(payload)
        resp = Response(body, status=status, mimetype="text/event-stream")
        resp.headers["Cache-Control"] = "no-store"
        # Never set Connection here: waitress rejects hop-by-hop headers from a
        # WSGI app (PEP 3333) with an AssertionError, which the client sees as a
        # 500. X-Accel-Buffering keeps a proxy from buffering the event instead.
        resp.headers["X-Accel-Buffering"] = "no"
    else:
        resp = Response(json.dumps(payload), status=status,
                        mimetype="application/json")
        resp.headers["Cache-Control"] = "no-store"
    if session_id:
        resp.headers["Mcp-Session-Id"] = session_id
    return resp


def _result(req_id: Any, result: Dict[str, Any],
            session_id: Optional[str] = None) -> Response:
    return _envelope({"jsonrpc": "2.0", "id": req_id, "result": result},
                     session_id=session_id)


def _error(req_id: Any, code: int, message: str,
           data: Optional[Dict[str, Any]] = None) -> Response:
    err: Dict[str, Any] = {"code": code, "message": message}
    if data:
        err["data"] = data
    return _envelope({"jsonrpc": "2.0", "id": req_id, "error": err})


def _pick_protocol(requested: Any) -> str:
    return requested if requested in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0]


# --- methods ---------------------------------------------------------------

def _initialize(params: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    client = (params or {}).get("clientInfo") or {}
    logger.info("mcp initialize client=%s/%s protocol=%s",
                client.get("name"), client.get("version"),
                (params or {}).get("protocolVersion"))
    result = {
        "protocolVersion": _pick_protocol((params or {}).get("protocolVersion")),
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        "instructions": (
            "Read-only context for a multifamily property portfolio: the community "
            "brief, current availability, the leasing funnel, authorized spend and "
            "work in flight. Call find_property first to get a company_id. Call "
            "get_metric_rules before reporting any number and get_compliance_rules "
            "before proposing any change — housing advertising is a Special Ad "
            "Category and several ordinary optimizations are prohibited. Where a "
            "source is missing you will get null plus a gap explaining why; report "
            "the gap rather than substituting a zero or an estimate."),
    }
    return result, _uuid.uuid4().hex


def _tools_list() -> Dict[str, Any]:
    from skills import mcp_context
    return {"tools": mcp_context.tool_descriptors()}


def _tools_call(params: Dict[str, Any]) -> Dict[str, Any]:
    """Run one tool. Tool-level failures come back as isError results, not
    JSON-RPC errors: the agent should see what went wrong and adapt, which is
    what the spec intends."""
    from skills import mcp_context

    name = (params or {}).get("name")
    args = (params or {}).get("arguments") or {}
    tool = mcp_context.BY_NAME.get(name)
    if tool is None:
        return {"content": [{"type": "text",
                             "text": "Unknown tool %r. Call tools/list." % name}],
                "isError": True}
    if not isinstance(args, dict):
        return {"content": [{"type": "text", "text": "`arguments` must be an object."}],
                "isError": True}

    allowed = set((tool.get("inputSchema") or {}).get("properties") or {})
    unexpected = [k for k in args if k not in allowed]
    if unexpected:
        return {"content": [{"type": "text",
                             "text": "Unexpected argument(s): %s. Accepted: %s." %
                                     (", ".join(sorted(unexpected)),
                                      ", ".join(sorted(allowed)) or "none")}],
                "isError": True}
    missing = [k for k in (tool.get("inputSchema") or {}).get("required", [])
               if not str(args.get(k) or "").strip()]
    if missing:
        return {"content": [{"type": "text",
                             "text": "Missing required argument(s): %s." %
                                     ", ".join(missing)}],
                "isError": True}

    started = time.monotonic()
    try:
        payload = tool["handler"](**args)
    except TypeError as exc:
        return {"content": [{"type": "text", "text": "Bad arguments: %s" % exc}],
                "isError": True}
    except Exception as exc:  # noqa: BLE001 — a connector failed under us
        logger.error("mcp tool %s failed: %s", name, exc, exc_info=True)
        return {"content": [{"type": "text",
                             "text": "%s could not complete (%s). The underlying "
                                     "source may be temporarily unavailable." %
                                     (name, type(exc).__name__)}],
                "isError": True}

    is_error = isinstance(payload, dict) and bool(payload.get("error"))
    logger.info("mcp tool=%s ms=%d error=%s", name,
                int((time.monotonic() - started) * 1000), is_error)
    return {
        "content": [{"type": "text", "text": json.dumps(payload, default=str)}],
        "structuredContent": payload if isinstance(payload, dict) else {"result": payload},
        "isError": is_error,
    }


# --- routes ----------------------------------------------------------------

@mcp_bp.route("/mcp/health", methods=["GET"])
def mcp_health():
    """Liveness only. Never reports whether a token is valid."""
    if not _enabled():
        return jsonify({"error": "not_found"}), 404
    from skills import mcp_context
    return jsonify({"status": "ok", "server": SERVER_NAME,
                    "version": SERVER_VERSION,
                    "tools": len(mcp_context.TOOLS)})


@mcp_bp.route("/mcp", methods=["GET"])
def mcp_get():
    if not _enabled():
        return jsonify({"error": "not_found"}), 404
    return jsonify({"error": "method_not_allowed",
                    "detail": "This endpoint answers POST only."}), 405


@mcp_bp.route("/mcp", methods=["POST"])
def mcp_post():
    if not _enabled():
        return jsonify({"error": "not_found"}), 404

    label = _caller_label()
    if not label:
        logger.warning("mcp auth rejected from %s", request.remote_addr)
        resp = jsonify({"error": "unauthorized"})
        resp.status_code = 401
        resp.headers["WWW-Authenticate"] = 'Bearer realm="mcp"'
        return resp

    try:
        body = request.get_json(force=True, silent=False)
    except Exception:  # noqa: BLE001
        return _error(None, PARSE_ERROR, "Request body is not valid JSON.")

    if isinstance(body, list):
        return _error(None, INVALID_REQUEST,
                      "Batched requests are not supported; send one at a time.")
    if not isinstance(body, dict):
        return _error(None, INVALID_REQUEST, "Request must be a JSON-RPC object.")

    method = body.get("method")
    req_id = body.get("id")
    params = body.get("params") or {}
    is_notification = "id" not in body

    if not method:
        return _error(req_id, INVALID_REQUEST, "`method` is required.")

    # Notifications get no response body, per JSON-RPC.
    if is_notification:
        logger.info("mcp notification token=%s method=%s", label, method)
        return Response("", status=202)

    try:
        if method == "initialize":
            result, session_id = _initialize(params)
            logger.info("mcp session opened token=%s session=%s", label, session_id)
            return _result(req_id, result, session_id=session_id)
        if method == "ping":
            return _result(req_id, {})
        if method == "tools/list":
            return _result(req_id, _tools_list())
        if method == "tools/call":
            return _result(req_id, _tools_call(params))
        if method in ("resources/list", "prompts/list"):
            # Declared unsupported in capabilities, but some clients probe anyway.
            key = "resources" if method.startswith("resources") else "prompts"
            return _result(req_id, {key: []})
        return _error(req_id, METHOD_NOT_FOUND, "Unsupported method: %s" % method)
    except Exception as exc:  # noqa: BLE001
        logger.error("mcp %s failed: %s", method, exc, exc_info=True)
        return _error(req_id, INTERNAL_ERROR, "The server could not complete that call.",
                      {"type": type(exc).__name__})
