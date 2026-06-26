"""Tests for the daily re-arm sweep (launch_rearm.py).

Confirms stranded Ready-to-Launch deals get their launch date re-armed change-type
aware (active → today, new channel → today + build buffer), and that a clean run
is a no-op. All HubSpot calls mocked.
"""

from __future__ import annotations

import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("HUBSPOT_API_KEY", "test-key")

import pytest  # noqa: E402

import hubspot_client  # noqa: E402
import launch_rearm  # noqa: E402

MON = date(2026, 6, 29)


@pytest.fixture
def patches(monkeypatch):
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(hubspot_client, "patch_deal",
                        lambda did, props: calls.append((did, props)) or {})
    return calls


def _stranded(deal_id, stamp, old="2026-06-20"):
    return {"id": deal_id, "properties": {
        "clickup_ticket_id": stamp, "launch_date__c": old}}


def test_active_increase_rearms_to_today(monkeypatch, patches):
    monkeypatch.setattr(hubspot_client, "search_deals",
                        lambda *a, **k: [_stranded("d1", "self_checkout:active_channel_increase:r1")])
    acted = launch_rearm.rearm_stranded_deals(today=MON)
    assert acted[0]["new_launch_date"] == "2026-06-29"
    assert patches[0][1]["launch_date__c"] == "2026-06-29"


def test_new_channel_rearms_with_buffer(monkeypatch, patches):
    monkeypatch.setattr(hubspot_client, "search_deals",
                        lambda *a, **k: [_stranded("d2", "self_checkout:new_channel_activation:r2")])
    acted = launch_rearm.rearm_stranded_deals(today=MON)
    assert acted[0]["new_launch_date"] == "2026-07-06"   # build window preserved
    assert patches[0][1]["launch_date__c"] == "2026-07-06"


def test_unknown_stamp_defaults_active(monkeypatch, patches):
    monkeypatch.setattr(hubspot_client, "search_deals",
                        lambda *a, **k: [_stranded("d3", None)])
    acted = launch_rearm.rearm_stranded_deals(today=MON)
    assert acted[0]["new_launch_date"] == "2026-06-29"


def test_no_stranded_is_noop(monkeypatch, patches):
    monkeypatch.setattr(hubspot_client, "search_deals", lambda *a, **k: [])
    assert launch_rearm.rearm_stranded_deals(today=MON) == []
    assert patches == []


def test_one_bad_deal_does_not_stop_the_rest(monkeypatch):
    monkeypatch.setattr(hubspot_client, "search_deals", lambda *a, **k: [
        _stranded("bad", "self_checkout:active_channel_increase:r1"),
        _stranded("good", "self_checkout:active_channel_increase:r2"),
    ])

    def flaky_patch(did, props):
        if did == "bad":
            raise RuntimeError("hubspot hiccup")
        return {}

    monkeypatch.setattr(hubspot_client, "patch_deal", flaky_patch)
    acted = launch_rearm.rearm_stranded_deals(today=MON)
    assert [a["deal_id"] for a in acted] == ["good"]     # bad skipped, good still done
