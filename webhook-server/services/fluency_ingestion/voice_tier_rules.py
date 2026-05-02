"""STAGING-ONLY: Voice tier derivation per spec section 4.10.

Voice tier is computed from rent percentile in the property's metro,
but pricing itself never reaches Fluency — only the resulting tier label
ships in the bundle.

Rules (locked in spec section 6):
    < 30%ile  → "value"
    30–59%ile → "standard"
    60–84%ile → "lifestyle"
    >= 85%ile → "luxury"

If override is set, return override unconditionally.
If we have no rent or no peer data, default to "standard".
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def compute_rent_percentile(my_rent: float | None, peer_rents: list[float]) -> float | None:
    """Percentile of `my_rent` against `peer_rents`. Returns 0.0–100.0 or None.

    "Same metro" peer set = all Apt IQ properties with the same Market Name
    that have a non-null Avg Rent. Caller is responsible for the filtering;
    this function just does the math.
    """
    if my_rent is None or my_rent <= 0:
        return None
    if not peer_rents:
        return None
    cleaned = [r for r in peer_rents if r and r > 0]
    if not cleaned:
        return None
    below = sum(1 for r in cleaned if r < my_rent)
    return round(100.0 * below / len(cleaned), 2)


def derive_voice_tier(
    *,
    override: str | None = None,
    rent_percentile: float | None = None,
) -> str:
    """Returns 'luxury' | 'standard' | 'value' | 'lifestyle'.

    Order:
        1. override (if any of the locked enum values)
        2. rent percentile bucketing
        3. fall back to 'standard'
    """
    LOCKED = {"luxury", "standard", "value", "lifestyle"}

    if override:
        normalized = override.strip().lower()
        if normalized in LOCKED:
            return normalized
        logger.warning("voice_tier override %r not in locked vocab; ignoring", override)

    if rent_percentile is None:
        return "standard"
    if rent_percentile < 30:
        return "value"
    if rent_percentile < 60:
        return "standard"
    if rent_percentile < 85:
        return "lifestyle"
    return "luxury"
