"""STAGING-ONLY: Lifecycle state derivation per spec section 4.11.

Order of precedence:
    1. HubSpot override (fluency_lifecycle_state_override)
    2. Computed from Apt IQ signals:
        - exposure_90d > 80% AND occupancy < 20%      → pre_lease
        - age <= 2 years AND occupancy < 90%          → lease_up
        - renov_age <= 3 years AND occupancy >= 85%   → renovated
        - else                                         → stabilized

Note: 'rebrand' is in the locked enum but cannot be auto-derived from
Apt IQ data — it's an explicit override value only.
"""

from __future__ import annotations

import datetime as dt
import logging

logger = logging.getLogger(__name__)

LOCKED_VALUES = {"lease_up", "pre_lease", "stabilized", "rebrand", "renovated"}


def derive_lifecycle_state(
    *,
    override: str | None = None,
    year_built: int | None = None,
    year_renovated: int | None = None,
    occupancy_pct: float | None = None,
    exposure_90d_pct: float | None = None,
    today: dt.date | None = None,
) -> str:
    """Compute lifecycle state. Defaults to 'stabilized' when signals are sparse.

    `occupancy_pct` and `exposure_90d_pct` are expected as percentages
    (0–100). Caller may also pass them as fractions (0–1) and we'll detect
    + rescale.
    """
    if override:
        normalized = override.strip().lower().replace("-", "_")
        if normalized in LOCKED_VALUES:
            return normalized
        logger.warning("lifecycle override %r not in locked vocab; ignoring", override)

    today = today or dt.date.today()

    # Tolerate both fraction and percent inputs
    occ = occupancy_pct
    exp = exposure_90d_pct
    if occ is not None and 0 < occ <= 1:
        occ *= 100
    if exp is not None and 0 < exp <= 1:
        exp *= 100

    # Pre-lease: very high exposure (lots of upcoming move-outs/move-ins)
    # paired with very low occupancy. Locked thresholds per spec.
    if exp is not None and occ is not None and exp > 80 and occ < 20:
        return "pre_lease"

    # Lease-up: new building (≤2 yrs old) still ramping (<90% occupied).
    if year_built and occ is not None:
        age = today.year - year_built
        if age <= 2 and occ < 90:
            return "lease_up"

    # Recently renovated and now stabilized at high occupancy.
    if year_renovated and occ is not None:
        renov_age = today.year - year_renovated
        if renov_age <= 3 and occ >= 85:
            return "renovated"

    return "stabilized"
