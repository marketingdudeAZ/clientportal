"""Launch-date policy for Loop 1 self-checkout — pure, no I/O.

Decides WHEN a self-checkout deal should go live, given the change type and
whether the PM wants it ASAP or on a scheduled date. The launch date is the
trigger the HubSpot 10pm automation keys off (launch_date == today AND stage ==
Ready to Launch → Closed Won → budget to Fluency).

Two rules that matter:

1. A NEW channel needs a build window. Campaigns have to be built before spend
   goes live, so an ASAP new-channel activation launches `BUILD_BUFFER_BUSINESS_DAYS`
   business days out, not today. An increase on an ALREADY-active channel has
   nothing to build, so ASAP means today.

2. The re-arm sweep (for deals signed after their launch date passed) must be
   change-type aware: re-arming a stranded new-channel deal has to restore the
   build window (today + buffer), or it would launch with no time to build.

Business-day math skips weekends. A holiday calendar is out of scope here — wire
one into `_add_business_days` if RPM adopts one.
"""

from __future__ import annotations

from datetime import date, timedelta

BUILD_BUFFER_BUSINESS_DAYS = 5

# Change types this slice reasons about (deck vocabulary).
ACTIVE_CHANNEL_INCREASE = "active_channel_increase"
NEW_CHANNEL_ACTIVATION = "new_channel_activation"

# Modes the PM picks at checkout.
MODE_ASAP = "asap"
MODE_SCHEDULED = "scheduled"


def _add_business_days(start: date, n: int) -> date:
    """Return `start` advanced by `n` business days (weekends skipped)."""
    d = start
    added = 0
    while added < n:
        d = d + timedelta(days=1)
        if d.weekday() < 5:  # Mon–Fri
            added += 1
    return d


def _needs_build_buffer(change_type: str) -> bool:
    return change_type == NEW_CHANNEL_ACTIVATION


def compute_launch_date(
    change_type: str,
    mode: str,
    requested_date: date | None = None,
    today: date | None = None,
) -> date:
    """The launch date to stamp on the deal at checkout.

    - ASAP + new channel  → today + BUILD_BUFFER (campaign build window)
    - ASAP + active increase → today (nothing to build)
    - scheduled → the requested date, but never sooner than the build buffer
      for a new channel (you can't launch a channel before it's built)
    """
    today = today or date.today()

    if mode == MODE_SCHEDULED:
        if requested_date is None:
            raise ValueError("scheduled launch requires a requested_date")
        if _needs_build_buffer(change_type):
            floor = _add_business_days(today, BUILD_BUFFER_BUSINESS_DAYS)
            return max(requested_date, floor)
        return requested_date

    if mode == MODE_ASAP:
        if _needs_build_buffer(change_type):
            return _add_business_days(today, BUILD_BUFFER_BUSINESS_DAYS)
        return today

    raise ValueError(f"unknown launch mode: {mode!r}")


def rearm_launch_date(change_type: str, today: date | None = None) -> date:
    """The launch date for a stranded (signed-late) Ready-to-Launch deal.

    The daily sweep calls this for deals whose launch_date already passed.
    Active increase → today (catch tonight's automation). New channel → today +
    build buffer, so a late-signed new channel still gets its build window from
    the moment it's actually ready.
    """
    today = today or date.today()
    if _needs_build_buffer(change_type):
        return _add_business_days(today, BUILD_BUFFER_BUSINESS_DAYS)
    return today
