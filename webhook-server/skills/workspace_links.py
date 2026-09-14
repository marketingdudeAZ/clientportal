"""Workspace flags and signed preview links.

Two jobs, both about whether a request to /api/workspace/* may proceed:

1. The master flag. `WORKSPACE_ENABLED` is read from the environment at request
   time (not frozen at import, the way `config.py` symbols are) so a flip takes
   effect without a redeploy and tests can toggle it per case.

2. Signed preview links for internal demos. A link is an HMAC-SHA256 token over
   (email, expiry), minted by `scripts/workspace_link.py` and sent by the page on
   every API call as the `X-Workspace-Link` header. It is honored only when
   `WORKSPACE_SIGNED_LINKS_ENABLED` is true, only for `@rpmliving.com` emails
   that `feature_access` also resolves to the internal role, and never for longer
   than 7 days.

   Links are READ-ONLY (Phase 2 amendment 2). A verified link sets the caller's
   email and `workspace.signed_link`; it sets `portal.identity_verified` only
   when `WORKSPACE_SIGNED_LINKS_CAN_DECIDE=true`. Decisions, requests,
   start-work and undo therefore still need a Clerk session by default.

Token format: ``v1.<payload>.<signature>``
    payload   = base64url(JSON {"email": "...", "exp": <unix seconds>})
    signature = base64url(HMAC-SHA256(secret, "v1." + payload))

The secret is `WORKSPACE_LINK_SECRET`. Rotating it invalidates every link.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

HEADER = "X-Workspace-Link"
SIGNED_LINK_ENVIRON = "workspace.signed_link"
MAX_DAYS = 7
_VERSION = "v1"
# A token whose expiry sits further out than MAX_DAYS was not minted by our
# script. A minute of slack covers clock skew between the minting machine and
# the server.
_SKEW_SECONDS = 60

_TRUE = ("1", "true", "yes")


def workspace_enabled() -> bool:
    """WORKSPACE_ENABLED, read now. Off → every workspace route 404s."""
    return os.environ.get("WORKSPACE_ENABLED", "").strip().lower() in _TRUE


def signed_links_enabled() -> bool:
    """WORKSPACE_SIGNED_LINKS_ENABLED, read now. Off → the header is ignored."""
    return os.environ.get("WORKSPACE_SIGNED_LINKS_ENABLED", "").strip().lower() in _TRUE


def signed_links_can_decide() -> bool:
    """WORKSPACE_SIGNED_LINKS_CAN_DECIDE, read now. Off (default) → links are read-only."""
    return os.environ.get("WORKSPACE_SIGNED_LINKS_CAN_DECIDE", "").strip().lower() in _TRUE


class LinkError(Exception):
    """A link that must not be honored. Always a 401 at the edge."""


def _secret(secret: str | None = None) -> bytes:
    value = secret if secret is not None else os.environ.get("WORKSPACE_LINK_SECRET", "")
    if not value:
        raise LinkError("Signed links are not configured")
    return value.encode("utf-8")


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _sign(payload: str, key: bytes) -> str:
    return _b64(hmac.new(key, f"{_VERSION}.{payload}".encode("ascii"), hashlib.sha256).digest())


def _eligible(email: str) -> bool:
    """An @rpmliving.com address that feature_access also calls internal."""
    from config import RPM_EMAIL_DOMAIN
    from feature_access import ROLE_INTERNAL, role_for
    return email.endswith("@" + RPM_EMAIL_DOMAIN) and role_for(email) == ROLE_INTERNAL


def mint(email: str, days: float = MAX_DAYS, *, now: float | None = None,
         secret: str | None = None) -> str:
    """A signed link token for an internal email. ValueError on bad input."""
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        raise ValueError("a valid email is required")
    if not (0 < float(days) <= MAX_DAYS):
        raise ValueError(f"days must be more than 0 and at most {MAX_DAYS}")
    if not _eligible(email):
        raise ValueError("signed links are only for RPM internal @rpmliving.com emails")
    try:
        key = _secret(secret)
    except LinkError as exc:
        raise ValueError(str(exc)) from exc
    exp = int((time.time() if now is None else now) + float(days) * 86400)
    payload = _b64(json.dumps({"email": email, "exp": exp},
                              sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return f"{_VERSION}.{payload}.{_sign(payload, key)}"


def verify(token: str, *, now: float | None = None, secret: str | None = None) -> str:
    """The email a token was minted for. LinkError if it must not be honored."""
    key = _secret(secret)
    parts = str(token or "").strip().split(".")
    if len(parts) != 3 or parts[0] != _VERSION or not parts[1] or not parts[2]:
        raise LinkError("Malformed link")
    # Signature first, in constant time, before anything in the payload is trusted.
    if not hmac.compare_digest(_sign(parts[1], key), parts[2]):
        raise LinkError("Invalid link signature")
    try:
        data = json.loads(_unb64(parts[1]))
        email = str(data["email"]).strip().lower()
        exp = int(data["exp"])
    except (ValueError, KeyError, TypeError):
        raise LinkError("Malformed link")
    t = time.time() if now is None else now
    if exp <= t:
        raise LinkError("Link expired")
    if exp - t > MAX_DAYS * 86400 + _SKEW_SECONDS:
        raise LinkError(f"Link lifetime exceeds {MAX_DAYS} days")
    if not _eligible(email):
        raise LinkError("Link is not for an internal @rpmliving.com user")
    return email


def apply_to_request():
    """The before_request step for /api/workspace/*. None, or a 401 response.

    * No header, or links disabled → nothing happens; the request carries on
      with whatever identity it already has.
    * Clerk already verified this request → the verified session wins.
    * Otherwise the token must verify, or the request is refused. A present but
      bad token is never silently downgraded to an asserted header.
    """
    from flask import jsonify, request

    token = request.headers.get(HEADER, "")
    if not token or not signed_links_enabled():
        return None
    if request.environ.get("portal.identity_verified"):
        return None
    try:
        email = verify(token)
    except LinkError as exc:
        return jsonify({"error": "Invalid preview link", "detail": str(exc)}), 401
    request.environ["HTTP_X_PORTAL_EMAIL"] = email
    request.environ[SIGNED_LINK_ENVIRON] = True
    if signed_links_can_decide():
        request.environ["portal.identity_verified"] = True
    return None
