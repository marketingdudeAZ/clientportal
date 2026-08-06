"""Clerk session-token verification (ADR 0002 — third-party portal auth).

Replaces HubSpot Memberships as the portal login layer. The portal page
stays PUBLIC on the CMS; ClerkJS gates the UI client-side and every API
call carries `Authorization: Bearer <session JWT>`. This module verifies
that JWT cryptographically (RS256 against Clerk's JWKS) and resolves the
user's email — a strictly stronger trust model than the old spoofable
X-Portal-Email header, which the before_request hook in server.py now
back-fills from the verified token so no endpoint needed changing.

Env:
    CLERK_SECRET_KEY       — sk_...  (Render) — backend API, email lookup
    CLERK_PUBLISHABLE_KEY  — pk_...  (Render) — encodes the frontend-API
                             domain (base64 tail), used to derive JWKS URL
    CLERK_JWKS_URL         — optional explicit override

No Clerk keys configured → verify_bearer() returns None and the portal
falls back to whatever X-Portal-Email the caller sent (legacy behavior).
"""

from __future__ import annotations

import base64
import logging
import os
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_TIMEOUT = 10

# user_id -> (email, fetched_at)
_email_cache: dict[str, tuple[str, float]] = {}
_EMAIL_TTL = 3600

_jwk_client = None
_jwk_client_url = None


def _frontend_api_domain() -> str:
    """Derive the Clerk frontend-API domain from the publishable key.

    pk_test_/pk_live_ keys are `pk_<env>_<base64("<domain>$")>`.
    """
    pk = os.environ.get("CLERK_PUBLISHABLE_KEY", "").strip()
    if not pk or "_" not in pk:
        return ""
    tail = pk.rsplit("_", 1)[-1]
    try:
        pad = tail + "=" * (-len(tail) % 4)
        domain = base64.b64decode(pad).decode("utf-8", "ignore").rstrip("$")
        return domain.strip()
    except Exception:
        return ""


def _jwks_url() -> str:
    explicit = os.environ.get("CLERK_JWKS_URL", "").strip()
    if explicit:
        return explicit
    domain = _frontend_api_domain()
    return f"https://{domain}/.well-known/jwks.json" if domain else ""


def _get_jwk_client():
    global _jwk_client, _jwk_client_url
    url = _jwks_url()
    if not url:
        return None
    if _jwk_client is None or _jwk_client_url != url:
        import jwt as pyjwt
        _jwk_client = pyjwt.PyJWKClient(url, cache_keys=True, lifespan=3600)
        _jwk_client_url = url
    return _jwk_client


def _email_for_user(user_id: str) -> str:
    """Resolve a Clerk user id to their primary email via the backend API."""
    now = time.time()
    hit = _email_cache.get(user_id)
    if hit and (now - hit[1]) < _EMAIL_TTL:
        return hit[0]
    sk = os.environ.get("CLERK_SECRET_KEY", "").strip()
    if not sk:
        return ""
    try:
        r = requests.get(
            f"https://api.clerk.com/v1/users/{user_id}",
            headers={"Authorization": f"Bearer {sk}"},
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        u = r.json()
        primary_id = u.get("primary_email_address_id")
        email = ""
        for e in u.get("email_addresses", []):
            if e.get("id") == primary_id or not email:
                email = (e.get("email_address") or "").lower().strip()
                if e.get("id") == primary_id:
                    break
        if email:
            _email_cache[user_id] = (email, now)
        return email
    except Exception as e:
        logger.warning("clerk_auth: email lookup failed for %s: %s", user_id, e)
        return ""


def verify_bearer(auth_header: str) -> Optional[dict]:
    """Verify `Authorization: Bearer <clerk session JWT>`.

    Returns {"user_id", "email"} on success, None on any failure (missing
    config, bad signature, expired). Never raises — auth failures must not
    500 the API.
    """
    if not auth_header or not auth_header.startswith("Bearer "):
        return None
    token = auth_header[7:].strip()
    if token.count(".") != 2:
        return None
    try:
        import jwt as pyjwt
        client = _get_jwk_client()
        if client is None:
            return None
        signing_key = client.get_signing_key_from_jwt(token)
        claims = pyjwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            options={"verify_aud": False},  # Clerk session tokens carry azp, not aud
            leeway=10,
        )
        user_id = claims.get("sub") or ""
        if not user_id:
            return None
        # Prefer an email claim if the instance's token template includes one.
        email = (claims.get("email") or "").lower().strip() or _email_for_user(user_id)
        if not email:
            logger.warning("clerk_auth: verified token for %s but no email resolvable", user_id)
            return None
        return {"user_id": user_id, "email": email}
    except Exception as e:
        logger.info("clerk_auth: token rejected: %s", e)
        return None
