"""Tests for budget_variance_flags — the alert path of the parallel run.

Offline. plan_flags() and watchdog() are pure; the HubSpot boundary is stubbed.
Nothing here can reach HubSpot or a live sheet.

The behaviour under test, stated once: the flag must describe reality right
now. Setting it is the easy half. Clearing it correctly — only after
re-verifying the variance is gone, never on absence of evidence — is the half
that decides whether the field is trustworthy.
"""

import os
import sys
import unittest
from datetime import date
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

import budget_compare as bc  # noqa: E402
import budget_variance_flags as vf  # noqa: E402


TODAY = date.today().strftime("%Y-%m-%d")


def _prop(uuid, company_id, name="Citria", channels=None):
    """One new_wrong property, as budget_compare.flagworthy() returns it."""
    return {
        "uuid": uuid,
        "company_id": company_id,
        "account_name": name,
        "verdict": bc.VERDICT_NEW_WRONG,
        "deal_id": f"d-{uuid}",
        "closedate": "2026-08-01T00:00:00Z",
        "cells": channels if channels is not None else [
            {"channel": "Paid Search Ads", "verdict": bc.VERDICT_NEW_WRONG,
             "hubspot": "$2,060.00", "live": "$2,060.00", "shadow": "$9.00"}],
    }


def _comparison(new_wrong=(), evaluated=(), flood=False, **extra):
    return {"ok": not new_wrong, "new_wrong": list(new_wrong),
            "new_wrong_count": len(new_wrong), "evaluated": list(evaluated),
            "flood": flood, "flag_ceiling": 25, **extra}


def _company(cid, uuid="", flagged_at=TODAY, task_id="", name="Citria"):
    return {"id": cid, "properties": {
        "uuid": uuid, "name": name,
        vf.PROP_FLAG: "true", vf.PROP_FLAGGED_AT: flagged_at,
        vf.PROP_TASK_ID: task_id}}


class Setting(unittest.TestCase):
    def test_flags_a_property_the_new_system_got_wrong(self):
        plan = vf.plan_flags(_comparison([_prop("u1", "101")], ["u1"]), [])
        self.assertEqual(len(plan["set"]), 1)
        props = plan["set"][0]["properties"]
        self.assertEqual(plan["set"][0]["id"], "101")
        self.assertEqual(props[vf.PROP_FLAG], "true")
        self.assertEqual(props[vf.PROP_FLAGGED_AT], TODAY)

    def test_detail_names_all_three_values(self):
        """'the new tab says $9' is not actionable on its own."""
        detail = vf.detail_text(_prop("u1", "101"))
        self.assertIn("Paid Search Ads", detail)
        self.assertIn("$2,060.00", detail)
        self.assertIn("$9.00", detail)

    def test_detail_renders_a_missing_row_as_such(self):
        detail = vf.detail_text(_prop("u1", "101", channels=[
            {"channel": "Geofence", "verdict": bc.VERDICT_NEW_WRONG,
             "hubspot": "$500.00", "live": "$500.00", "shadow": None}]))
        self.assertIn("(no row)", detail)

    def test_detail_is_capped(self):
        many = [{"channel": f"Channel {i}", "verdict": bc.VERDICT_NEW_WRONG,
                 "hubspot": "$1.00", "live": "$1.00", "shadow": "$2.00"}
                for i in range(300)]
        self.assertLessEqual(len(vf.detail_text(_prop("u1", "101", channels=many))),
                             vf.MAX_DETAIL_CHARS)

    def test_property_without_a_company_id_is_not_silently_dropped(self):
        """No addressable record, so nothing to write — but it must not look
        like a property that had no variance."""
        plan = vf.plan_flags(_comparison([_prop("u1", "")], ["u1"]), [])
        self.assertEqual(plan["set"], [])

    def test_already_flagged_and_still_wrong_is_not_cleared(self):
        plan = vf.plan_flags(_comparison([_prop("u1", "101")], ["u1"]),
                             [_company("101", "u1")])
        self.assertEqual(plan["clear"], [])
        self.assertEqual(len(plan["set"]), 1)


class Clearing(unittest.TestCase):
    """The half that decides whether the field is trustworthy."""

    def test_clears_a_flag_when_the_run_re_verified_it_as_resolved(self):
        plan = vf.plan_flags(_comparison([], ["u1"]), [_company("101", "u1")])
        self.assertEqual(len(plan["clear"]), 1)
        self.assertEqual(plan["clear"][0]["properties"][vf.PROP_FLAG], "false")

    def test_clearing_also_resets_the_task_id(self):
        """Otherwise the next flag on that company inherits the old ticket's id
        and the watchdog reads it as already-notified."""
        plan = vf.plan_flags(_comparison([], ["u1"]),
                             [_company("101", "u1", task_id="CU-123")])
        self.assertEqual(plan["clear"][0]["properties"][vf.PROP_TASK_ID], "")

    def test_holds_a_flag_the_run_had_no_opinion_about(self):
        """The company is flagged but absent from this run — HubSpot returned
        nothing for it, or it left the portfolio. Clearing on absence of
        evidence is how a real variance gets silently dropped."""
        plan = vf.plan_flags(_comparison([], ["u2"]), [_company("101", "u1")])
        self.assertEqual(plan["clear"], [])
        self.assertEqual(len(plan["held"]), 1)
        self.assertEqual(plan["held"][0]["reason"], "not_evaluated_this_run")

    def test_holds_a_flag_on_a_company_with_no_uuid(self):
        plan = vf.plan_flags(_comparison([], ["u1"]), [_company("101", "")])
        self.assertEqual(plan["clear"], [])
        self.assertEqual(len(plan["held"]), 1)

    def test_a_resolved_and_a_still_wrong_property_in_one_run(self):
        plan = vf.plan_flags(
            _comparison([_prop("u1", "101")], ["u1", "u2"]),
            [_company("101", "u1"), _company("202", "u2")])
        self.assertEqual([i["id"] for i in plan["set"]], ["101"])
        self.assertEqual([i["id"] for i in plan["clear"]], ["202"])


class FloodCeiling(unittest.TestCase):
    def test_writes_nothing_at_all_above_the_ceiling(self):
        """Not even clears. A comparison that wants hundreds of flags is a
        broken comparison, so acting on any part of it is unsafe."""
        plan = vf.plan_flags(
            _comparison([_prop(f"u{i}", str(i)) for i in range(50)],
                        [f"u{i}" for i in range(50)], flood=True),
            [_company("101", "u1")])
        self.assertEqual(plan["set"], [])
        self.assertEqual(plan["clear"], [])
        self.assertIn("flood ceiling", plan["aborted"])


class Watchdog(unittest.TestCase):
    """A flag with no evidence the workflow ran is an enrolment failure — §1a,
    the original defect."""

    def test_counts_a_stale_flag_with_no_task_id(self):
        out = vf.watchdog([_company("101", "u1", flagged_at="2026-08-01")],
                          today="2026-08-13")
        self.assertEqual(out["stale_count"], 1)
        self.assertEqual(out["stale"][0]["reason"], "no_workflow_evidence")

    def test_a_flag_with_a_task_id_is_fine(self):
        out = vf.watchdog(
            [_company("101", "u1", flagged_at="2026-08-01", task_id="CU-9")],
            today="2026-08-13")
        self.assertEqual(out["stale_count"], 0)

    def test_a_flag_raised_today_is_not_yet_stale(self):
        """The workflow fires within seconds, but a run racing it must not
        report a false enrolment failure."""
        out = vf.watchdog([_company("101", "u1", flagged_at="2026-08-13")],
                          today="2026-08-13")
        self.assertEqual(out["stale_count"], 0)

    def test_a_flag_with_no_date_is_stale(self):
        out = vf.watchdog([_company("101", "u1", flagged_at="")],
                          today="2026-08-13")
        self.assertEqual(out["stale"][0]["reason"], "no_flag_date")

    def test_tolerates_a_datetime_valued_flag_date(self):
        """If the property was created as a datetime rather than a date."""
        out = vf.watchdog(
            [_company("101", "u1", flagged_at="2026-08-01T14:03:00Z")],
            today="2026-08-13")
        self.assertEqual(out["stale_count"], 1)


class RunEntryPoint(unittest.TestCase):
    def _run(self, comparison, flagged, **kw):
        with mock.patch.object(vf, "_currently_flagged", return_value=flagged), \
             mock.patch.object(vf, "_apply") as apply_:
            report = vf.run(comparison=comparison, **kw)
        return report, apply_

    def test_dry_run_is_the_default_and_writes_nothing(self):
        report, apply_ = self._run(_comparison([_prop("u1", "101")], ["u1"]), [])
        apply_.assert_not_called()
        self.assertEqual(report["would_set"], 1)

    def test_apply_without_the_env_flag_writes_nothing(self):
        with mock.patch.object(vf, "FLAGS_ENABLED", False):
            report, apply_ = self._run(
                _comparison([_prop("u1", "101")], ["u1"]), [], dry_run=False)
        apply_.assert_not_called()
        self.assertIn("BUDGET_VARIANCE_FLAGS_ENABLED", report["skipped"])

    def test_apply_with_the_env_flag_writes(self):
        with mock.patch.object(vf, "FLAGS_ENABLED", True):
            report, apply_ = self._run(
                _comparison([_prop("u1", "101")], ["u1"]), [], dry_run=False)
        apply_.assert_called_once()
        self.assertEqual(report["set"], 1)

    def test_clears_are_written_before_sets(self):
        """If the run dies partway the surviving state is 'flagged' rather than
        'silently cleared' — the safe direction to fail in."""
        order = []
        with mock.patch.object(vf, "FLAGS_ENABLED", True), \
             mock.patch.object(vf, "_currently_flagged",
                               return_value=[_company("202", "u2")]), \
             mock.patch.object(vf, "_apply",
                               side_effect=lambda items: order.append(
                                   items[0]["properties"][vf.PROP_FLAG])):
            vf.run(dry_run=False,
                   comparison=_comparison([_prop("u1", "101")], ["u1", "u2"]))
        self.assertEqual(order, ["false", "true"])

    def test_watchdog_runs_even_on_a_dry_run(self):
        report, _ = self._run(_comparison([], ["u1"]),
                              [_company("101", "u1", flagged_at="2026-01-01")])
        self.assertEqual(report["watchdog"]["stale_count"], 1)

    def test_flood_aborts_the_run(self):
        report, apply_ = self._run(
            _comparison([_prop(f"u{i}", str(i)) for i in range(50)],
                        [f"u{i}" for i in range(50)], flood=True),
            [], dry_run=False)
        apply_.assert_not_called()
        self.assertFalse(report["ok"])
        self.assertIn("flood ceiling", report["aborted"])


if __name__ == "__main__":
    unittest.main()
