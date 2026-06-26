"""Tests for the Loop 1 recommendation generator (recommendation_gen.py).

Locks the decision core (recovery heuristic + every guardrail) and the
duplicate-budget DISPLAY suppression. Pure-function tests for the core; the
loop-event emit and the open-deal suppression are exercised with fakes.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

import recommendation_gen as rg  # noqa: E402
import loop_terminal_events  # noqa: E402


def _sig(**kw):
    base = dict(channel="paid_search", current_budget=1500.0,
               impression_share_lost_pct=0.28, marketing_status="RED", active=True)
    base.update(kw)
    return rg.ChannelSignal(**base)


# ── decision core: the recovery heuristic ────────────────────────────────────


def test_recovers_lost_share_within_cap():
    # 28% lost on $1500 → recover target 1500/0.72 = 2083, under the +50% cap (2250).
    rec = rg.recommend_for_channel("u-1", "c-1", _sig(), "2026-Q3")
    assert rec is not None
    assert rec.recommended_budget == 2083
    assert rec.delta == pytest.approx(583.0)
    assert rec.change_type == "active_channel_increase"
    assert "impressions" in rec.rationale


def test_increase_is_capped_at_max_pct():
    # 60% lost would target 1500/0.40=3750, but +50% cap holds it to 2250.
    rec = rg.recommend_for_channel("u-1", "c-1", _sig(impression_share_lost_pct=0.60), "2026-Q3")
    assert rec.recommended_budget == 2250  # current * 1.5


def test_capped_at_absolute_max_budget():
    g = rg.Guardrails(max_budget=2000.0)
    rec = rg.recommend_for_channel("u-1", "c-1", _sig(impression_share_lost_pct=0.60), "2026-Q3", g)
    assert rec.recommended_budget == 2000


# ── guardrails: when NOT to recommend ────────────────────────────────────────


def test_green_status_yields_no_card():
    assert rg.recommend_for_channel("u", "c", _sig(marketing_status="GREEN"), "p") is None


def test_missing_status_yields_no_card():
    assert rg.recommend_for_channel("u", "c", _sig(marketing_status=None), "p") is None


def test_small_gap_below_threshold_suppressed():
    assert rg.recommend_for_channel("u", "c", _sig(impression_share_lost_pct=0.05), "p") is None


def test_inactive_channel_suppressed():
    # This slice is increases on ALREADY-active channels only.
    assert rg.recommend_for_channel("u", "c", _sig(active=False), "p") is None


def test_zero_budget_suppressed():
    assert rg.recommend_for_channel("u", "c", _sig(current_budget=0.0), "p") is None


def test_already_at_ceiling_is_frozen():
    g = rg.Guardrails(max_budget=1500.0)
    assert rg.recommend_for_channel("u", "c", _sig(current_budget=1500.0), "p", g) is None


def test_recommendation_id_buckets_by_period():
    a = rg.recommend_for_channel("u-1", "c", _sig(), "2026-Q3")
    b = rg.recommend_for_channel("u-1", "c", _sig(), "2026-Q4")
    assert a.recommendation_id != b.recommendation_id  # re-purchase next period allowed


# ── property-level: suppression + emit ───────────────────────────────────────


def test_open_deal_channel_is_suppressed(monkeypatch):
    emitted = []
    monkeypatch.setattr(loop_terminal_events, "record_recommendation_proposed",
                        lambda *a, **k: emitted.append(k))
    signals = [_sig(channel="paid_search"), _sig(channel="paid_social")]
    recs = rg.recommend_for_property(
        "u-1", "c-1", signals, "2026-Q3",
        open_deal_channels=lambda: {"paid_search"},  # already in flight
    )
    channels = {r.channel for r in recs}
    assert channels == {"paid_social"}        # paid_search suppressed
    assert len(emitted) == 1                  # only the surfaced card emits


def test_emit_can_be_disabled(monkeypatch):
    emitted = []
    monkeypatch.setattr(loop_terminal_events, "record_recommendation_proposed",
                        lambda *a, **k: emitted.append(k))
    rg.recommend_for_property("u-1", "c-1", [_sig()], "2026-Q3", emit=False)
    assert emitted == []


# ── ChannelSignal adapter (reuses spend_sheet for current budget) ────────────


def test_build_channel_signals_pulls_current_budget(monkeypatch):
    import spend_sheet
    monkeypatch.setattr(spend_sheet, "get_company_monthly_spend",
                        lambda cid: {"by_sku": {"paid_search": 1500.0}})
    sigs = rg.build_channel_signals(
        "c-1", {"paid_search": 0.28, "paid_social": 0.2}, "RED")
    by_ch = {s.channel: s for s in sigs}
    assert by_ch["paid_search"].current_budget == 1500.0
    assert by_ch["paid_search"].active is True        # has spend → active
    assert by_ch["paid_search"].impression_share_lost_pct == 0.28
    assert by_ch["paid_social"].current_budget == 0.0
    assert by_ch["paid_social"].active is False       # no spend → inactive
