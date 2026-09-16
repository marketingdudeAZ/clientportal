"""The dashboard reads loop_events once for the page, not three times per property.

Priming fills the caches the per-property readers already consult, so those
readers must issue no query of their own afterwards. Without this test the
batching can be removed and every screen still passes.
"""

import pathlib
import sys
import types

TESTS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent / "webhook-server"))
sys.path.insert(0, str(TESTS))

import loop_writer
from skills import workspace_fair_housing_review as fhr
from skills import workspace_profile as wpr

CID, UUID = "c-1", "u-1"
EVENTS = {CID: [
    {"event_type": "workspace_fair_housing_review", "occurred_at": "2026-09-01T00:00:00Z",
     "property_uuid": UUID, "payload": {"findings": [], "run_at": "2026-09-01"}},
    {"event_type": "workspace_profile_update_proposed", "occurred_at": "2026-09-02T00:00:00Z",
     "property_uuid": UUID, "payload": {"proposal_id": "p1", "field_key": "goals"}},
    {"event_type": "workspace_decision", "occurred_at": "2026-09-03T00:00:00Z",
     "property_uuid": UUID, "payload": {"item_id": "hubdb_rec:9", "action": "approve", "actor": "a@b.c"}},
]}


def _no_queries(monkeypatch):
    """Any per-property read of loop_events is a failure, not a slow path."""
    def boom(*a, **k):
        raise AssertionError("query_recent was called: the batched read was not used")
    monkeypatch.setattr(loop_writer, "query_recent", boom)
    monkeypatch.setattr(loop_writer, "_bq", lambda: object())


def test_a_primed_fair_housing_review_needs_no_query(monkeypatch):
    fhr._latest.clear()
    _no_queries(monkeypatch)
    fhr.prime(EVENTS)
    ctx = types.SimpleNamespace(company_id=CID, uuid=UUID)
    assert fhr.latest(ctx, [])["run_at"] == "2026-09-01"


def test_a_property_with_no_review_is_primed_too(monkeypatch):
    # The expensive case: nothing found. Cached as None rather than falling
    # through to a query per property.
    fhr._latest.clear()
    _no_queries(monkeypatch)
    fhr.prime({CID: []})
    assert fhr.latest(types.SimpleNamespace(company_id=CID, uuid=UUID), []) is None


def test_primed_profile_events_carry_proposals_and_history(monkeypatch):
    wpr._stored.clear()
    _no_queries(monkeypatch)
    wpr.prime_stored(EVENTS)
    out = wpr._stored_events(types.SimpleNamespace(company_id=CID, uuid=UUID), [])
    assert [p["proposal_id"] for p in out["proposals"]] == ["p1"]
    assert list(out["history"]) == ["hubdb_rec:9"]
