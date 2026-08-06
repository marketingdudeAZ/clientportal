"""Lease-up ramp scoring — a time-aware occupancy target for lease-up properties.

A stabilized property has a FLAT target (~95%). A lease-up should be scored
against WHERE IT SHOULD BE by now — a linear ramp from its takeover date to a
target occupancy over N months. Being at 50% in month 6 (expected ~48%) is on
track; the same 50% in month 10 (expected ~79%) is behind and flags.

Decisions locked with Kyle (2026-07-07):
  - Lease-up start = the property's takeover date (HubSpot `managementstart`).
  - Ramp is LINEAR to the target; target (95%) and ramp length (12mo) are both
    per-property overridable (`target_occupancy`, `lease_up_ramp_months`).
  - Ramp is measured on OCCUPANCY (no leased-% feed yet).
  - ATR is dropped from lease-up scoring — early lease-ups have naturally high
    ATR, so pace-vs-ramp is the signal, not availability.

Shared by portfolio.py and server.py so the two `_compute_leasing_score`
copies stay in sync.
"""

from __future__ import annotations

from datetime import date, datetime

DEFAULT_TARGET = 95.0
DEFAULT_RAMP_MONTHS = 12


def normalize_occupancy_status(raw) -> str:
    """Fold the messy HubSpot `occupancy_status` values into a canonical class.

    Real data has 'Lease Up' (no hyphen), 'Lease-Up', 'In-Transition',
    'Stable', 'Stabilized', blank, etc. Returns one of:
    'lease_up' | 'renovation' | 'stabilized' | '' (unknown → treat as stabilized).
    """
    s = (raw or "").strip().lower().replace("-", " ").replace("_", " ")
    s = " ".join(s.split())
    if s in ("lease up", "leaseup", "in transition", "transition", "leasing up"):
        return "lease_up"
    if s in ("renovation", "renovating", "reno"):
        return "renovation"
    if s in ("stable", "stabilized", "stabilised"):
        return "stabilized"
    return ""


def parse_date(v):
    """Parse a HubSpot date value (epoch-millis string or YYYY-MM-DD) → date."""
    if v is None or v == "":
        return None
    try:
        if isinstance(v, (int, float)):
            return datetime.utcfromtimestamp(int(v) / 1000).date()
        vs = str(v).strip()
        if vs.isdigit():
            return datetime.utcfromtimestamp(int(vs) / 1000).date()
        return datetime.strptime(vs[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def _months_elapsed(start: date, today: date | None = None) -> float:
    today = today or date.today()
    return (today - start).days / 30.44


def lease_up_ramp(occ_pct, takeover_raw, target=None, ramp_months=None, today=None) -> dict:
    """Score a lease-up against its linear occupancy ramp.

    occ_pct: current occupancy on a 0–100 scale.
    Returns {"applies": bool, ...}. When applies is False the caller falls back
    (no takeover date → can't place the property on the ramp).

    On success returns: expected, gap (actual − expected, in points), months_in,
    graduated (past ramp end AND at/above target), score (0–100), status,
    target, ramp_months.
    """
    start = parse_date(takeover_raw)
    if start is None or occ_pct is None:
        return {"applies": False, "reason": "no_takeover_date" if start is None else "no_occupancy"}

    target = float(target) if target else DEFAULT_TARGET
    ramp_months = float(ramp_months) if ramp_months else DEFAULT_RAMP_MONTHS
    if ramp_months <= 0:
        ramp_months = DEFAULT_RAMP_MONTHS

    m = max(0.0, _months_elapsed(start, today))
    expected = target * min(m / ramp_months, 1.0)
    graduated = (m >= ramp_months and occ_pct >= target)
    gap = occ_pct - expected

    # Bands (locked with Kyle): on pace ±5 = ON TRACK, 5–10 behind = WATCH,
    # >10 behind = NEEDS ATTENTION. Score is continuous and lines up with the
    # 75 / 50 cutoffs the rest of the health score uses.
    if gap >= -5:
        status = "ON TRACK"
        score = min(100, round(75 + (gap + 5) * 2.5))      # on-pace 0 → ~87, +5 → 100
    elif gap >= -10:
        status = "WATCH"
        score = round(50 + (gap + 10) * 5)                 # −10 → 50, −5 → 75
    else:
        status = "NEEDS ATTENTION"
        score = max(10, round(50 + (gap + 10) * 2.5))      # steeper below −10

    return {
        "applies": True,
        "graduated": graduated,
        "expected": round(expected, 1),
        "gap": round(gap, 1),
        "months_in": round(m, 1),
        "score": int(score),
        "status": status,
        "target": target,
        "ramp_months": int(ramp_months),
        "ahead": gap >= 5,
    }
