"""Round 4, bug 3: a media plan note never contradicts a pending recommendation.

Offline; every reader is mocked and `requests` is disabled.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent / "webhook-server"))
sys.path.insert(0, str(TESTS))

from skills import workspace_cache as wcache  # noqa: E402
from skills import workspace_inbox as wi  # noqa: E402
from skills import workspace_media_plan as wmp  # noqa: E402

TODAY = date(2026, 9, 14)
TS = "2026-09-14T00:00:00Z"


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    def _no_network(*a, **k):
        raise RuntimeError("network disabled in workspace tests")
    monkeypatch.setattr("requests.sessions.Session.request", _no_network)
    wcache.clear()


def _pending(title, **kw):
    return wi.finalize(wi._new_item("hubdb_rec", kw.pop("sid", "1"), title, needs_approval=True, **kw))


STEP_DOWN = _pending("Step down Apartments.com Premium to Standard",
                     _raw={"rec_id": "1", "rec_type": "budget_change",
                           "title": "Step down Apartments.com Premium to Standard", "body": ""})


class TestGuard:
    def test_keep_running_note_is_dropped_when_a_step_down_is_pending(self):
        notes = ["Keep Apartments.com running all year.", "Occupancy is roughly on target."]
        assert wmp.guard_notes(notes, [STEP_DOWN]) == ["Occupancy is roughly on target."]

    @pytest.mark.parametrize("note", [
        "Keep Apartments.com running all year.",
        "Continue the ILS listing at its current tier.",
        "Increase Zillow exposure in October.",
    ])
    def test_every_continue_phrasing_about_the_same_channel_contradicts(self, note):
        assert wmp.contradicts(note, [STEP_DOWN]) is True

    def test_other_channels_and_neutral_notes_are_kept(self):
        assert wmp.contradicts("Keep SEO running all year.", [STEP_DOWN]) is False
        assert wmp.contradicts("Apartments.com: the step-down is waiting on a decision.", [STEP_DOWN]) is False
        assert wmp.contradicts("Keep Apartments.com running all year.", []) is False

    def test_loop_recommendation_channels_count(self):
        shift = wi.finalize(wi._new_item("loop_rec", "h", "Shift $300 from paid_social to paid_search",
                                         needs_approval=True, channels=["paid_social", "paid_search"],
                                         _raw={"recommendation": {"from_channel": "paid_social",
                                                                  "to_channel": "paid_search"}}))
        assert {"paid_social", "paid_search"} <= wmp.item_channels(shift)
        assert wmp.contradicts("Keep Meta running all year.", [shift]) is True


class TestPlanNotes:
    def test_a_channel_with_a_pending_recommendation_holds_instead_of_keeps(self):
        pending = [_pending("Cut paid search to fund SEO", channels=["paid_search"])]
        notes = wmp.plan_notes(["paid_search", "seo"], {"paid_search": "Paid search", "seo": "SEO"}, pending,
                               "Occupancy is roughly on target.", always_on={"paid_search", "seo"})
        assert notes[0] == "Occupancy is roughly on target."
        assert any(n.startswith("Paid search: “Cut paid search to fund SEO” is waiting on a decision") for n in notes)
        assert "Keep Paid search running all year." not in notes
        assert "Keep SEO running all year." not in notes      # the same recommendation names SEO too

    def test_generated_notes_never_contradict_pending(self):
        pending = [STEP_DOWN, _pending("Pause Meta for October", sid="2", channels=["paid_social"])]
        keys = ["ils", "paid_social", "seo", "gbp"]
        labels = {"ils": "Apartments.com", "paid_social": "Meta", "seo": "SEO", "gbp": "Google Business Profile"}
        notes = wmp.plan_notes(keys, labels, pending, None, always_on=set(keys))
        assert not [n for n in notes if wmp.contradicts(n, pending)]
        assert "Keep Google Business Profile running all year." in notes


class TestBuildMediaPlan:
    def test_build_uses_pending_recommendations(self, monkeypatch):
        ctx = wi.PropertyContext("123", "u-1", "LYV Broadway", {"uuid": "u-1", "totalunits": "390",
                                                                "aptiq_property_id": ""})
        monkeypatch.setattr(wcache, "monthly_spend", lambda cid: ({
            "company_id": cid, "total": 3000.0, "deal_id": "d", "zero_skus": [],
            "by_sku": {"search": 2000.0, "seo": 1000.0}}, TS))
        pending = [_pending("Reduce paid search spend in October", channels=["paid_search"])]
        monkeypatch.setattr(wi, "collect", lambda c, **kw: (pending, []))
        body = wmp.build_media_plan(ctx, today=TODAY)
        assert any("Paid search: “Reduce paid search spend in October” is waiting" in n for n in body["notes"])
        assert not [n for n in body["notes"] if wmp.contradicts(n, pending)]
