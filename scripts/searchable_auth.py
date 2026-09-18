"""Authorize the portal against Searchable's MCP server, once, and print the
values to paste into Render.

WHY THIS EXISTS
    Searchable's programmatic surface is an MCP server, not a REST API. The
    REST base the portal shipped with — api.searchable.ai — does not resolve at
    all, and no REST path on app.searchable.com answers; the only endpoint that
    responds is:

        https://app.searchable.com/api/mcp-server/mcp

    That endpoint is OAuth-protected and its authorization server advertises
    `authorization_code` and `refresh_token` ONLY — no `client_credentials`. A
    server therefore cannot authorize itself. So a human authorizes once here,
    in a browser, and the portal lives on the refresh token afterwards. Same
    shape as the Google Ads installed-app flow.

WHAT IT DOES
    1. Registers a public client dynamically (the AS allows
       token_endpoint_auth_method "none", so there is no client secret to leak).
    2. Runs authorization_code + PKCE against a loopback redirect.
    3. Exchanges the code and prints SEARCHABLE_MCP_CLIENT_ID and
       SEARCHABLE_MCP_REFRESH_TOKEN.
    4. Calls tools/list with the fresh access token and prints what the server
       actually offers, so the mapping is written against real tools rather
       than guessed ones — which is exactly the mistake the REST client made.

    Read-only: it lists tools, it does not call any.

    python3 scripts/searchable_auth.py
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import secrets
import socket
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser

RESOURCE = "https://app.searchable.com/api/mcp-server/mcp"
DISCOVERY = "https://app.searchable.com/api/mcp-server/.well-known/oauth-protected-resource"
CLIENT_NAME = "RPM Living client portal"
SCOPES = "read"          # write is offered; the portal only reads.


def _get_json(url: str, data: bytes | None = None, headers: dict | None = None) -> dict:
    req = urllib.request.Request(url, data=data, headers=headers or {},
                                 method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def discover() -> dict:
    meta = _get_json(DISCOVERY)
    issuer = (meta.get("authorization_servers") or [None])[0]
    if not issuer:
        sys.exit("The resource did not name an authorization server.")
    for suffix in ("/.well-known/oauth-authorization-server",
                   "/.well-known/openid-configuration"):
        try:
            return _get_json(issuer.rstrip("/") + suffix)
        except Exception:  # noqa: BLE001 — try the next well-known path
            continue
    sys.exit("Could not read the authorization server's metadata.")


def register(server: dict, redirect_uri: str) -> str:
    endpoint = server.get("registration_endpoint")
    if not endpoint:
        sys.exit("This server does not support dynamic client registration; "
                 "ask Searchable for a client id.")
    body = json.dumps({
        "client_name": CLIENT_NAME,
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "scope": SCOPES,
    }).encode()
    out = _get_json(endpoint, body, {"Content-Type": "application/json"})
    client_id = out.get("client_id")
    if not client_id:
        sys.exit("Registration returned no client_id: %s" % json.dumps(out)[:300])
    return client_id


class _Catcher(http.server.BaseHTTPRequestHandler):
    code: str | None = None
    error: str | None = None

    def do_GET(self):  # noqa: N802
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _Catcher.code = (query.get("code") or [None])[0]
        _Catcher.error = (query.get("error_description") or query.get("error") or [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        done = ("Authorized. You can close this window and go back to the terminal."
                if _Catcher.code else "Authorization failed: %s" % (_Catcher.error or "unknown"))
        self.wfile.write(("<!doctype html><meta charset=utf-8>"
                          "<body style='font:16px/1.5 system-ui;padding:48px'>"
                          "<p>%s</p></body>" % done).encode())

    def log_message(self, *args):  # keep the terminal clean
        return


def main() -> int:
    server = discover()
    port = _free_port()
    redirect_uri = "http://127.0.0.1:%d/callback" % port
    print("Authorization server: %s" % server.get("issuer"))
    print("Grants offered:       %s" % ", ".join(server.get("grant_types_supported") or []))
    if "refresh_token" not in (server.get("grant_types_supported") or []):
        print("\nWARNING: this server does not offer refresh_token, so the portal "
              "would need a person to re-authorize whenever the access token "
              "expires. Stop and ask Searchable for machine credentials.")

    client_id = os.environ.get("SEARCHABLE_MCP_CLIENT_ID") or register(server, redirect_uri)
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(16)

    params = {
        "response_type": "code", "client_id": client_id,
        "redirect_uri": redirect_uri, "scope": SCOPES, "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256",
        # RFC 8707: name the MCP endpoint this token is for.
        "resource": RESOURCE,
    }
    url = server["authorization_endpoint"] + "?" + urllib.parse.urlencode(params)

    httpd = http.server.HTTPServer(("127.0.0.1", port), _Catcher)
    threading.Thread(target=httpd.handle_request, daemon=True).start()
    print("\nOpening your browser to approve access as the Searchable account "
          "that can see the RPMI projects.\nIf it does not open, paste this:\n\n%s\n" % url)
    webbrowser.open(url)

    for _ in range(600):                      # ~5 minutes
        if _Catcher.code or _Catcher.error:
            break
        import time
        time.sleep(0.5)
    if not _Catcher.code:
        return print("No authorization came back: %s" % (_Catcher.error or "timed out")) or 1

    token = _get_json(server["token_endpoint"], urllib.parse.urlencode({
        "grant_type": "authorization_code", "code": _Catcher.code,
        "redirect_uri": redirect_uri, "client_id": client_id,
        "code_verifier": verifier, "resource": RESOURCE,
    }).encode(), {"Content-Type": "application/x-www-form-urlencoded"})

    refresh = token.get("refresh_token")
    access = token.get("access_token")
    if not access:
        return print("The token exchange returned no access token: %s"
                     % json.dumps(token)[:300]) or 1

    print("\n" + "=" * 72)
    print("PASTE THESE INTO RENDER")
    print("=" * 72)
    print("SEARCHABLE_MCP_URL           %s" % RESOURCE)
    print("SEARCHABLE_MCP_CLIENT_ID     %s" % client_id)
    if refresh:
        print("SEARCHABLE_MCP_REFRESH_TOKEN %s" % refresh)
    else:
        print("\nNOTE: no refresh token came back. The portal would lose access when "
              "this access token expires (in %s seconds). Ask Searchable to enable "
              "offline access for this client." % token.get("expires_in", "?"))
    print("=" * 72)

    # What the server actually offers. The REST client in this repo was written
    # against guessed endpoints; this is how we avoid repeating that.
    print("\nAsking the MCP server what it can do…")
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode()
    try:
        req = urllib.request.Request(RESOURCE, data=body, method="POST", headers={
            "Authorization": "Bearer %s" % access,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        })
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
    except Exception as exc:  # noqa: BLE001
        print("  tools/list failed (%s). The credentials above are still good." % exc)
        return 0
    if raw.startswith("event:"):
        raw = raw.split("data: ", 1)[-1].strip()
    try:
        tools = (json.loads(raw).get("result") or {}).get("tools") or []
    except ValueError:
        print("  Unexpected reply: %s" % raw[:300])
        return 0
    print("  %d tools:\n" % len(tools))
    for tool in tools:
        print("  - %s" % tool.get("name"))
        desc = (tool.get("description") or "").strip().splitlines()
        if desc:
            print("      %s" % desc[0][:120])
        props = ((tool.get("inputSchema") or {}).get("properties") or {})
        if props:
            print("      args: %s" % ", ".join(sorted(props)))
    print("\nPaste that list back to Claude — the portal's mapping gets written "
          "against these, not against guesses.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
