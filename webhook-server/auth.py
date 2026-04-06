"""Portal authentication — HubSpot Membership integration.

With HubSpot Memberships handling login, this module provides a simple
HMAC-based request verification so the Flask API can trust requests
forwarded from the HubSpot CMS page.

The HubL template signs (email + timestamp) with a shared secret and
passes it to dashboard.js, which includes it in API requests.
"""

import hashlib
import hmac
import logging
import time

from config import WEBHOOK_SECRET

logger = logging.getLogger(__name__)

# Signature validity window (seconds)
SIGNATURE_MAX_AGE = 300  # 5 minutes


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
