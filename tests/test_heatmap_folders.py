"""Quarterly heatmap folders: quarter math, eligibility, folder matching, and
the run loop against a fake Drive. Offline."""

import os
import sys
import unittest
from datetime import date
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

import heatmap_folders as hf  # noqa: E402
import heatmap_folders_cron as cron  # noqa: E402


def co(cid, name, budget="500", status="RPM Managed"):
    return {"id": cid, "properties": {"name": name, "seo_budget": budget, "plestatus": status}}


class QuarterLabel(unittest.TestCase):
    def test_label_is_the_quarter_containing_the_date(self):
        self.assertEqual(hf.quarter_label(date(2026, 9, 1)), "Q3 26")   # the Sep 1 run → Q3, not Q4
        self.assertEqual(hf.quarter_label(date(2026, 3, 1)), "Q1 26")
        self.assertEqual(hf.quarter_label(date(2026, 12, 1)), "Q4 26")
        self.assertEqual(hf.quarter_label(date(2027, 1, 15)), "Q1 27")


class Eligibility(unittest.TestCase):
    def test_positive_budget_managed_is_eligible(self):
        self.assertTrue(hf.eligible(co("1", "A")))

    def test_zero_or_blank_budget_is_not(self):
        self.assertFalse(hf.eligible(co("1", "A", budget="0")))
        self.assertFalse(hf.eligible(co("1", "A", budget="")))

    def test_finished_statuses_are_skipped(self):
        self.assertFalse(hf.eligible(co("1", "A", status="Disposition Complete")))
        self.assertTrue(hf.eligible(co("1", "A", status="Dispositioning")))

    def test_money_formatting_is_tolerated(self):
        self.assertTrue(hf.eligible(co("1", "A", budget="$1,300")))


class Matching(unittest.TestCase):
    def test_folder_name_matches_existing_convention(self):
        self.assertEqual(hf.folder_name("Attiva Pearland by Cortland", "58465963660"),
                         "Attiva Pearland by Cortland - 58465963660")
        self.assertEqual(hf.folder_name('Bad/Name: "x"', "1"), "BadName x - 1")

    def test_renamed_property_reuses_its_folder_by_id(self):
        idx = hf.index_company_folders([{"id": "F1", "name": "Alexan Memorial - 42"}])
        steps = hf.plan([co("42", "Maxwell Memorial")], idx)
        self.assertEqual(steps[0]["company_folder_id"], "F1")

    def test_new_property_has_no_folder_yet(self):
        steps = hf.plan([co("7", "New Place")], {})
        self.assertIsNone(steps[0]["company_folder_id"])
        self.assertEqual(steps[0]["folder_name"], "New Place - 7")


class FakeDrive:
    """In-memory Drive: {folder_id: [children]}."""
    def __init__(self, tree):
        self.tree = tree
        self.created = []

    def children(self, sess, parent):
        return list(self.tree.get(parent, []))

    def create(self, sess, name, parent):
        fid = f"new{len(self.created)}"
        self.created.append((name, parent))
        self.tree.setdefault(parent, []).append({"id": fid, "name": name})
        self.tree[fid] = []
        return fid


class RunLoop(unittest.TestCase):
    def _run(self, tree, companies, dry_run):
        fake = FakeDrive(tree)
        with mock.patch.object(hf, "seo_companies", lambda: companies), \
             mock.patch.object(hf, "_drive_session", lambda: object()), \
             mock.patch.object(hf, "child_folders", fake.children), \
             mock.patch.object(hf, "create_folder", fake.create):
            return hf.run(dry_run=dry_run, quarter="Q3 26"), fake

    def test_creates_quarter_and_screenshots_under_existing_property(self):
        rep, fake = self._run({hf.PARENT_FOLDER_ID: [{"id": "P1", "name": "A - 1"}]}, [co("1", "A")], False)
        self.assertEqual(rep["quarter_folders_created"], 1)
        self.assertEqual(rep["company_folders_created"], 0)
        self.assertEqual([n for n, _ in fake.created], ["Q3 26", "Screenshots"])

    def test_creates_property_folder_when_missing(self):
        rep, fake = self._run({hf.PARENT_FOLDER_ID: []}, [co("9", "B")], False)
        self.assertEqual(rep["company_folders_created"], 1)
        self.assertEqual(fake.created[0], ("B - 9", hf.PARENT_FOLDER_ID))

    def test_rerun_skips_existing_quarter(self):
        tree = {hf.PARENT_FOLDER_ID: [{"id": "P1", "name": "A - 1"}], "P1": [{"id": "Q", "name": "Q3 26"}]}
        rep, fake = self._run(tree, [co("1", "A")], False)
        self.assertEqual(rep["already_existed"], 1)
        self.assertEqual(fake.created, [])

    def test_dry_run_creates_nothing(self):
        rep, fake = self._run({hf.PARENT_FOLDER_ID: []}, [co("9", "B")], True)
        self.assertEqual(fake.created, [])
        self.assertEqual(rep["would_create_count"], 1)

    def test_one_failure_does_not_stop_the_rest(self):
        fake = FakeDrive({hf.PARENT_FOLDER_ID: []})
        calls = {"n": 0}

        def flaky(sess, name, parent):
            calls["n"] += 1
            if name == "Bad - 1":
                raise RuntimeError("boom")
            return fake.create(sess, name, parent)
        with mock.patch.object(hf, "seo_companies", lambda: [co("1", "Bad"), co("2", "Good")]), \
             mock.patch.object(hf, "_drive_session", lambda: object()), \
             mock.patch.object(hf, "child_folders", fake.children), \
             mock.patch.object(hf, "create_folder", flaky):
            rep = hf.run(dry_run=False, quarter="Q3 26")
        self.assertEqual(len(rep["errors"]), 1)
        self.assertEqual(rep["quarter_folders_created"], 1)
        self.assertFalse(rep["ok"])


class CronReporting(unittest.TestCase):
    def test_summary_says_dry_run_plainly(self):
        text = cron.summary({"quarter": "Q4 26", "dry_run": True, "properties": 712,
                             "quarter_folders_created": 0, "company_folders_created": 0,
                             "already_existed": 0, "errors": [], "would_create_count": 712})
        self.assertIn("DRY RUN", text)
        self.assertIn("712", text)

    def test_errors_raise_an_alert_and_exit_1(self):
        rep = {"quarter": "Q4 26", "dry_run": False, "properties": 2, "quarter_folders_created": 1,
               "company_folders_created": 0, "already_existed": 0, "errors": ["X: boom"], "ok": False}
        with mock.patch.object(cron.hf, "run", lambda **k: rep), \
             mock.patch.object(cron, "_cu") as cu:
            self.assertEqual(cron.main(["x"]), 1)
        self.assertTrue(any("FAILING" in str(c) or "list/" in str(c.args[1]) for c in cu.call_args_list))

    def test_missing_config_exits_2(self):
        def boom(**k):
            raise RuntimeError("HEATMAP_DRIVE_SA_JSON not set")
        with mock.patch.object(cron.hf, "run", boom), mock.patch.object(cron, "_cu"):
            self.assertEqual(cron.main(["x"]), 2)


if __name__ == "__main__":
    unittest.main()
