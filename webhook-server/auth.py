"""Portal authentication — HubSpot Membership integration.

With HubSpot Memberships handling login, this module provides:
  1. HMAC-based request verification for future signed-request rollout
     (generate_request_signature / verify_request_signature).
  2. require_internal_key: Flask decorator for server-to-server endpoints
     (cron jobs, internal sync). Uses timing-safe comparison.
"""

import hashlib
import hmac
import logging
import os
import time
from functools import wraps

from flask import jsonify, request

from config import WEBHOOK_SECRET

logger = logging.getLogger(__name__)

# Signature validity window (seconds)
SIGNATURE_MAX_AGE = 300  # 5 minutes


def require_internal_key(fn):
    """Guard an endpoint with the INTERNAL_API_KEY shared secret.

    Caller must send X-Internal-Key header matching the env var. Uses
    timing-safe comparison to avoid character-by-character leaks. Passes
    OPTIONS through untouched so CORS preflight still works.
    """

    @wraps(fn)
    def wrapper(*args, **kwargs):
        if request.method == "OPTIONS":
            return fn(*args, **kwargs)
        expected = os.getenv("INTERNAL_API_KEY", "")
        provided = request.headers.get("X-Internal-Key", "")
        if not expected or not provided or not hmac.compare_digest(expected, provided):
            return jsonify({"error": "Authentication required"}), 401
        return fn(*args, **kwargs)

    return wrapper


def generate_request_signature(email, timestamp, secret=None):
    """Generate HMAC-SHA256 signature for API request verification.

    Used by the HubL template (via serverless function proxy or
    embedded in the page) to sign the logged-in user's identity.
    """
    secret = secret or WEBHOOK_SECRET
    message = f"{email.lower().strip()}:{timestamp}"
    return hmac.new(
        secret.encode(),
        message.encode(),
        hashlib.sha256,
    ).hexdigest()


def verify_request_signature(email, timestamp, signature, secret=None):
    """Verify that a request signature is valid and not expired.

    Returns True if valid, False otherwise.
    """
    secret = secret or WEBHOOK_SECRET

    # Check timestamp freshness
    try:
        ts = int(timestamp)
    except (ValueError, TypeError):
        logger.warning("Invalid timestamp in request signature")
        return False

    now = int(time.time())
    if abs(now - ts) > SIGNATURE_MAX_AGE:
        logger.warning("Expired request signature (age: %ds)", abs(now - ts))
        return False

    # Compute expected signature
    expected = generate_request_signature(email, timestamp, secret)

    # Timing-safe comparison
    if not hmac.compare_digest(expected, signature):
        logger.warning("Invalid request signature for %s", email)
        return False

    return True
