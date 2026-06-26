"""Tests for loop_writer write-health tracking + dead-letter.

These make silent BigQuery drops visible. The BQ client is faked (success /
row-errors / raises), so no real BQ is touched. slack_notifier is stubbed so
record()'s best-effort notify never reaches the network.
"""

from __future__ import annotations

import os
import sys
import tempfile
import types
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("HUBSPOT_API_KEY", "test-key")

# Stub slack so record()'s best-effort notify is a no-op (no import/network).
_fake_slack = types.ModuleType("slack_notifier")
_fake_slack.post_loop_event = lambda *a, **k: None
sys.modules["slack_notifier"] = _fake_slack

import loop_writer as lw  # noqa: E402


class _OKClient:
    def insert_rows_json(self, table, rows):
        return []  # BQ success: no row errors


class _ErrClient:
    def insert_rows_json(self, table, rows):
        return [{"index": 0, "errors": [{"reason": "invalid"}]}]


class _RaiseClient:
    def insert_rows_json(self, table, rows):
        raise RuntimeError("bq down")


def setup_function(_):
    lw.reset_write_stats()
    os.environ.pop("LOOP_EVENTS_DEADLETTER_PATH", None)


def test_skipped_when_bq_unavailable():
    with mock.patch.object(lw, "_bq", return_value=None):
        lw.record("ops", "cron_completed", property_uuid="u1")
    s = lw.write_stats()
    assert s["attempted"] == 1
    assert s["skipped_no_bq"] == 1
    assert s["succeeded"] == 0 and s["failed"] == 0


def test_success_increments_and_stamps():
    lw._bq_cache["table_ref"] = "p.d.loop_events"
    with mock.patch.object(lw, "_bq", return_value=_OKClient()):
        lw.record("ops", "cron_completed", property_uuid="u1")
    s = lw.write_stats()
    assert s["succeeded"] == 1
    assert s["failed"] == 0
    assert s["last_success_at"] is not None


def test_row_errors_count_as_failure():
    lw._bq_cache["table_ref"] = "p.d.loop_events"
    with mock.patch.object(lw, "_bq", return_value=_ErrClient()):
        lw.record("ops", "cron_completed", property_uuid="u1")
    s = lw.write_stats()
    assert s["failed"] == 1
    assert s["last_error"] and "errors" in s["last_error"]
    assert s["deadletter_written"] == 0  # no path configured


def test_exception_failure_writes_deadletter():
    lw._bq_cache["table_ref"] = "p.d.loop_events"
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "deadletter.jsonl")
        os.environ["LOOP_EVENTS_DEADLETTER_PATH"] = path
        with mock.patch.object(lw, "_bq", return_value=_RaiseClient()):
            lw.record("convert", "lead_submitted", property_uuid="u1", company_id="c1")
        s = lw.write_stats()
        assert s["failed"] == 1
        assert s["deadletter_written"] == 1
        with open(path) as fp:
            lines = [ln for ln in fp.read().splitlines() if ln.strip()]
        assert len(lines) == 1
        assert '"event_type": "lead_submitted"' in lines[0]


def test_attempted_equals_sum_invariant():
    lw._bq_cache["table_ref"] = "p.d.loop_events"
    with mock.patch.object(lw, "_bq", return_value=_OKClient()):
        lw.record("ops", "cron_completed")
        lw.record("ops", "cron_completed")
    with mock.patch.object(lw, "_bq", return_value=None):
        lw.record("ops", "cron_completed")
    s = lw.write_stats()
    assert s["attempted"] == s["succeeded"] + s["failed"] + s["skipped_no_bq"]
    assert s["attempted"] == 3
