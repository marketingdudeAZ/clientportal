"""HMAC signature validation for webhook requests."""

import hashlib
import hmac

from config import WEBHOOK_SECRET


def validate_signature(payload_body: bytes, signature: str) -> bool:
    """Validate HMAC-SHA256 signature on incoming webhook payload."""
    if not WEBHOOK_SECRET or not signature:
        return False
    expected = hmac.new(
        WEBHOOK_SECRET.encode(), payload_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)
