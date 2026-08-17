"""Tests for budget_compare — the three-way comparison of the parallel run.

Offline. classify() is pure, so most of this needs no stubbing at all; the two
Sheets reads in compare() are patched. Nothing here can reach a live sheet.

The behaviour under test, stated once: a difference between the two tabs is not
interesting on its own. What is interesting is which tab disagrees with HubSpot,
and only "the new system is wrong where the old one is right" is worth waking
somebody for.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

import budget_compare as bc  # noqa: E402
import budget_reconcile as rec  # noqa: E402


HEADER = ["UUID", "Account Name", "Budget Group", "Budget"]
LABELS = [l for _, l in rec.BUDGET_CHANNELS]
PAID_SEARCH = LABELS[0]
PMAX = LABELS[1]


def _expected(*specs):
    """{uuid: expected-budget block}, mirroring budget_reconcile.expected_budgets."""
    out = {}
    for uuid, name, budgets in specs:
        full = {l: rec.NOT_PURCHASED for l in LABELS}
        full.update(budgets)
        out[uuid] = {"account_name": name, "deal_id": f"d-{uuid}",
                     "deal_name": f"{name} deal",
                     "closedate": "2026-08-01T00:00:00Z", "budgets": full}
    return out


def _tab(*specs):
    """{uuid: {label: value}}, the parse_sheet half for one tab."""
    out = {}
    for uuid, budgets in specs:
        full = {l: rec.NOT_PURCHASED for l in LABELS}
        full.update(budgets)
        out[uuid] = full
    return out


def _rows(*specs):
    """Raw sheet rows, for the compare() integration tests."""
    out = [HEADER]
    for uuid, name, budgets in specs:
        for label in LABELS:
            out.append([uuid, name, label,
                        budgets.get(label, rec.NOT_PURCHASED)])
    return out


class Verdicts(unittest.TestCase):
    """The four-way classification, one test per row of the §4e table."""

    def test_both_matching_hubspot_is_agreement(self):
        exp = _expected(("u1", "Citria", {PAID_SEARCH: "$2,060.00"}))
        live = _tab(("u1", {PAID_SEARCH: "$2,060.00"}))
        shadow = _tab(("u1", {PAID_SEARCH: "$2,060.00"}))
        out = bc.classify(exp, live, shadow)
        self.assertEqual(out["properties"]["u1"]["verdict"], bc.VERDICT_BOTH_RIGHT)
        self.assertEqual(out["counts"][bc.VERDICT_BOTH_RIGHT], 1)

    def test_old_system_wrong_is_expected_and_does_not_alert(self):
        """The 8/1 failure shape: HubSpot and the new tab agree, the live tab lags.

        This is the finding the parallel run exists to produce. It must NOT be
        flagworthy, or the alert channel opens with 46 tickets that all mean
        'working as intended'.
        """
        exp = _expected(("u1", "Citria", {PAID_SEARCH: "$2,060.00"}))
        live = _tab(("u1", {PAID_SEARCH: "$1,500.00"}))     # stale
        shadow = _tab(("u1", {PAID_SEARCH: "$2,060.00"}))   # correct
        out = bc.classify(exp, live, shadow)
        self.assertEqual(out["properties"]["u1"]["verdict"], bc.VERDICT_OLD_WRONG)
        self.assertEqual(bc.flagworthy(out), [])

    def test_new_system_wrong_is_the_only_alerting_verdict(self):
        exp = _expected(("u1", "Citria", {PAID_SEARCH: "$2,060.00"}))
        live = _tab(("u1", {PAID_SEARCH: "$2,060.00"}))     # correct
        shadow = _tab(("u1", {PAID_SEARCH: "$9,999.00"}))   # wrong
        out = bc.classify(exp, live, shadow)
        self.assertEqual(out["properties"]["u1"]["verdict"], bc.VERDICT_NEW_WRONG)
        self.assertEqual([p["uuid"] for p in bc.flagworthy(out)], ["u1"])

    def test_both_wrong_is_reported_but_not_flagged(self):
        exp = _expected(("u1", "Citria", {PAID_SEARCH: "$2,060.00"}))
        live = _tab(("u1", {PAID_SEARCH: "$1.00"}))
        shadow = _tab(("u1", {PAID_SEARCH: "$2.00"}))
        out = bc.classify(exp, live, shadow)
        self.assertEqual(out["properties"]["u1"]["verdict"], bc.VERDICT_BOTH_WRONG)
        self.assertEqual(bc.flagworthy(out), [])


class MissingRows(unittest.TestCase):
    def test_absent_shadow_row_counts_as_the_new_system_being_wrong(self):
        """An unseeded shadow tab is not 'no opinion' — it is a wrong answer.

        This is the false-pass the plan's §4e seeding argument turns on: if a
        missing row were treated as agreement, a property the loop cannot
        compute would silently certify as correct.
        """
        exp = _expected(("u1", "Citria", {PAID_SEARCH: "$2,060.00"}))
        live = _tab(("u1", {PAID_SEARCH: "$2,060.00"}))
        out = bc.classify(exp, live, {})
        self.assertEqual(out["properties"]["u1"]["verdict"], bc.VERDICT_NEW_WRONG)

    def test_missing_row_is_distinct_from_not_purchased(self):
        """'$ -' is a row saying the channel is not bought. Absent is not that."""
        exp = _expected(("u1", "Citria", {}))               # all NOT_PURCHASED
        live = _tab(("u1", {}))                             # explicit '$ -' rows
        out = bc.classify(exp, live, {})
        self.assertEqual(out["properties"]["u1"]["verdict"], bc.VERDICT_NEW_WRONG)
        cell = out["properties"]["u1"]["cells"][0]
        self.assertEqual(cell["live"], rec.NOT_PURCHASED)
        self.assertIsNone(cell["shadow"])

    def test_empty_shadow_tab_makes_every_property_new_wrong(self):
        exp = _expected(("u1", "A", {}), ("u2", "B", {}))
        live = _tab(("u1", {}), ("u2", {}))
        out = bc.classify(exp, live, {})
        self.assertEqual(out["counts"][bc.VERDICT_NEW_WRONG], 2)


class PropertyRollup(unittest.TestCase):
    def test_worst_cell_decides_the_property(self):
        """One new_wrong channel outranks nine agreeing ones."""
        exp = _expected(("u1", "Citria",
                         {PAID_SEARCH: "$100.00", PMAX: "$200.00"}))
        live = _tab(("u1", {PAID_SEARCH: "$100.00", PMAX: "$200.00"}))
        shadow = _tab(("u1", {PAID_SEARCH: "$100.00", PMAX: "$999.00"}))
        out = bc.classify(exp, live, shadow)
        self.assertEqual(out["properties"]["u1"]["verdict"], bc.VERDICT_NEW_WRONG)

    def test_new_wrong_outranks_old_wrong_on_the_same_property(self):
        exp = _expected(("u1", "Citria",
                         {PAID_SEARCH: "$100.00", PMAX: "$200.00"}))
        live = _tab(("u1", {PAID_SEARCH: "$1.00", PMAX: "$200.00"}))     # old wrong here
        shadow = _tab(("u1", {PAID_SEARCH: "$100.00", PMAX: "$2.00"}))   # new wrong here
        out = bc.classify(exp, live, shadow)
        self.assertEqual(out["properties"]["u1"]["verdict"], bc.VERDICT_NEW_WRONG)

    def test_agreeing_channels_are_not_listed_as_cells(self):
        exp = _expected(("u1", "Citria", {PAID_SEARCH: "$100.00"}))
        live = _tab(("u1", {PAID_SEARCH: "$100.00"}))
        shadow = _tab(("u1", {PAID_SEARCH: "$999.00"}))
        cells = bc.classify(exp, live, shadow)["properties"]["u1"]["cells"]
        self.assertEqual([c["channel"] for c in cells], [PAID_SEARCH])


class Orphans(unittest.TestCase):
    def test_uuid_in_a_tab_but_not_hubspot_is_an_orphan_not_an_alert(self):
        exp = _expected(("u1", "Citria", {}))
        live = _tab(("u1", {}), ("gone", {}))
        shadow = _tab(("u1", {}))
        out = bc.classify(exp, live, shadow)
        self.assertEqual([o["uuid"] for o in out["orphans"]], ["gone"])
        self.assertNotIn("gone", out["properties"])
        self.assertEqual(bc.flagworthy(out), [])

    def test_orphan_records_which_tabs_it_appears_in(self):
        out = bc.classify({}, _tab(("x", {})), _tab(("x", {}), ("y", {})))
        by_uuid = {o["uuid"]: o for o in out["orphans"]}
        self.assertTrue(by_uuid["x"]["in_live"] and by_uuid["x"]["in_shadow"])
        self.assertFalse(by_uuid["y"]["in_live"])


class FlagOrdering(unittest.TestCase):
    def test_flagworthy_is_sorted_by_account_name(self):
        exp = _expected(("u1", "Zulu", {PAID_SEARCH: "$1.00"}),
                        ("u2", "Alpha", {PAID_SEARCH: "$1.00"}))
        live = _tab(("u1", {PAID_SEARCH: "$1.00"}), ("u2", {PAID_SEARCH: "$1.00"}))
        shadow = _tab(("u1", {PAID_SEARCH: "$9.00"}), ("u2", {PAID_SEARCH: "$9.00"}))
        names = [p["account_name"] for p in bc.flagworthy(bc.classify(exp, live, shadow))]
        self.assertEqual(names, ["Alpha", "Zulu"])


class CompareEndToEnd(unittest.TestCase):
    """compare() with both Sheets reads stubbed."""

    def _run(self, expected, live_rows, shadow_rows, **env):
        def fake_read(tab=None):
            return shadow_rows if "SHADOW" in (tab or "") else live_rows

        with mock.patch.object(rec, "expected_budgets", return_value=expected), \
             mock.patch.object(rec, "read_sheet_rows", side_effect=fake_read), \
             mock.patch.dict(os.environ, env):
            return bc.compare()

    def test_reports_ok_when_the_new_system_is_never_uniquely_wrong(self):
        exp = _expected(("u1", "Citria", {PAID_SEARCH: "$2,060.00"}))
        live = _rows(("u1", "Citria", {PAID_SEARCH: "$1,500.00"}))    # stale
        shadow = _rows(("u1", "Citria", {PAID_SEARCH: "$2,060.00"}))  # right
        r = self._run(exp, live, shadow)
        self.assertTrue(r["ok"])
        self.assertEqual(r["counts"][bc.VERDICT_OLD_WRONG], 1)
        self.assertEqual(r["new_wrong"], [])

    def test_not_ok_when_the_new_system_is_wrong(self):
        exp = _expected(("u1", "Citria", {PAID_SEARCH: "$2,060.00"}))
        live = _rows(("u1", "Citria", {PAID_SEARCH: "$2,060.00"}))
        shadow = _rows(("u1", "Citria", {PAID_SEARCH: "$9.00"}))
        r = self._run(exp, live, shadow)
        self.assertFalse(r["ok"])
        self.assertEqual(r["new_wrong_count"], 1)

    def test_reads_the_two_tabs_by_name(self):
        exp = _expected(("u1", "Citria", {}))
        rows = _rows(("u1", "Citria", {}))
        seen = []

        def fake_read(tab=None):
            seen.append(tab)
            return rows

        with mock.patch.object(rec, "expected_budgets", return_value=exp), \
             mock.patch.object(rec, "read_sheet_rows", side_effect=fake_read):
            r = bc.compare()
        self.assertEqual(seen, [r["live_tab"], r["shadow_tab"]])
        self.assertNotEqual(r["live_tab"], r["shadow_tab"])

    def test_flood_ceiling_suppresses_the_flag_list_but_not_the_count(self):
        """Above the ceiling: one summary, no per-property flags.

        The count survives because "we suppressed 200" and "there were none"
        must never render identically.
        """
        specs = [(f"u{i}", f"P{i}", {PAID_SEARCH: "$1.00"}) for i in range(5)]
        exp = _expected(*specs)
        live = _rows(*[(u, n, b) for u, n, b in specs])
        shadow = _rows(*[(u, n, {PAID_SEARCH: "$9.00"}) for u, n, _ in specs])
        # MAX_FLAGS is read from env at import, so patch the module attribute.
        with mock.patch.object(bc, "MAX_FLAGS", 2):
            r = self._run(exp, live, shadow)
        self.assertTrue(r["flood"])
        self.assertEqual(r["new_wrong"], [])
        self.assertEqual(r["new_wrong_count"], 5)

    def test_under_the_ceiling_the_flags_are_listed(self):
        specs = [(f"u{i}", f"P{i}", {PAID_SEARCH: "$1.00"}) for i in range(2)]
        exp = _expected(*specs)
        live = _rows(*[(u, n, b) for u, n, b in specs])
        shadow = _rows(*[(u, n, {PAID_SEARCH: "$9.00"}) for u, n, _ in specs])
        with mock.patch.object(bc, "MAX_FLAGS", 25):
            r = self._run(exp, live, shadow)
        self.assertFalse(r["flood"])
        self.assertEqual(len(r["new_wrong"]), 2)

    def test_writes_nothing(self):
        """The whole module must be safe to run by hand in production."""
        exp = _expected(("u1", "Citria", {}))
        rows = _rows(("u1", "Citria", {}))
        with mock.patch.object(rec, "expected_budgets", return_value=exp), \
             mock.patch.object(rec, "read_sheet_rows", return_value=rows), \
             mock.patch("budget_sync._rw_client") as rw:
            bc.compare()
        rw.assert_not_called()


if __name__ == "__main__":
    unittest.main()
