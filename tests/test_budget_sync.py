"""Tests for budget_sync — the desired-state writer.

Offline. Planning and the circuit breakers are pure functions; the sheet and
HubSpot boundaries are stubbed. Nothing here can reach the live sheet.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

import budget_reconcile as rec  # noqa: E402
import budget_sync as bs  # noqa: E402


HEADER = ["UUID", "Account Name", "Budget Group", "Budget"]
LABELS = [l for _, l in rec.BUDGET_CHANNELS]


def _rows(*blocks):
    out = [HEADER]
    for uuid, name, overrides in blocks:
        for label in LABELS:
            out.append([uuid, name, label, overrides.get(label, rec.NOT_PURCHASED)])
    return out


def _expected(*specs):
    out = {}
    for uuid, name, budgets in specs:
        full = {l: rec.NOT_PURCHASED for l in LABELS}
        full.update(budgets)
        out[uuid] = {"account_name": name, "deal_id": f"d-{uuid}",
                     "deal_name": f"{name} deal", "closedate": "2026-08-01T00:00:00Z",
                     "budgets": full}
    return out


def _state(rows):
    actual, _ = rec.parse_sheet(rows)
    return actual, bs.index_rows(rows)


class RowIndexing(unittest.TestCase):
    def test_maps_uuid_and_label_to_one_based_row(self):
        rows = _rows(("u1", "Citria", {}))
        idx = bs.index_rows(rows)
        self.assertEqual(idx[("u1", LABELS[0])], 2)      # header is row 1
        self.assertEqual(idx[("u1", LABELS[-1])], 11)

    def test_second_property_continues_the_numbering(self):
        rows = _rows(("u1", "A", {}), ("u2", "B", {}))
        idx = bs.index_rows(rows)
        self.assertEqual(idx[("u2", LABELS[0])], 12)

    def test_first_occurrence_wins_on_duplicates(self):
        rows = _rows(("u1", "A", {}))
        rows.append(["u1", "A", LABELS[0], "$999.00"])
        self.assertEqual(bs.index_rows(rows)[("u1", LABELS[0])], 2)

    def test_unknown_labels_and_blank_uuids_are_not_indexed(self):
        rows = _rows(("u1", "A", {}))
        rows.append(["u1", "A", "Crypto Ads", "$1.00"])
        rows.append(["", "", LABELS[0], "$1.00"])
        idx = bs.index_rows(rows)
        self.assertNotIn(("u1", "Crypto Ads"), idx)
        self.assertNotIn(("", LABELS[0]), idx)


class Planning(unittest.TestCase):
    def test_no_changes_when_sheet_already_matches(self):
        """Idempotence: the second run of an unchanged portfolio writes nothing."""
        rows = _rows(("u1", "Citria", {"Geofence": "$500.00"}))
        actual, idx = _state(rows)
        p = bs.plan(_expected(("u1", "Citria", {"Geofence": "$500.00"})), actual, idx)
        self.assertEqual(p["updates"], [])
        self.assertEqual(p["appends"], [])

    def test_value_change_produces_one_targeted_cell_update(self):
        rows = _rows(("u1", "Citria", {"Geofence": "$500.00"}))
        actual, idx = _state(rows)
        p = bs.plan(_expected(("u1", "Citria", {"Geofence": "$750.00"})), actual, idx)
        self.assertEqual(len(p["updates"]), 1)
        rng, val = p["updates"][0]
        self.assertEqual(val, "$750.00")
        self.assertTrue(rng.startswith("D"), rng)
        self.assertEqual(p["appends"], [])

    def test_new_property_appends_a_full_block_in_channel_order(self):
        actual, idx = _state([HEADER])
        p = bs.plan(_expected(("u9", "New Place", {"Geofence": "$100.00"})), actual, idx)
        self.assertEqual(p["updates"], [])
        self.assertEqual(len(p["appends"]), len(LABELS))
        self.assertEqual([r[2] for r in p["appends"]], LABELS)
        self.assertEqual(p["appends"][0][0], "u9")

    def test_cancellation_to_zero_is_written(self):
        """'Pause Paid Social' style deals — the ones that failed on 8/1."""
        rows = _rows(("u1", "Citria", {"Paid Meta Ads": "$1200.00"}))
        actual, idx = _state(rows)
        p = bs.plan(_expected(("u1", "Citria", {"Paid Meta Ads": "$0.00"})), actual, idx)
        self.assertEqual([v for _, v in p["updates"]], ["$0.00"])

    def test_orphan_rows_are_never_touched(self):
        """A property HubSpot no longer has an opinion about keeps its rows."""
        rows = _rows(("ghost", "Old", {"Geofence": "$500.00"}))
        actual, idx = _state(rows)
        p = bs.plan({}, actual, idx)
        self.assertEqual(p["updates"], [])
        self.assertEqual(p["appends"], [])

    def test_incomplete_block_is_reported_not_silently_appended(self):
        rows = _rows(("u1", "Citria", {}))[:-3]     # drop the last 3 channels
        actual, idx = _state(rows)
        p = bs.plan(_expected(("u1", "Citria", {})), actual, idx)
        reasons = [s["reason"] for s in p["skipped"]]
        self.assertIn("incomplete_block", reasons)
        self.assertEqual(p["appends"], [])

    def test_only_differing_channels_are_written(self):
        rows = _rows(("u1", "A", {"Geofence": "$1.00", "Demand Gen": "$2.00"}))
        actual, idx = _state(rows)
        p = bs.plan(_expected(("u1", "A", {"Geofence": "$1.00",
                                           "Demand Gen": "$99.00"})), actual, idx)
        self.assertEqual(len(p["updates"]), 1)
        self.assertEqual(p["updates"][0][1], "$99.00")


class CircuitBreakers(unittest.TestCase):
    """The guards that stop a bad 'desired state' from zeroing the portfolio.

    These matter more than anything else in the module: a desired-state loop
    fed garbage will confidently destroy every budget in one pass.
    """

    def test_too_few_properties_aborts(self):
        """An auth failure yields few/no deals. That must not read as
        'every budget should now be $ -'."""
        rows = _rows(("u1", "A", {}))
        actual, idx = _state(rows)
        expected = _expected(("u1", "A", {}))
        with mock.patch.object(bs, "MIN_EXPECTED_PROPERTIES", 300):
            with self.assertRaises(bs.SyncAborted) as ctx:
                bs._preflight(expected, actual, bs.plan(expected, actual, idx))
        self.assertIn("below the floor", str(ctx.exception))

    def test_within_the_floor_does_not_abort(self):
        rows = _rows(("u1", "A", {}))
        actual, idx = _state(rows)
        expected = _expected(("u1", "A", {}))
        with mock.patch.object(bs, "MIN_EXPECTED_PROPERTIES", 1), \
             mock.patch.object(bs, "MAX_DRIFT_RATIO", 1.0):
            bs._preflight(expected, actual, bs.plan(expected, actual, idx))

    def test_cell_write_ceiling_aborts(self):
        rows = _rows(("u1", "A", {}), ("u2", "B", {}))
        actual, idx = _state(rows)
        expected = _expected(("u1", "A", {"Geofence": "$5.00"}),
                             ("u2", "B", {"Geofence": "$5.00"}))
        with mock.patch.object(bs, "MIN_EXPECTED_PROPERTIES", 0), \
             mock.patch.object(bs, "MAX_CELL_WRITES", 1):
            with self.assertRaises(bs.SyncAborted) as ctx:
                bs._preflight(expected, actual, bs.plan(expected, actual, idx))
        self.assertIn("above the ceiling", str(ctx.exception))

    def test_drift_ratio_ceiling_aborts(self):
        rows = _rows(("u1", "A", {}), ("u2", "B", {}), ("u3", "C", {}))
        actual, idx = _state(rows)
        expected = _expected(("u1", "A", {"Geofence": "$5.00"}),
                             ("u2", "B", {"Geofence": "$5.00"}),
                             ("u3", "C", {"Geofence": "$5.00"}))
        with mock.patch.object(bs, "MIN_EXPECTED_PROPERTIES", 0), \
             mock.patch.object(bs, "MAX_CELL_WRITES", 10_000), \
             mock.patch.object(bs, "MAX_DRIFT_RATIO", 0.15):
            with self.assertRaises(bs.SyncAborted) as ctx:
                bs._preflight(expected, actual, bs.plan(expected, actual, idx))
        self.assertIn("above the", str(ctx.exception))

    def test_one_property_changing_every_channel_is_normal(self):
        """The ratio counts properties, not cells — a single property doing a
        full rebuild must not trip the breaker."""
        rows = _rows(*[(f"u{i}", f"P{i}", {}) for i in range(10)])
        actual, idx = _state(rows)
        specs = [(f"u{i}", f"P{i}", {}) for i in range(10)]
        specs[0] = ("u0", "P0", {l: "$10.00" for l in LABELS})
        expected = _expected(*specs)
        with mock.patch.object(bs, "MIN_EXPECTED_PROPERTIES", 0), \
             mock.patch.object(bs, "MAX_CELL_WRITES", 10_000), \
             mock.patch.object(bs, "MAX_DRIFT_RATIO", 0.15):
            bs._preflight(expected, actual, bs.plan(expected, actual, idx))

    def test_touched_properties_counts_appends_and_diffs(self):
        rows = _rows(("u1", "A", {"Geofence": "$1.00"}))
        actual, idx = _state(rows)
        expected = _expected(("u1", "A", {"Geofence": "$2.00"}),
                             ("u2", "B", {}))
        p = bs.plan(expected, actual, idx)
        self.assertEqual(bs.touched_properties(expected, actual, p), {"u1", "u2"})


class SyncEntryPoint(unittest.TestCase):
    def test_dry_run_is_the_default_and_writes_nothing(self):
        rows = _rows(("u1", "A", {"Geofence": "$1.00"}))
        with mock.patch.object(rec, "expected_budgets",
                               return_value=_expected(("u1", "A", {"Geofence": "$2.00"}))), \
             mock.patch.object(rec, "read_sheet_rows", return_value=rows), \
             mock.patch.object(bs, "MIN_EXPECTED_PROPERTIES", 0), \
             mock.patch.object(bs, "MAX_DRIFT_RATIO", 1.0), \
             mock.patch.object(bs, "_worksheet") as ws:
            report = bs.sync()
        self.assertTrue(report["dry_run"])
        self.assertEqual(report["planned_updates"], 1)
        ws.assert_not_called()

    def test_disabled_flag_blocks_a_real_run(self):
        with mock.patch.object(bs, "SYNC_ENABLED", False), \
             mock.patch.object(bs, "_worksheet") as ws:
            report = bs.sync(dry_run=False)
        self.assertFalse(report["ok"])
        self.assertIn("BUDGET_SYNC_ENABLED", report["skipped"])
        ws.assert_not_called()

    def test_a_tripped_breaker_writes_nothing_and_deadletters(self):
        rows = _rows(("u1", "A", {}))
        with mock.patch.object(rec, "expected_budgets",
                               return_value=_expected(("u1", "A", {}))), \
             mock.patch.object(rec, "read_sheet_rows", return_value=rows), \
             mock.patch.object(bs, "SYNC_ENABLED", True), \
             mock.patch.object(bs, "MIN_EXPECTED_PROPERTIES", 300), \
             mock.patch.object(bs, "_worksheet") as ws, \
             mock.patch.object(bs, "_deadletter") as dl:
            report = bs.sync(dry_run=False)
        self.assertFalse(report["ok"])
        self.assertIn("below the floor", report["aborted"])
        ws.assert_not_called()
        self.assertEqual(dl.call_args[0][0]["kind"], "circuit_breaker")

    def test_no_changes_short_circuits_without_writing(self):
        rows = _rows(("u1", "A", {"Geofence": "$1.00"}))
        with mock.patch.object(rec, "expected_budgets",
                               return_value=_expected(("u1", "A", {"Geofence": "$1.00"}))), \
             mock.patch.object(rec, "read_sheet_rows", return_value=rows), \
             mock.patch.object(bs, "SYNC_ENABLED", True), \
             mock.patch.object(bs, "MIN_EXPECTED_PROPERTIES", 0), \
             mock.patch.object(bs, "_worksheet") as ws:
            report = bs.sync(dry_run=False)
        self.assertTrue(report["ok"])
        self.assertTrue(report["no_changes"])
        ws.assert_not_called()

    def test_successful_write_is_verified_by_reading_back(self):
        before = _rows(("u1", "A", {"Geofence": "$1.00"}))
        after = _rows(("u1", "A", {"Geofence": "$2.00"}))
        exp = _expected(("u1", "A", {"Geofence": "$2.00"}))
        with mock.patch.object(rec, "expected_budgets", return_value=exp), \
             mock.patch.object(rec, "read_sheet_rows", side_effect=[before, after]), \
             mock.patch.object(bs, "SYNC_ENABLED", True), \
             mock.patch.object(bs, "MIN_EXPECTED_PROPERTIES", 0), \
             mock.patch.object(bs, "MAX_DRIFT_RATIO", 1.0), \
             mock.patch.object(bs, "_worksheet"), \
             mock.patch.object(bs, "_apply") as apply_:
            report = bs.sync(dry_run=False)
        self.assertTrue(report["ok"])
        self.assertEqual(report["unverified"], [])
        apply_.assert_called_once()

    def test_a_write_that_does_not_land_is_reported_as_failure(self):
        """The 8/1 failure mode: reported success, nothing written. Here the
        read-back still disagrees, so ok is False and it dead-letters."""
        before = _rows(("u1", "A", {"Geofence": "$1.00"}))
        exp = _expected(("u1", "A", {"Geofence": "$2.00"}))
        with mock.patch.object(rec, "expected_budgets", return_value=exp), \
             mock.patch.object(rec, "read_sheet_rows", side_effect=[before, before]), \
             mock.patch.object(bs, "SYNC_ENABLED", True), \
             mock.patch.object(bs, "MIN_EXPECTED_PROPERTIES", 0), \
             mock.patch.object(bs, "MAX_DRIFT_RATIO", 1.0), \
             mock.patch.object(bs, "_worksheet"), \
             mock.patch.object(bs, "_apply"), \
             mock.patch.object(bs, "_deadletter") as dl:
            report = bs.sync(dry_run=False)
        self.assertFalse(report["ok"])
        self.assertEqual(len(report["unverified"]), 1)
        self.assertEqual(dl.call_args[0][0]["kind"], "verification_failed")

    def test_overlapping_runs_skip_rather_than_race(self):
        bs._write_lock.acquire()
        try:
            with mock.patch.object(bs, "SYNC_ENABLED", True), \
                 mock.patch.object(bs, "_worksheet") as ws:
                report = bs.sync(dry_run=False)
        finally:
            bs._write_lock.release()
        self.assertEqual(report["skipped"], "already_running")
        self.assertTrue(report["ok"])       # skipping is a correct outcome:
        ws.assert_not_called()              # the next run recomputes from source

    def test_an_exception_deadletters_and_reraises(self):
        with mock.patch.object(rec, "expected_budgets", side_effect=RuntimeError("hubspot down")), \
             mock.patch.object(bs, "SYNC_ENABLED", True), \
             mock.patch.object(bs, "_deadletter") as dl:
            with self.assertRaises(RuntimeError):
                bs.sync(dry_run=False)
        self.assertEqual(dl.call_args[0][0]["kind"], "exception")

    def test_the_lock_is_released_after_a_failure(self):
        """A leaked lock would silently stop every future run — the exact
        shape of failure this whole project exists to prevent."""
        with mock.patch.object(rec, "expected_budgets", side_effect=RuntimeError("boom")), \
             mock.patch.object(bs, "SYNC_ENABLED", True), \
             mock.patch.object(bs, "_deadletter"):
            with self.assertRaises(RuntimeError):
                bs.sync(dry_run=False)
        self.assertTrue(bs._write_lock.acquire(blocking=False))
        bs._write_lock.release()


class TargetResolution(unittest.TestCase):
    """Which tab a run writes. See docs/budget-sync-plan.md §4e."""

    def test_shadow_is_the_default(self):
        """A forgotten env var must not be the difference between a shadow
        write and a production one."""
        with mock.patch.object(bs, "SYNC_TARGET", "shadow"):
            self.assertEqual(bs.target_tab(), bs.SHADOW_TAB_NAME)

    def test_live_resolves_to_the_tab_fluency_reads(self):
        self.assertEqual(bs.target_tab("live"), rec.BUDGET_TAB_NAME)

    def test_case_is_tolerated(self):
        self.assertEqual(bs.target_tab("LIVE"), rec.BUDGET_TAB_NAME)

    def test_unrecognised_target_refuses_rather_than_defaulting(self):
        """A typo must abort, not silently pick a tab."""
        with self.assertRaises(bs.SyncAborted):
            bs.target_tab("liev")

    def test_the_two_tabs_are_different(self):
        self.assertNotEqual(bs.target_tab("live"), bs.target_tab("shadow"))


class BootstrapGuard(unittest.TestCase):
    """Bootstrap exempts the write ceilings, so it must be unreachable against
    the live tab — not merely discouraged there."""

    def test_bootstrap_against_the_live_tab_is_refused(self):
        with self.assertRaises(bs.SyncAborted) as cm:
            bs._assert_bootstrap_safe(rec.BUDGET_TAB_NAME)
        self.assertIn("live tab", str(cm.exception))

    def test_bootstrap_against_the_shadow_tab_is_allowed(self):
        bs._assert_bootstrap_safe(bs.SHADOW_TAB_NAME)      # does not raise

    def test_sync_refuses_a_live_bootstrap_before_reading_anything(self):
        """The guard fires before the HubSpot sweep and before any Sheets call.

        Checked by asserting the expensive calls never happened: a guard that
        only fires after a full portfolio read is a guard that already let the
        run begin.
        """
        with mock.patch.object(rec, "expected_budgets") as exp, \
             mock.patch.object(rec, "read_sheet_rows") as read, \
             mock.patch.object(bs, "_deadletter"):
            r = bs.sync(dry_run=False, target="live", bootstrap=True)
        self.assertFalse(r["ok"])
        self.assertIn("live tab", r["aborted"])
        exp.assert_not_called()
        read.assert_not_called()

    def test_bootstrap_exempts_the_write_ceilings(self):
        """~6,640 appends into an empty tab is the seed, not a runaway."""
        expected = _expected(*[(f"u{i}", f"P{i}", {}) for i in range(600)])
        plan_obj = {"updates": [], "skipped": [],
                    "appends": [[f"u{i}", "n", "l", "$1.00"] for i in range(6000)]}
        with mock.patch.object(bs, "MIN_EXPECTED_PROPERTIES", 500):
            with self.assertRaises(bs.SyncAborted):
                bs._preflight(expected, {}, plan_obj)          # normal run: refused
            bs._preflight(expected, {}, plan_obj, bootstrap=True)   # seed: allowed

    def test_bootstrap_still_enforces_the_volume_floor(self):
        """The floor guards against bad HubSpot data, which is tab-independent.

        A run that sees 12 properties is wrong wherever it is about to write.
        """
        expected = _expected(*[(f"u{i}", f"P{i}", {}) for i in range(12)])
        plan_obj = {"updates": [], "appends": [], "skipped": []}
        with mock.patch.object(bs, "MIN_EXPECTED_PROPERTIES", 500):
            with self.assertRaises(bs.SyncAborted) as cm:
                bs._preflight(expected, {}, plan_obj, bootstrap=True)
        self.assertIn("below the floor", str(cm.exception))


class TabThreading(unittest.TestCase):
    """The tab is a parameter, not a global — the parallel run reads both in
    one process, and a global would make the answer depend on call order."""

    def test_sync_reads_and_verifies_the_tab_it_writes(self):
        reads: list = []
        expected = _expected(("u1", "Citria", {"Geofence": "$500.00"}))
        rows = _rows(("u1", "Citria", {}))

        with mock.patch.object(rec, "expected_budgets", return_value=expected), \
             mock.patch.object(rec, "read_sheet_rows",
                               side_effect=lambda tab=None: (reads.append(tab), rows)[1]), \
             mock.patch.object(bs, "SYNC_ENABLED", True), \
             mock.patch.object(bs, "_apply") as apply_, \
             mock.patch.object(bs, "_worksheet") as ws, \
             mock.patch.object(bs, "MIN_EXPECTED_PROPERTIES", 0), \
             mock.patch.object(bs, "MAX_DRIFT_RATIO", 0):
            bs.sync(dry_run=False, target="shadow")

        apply_.assert_called_once()
        ws.assert_called_once_with(bs.SHADOW_TAB_NAME)
        # Read for the plan, then re-read for verification — both the shadow tab.
        self.assertEqual(reads, [bs.SHADOW_TAB_NAME, bs.SHADOW_TAB_NAME])

    def test_report_names_the_tab(self):
        expected = _expected(("u1", "Citria", {}))
        rows = _rows(("u1", "Citria", {}))
        with mock.patch.object(rec, "expected_budgets", return_value=expected), \
             mock.patch.object(rec, "read_sheet_rows", return_value=rows), \
             mock.patch.object(bs, "MIN_EXPECTED_PROPERTIES", 0):
            r = bs.sync(dry_run=True, target="shadow")
        self.assertEqual(r["tab"], bs.SHADOW_TAB_NAME)


if __name__ == "__main__":
    unittest.main()


class BootstrapHeader(unittest.TestCase):
    """parse_sheet skips row 1 as the header unconditionally. A tab without one
    silently loses its first data row on every read — and an empty shadow tab
    is exactly that case."""

    def test_detects_a_tab_with_no_header(self):
        self.assertTrue(bs.needs_header([]))
        self.assertTrue(bs.needs_header([[]]))
        self.assertTrue(bs.needs_header([["", "", "", ""]]))

    def test_a_populated_header_is_left_alone(self):
        self.assertFalse(bs.needs_header([rec.SHEET_HEADER]))

    def test_bootstrap_writes_the_header_before_the_data(self):
        expected = _expected(("u1", "Citria", {}))
        with mock.patch.object(rec, "expected_budgets", return_value=expected), \
             mock.patch.object(rec, "read_sheet_rows", return_value=[[]]), \
             mock.patch.object(bs, "SYNC_ENABLED", True), \
             mock.patch.object(bs, "MIN_EXPECTED_PROPERTIES", 0), \
             mock.patch.object(bs, "_worksheet"), \
             mock.patch.object(bs, "_verify", return_value=[]), \
             mock.patch.object(bs, "_apply") as apply_:
            r = bs.sync(dry_run=False, target="shadow", bootstrap=True)
        appends = apply_.call_args[0][1]["appends"]
        self.assertEqual(appends[0], rec.SHEET_HEADER)
        self.assertEqual(len(appends), 1 + len(rec.BUDGET_CHANNELS))
        self.assertTrue(r["wrote_header"])

    def test_a_normal_run_never_writes_a_header(self):
        """Only bootstrap does this. A routine run appending a new property to
        a populated tab must not insert a second header mid-sheet."""
        expected = _expected(("u1", "Citria", {}))
        rows = _rows(("u2", "Other", {}))
        with mock.patch.object(rec, "expected_budgets", return_value=expected), \
             mock.patch.object(rec, "read_sheet_rows", return_value=rows), \
             mock.patch.object(bs, "SYNC_ENABLED", True), \
             mock.patch.object(bs, "MIN_EXPECTED_PROPERTIES", 0), \
             mock.patch.object(bs, "MAX_DRIFT_RATIO", 0), \
             mock.patch.object(bs, "_worksheet"), \
             mock.patch.object(bs, "_verify", return_value=[]), \
             mock.patch.object(bs, "_apply") as apply_:
            bs.sync(dry_run=False, target="shadow")
        self.assertNotIn(rec.SHEET_HEADER, apply_.call_args[0][1]["appends"])
