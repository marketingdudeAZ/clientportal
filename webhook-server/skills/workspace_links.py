"""Workspace flags and signed preview links.

Two jobs, both about whether a request to /api/workspace/* may proceed:

1. The master flag. `WORKSPACE_ENABLED` is read from the environment at request
   time (not frozen at import, the way `config.py` symbols are) so a flip takes
   effect without a redeploy and tests can toggle it per case.

2. Signed preview links for internal demos. A link is an HMAC-SHA256 token over
   (email, expiry), minted by `scripts/workspace_link.py` and sent by the page on
   every API call as the `X-Workspace-Link` header. It is honored only when
   `WORKSPACE_SIGNED_LINKS_ENABLED` is true, only for emails `feature_access`
   resolves to the internal role, and never for longer than 7 days. A verified
   link sets the same two request-environ keys the Clerk hook sets, so every gate
   downstream (`current_portal_email`, `identity_is_verified`) treats it exactly
   like a verified sign-in.

Token format: ``v1.<payload>.<signature>``
    payload   = base64url(JSON {"email": "...", "exp": <unix seconds>})
    signature = base64url(HMAC-SHA256(secret, "v1." + payload))
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

HEADER = "X-Workspace-Link"
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
