"""T7 — approve/reject events join back to the proposal, and the digest counts
what a client can actually act on.

The funnel used to jump from recommendation_proposed straight to
self_checkout_submitted, which only covers the self-checkout path; a Red Light
rec approved in the portal emitted nothing at all.
"""

from __future__ import annotations

import json
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

import loop_terminal_events as lte  # noqa: E402
import recommendation_gen as rg  # noqa: E402


@pytest.fixture
def captured(monkeypatch):
    events: list[dict] = []

    def _record(**kwargs):
        events.append(kwargs)
        return "evt-%d" % len(events)

    monkeypatch.setattr(lte.loop_writer, "record", _record)
    return events


# ── T7: the join key ────────────────────────────────────────────────────────

def test_t7_approved_event_shares_source_id_with_the_proposal(captured):
    sig = rg.ChannelSignal(channel="paid_search", current_budget=1500.0,
                           impression_share_lost_pct=0.28,
                           marketing_status="RED", active=True)
    (rec,) = rg.recommend_for_property("u-1", "123", [sig], "2026-Q3")

    lte.record_recommendation_approved(
        "u-1", "123", recommendation_id=rec.recommendation_id,
        rec_type="budget_change", actor="pm@example.com")

    proposed, approved = captured
    assert proposed["event_type"] == "recommendation_proposed"
    assert approved["event_type"] == "recommendation_approved"
    assert proposed["source_id"] == approved["source_id"] == rec.recommendation_id
    assert approved["stage"] == "optimize"
    assert approved["trigger"] == "client_action"
    assert approved["payload"]["actor"] == "pm@example.com"


def test_t7_rejected_event_shares_source_id_too(captured):
    lte.record_recommendation_rejected(
        "u-1", "123", recommendation_id="rec1:loop1:u-1:paid_search:2026-q3",
        actor="pm@example.com", reason="not now")
    (evt,) = captured
    assert evt["event_type"] == "recommendation_rejected"
    assert evt["source_id"] == "rec1:loop1:u-1:paid_search:2026-q3"
    assert evt["stage"] == "optimize"
    assert evt["trigger"] == "client_action"
    assert evt["payload"]["reason"] == "not now"


def test_t7_event_types_are_registered():
    """Both types were already declared in LOOP_EVENT_TYPES and never emitted —
    the funnel gap was known. Assert they stay registered so the writer does
    not downgrade them to logged-but-unknown."""
    import loop_writer
    assert "recommendation_approved" in loop_writer.LOOP_EVENT_TYPES
    assert "recommendation_rejected" in loop_writer.LOOP_EVENT_TYPES


def test_t7_writer_swallows_backend_failure(monkeypatch):
    """loop_writer.record is the layer that guarantees non-propagation."""
    import loop_writer

    def _boom(*a, **kw):
        raise RuntimeError("bigquery down")

    monkeypatch.setattr(loop_writer, "_insert_event", _boom, raising=False)
    monkeypatch.setattr(loop_writer, "_bq_client",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("no creds")),
                        raising=False)
    # Must not raise even though the backend is dead.
    lte.record_recommendation_approved("u-1", "123", recommendation_id="r")
    lte.record_recommendation_rejected("u-1", "123", recommendation_id="r")


def test_t7_server_emit_helper_swallows_failures(monkeypatch):
    """The server-side wrapper is the layer that guarantees non-propagation."""
    calls = []

    fake = types.ModuleType("loop_terminal_events")

    def _boom(*a, **kw):
        calls.append(kw)
        raise RuntimeError("event bus down")

    fake.record_recommendation_approved = _boom
    fake.record_recommendation_rejected = _boom
    monkeypatch.setitem(sys.modules, "loop_terminal_events", fake)

    emit = _load_server_emit_helper()
    payload = {"recommendations": [{"rec_id": "r1", "rec_type": "budget_change"}],
               "property_uuid": "u-1"}
    emit(payload, "123", "r1", "approved", "pm@example.com")  # must not raise
    assert calls, "the helper should have attempted the emit"


def _load_server_emit_helper():
    """Extract _emit_rec_terminal_event from server.py without importing Flask.

    server.py pulls in the whole app; the helper is self-contained, so compile
    just that function.
    """
    import inspect
    import re
    path = os.path.join(os.path.dirname(__file__), "..", "webhook-server", "server.py")
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    m = re.search(r"^def _emit_rec_terminal_event\(.*?(?=^def |\Z)", src,
                  re.S | re.M)
    assert m, "_emit_rec_terminal_event not found in server.py"
    ns: dict = {"logger": _NullLogger()}
    exec(compile(m.group(0), "server_emit", "exec"), ns)
    return ns["_emit_rec_terminal_event"]


class _NullLogger:
    def warning(self, *a, **kw):
        pass


# ── the digest count now reflects the actionable surface ────────────────────

class _Resp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def _digest_with_callprep(monkeypatch, payload_obj, status_code=200):
    import digest
    body = {"properties": {"callprep_data_json": json.dumps(payload_obj)}} \
        if payload_obj is not None else {"properties": {}}

    fake = types.SimpleNamespace(
        get=lambda *a, **kw: _Resp(status_code, body),
        RequestException=Exception,
    )
    monkeypatch.setattr(digest, "requests", fake)
    return digest


def test_open_recs_counts_only_pending(monkeypatch):
    digest = _digest_with_callprep(monkeypatch, {"recommendations": [
        {"rec_id": "a", "status": "pending"},
        {"rec_id": "b", "status": "approved"},
        {"rec_id": "c", "status": "dismissed"},
        {"rec_id": "d"},  # missing status defaults to pending
    ]})
    assert digest.get_open_recs_count("123") == 2


def test_open_recs_drops_to_zero_when_client_actions_everything(monkeypatch):
    """The pending-forever bug: dismissing used to leave the count unchanged."""
    digest = _digest_with_callprep(monkeypatch, {"recommendations": [
        {"rec_id": "a", "status": "dismissed"},
        {"rec_id": "b", "status": "approved"},
    ]})
    assert digest.get_open_recs_count("123") == 0


def test_open_recs_degrades_to_zero_not_wrong(monkeypatch):
    digest = _digest_with_callprep(monkeypatch, None)
    assert digest.get_open_recs_count("123") == 0
    assert digest.get_open_recs_count("") == 0

    digest = _digest_with_callprep(monkeypatch, {"recommendations": []}, status_code=500)
    assert digest.get_open_recs_count("123") == 0
