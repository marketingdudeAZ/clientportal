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
    """Run main() with requests.post answering `bodies` in order."""
    with mock.patch.dict(os.environ, {"INTERNAL_API_KEY": "k"}), \
         mock.patch.object(cron.requests, "post",
                           side_effect=[_resp(b) if isinstance(b, dict) else b
                                        for b in bodies]) as post:
        return cron.main(), post


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
        with mock.patch.dict(os.environ, {"INTERNAL_API_KEY": ""}), \
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


if __name__ == "__main__":
    unittest.main()
