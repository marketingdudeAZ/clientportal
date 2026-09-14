"""Round 4 approvals: categories, no pacing, the review-panel fields and the
build-from-existing-assets creative rule.

Offline; every reader is mocked and `requests` is disabled.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent / "webhook-server"))
sys.path.insert(0, str(TESTS))

import workspace_contract as contract  # noqa: E402
from skills import workspace_approvals as wapp  # noqa: E402
from skills import workspace_creative_rules as rules  # noqa: E402
from skills import workspace_inbox as wi  # noqa: E402

TODAY = date(2026, 9, 14)


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    def _no_network(*a, **k):
        raise RuntimeError("network disabled in workspace tests")
    monkeypatch.setattr("requests.sessions.Session.request", _no_network)


def _ctx(**props):
    base = {"uuid": "u-1", "name": "The Maddux at Shadowood"}
    base.update(props)
    return wi.PropertyContext("1", base["uuid"], base["name"], base)


POOL = {"asset_name": "maddux-pool-03", "category": "Photography", "subcategory": "Amenity",
        "file_type": "jpg", "status": "live"}
LOBBY = {"asset_name": "maddux-lobby", "category": "Photography", "subcategory": "Interior",
         "file_type": "jpg", "status": "live"}
BROCHURE = {"asset_name": "pool brochure", "file_type": "pdf", "status": "live"}


class TestCategories:
    def test_negotiate_is_gone_and_pacing_is_not_an_approval(self):
        assert wapp.CATEGORIES == ("cost", "vendor", "content", "creative", "compliance")
        assert not hasattr(wapp, "_pacing_interrupts")
        assert "negotiate" not in contract.APPROVAL_CATEGORY.values


class TestCreativeRule:
    @pytest.mark.parametrize("text", ["Refresh amenity photos", "Schedule a photo shoot of the pool",
                                      "Book a reshoot for the clubhouse", "Get new pool photos"])
    def test_detects_shoot_proposals(self, text):
        assert rules.proposes_shoot(text)

    def test_leaves_other_creative_alone(self):
        assert not rules.proposes_shoot("Cut a 15-second video from the tour footage")

    def test_existing_assets_mean_build_new_creative(self):
        assert rules.subject_of("Refresh amenity photos") == "amenity"
        rec = rules.creative_recommendation("pool", [POOL, LOBBY, BROCHURE], [], TODAY)
        assert rec["kind"] == "build_from_assets"
        assert rec["title"] == "Build new creative that highlights pool from existing assets"
        assert "1 usable asset" in rec["reason"]
        assert {s["owner"] for s in rec["approving_does"]} == {"RPM Digital", "you"}
        assert any("Fair Housing" in s["label"] for s in rec["approving_does"])

    def test_shoot_only_without_usable_assets(self):
        rec = rules.creative_recommendation("rooftop", [POOL, LOBBY, BROCHURE], [], TODAY)
        assert rec["kind"] == "photo_shoot" and rec["title"] == "Schedule a photo shoot for rooftop"

    def test_at_most_one_shoot_per_twelve_months(self):
        recent = [TODAY - timedelta(days=100)]
        held = rules.creative_recommendation("rooftop", [], recent, TODAY)
        assert held["kind"] == "none" and "2027-06-06" in held["reason"]
        old = [TODAY - timedelta(days=400)]
        assert rules.creative_recommendation("rooftop", [], old, TODAY)["kind"] == "photo_shoot"

    def test_unknown_history_never_proposes_a_shoot(self):
        assert rules.creative_recommendation("rooftop", [], None, TODAY)["kind"] == "none"

    def test_the_maddux_sample_becomes_a_build_new_creative_item(self, monkeypatch):
        from skills import workspace_creative
        item = wi._new_item("call_prep", "r1", "Refresh amenity photos", found="The amenity set is dated.",
                            needs_approval=True, channels=["video_creative"])
        monkeypatch.setitem(wi.ADAPTERS, "call_prep", lambda ctx, gaps, today: [item])
        monkeypatch.setattr(workspace_creative, "_asset_rows", lambda ctx, gaps: [POOL, LOBBY])
        items, gaps = wi.collect(_ctx(), sources=["call_prep"], today=TODAY, with_history=False)
        it = items[0]
        assert it["title"] == "Build new creative that highlights amenities from existing assets"
        assert it["_creative_kind"] == "build_from_assets" and it["actions"]["approve"] is True
        assert it["why"]["text"] == "The amenity set is dated."
        assert "photo shoot" not in " ".join(s["label"] for s in it["approving_does"]).lower()

    def test_a_held_shoot_needs_no_approval(self, monkeypatch):
        from skills import workspace_creative
        item = wi._new_item("call_prep", "r1", "Schedule a photo shoot for the rooftop", needs_approval=True)
        monkeypatch.setitem(wi.ADAPTERS, "call_prep", lambda ctx, gaps, today: [item])
        monkeypatch.setattr(workspace_creative, "_asset_rows", lambda ctx, gaps: [])
        items, gaps = wi.collect(_ctx(), sources=["call_prep"], today=TODAY, with_history=False)
        assert items[0]["actions"]["approve"] is False
        assert any("held" in g["message"] for g in gaps)

    def test_shoot_history_comes_from_approved_decisions(self):
        history = {"call_prep:a": [{"at": "2026-03-01T00:00:00Z", "action": "approve", "creative_kind": "photo_shoot"}],
                   "call_prep:b": [{"at": "2026-04-01T00:00:00Z", "action": "not_now", "creative_kind": "photo_shoot"}],
                   "call_prep:c": [{"at": "2026-05-01T00:00:00Z", "action": "approve", "creative_kind": "photo_shoot",
                                    "undone": True}]}
        assert wi.shoot_dates(history) == [date(2026, 3, 1)]
        assert wi.shoot_dates(None) is None

    def test_call_prep_generator_prompt_carries_the_rule(self):
        text = (TESTS.parent / "webhook-server" / "server.py").read_text()
        assert "Never recommend a photo shoot" in text


class TestReviewPanelFields:
    def test_why_for_whom_and_approving_does(self, monkeypatch):
        rec = wi._new_item("hubdb_rec", "991", "Step down Apartments.com Premium to Standard",
                           found="Leads from the Premium tier fell for three months.", needs_approval=True,
                           receipts=[{"label": "Red Light report finding", "source": "red_light", "as_of": None}],
                           steps=[dict(s) for s in wi._REC_STEPS["budget_change"]],
                           _raw={"rec_id": "991", "rec_type": "budget_change"})
        brief = wi._new_item("content_brief", "b1", "Content brief: parking", needs_approval=True,
                             _raw={"brief_id": "b1", "hub_keyword": "parking at lyv broadway"})
        ticket = wi._new_item("portal_ticket", "t1", "New pool photos", status="in_motion")
        monkeypatch.setitem(wi.ADAPTERS, "hubdb_rec", lambda ctx, gaps, today: [rec, brief, ticket])
        items, _ = wi.collect(_ctx(overarching_goals="Reach 95% occupancy by spring"),
                              sources=["hubdb_rec"], today=TODAY, with_history=False)
        r, b, t = items
        assert r["why"] == {"text": "Leads from the Premium tier fell for three months.",
                            "receipts": [{"label": "Red Light report finding", "source": "red_light", "as_of": None}]}
        assert r["for_whom"] == {"text": "The property's leasing goal: Reach 95% occupancy by spring", "questions": []}
        owners = [(s["owner"], s["when"]) for s in r["approving_does"]]
        assert owners[0] == ("RPM Digital", "When you approve")
        assert owners[-1] == ("you", "Before anything is billed or spent")
        assert b["for_whom"] == {"text": "Renters searching for “parking at lyv broadway”",
                                 "questions": ["parking at lyv broadway"]}
        assert t["why"] is None and t["for_whom"] is None and t["approving_does"] == []
        for it in items:
            contract.assert_shape(wi.view_item(it), "item")
