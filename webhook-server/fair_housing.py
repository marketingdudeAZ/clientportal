"""Fair Housing compliance guards for Paid Media targeting.

Housing ads fall under Meta's and Google's Special Ad Categories — they restrict
granular targeting to prevent discrimination against HUD-protected classes.
This module provides the two checks the Paid Media surface needs:

  * validate_radius(platform, miles)     — enforces per-platform minimums
  * validate_audience_terms(text)        — blocks protected-class language

Constants codify the platform minimums current as of 2026 (Meta's Housing
special-ad category enforces a 15-mile minimum radius, Google Ads' Housing
category applies equivalent restrictions). Update as platform policies change.

Reference:
  - HUD Fair Housing Act protected classes: race, color, national origin,
    religion, sex (incl. gender identity, sexual orientation), familial
    status, disability.
  - Meta Special Ad Categories (Housing): radius-based minimum + no targeting
    by protected class or age.
"""

from __future__ import annotations

import re
from typing import Iterable

# Platform radius minimums (miles) for Housing special ad category.
# Meta enforces a 15-mile minimum radius on Housing ads; Google Ads applies
# equivalent geo restrictions. Values are conservative defaults — verify
# against the current platform policy before changing.
MIN_RADIUS_MILES = {
    "meta":     15,
    "facebook": 15,
    "instagram": 15,
    "google":   15,
    "google_ads": 15,
}
DEFAULT_MIN_RADIUS_MILES = 15

# HUD Fair Housing Act protected classes. Keep phrasing broad — we block
# audience descriptors that *reference* a protected class, not just exact
# category names.
PROTECTED_CLASSES = [
    "race", "color", "national origin", "religion",
    "sex", "gender", "sexual orientation",
    "familial status", "family status", "marital status",
    "disability", "disabled", "age",
]

# Expanded terms that are common in audience briefs but imply a protected
# class. Matched case-insensitively as whole words.
_PROTECTED_TERM_PATTERNS = [
    r"\bchristian\b", r"\bmuslim\b", r"\bjewish\b", r"\bcatholic\b", r"\bhindu\b",
    r"\bmale\b", r"\bfemale\b", r"\bmen\b", r"\bwomen\b",
    r"\bmarried\b", r"\bsingle\b", r"\bdivorced\b", r"\bwidowed\b",
    r"\bchildless\b", r"\bparents?\b", r"\bfamilies? with children\b",
    r"\belderly\b", r"\byoung adults?\b", r"\bseniors?\b",
    r"\bblack\b", r"\bwhite\b", r"\bhispanic\b", r"\blatin[oax]\b", r"\basian\b",
    r"\bdisab(?:led|ility)\b", r"\bwheelchair\b",
    r"\blgbtq?\b", r"\bgay\b", r"\bstraight\b",
]


class FairHousingViolation(Exception):
    """Raised when a targeting input violates fair-housing rules."""


def min_radius_for(platform: str) -> int:
    """Return the minimum radius (miles) for a platform, case-insensitive.

    Unknown platforms fall back to DEFAULT_MIN_RADIUS_MILES rather than
    passing through — fail safe, not open.
    """
    key = (platform or "").strip().lower()
    return MIN_RADIUS_MILES.get(key, DEFAULT_MIN_RADIUS_MILES)


def validate_radius(platform: str, miles: float | int | None) -> tuple[bool, str]:
    """Return (ok, reason).

    A valid radius must be numeric and >= the platform's minimum. The reason
    string is UI-safe — surface it directly in the compliance banner when ok
    is False.
    """
    if miles is None:
        return False, "Radius is required for Housing ads."
    try:
        m = float(miles)
    except (TypeError, ValueError):
        return False, "Radius must be a number."
    minimum = min_radius_for(platform)
    if m < minimum:
        return (
            False,
            f"{platform.title() if platform else 'This platform'} requires a "
            f"minimum {minimum}-mile radius on Housing ads. "
            f"Requested: {m} mi.",
        )
    return True, ""


def validate_audience_terms(text: str | Iterable[str]) -> tuple[bool, list[str]]:
    """Return (ok, flagged_terms).

    Scans free-text audience descriptors for protected-class language. Returns
    the list of matched terms so the caller can show "we removed: X, Y, Z".
    """
    if not text:
        return True, []
    if isinstance(text, str):
        blob = text
    else:
        blob = " ".join(str(t) for t in text if t)
    blob_lc = blob.lower()

    hits: list[str] = []
    for token in PROTECTED_CLASSES:
        if re.search(rf"\b{re.escape(token)}\b", blob_lc):
            hits.append(token)
    for pattern in _PROTECTED_TERM_PATTERNS:
        match = re.search(pattern, blob_lc)
        if match:
            hits.append(match.group(0))

    # Dedupe preserving order
    seen: set[str] = set()
    ordered: list[str] = []
    for h in hits:
        if h not in seen:
            seen.add(h)
            ordered.append(h)
    return (len(ordered) == 0), ordered


def compliance_banner(platform: str = "meta") -> dict:
    """Return UI banner content for the Targeting & Coverage tab.

    Callable from the paid_media route so the frontend doesn't have to hardcode
    platform policy copy.
    """
    minimum = min_radius_for(platform)
    return {
        "title": "Housing Special Ad Category",
        "body": (
            f"This property runs under {platform.title() if platform else 'the platform'}'s "
            f"Housing Special Ad Category. Radius targeting must be at least {minimum} miles, "
            "and audiences cannot be segmented by HUD-protected classes (race, color, national "
            "origin, religion, sex, familial status, or disability)."
        ),
        "min_radius_miles": minimum,
    }
