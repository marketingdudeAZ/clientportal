"""Tests for launch-date policy (launch_policy.py) — pure, no I/O.

Locks the two rules: a new channel gets a 5-business-day build buffer (ASAP and
as a scheduled floor), an active-channel increase launches today; and the
re-arm sweep preserves the build window for stranded new-channel deals.
"""

from __future__ import annotations

import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

import launch_policy as lp  # noqa: E402

# Mon 2026-06-29 .. Fri 07-03, weekend 07-04/05, Mon 07-06.
MON = date(2026, 6, 29)


def test_asap_active_increase_launches_today():
    assert lp.compute_launch_date(lp.ACTIVE_CHANNEL_INCREASE, lp.MODE_ASAP, today=MON) == MON


def test_asap_new_channel_gets_5_business_day_buffer():
    # Mon + 5 business days = next Mon (skips Sat/Sun).
    assert lp.compute_launch_date(lp.NEW_CHANNEL_ACTIVATION, lp.MODE_ASAP, today=MON) == date(2026, 7, 6)


def test_business_day_math_skips_weekend():
    # Thu + 5 business days crosses one weekend → next Thu.
    thu = date(2026, 7, 2)
    assert lp._add_business_days(thu, 5) == date(2026, 7, 9)


def test_scheduled_active_increase_uses_requested_date():
    want = date(2026, 7, 15)
    assert lp.compute_launch_date(lp.ACTIVE_CHANNEL_INCREASE, lp.MODE_SCHEDULED, want, MON) == want


def test_scheduled_new_channel_respects_buffer_floor():
    # Requested too soon (next day) for a new channel → floored to today + buffer.
    too_soon = date(2026, 6, 30)
    out = lp.compute_launch_date(lp.NEW_CHANNEL_ACTIVATION, lp.MODE_SCHEDULED, too_soon, MON)
    assert out == date(2026, 7, 6)


def test_scheduled_new_channel_keeps_later_date():
    # Requested comfortably past the buffer → honored as-is.
    far = date(2026, 8, 1)
    assert lp.compute_launch_date(lp.NEW_CHANNEL_ACTIVATION, lp.MODE_SCHEDULED, far, MON) == far


def test_scheduled_requires_a_date():
    with pytest.raises(ValueError):
        lp.compute_launch_date(lp.ACTIVE_CHANNEL_INCREASE, lp.MODE_SCHEDULED, None, MON)


def test_unknown_mode_raises():
    with pytest.raises(ValueError):
        lp.compute_launch_date(lp.ACTIVE_CHANNEL_INCREASE, "whenever", today=MON)


# ── re-arm sweep ─────────────────────────────────────────────────────────────


def test_rearm_active_increase_to_today():
    assert lp.rearm_launch_date(lp.ACTIVE_CHANNEL_INCREASE, MON) == MON


def test_rearm_new_channel_preserves_build_window():
    # A stranded new-channel deal must NOT launch today — restore the buffer.
    assert lp.rearm_launch_date(lp.NEW_CHANNEL_ACTIVATION, MON) == date(2026, 7, 6)
