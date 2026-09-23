"""Tests for the daily onboarding-sheet watchdog.

Offline; HubSpot, the sheet and ClickUp are stubbed. What is under test is that
each way a sold property fails to reach "Accounts For Onboarding" becomes one
ClickUp task with the right reason, that nothing is reported before it is due,
and that the watchdog never passes a run it could not see.
"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

import onboarding_watch_cron as cron  # noqa: E402

# Wed 2026-09-23 14:00 UTC = 9 AM CDT, when the cron runs.
NOW = datetime(2026, 9, 23, 14, 0, tzinfo=timezone.utc)
HEADER = ["UUID", "Vertical", "Label", "", "Account name", "address 1", "city",
          "state", "zip", "domain"]


def _row(uuid, name="Dallas - Somewhere", addr="1 Main St", domain="x.com"):
    return [uuid, "Multi-Housing", "Dallas", "", name, addr, "Dallas", "Texas", "75001", domain]


def _company(uuid="u1", plestatus="RPM Managed", created="2026-06-01T00:00:00Z",
             flag=None, name="Somewhere", domain="x.com"):
    return {"name": name, "uuid": uuid, "plestatus": plestatus, "createdate": created,
            "domain": domain, "onboarding_sheet_written": flag, "address": "1 Main St",
            "city": "Dallas", "state": "Texas", "zip": "75001"}


def _deal(did="d1", stage="closedwon", entered=NOW - timedelta(days=5), launch="2026-09-18",
          name="Somewhere - New Account Build"):
    return {"id": did, "properties": {
        "dealname": name, "dealstage": stage, "launch_date__c": launch,
        "createdate": (entered - timedelta(days=1)).isoformat(),
        f"hs_v2_date_entered_{cron.READY_TO_LAUNCH}": entered.isoformat()}}


def _eval(rows, deals=(), links=None, companies=None, expected=(), twin=None,
          eligible=NOW - timedelta(days=3), now=NOW):
    return cron.evaluate(now, cron.rows_from_values([HEADER] + rows), list(deals),
                         links or {}, companies or {}, set(expected), 2, 5,
                         twin or (lambda cid, p: None), lambda cid, p: eligible)


class TestSheetParsing(unittest.TestCase):
    def test_columns_found_by_header_not_position(self):
        rows = cron.rows_from_values([["domain", "UUID", "Account name", "address 1", "city",
                                       "state", "zip"],
                                      ["x.com", "u1", "A", "1 Main", "Dallas", "TX", "75001"]])
        self.assertEqual(rows["u1"]["address 1"], "1 Main")

    def test_missing_required_column_cannot_run(self):
        with self.assertRaises(cron.CannotRun):
            cron.rows_from_values([["UUID", "Account name"], ["u1", "A"]])

    def test_live_row_beats_a_dispo_row_with_the_same_uuid(self):
        rows = cron.rows_from_values([HEADER, _row("u1"), _row("u1", name="DISPO - Old")])
        self.assertFalse(cron._is_dispo(rows["u1"]))


class TestDue(unittest.TestCase):
    def test_never_due_before_the_next_6am_run(self):
        entered = datetime(2026, 9, 23, 13, 0, tzinfo=timezone.utc)   # 8 AM CDT
        due = cron.deal_due_at(_deal(entered=entered, launch="2026-09-23"), 2, 5)
        self.assertEqual(due, datetime(2026, 9, 24, 13, 0, tzinfo=timezone.utc))

    def test_near_launch_pulls_the_deadline_in(self):
        entered = datetime(2026, 9, 1, 15, 0, tzinfo=timezone.utc)
        due = cron.deal_due_at(_deal(entered=entered, launch="2026-10-30"), 2, 5)
        self.assertEqual(due, entered + timedelta(days=2))


class TestEvaluate(unittest.TestCase):
    def test_deal_with_row_is_clean(self):
        issues = _eval([_row("u1")], [_deal()], {"d1": ["c1"]}, {"c1": _company()})
        self.assertEqual(issues, [])

    def test_unmerged_bi_twin_is_named_in_the_task(self):
        issues = _eval([_row("other")], [_deal()], {"d1": ["c1"]},
                       {"c1": _company(plestatus=None)},
                       twin=lambda cid, p: {"id": "c2", "name": "Somewhere (BI)"})
        self.assertEqual([i.key for i in issues], ["missing:c1"])
        self.assertIn("Unmerged BI duplicate", issues[0].detail[0])
        self.assertIn("c2", issues[0].detail[0])

    def test_not_reported_before_due(self):
        fresh = _deal(entered=NOW - timedelta(hours=2), launch="2026-10-30")
        issues = _eval([_row("other")], [fresh], {"d1": ["c1"]}, {"c1": _company(plestatus=None)})
        self.assertEqual(issues, [])

    def test_returning_property_on_dispo_row(self):
        issues = _eval([_row("u1", name="DISPO 5/27/25 - Strata")], [_deal()], {"d1": ["c1"]},
                       {"c1": _company(plestatus="Dispositioning", created="2024-06-14T00:00:00Z")})
        self.assertEqual([i.key for i in issues], ["dispo_row:c1"])

    def test_old_record_explains_the_cutoff(self):
        issues = _eval([_row("other")], [_deal()], {"d1": ["c1"]},
                       {"c1": _company(created="2024-01-01T00:00:00Z")})
        self.assertIn("before the workflow's 5/1/26 cutoff", issues[0].detail[0])

    def test_deal_without_company(self):
        issues = _eval([_row("other")], [_deal()], {"d1": []}, {})
        self.assertEqual([i.key for i in issues], ["no_company:d1"])

    def test_incomplete_row_is_normal_priority(self):
        issues = _eval([_row("u1", addr="")], [_deal()], {"d1": ["c1"]}, {"c1": _company()})
        self.assertEqual([(i.key, i.priority) for i in issues], [("incomplete:c1", 3)])
        self.assertIn("address 1", issues[0].headline)

    def test_flag_set_without_row_is_reported_immediately(self):
        issues = _eval([_row("other")], companies={"c1": _company(flag="true")},
                       expected=["c1"], eligible=NOW)
        self.assertIn("flag is set but no row", issues[0].detail[0])

    def test_workflow_candidate_waits_for_its_first_run(self):
        # Became eligible at 7 AM CDT today: the 6 AM run could not have seen it.
        issues = _eval([_row("other")], companies={"c1": _company()}, expected=["c1"],
                       eligible=datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc))
        self.assertEqual(issues, [])

    def test_workflow_candidate_missed_by_the_run(self):
        issues = _eval([_row("other")], companies={"c1": _company()}, expected=["c1"])
        self.assertIn(cron.WORKFLOW_ID, issues[0].detail[0])

    def test_one_task_per_company_across_both_sources(self):
        issues = _eval([_row("other")], [_deal()], {"d1": ["c1"]},
                       {"c1": _company()}, expected=["c1"])
        self.assertEqual(len(issues), 1)


def _resp(body, status=200):
    r = mock.Mock()
    r.ok = 200 <= status < 300
    r.status_code = status
    r.json.return_value = body
    r.text = str(body)
    return r


class FakeClickUp:
    def __init__(self, open_tasks=()):
        self.open, self.calls = list(open_tasks), []

    def __call__(self, method, url, **kw):
        path = url.split("/api/v2/", 1)[1]
        self.calls.append((method, path, kw.get("json")))
        if method == "GET":
            return _resp({"tasks": self.open, "last_page": True})
        return _resp({"id": "new", "url": "u"})

    def writes(self, method, fragment):
        return [c for c in self.calls if c[0] == method and fragment in c[1]]


def _open(key, desc="old reason", status_type="open"):
    return {"id": "t-" + key, "name": f"X — y [{key}]", "description": desc,
            "status": {"type": status_type}}


class TestAlerting(unittest.TestCase):
    def setUp(self):
        self.issue = cron.Issue("missing", "c1", "Cadia", "not on tab", ["Unmerged BI duplicate"])

    def _sync(self, fake, issues):
        with mock.patch.dict(os.environ, {"CLICKUP_API_KEY": "k"}), \
             mock.patch.object(cron.requests, "request", side_effect=fake):
            cron.sync_tasks(issues, "2026-09-23 14:00")

    def test_new_problem_files_one_tagged_task(self):
        fake = FakeClickUp()
        self._sync(fake, [self.issue])
        body = fake.writes("POST", f"list/{cron.ALERT_LIST_ID}/task")[0][2]
        self.assertTrue(body["name"].endswith("[missing:c1]"))
        self.assertEqual(body["tags"], [cron.ALERT_TAG])

    def test_same_reason_does_not_comment_daily(self):
        fake = FakeClickUp([_open("missing:c1", desc="Unmerged BI duplicate ...")])
        self._sync(fake, [self.issue])
        self.assertFalse(fake.writes("POST", "comment"))
        self.assertFalse(fake.writes("POST", f"list/{cron.ALERT_LIST_ID}/task"))

    def test_changed_reason_comments_once(self):
        fake = FakeClickUp([_open("missing:c1")])
        self._sync(fake, [self.issue])
        self.assertTrue(fake.writes("POST", "task/t-missing:c1/comment"))

    def test_resolved_problem_closes_its_task(self):
        fake = FakeClickUp([_open("missing:c1")])
        self._sync(fake, [])
        self.assertEqual(fake.writes("PUT", "task/t-missing:c1")[0][2],
                         {"status": cron.ALERT_CLOSED_STATUS})

    def test_done_tasks_are_not_reused(self):
        fake = FakeClickUp([_open("missing:c1", status_type="done")])
        self._sync(fake, [self.issue])
        self.assertTrue(fake.writes("POST", f"list/{cron.ALERT_LIST_ID}/task"))


class TestMain(unittest.TestCase):
    def test_no_clickup_key_is_a_config_error(self):
        with mock.patch.dict(os.environ, {"CLICKUP_API_KEY": ""}):
            self.assertEqual(cron.main([]), 2)

    def test_unreadable_sheet_files_cannot_run_and_exits_2(self):
        fake = FakeClickUp()
        with mock.patch.dict(os.environ, {"CLICKUP_API_KEY": "k", "HUBSPOT_API_KEY": "h"}), \
             mock.patch.object(cron, "read_sheet", side_effect=cron.CannotRun("403")), \
             mock.patch.object(cron.requests, "request", side_effect=fake):
            self.assertEqual(cron.main([]), 2)
        body = fake.writes("POST", f"list/{cron.ALERT_LIST_ID}/task")[0][2]
        self.assertIn(cron.CANNOT_RUN_KEY, body["name"])

    def test_a_short_read_is_not_a_clean_run(self):
        with mock.patch.dict(os.environ, {"HUBSPOT_API_KEY": "h"}), \
             mock.patch.object(cron, "read_sheet",
                               return_value=cron.rows_from_values([HEADER, _row("u1")])):
            self.assertEqual(cron.main(["--dry-run"]), 2)

    def test_problems_exit_1(self):
        fake = FakeClickUp()
        issue = cron.Issue("missing", "c1", "Cadia", "not on tab", ["why"])
        with mock.patch.dict(os.environ, {"CLICKUP_API_KEY": "k", "HUBSPOT_API_KEY": "h"}), \
             mock.patch.object(cron, "run", return_value=[issue]), \
             mock.patch.object(cron.requests, "request", side_effect=fake):
            self.assertEqual(cron.main([]), 1)


if __name__ == "__main__":
    unittest.main()
