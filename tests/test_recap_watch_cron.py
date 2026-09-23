"""Tests for the hourly ticket-recap watchdog.

Offline; ClickUp and the portal are stubbed. What is under test is that each
silent failure mode — portal down, webhook suspended or missing, a ticket that
finished without a note — becomes a non-zero exit and one FAILING ERRORS task.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

import recap_watch_cron as cron  # noqa: E402

NOW = 1_790_000_000_000
HOUR = 3_600_000


def _resp(body, status=200):
    r = mock.Mock()
    r.ok = 200 <= status < 300
    r.status_code = status
    r.json.return_value = body
    r.text = str(body)
    return r


def _hooks(overrides=None):
    overrides = overrides or {}
    return {"webhooks": [
        {"id": f"wh{lid}", "list_id": lid, "events": ["taskStatusUpdated"],
         "endpoint": "https://x" + cron.ENDPOINT_PATH,
         "health": overrides.get(lid, {"status": "active", "fail_count": 0})}
        for lid in cron.LISTS if overrides.get(lid) != "missing"]}


def _task(status="done - add to hubspot", done=NOW - 2 * HOUR, tags=()):
    return {"id": "t1", "name": "Skytop", "url": "https://app.clickup.com/t/t1",
            "status": {"status": status}, "date_done": str(done),
            "tags": [{"name": t} for t in tags]}


class FakeClickUp:
    def __init__(self, hooks, tasks=(), open_alert=None):
        self.hooks, self.tasks, self.open_alert = hooks, list(tasks), open_alert
        self.calls = []

    def __call__(self, method, url, **kw):
        path = url.split("/api/v2/", 1)[1]
        self.calls.append((method, path, kw.get("json")))
        if method == "GET" and path.endswith("/webhook"):
            return _resp(self.hooks)
        if method == "GET" and path == f"list/{cron.ALERT_LIST_ID}/task":
            return _resp({"tasks": [self.open_alert] if self.open_alert else []})
        if method == "GET" and path.startswith("list/901111999695/task"):
            return _resp({"tasks": self.tasks, "last_page": True})
        if method == "GET" and "/task" in path:
            return _resp({"tasks": [], "last_page": True})
        return _resp({"id": "new", "url": "u"})

    def writes(self, method, fragment):
        return [c for c in self.calls if c[0] == method and fragment in c[1]]


def _main(fake, health=200):
    with mock.patch.dict(os.environ, {"CLICKUP_API_KEY": "k"}), \
         mock.patch.object(cron.requests, "request", side_effect=fake), \
         mock.patch.object(cron.requests, "get", return_value=_resp({}, health)), \
         mock.patch.object(cron.time, "time", return_value=NOW / 1000):
        return cron.main()


class TestWatchdog(unittest.TestCase):
    def test_healthy_run_exits_zero_and_files_nothing(self):
        fake = FakeClickUp(_hooks())
        self.assertEqual(_main(fake), 0)
        self.assertFalse(fake.writes("POST", "task"))

    def test_healthy_run_closes_an_open_alert(self):
        fake = FakeClickUp(_hooks(), open_alert={
            "id": "a1", "name": cron.ALERT_TASK_PREFIX + ": x", "status": {"type": "open"}})
        self.assertEqual(_main(fake), 0)
        self.assertEqual(fake.writes("PUT", "task/a1")[0][2], {"status": "complete"})

    def test_suspended_webhook_is_reactivated_and_still_alerts(self):
        fake = FakeClickUp(_hooks({"901111999695": {"status": "suspended", "fail_count": 101}}))
        self.assertEqual(_main(fake), 1)
        put = fake.writes("PUT", "webhook/wh901111999695")
        self.assertEqual(put[0][2]["status"], "active")
        opened = fake.writes("POST", f"list/{cron.ALERT_LIST_ID}/task")
        self.assertIn("SUSPENDED", opened[0][2]["description"])

    def test_suspended_webhook_not_reactivated_while_portal_is_down(self):
        fake = FakeClickUp(_hooks({"901111999695": {"status": "suspended", "fail_count": 101}}))
        self.assertEqual(_main(fake, health=502), 1)
        self.assertFalse(fake.writes("PUT", "webhook/"))

    def test_missing_webhook_alerts(self):
        fake = FakeClickUp(_hooks({"901111120522": "missing"}))
        self.assertEqual(_main(fake), 1)
        body = fake.writes("POST", f"list/{cron.ALERT_LIST_ID}/task")[0][2]["description"]
        self.assertIn("Creative + Ad Copy Updates: no ticket-complete webhook", body)

    def test_stuck_ticket_alerts(self):
        fake = FakeClickUp(_hooks(), tasks=[_task()])
        self.assertEqual(_main(fake), 1)
        opened = fake.writes("POST", f"list/{cron.ALERT_LIST_ID}/task")[0][2]
        self.assertIn("1 ticket(s) with no HubSpot note", opened["name"])
        self.assertIn("Skytop", opened["description"])

    def test_posted_recent_or_other_status_tickets_are_fine(self):
        fake = FakeClickUp(_hooks(), tasks=[
            _task(tags=["recap-posted"]),
            _task(done=NOW - 10 * 60_000),          # inside the grace period
            _task(status="complete"),               # not a watched status
        ])
        self.assertEqual(_main(fake), 0)

    def test_persisting_failure_comments_instead_of_filing_again(self):
        fake = FakeClickUp(_hooks(), tasks=[_task()], open_alert={
            "id": "a1", "name": cron.ALERT_TASK_PREFIX + ": x", "status": {"type": "open"}})
        self.assertEqual(_main(fake), 1)
        self.assertTrue(fake.writes("POST", "task/a1/comment"))
        self.assertFalse(fake.writes("POST", f"list/{cron.ALERT_LIST_ID}/task"))

    def test_finished_alert_task_is_not_reused(self):
        fake = FakeClickUp(_hooks(), tasks=[_task()], open_alert={
            "id": "a1", "name": cron.ALERT_TASK_PREFIX + ": x", "status": {"type": "done"}})
        self.assertEqual(_main(fake), 1)
        self.assertTrue(fake.writes("POST", f"list/{cron.ALERT_LIST_ID}/task"))

    def test_no_clickup_key_is_a_config_error(self):
        with mock.patch.dict(os.environ, {"CLICKUP_API_KEY": ""}):
            self.assertEqual(cron.main(), 2)


if __name__ == "__main__":
    unittest.main()
