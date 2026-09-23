"""Tests for the hourly budget-sync cron entry point.

Offline; requests.post is stubbed. What is under test is the exit code, because
the exit code is the only thing Render shows: every refusal the endpoints
report inside an HTTP 200 must come out non-zero.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

import budget_sync_cron as cron  # noqa: E402

SYNC_OK = {"ok": True, "tab": "SHADOW", "planned_updates": 3, "planned_appends": 0}
COMPARE_OK = {"ok": True, "counts": {}, "new_wrong_count": 0}


def _resp(body, status=200):
    r = mock.Mock()
    r.ok = 200 <= status < 300
    r.status_code = status
    r.json.return_value = body
    r.text = str(body)
    return r


def _run(*bodies):
    """Run main() with requests.post answering `bodies` in order. ClickUp is
    unconfigured here, so alerting is a logged no-op."""
    with mock.patch.dict(os.environ, {"INTERNAL_API_KEY": "k", "CLICKUP_API_KEY": ""}), \
         mock.patch.object(cron.requests, "request") as cu, \
         mock.patch.object(cron.requests, "post",
                           side_effect=[_resp(b) if isinstance(b, dict) else b
                                        for b in bodies]) as post:
        code = cron.main()
        cu.assert_not_called()
        return code, post


class ExitCodes(unittest.TestCase):
    def test_healthy_pass_exits_zero(self):
        code, _ = _run(SYNC_OK, COMPARE_OK)
        self.assertEqual(code, 0)

    def test_circuit_breaker_abort_is_a_failed_run(self):
        code, _ = _run({"ok": False, "aborted": "plan would write 510 cells"},
                       COMPARE_OK)
        self.assertEqual(code, 1)

    def test_sync_disabled_is_a_failed_run(self):
        code, _ = _run({"ok": False, "skipped": "BUDGET_SYNC_ENABLED is not true"},
                       COMPARE_OK)
        self.assertEqual(code, 1)

    def test_unverified_write_is_a_failed_run(self):
        code, _ = _run({"ok": False, "unverified": [{"uuid": "1"}]}, COMPARE_OK)
        self.assertEqual(code, 1)

    def test_lock_held_by_manual_run_is_not_a_failure(self):
        code, _ = _run({"ok": True, "skipped": "already_running"}, COMPARE_OK)
        self.assertEqual(code, 0)

    def test_new_system_wrong_is_a_failed_run(self):
        code, _ = _run(SYNC_OK, {"ok": False, "new_wrong_count": 2,
                                 "new_wrong": [{"uuid": "1"}, {"uuid": "2"}]})
        self.assertEqual(code, 1)

    def test_http_error_is_transport_failure(self):
        code, _ = _run(_resp({"error": "internal key required"}, status=401))
        self.assertEqual(code, 2)

    def test_missing_key_never_calls_out(self):
        with mock.patch.dict(os.environ, {"INTERNAL_API_KEY": "", "CLICKUP_API_KEY": ""}), \
             mock.patch.object(cron.requests, "post") as post:
            self.assertEqual(cron.main(), 2)
            post.assert_not_called()


class Calls(unittest.TestCase):
    def test_sync_pins_shadow_and_applies(self):
        _, post = _run(SYNC_OK, COMPARE_OK)
        url, = post.call_args_list[0].args
        self.assertTrue(url.endswith("/api/internal/budget-sync/sync"))
        self.assertEqual(post.call_args_list[0].kwargs["params"],
                         {"apply": "true", "target": "shadow"})

    def test_compare_still_runs_after_failed_sync(self):
        _, post = _run({"ok": False, "aborted": "x"}, COMPARE_OK)
        self.assertEqual(post.call_count, 2)
        self.assertTrue(post.call_args_list[1].args[0]
                        .endswith("/api/internal/budget-sync/compare"))



class FakeClickUp:
    """Records ClickUp calls; `open_tasks` is what the list currently holds."""
    def __init__(self, open_tasks=(), fail=False):
        self.open_tasks = list(open_tasks)
        self.fail = fail
        self.calls = []

    def __call__(self, method, url, **kw):
        self.calls.append((method, url.split("/api/v2/")[1], kw))
        if self.fail:
            return _resp({"err": "down"}, status=500)
        if method == "GET":
            return _resp({"tasks": self.open_tasks})
        if method == "POST" and url.endswith("/task"):
            return _resp({"id": "new", "url": "https://app.clickup.com/t/new"})
        return _resp({})

    def made(self, method, suffix):
        return [c for c in self.calls if c[0] == method and c[1].endswith(suffix)]


def _run_alerting(cu, *bodies):
    with mock.patch.dict(os.environ, {"INTERNAL_API_KEY": "k", "CLICKUP_API_KEY": "cu"}), \
         mock.patch.object(cron.requests, "request", side_effect=cu), \
         mock.patch.object(cron.requests, "post",
                           side_effect=[_resp(b) if isinstance(b, dict) else b
                                        for b in bodies]):
        return cron.main()


class Alerting(unittest.TestCase):
    def test_failure_opens_a_task_on_the_failing_errors_list(self):
        cu = FakeClickUp()
        code = _run_alerting(cu, {"ok": False, "aborted": "plan would write 510 cells"},
                             COMPARE_OK)
        self.assertEqual(code, 1)
        created = cu.made("POST", f"list/{cron.ALERT_LIST_ID}/task")
        self.assertEqual(len(created), 1)
        body = created[0][2]["json"]
        self.assertTrue(body["name"].startswith(cron.ALERT_TASK_PREFIX))
        self.assertIn("510 cells", body["description"])

    def test_a_persisting_failure_comments_instead_of_duplicating(self):
        cu = FakeClickUp(open_tasks=[{"id": "t1", "name": "Budget sync failing: x"}])
        _run_alerting(cu, {"ok": False, "aborted": "x"}, COMPARE_OK)
        self.assertEqual(cu.made("POST", f"list/{cron.ALERT_LIST_ID}/task"), [])
        self.assertEqual(len(cu.made("POST", "task/t1/comment")), 1)

    def test_affected_properties_are_named(self):
        cu = FakeClickUp()
        _run_alerting(cu, SYNC_OK, {"ok": False, "new_wrong_count": 1, "new_wrong": [
            {"account_name": "Aerie Happy Valley", "cells": [
                {"channel": "Paid Search Ads", "verdict": "new_wrong",
                 "shadow": "$2400.00", "hubspot": "$3500.00"}]}]})
        desc = cu.made("POST", "/task")[0][2]["json"]["description"]
        self.assertIn("Aerie Happy Valley", desc)
        self.assertIn("$3500.00", desc)

    def test_portal_down_still_alerts(self):
        cu = FakeClickUp()
        code = _run_alerting(cu, _resp({"error": "bad gateway"}, status=502))
        self.assertEqual(code, 2)
        self.assertEqual(len(cu.made("POST", "/task")), 1)

    def test_healthy_run_closes_the_open_task(self):
        cu = FakeClickUp(open_tasks=[{"id": "t1", "name": "Budget sync failing: x"}])
        self.assertEqual(_run_alerting(cu, SYNC_OK, COMPARE_OK), 0)
        self.assertEqual(len(cu.made("POST", "task/t1/comment")), 1)
        closed = cu.made("PUT", "task/t1")
        self.assertEqual(closed[0][2]["json"]["status"], cron.ALERT_CLOSED_STATUS)

    def test_a_completed_task_does_not_absorb_a_new_failure(self):
        """ClickUp returns "done"-type tasks even with include_closed=false."""
        cu = FakeClickUp(open_tasks=[{"id": "t1", "name": "Budget sync failing: x",
                                      "status": {"status": "complete", "type": "done"}}])
        _run_alerting(cu, {"ok": False, "aborted": "x"}, COMPARE_OK)
        self.assertEqual(cu.made("POST", "task/t1/comment"), [])
        self.assertEqual(len(cu.made("POST", f"list/{cron.ALERT_LIST_ID}/task")), 1)

    def test_healthy_run_leaves_unrelated_tasks_alone(self):
        cu = FakeClickUp(open_tasks=[{"id": "t9", "name": "Some other error"}])
        _run_alerting(cu, SYNC_OK, COMPARE_OK)
        self.assertEqual(cu.made("PUT", "task/t9"), [])

    def test_clickup_outage_does_not_change_the_exit_code(self):
        cu = FakeClickUp(fail=True)
        self.assertEqual(_run_alerting(cu, {"ok": False, "aborted": "x"}, COMPARE_OK), 1)
        self.assertEqual(_run_alerting(FakeClickUp(fail=True), SYNC_OK, COMPARE_OK), 0)


if __name__ == "__main__":
    unittest.main()
