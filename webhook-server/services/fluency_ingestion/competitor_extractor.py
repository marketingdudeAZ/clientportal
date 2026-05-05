"""STAGING-ONLY: Competitor extraction from Apt IQ Market ID grouping.

Per spec section 4.8, the canonical competitors source is:
  Override > Apt IQ comp set + Form 6

Apt IQ has a `Comp Score` and per-property `comp_set_id` accessible via the
`/comp_sets/{id}/market_survey` API endpoint, but our token returns 403 there.
The daily CSV does NOT include a comp_set column. As a proxy until Apt IQ API
access is restored:

  • Group by Market ID (Apt IQ's market segmentation, e.g. "AXIS Crossroads
    Market" id=11931226 has every property in that submarket).
  • For each property, return the N closest-by-Avg-Rent OTHER properties in
    the same Market ID.

This is a coarser definition than Apt IQ's own comp set but it's directionally
correct (same metro/submarket, similar price point) and has zero auth issues.
Phase 2.3 of Track 2 will refine when Form 6 (manual competitor list) ships.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_TOP_N = 5


def build_market_index(csv_rows: dict[str, dict]) -> dict[str, list[dict]]:
    """Group all CSV rows by `Market ID`. Returns {market_id: [rows]}."""
    idx: dict[str, list[dict]] = defaultdict(list)
    for row in csv_rows.values():
        mid = (row.get("Market ID") or "").strip()
        if mid:
            idx[mid].append(row)
    return idx


def _to_float(val: Any) -> float | None:
    if val is None or val == "":
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        cleaned = val.replace("$", "").replace(",", "").strip()
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def closest_competitors(
    *,
    self_property_id: str,
    market_id: str,
    market_index: dict[str, list[dict]],
    top_n: int = DEFAULT_TOP_N,
) -> list[str]:
    """Return up to `top_n` competitor property names from the same Market ID.

    Sort key: absolute distance in Avg Rent from the self property. Properties
    without an Avg Rent are deprioritized (sort last).
    """
    if not market_id:
        return []

    cohort = market_index.get(market_id, [])
    if not cohort:
        return []

    # Find self in cohort to get our rent baseline
    self_row = None
    for r in cohort:
        if (r.get("Property ID") or "").strip() == self_property_id:
            self_row = r
            break
    if self_row is None:
        # Self isn't in the cohort (shouldn't happen if data's consistent)
        return []

    self_rent = _to_float(self_row.get("Avg Rent"))

    candidates = [r for r in cohort if (r.get("Property ID") or "").strip() != self_property_id]

    def sort_key(r):
        rent = _to_float(r.get("Avg Rent"))
        if self_rent is None or rent is None:
            return (1, float("inf"))  # missing rent → deprioritize
        return (0, abs(rent - self_rent))

    candidates.sort(key=sort_key)
    out = []
    for r in candidates[:top_n]:
        name = (r.get("Property") or "").strip()
        if name and name not in out:
            out.append(name)
    return out
