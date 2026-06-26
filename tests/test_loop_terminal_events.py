"""Tests for the Loop 1 funnel event vocabulary (loop_terminal_events.py).

Locks that every funnel hop emits with the right stage + event_type so the
analytics funnel (recommendation_proposed → self_checkout_submitted →
deal_created → deal_closed_won → fluency_provisioned) is measurable end to end,
including the terminal money hop the HubSpot automations own. `loop_writer.record`
is monkeypatched — no BigQuery I/O.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

import loop_writer  # noqa: E402
import loop_terminal_events as lte  # noqa: E402


@pytest.fixture
def captured(monkeypatch):
    calls: list[dict] = []

    def fake_record(stage, event_type, **kw):
        calls.append({"stage": stage, "event_type": event_type, **kw})
        return "event-id"

    monkeypatch.setattr(loop_writer, "record", fake_record)
    return calls


def test_funnel_event_types_are_registered():
    # Newly added convert types must be in the registry so analytics recognizes them.
    for et in ("self_checkout_submitted", "deal_created", "deal_closed_won"):
        assert et in loop_writer.LOOP_EVENT_TYPES
    # The terminal money-hop reuses an existing attract type.
    assert "fluency_provisioned" in loop_writer.LOOP_EVENT_TYPES


def test_recommendation_proposed(captured):
    lte.record_recommendation_proposed(
        "u-1", "c-1", recommendation_id="r-1", channel="paid_search",
        current_budget=1500, recommended_budget=1700,
    )
    c = captured[0]
    assert c["stage"] == "optimize"
    assert c["event_type"] == "recommendation_proposed"
    assert c["property_uuid"] == "u-1"
    assert c["magnitude"] == 200.0  # the recommended delta
    assert c["payload"]["channel"] == "paid_search"


def test_self_checkout_submitted_is_client_action(captured):
    lte.record_self_checkout_submitted(
        "u-1", "c-1", recommendation_id="r-1", channel="paid_search",
        amount=1700, actor="pm@example.com",
    )
    c = captured[0]
    assert c["stage"] == "convert"
    assert c["event_type"] == "self_checkout_submitted"
    assert c["trigger"] == "client_action"
    assert c["magnitude"] == 1700.0
    assert c["payload"]["actor"] == "pm@example.com"


def test_deal_created(captured):
    lte.record_deal_created("u-1", "c-1", deal_id="d-9", channel="paid_search", amount=1700)
    c = captured[0]
    assert c["stage"] == "convert"
    assert c["event_type"] == "deal_created"
    assert c["source_id"] == "d-9"
    assert c["magnitude"] == 1700.0


def test_deal_closed_won_is_cron(captured):
    lte.record_deal_closed_won("u-1", "c-1", deal_id="d-9", amount=1700)
    c = captured[0]
    assert c["event_type"] == "deal_closed_won"
    assert c["trigger"] == "cron"  # reconciliation, not the portal


def test_fluency_provisioned_money_hop(captured):
    lte.record_fluency_provisioned(
        "u-1", "c-1", channel="paid_search", amount=1700,
        changed_fields=["paid_search_budget"],
    )
    c = captured[0]
    assert c["stage"] == "attract"
    assert c["event_type"] == "fluency_provisioned"
    assert c["magnitude"] == 1700.0
    assert c["payload"]["changed_fields"] == ["paid_search_budget"]


def test_fluency_provisioned_allows_missing_amount(captured):
    lte.record_fluency_provisioned("u-1", "c-1")
    c = captured[0]
    assert c["magnitude"] is None  # amount optional — sync may not know the $
