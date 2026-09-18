"""Layer 1 — Searchable, over MCP.

WHY THIS REPLACES THE REST CLIENT
    `searchable_client.py` was written against `https://api.searchable.ai/v1`.
    That host does not resolve — no DNS, no server, nothing — so every read in
    it would have failed the moment a key was set. Its normalizers are good
    (they were built from captured payloads); its transport was aimed at a
    server that does not exist.

    Searchable's real programmatic surface is an MCP endpoint:

        https://app.searchable.com/api/mcp-server/mcp

    It speaks the same JSON-RPC-over-POST that this portal's own /mcp speaks,
    which is why this file is small: we already know the protocol.

AUTH, IN TWO SHAPES
    A static token (SEARCHABLE_API_TOKEN) if the vendor's token is accepted as
    a bearer, because a server holding one secret beats a server refreshing an
    OAuth grant. Otherwise the OAuth refresh token from
    scripts/searchable_auth.py, because the authorization server offers
    authorization_code and refresh_token only — no client_credentials, so the
    portal cannot authorize itself from cold.

    `probe()` exists to answer which of those actually works, from the machine
    that holds the credentials, without printing any of them.

FAILURE POLICY
    Nothing here raises. Every call returns (value, reason) and an unmeasured
    property must read as "not measured yet", never as a zero.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_URL = "https://app.searchable.com/api/mcp-server/mcp"
TOKEN_URL = "https://app.searchable.com/api/mcp-auth/oauth/token"

_TIMEOUT = float(os.environ.get("SEARCHABLE_TIMEOUT", "30"))
_CACHE_TTL = float(os.environ.get("SEARCHABLE_CACHE_TTL", "900"))

# Every tool this portal is allowed to call. Searchable also exposes
# manage_associated_source, generate_report, trigger_audit and refresh_sitemap,
# which change state in the vendor's account — and the whole arrangement here is
# that the vendor MEASURES and the portal DECIDES. A read-only allowlist means a
# rule, an agent or a bad argument cannot reach a write, and adding one is a
# deliberate edit rather than an accident.
READ_TOOLS = frozenset({
    "get_current_date", "whoami", "search_docs", "read_doc", "list_projects",
    "list_prompts", "get_brand_profile", "get_domain_authority",
    "get_visibility", "get_visibility_history", "get_share_of_voice",
    "investigate", "get_topic_analysis", "get_competitors", "search_sources",
    "get_source_detail", "get_source_trends", "get_sentiment",
    "get_sentiment_history", "get_sentiment_competitors", "get_ga4_traffic",
    "get_gsc_performance", "get_site_health", "list_articles", "get_article",
    "get_opportunities", "get_query_fanout", "get_ai_traffic",
    "get_page_metrics", "get_shopping_visibility", "get_prompt_answers",
    "get_ads", "get_associated_sources",
})

# Named so a refusal can say what it refused rather than "unknown tool".
WRITE_TOOLS = frozenset({
    "manage_associated_source", "generate_report", "trigger_audit",
    "refresh_sitemap",
})


class WriteRefused(RuntimeError):
    """A write tool was asked for. The portal does not call these."""


_lock = threading.Lock()
_access: Dict[str, Any] = {"token": None, "expires_at": 0.0}
_cache: Dict[str, Tuple[float, Any]] = {}


def endpoint() -> str:
    return (os.environ.get("SEARCHABLE_MCP_URL") or DEFAULT_URL).strip()


def credential_names() -> Dict[str, bool]:
    """Which variables are set. Names and presence only — never a value."""
    return {name: bool((os.environ.get(name) or "").strip()) for name in (
        "SEARCHABLE_API_TOKEN", "SEARCHABLE_MCP_REFRESH_TOKEN",
        "SEARCHABLE_MCP_CLIENT_ID", "SEARCHABLE_MCP_URL")}


def is_configured() -> bool:
    names = credential_names()
    return names["SEARCHABLE_API_TOKEN"] or (
        names["SEARCHABLE_MCP_REFRESH_TOKEN"] and names["SEARCHABLE_MCP_CLIENT_ID"])


def clear_cache() -> None:
    with _lock:
        _cache.clear()
        _access.update({"token": None, "expires_at": 0.0})


# --- auth ------------------------------------------------------------------

def _refresh_access_token() -> Tuple[Optional[str], Optional[str]]:
    """Trade the stored refresh token for an access token."""
    refresh = (os.environ.get("SEARCHABLE_MCP_REFRESH_TOKEN") or "").strip()
    client_id = (os.environ.get("SEARCHABLE_MCP_CLIENT_ID") or "").strip()
    if not (refresh and client_id):
        return None, "No Searchable credentials are set on this server."
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token", "refresh_token": refresh,
        "client_id": client_id, "resource": endpoint()}).encode()
    req = urllib.request.Request(
        os.environ.get("SEARCHABLE_OAUTH_TOKEN_URL") or TOKEN_URL,
        data=body, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        logger.error("searchable: refresh refused (%s)", exc.code)
        return None, ("Searchable refused the stored authorization. Re-run "
                      "scripts/searchable_auth.py.")
    except Exception as exc:  # noqa: BLE001
        logger.warning("searchable: token endpoint unreachable (%s)", type(exc).__name__)
        return None, "The Searchable sign-in service is temporarily unreachable."
    token = payload.get("access_token")
    if not token:
        return None, "Searchable returned no access token."
    with _lock:
        # 60s of slack so a token never expires mid-request.
        _access["token"] = token
        _access["expires_at"] = time.time() + max(float(payload.get("expires_in") or 300) - 60, 30)
    return token, None


def _bearer() -> Tuple[Optional[str], Optional[str]]:
    static = (os.environ.get("SEARCHABLE_API_TOKEN") or "").strip()
    if static:
        return static, None
    with _lock:
        if _access["token"] and time.time() < _access["expires_at"]:
            return _access["token"], None
    return _refresh_access_token()


# --- transport -------------------------------------------------------------

def call(method: str, params: Optional[Dict[str, Any]] = None,
         *, token: Optional[str] = None) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """One JSON-RPC call. Returns (result, None) or (None, reason). Never raises."""
    if token is None:
        token, reason = _bearer()
        if not token:
            return None, reason
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                       "params": params or {}}).encode()
    req = urllib.request.Request(endpoint(), data=body, method="POST", headers={
        "Authorization": "Bearer %s" % token,
        "Content-Type": "application/json",
        # Their server may answer either way; we accept both and unwrap below.
        "Accept": "application/json, text/event-stream",
    })
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return None, "Searchable refused our credentials."
        if exc.code == 429:
            return None, "Searchable is rate-limiting us right now."
        logger.warning("searchable: %s returned %s", method, exc.code)
        return None, "Searchable returned an error (%s)." % exc.code
    except Exception as exc:  # noqa: BLE001
        logger.warning("searchable: unreachable (%s)", type(exc).__name__)
        return None, "Searchable is temporarily unreachable."

    if raw.lstrip().startswith("event:"):          # SSE framing
        parts = [ln[6:] for ln in raw.splitlines() if ln.startswith("data: ")]
        raw = parts[-1] if parts else ""
    try:
        payload = json.loads(raw or "{}")
    except ValueError:
        return None, "Searchable returned something that is not JSON."
    if payload.get("error"):
        message = (payload["error"] or {}).get("message") or "unknown error"
        return None, "Searchable refused the request: %s" % message
    return payload.get("result") or {}, None


def list_tools(*, token: Optional[str] = None) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    result, reason = call("tools/list", token=token)
    if result is None:
        return None, reason
    return result.get("tools") or [], None


def call_tool(name: str, arguments: Optional[Dict[str, Any]] = None
              ) -> Tuple[Optional[Any], Optional[str]]:
    """Call one READ tool and hand back its content, cached briefly.

    Raises WriteRefused for a tool that changes the vendor's account. Loud on
    purpose: a caller reaching for one has misunderstood the arrangement, and a
    quiet `(None, reason)` would let it keep trying.
    """
    if name in WRITE_TOOLS:
        raise WriteRefused(
            "%s changes Searchable's own account. The portal reads Searchable "
            "and decides here; it does not write there." % name)
    if name not in READ_TOOLS:
        return None, ("%s is not a tool this portal calls. Add it to READ_TOOLS "
                      "if it should be." % name)
    key = "%s:%s" % (name, json.dumps(arguments or {}, sort_keys=True))
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < _CACHE_TTL:
            return hit[1], None
    result, reason = call("tools/call", {"name": name, "arguments": arguments or {}})
    if result is None:
        return None, reason
    if result.get("isError"):
        return None, "Searchable could not answer that request."
    value = _content(result)
    with _lock:
        _cache[key] = (time.time(), value)
    return value, None


def _content(result: Dict[str, Any]) -> Any:
    """The useful part of an MCP tool result.

    Prefer structured content; fall back to the text blocks, parsed as JSON when
    they are JSON, because most servers return a JSON string in a text block.
    """
    if isinstance(result.get("structuredContent"), (dict, list)):
        return result["structuredContent"]
    texts = [b.get("text") for b in (result.get("content") or [])
             if isinstance(b, dict) and b.get("type") == "text" and b.get("text")]
    if not texts:
        return None
    joined = "\n".join(texts)
    try:
        return json.loads(joined)
    except ValueError:
        return joined


# --- diagnosis -------------------------------------------------------------

def probe() -> Dict[str, Any]:
    """Which credential actually works, answered where the credentials live.

    Tries the static token first and the OAuth grant second, and reports each
    outcome separately: "a token is set" and "the token is accepted" are
    different facts, and only the second one means anything.
    """
    out: Dict[str, Any] = {
        "endpoint": endpoint(),
        "credentials_present": credential_names(),
        "static_token": None,
        "oauth": None,
        "tools": None,
        "works": False,
    }
    static = (os.environ.get("SEARCHABLE_API_TOKEN") or "").strip()
    if static:
        tools, reason = list_tools(token=static)
        out["static_token"] = {"accepted": tools is not None, "reason": reason}
        if tools is not None:
            out["tools"] = [{"name": t.get("name"),
                             "description": (t.get("description") or "").strip()[:200],
                             "args": sorted(((t.get("inputSchema") or {})
                                             .get("properties") or {}).keys())}
                            for t in tools]
            out["works"] = True
            return out
    if (os.environ.get("SEARCHABLE_MCP_REFRESH_TOKEN") or "").strip():
        token, reason = _refresh_access_token()
        if not token:
            out["oauth"] = {"accepted": False, "reason": reason}
            return out
        tools, reason = list_tools(token=token)
        out["oauth"] = {"accepted": tools is not None, "reason": reason}
        if tools is not None:
            out["tools"] = [{"name": t.get("name"),
                             "description": (t.get("description") or "").strip()[:200],
                             "args": sorted(((t.get("inputSchema") or {})
                                             .get("properties") or {}).keys())}
                            for t in tools]
            out["works"] = True
    return out
