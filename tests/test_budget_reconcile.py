"""Tests for budget_reconcile — the Fluency budget sheet drift detector.

Everything here is offline. The HubSpot and Sheets boundaries are the only
I/O in the module and they are exercised through injected data, so these tests
run without credentials and without touching the live sheet.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

import budget_reconcile as br  # noqa: E402


HEADER = ["UUID", "Account Name", "Budget Group", "Budget"]


def _sheet_rows(uuid, name, overrides=None, channels=None):
    """Build a well-formed 10-row block for one property."""
    overrides = overrides or {}
    labels = channels if channels is not None else [l for _, l in br.BUDGET_CHANNELS]
    return [[uuid, name, label, overrides.get(label, br.NOT_PURCHASED)]
            for label in labels]


def _expected(uuid, name, budgets=None, deal_id="d1"):
    full = {label: br.NOT_PURCHASED for _, label in br.BUDGET_CHANNELS}
    full.update(budgets or {})
    return {uuid: {"account_name": name, "deal_id": deal_id,
                   "deal_name": f"{name} - Budget Change",
                   "closedate": "2026-08-01T00:00:00Z", "budgets": full}}


class MoneyFormatting(unittest.TestCase):
    """The writer's exact format. A mismatch here reports every property as
    drifted, so these are the load-bearing assertions in the file."""

    def test_matches_tofixed_two(self):
        self.assertEqual(br.format_money(3500), "$3500.00")
        self.assertEqual(br.format_money("3500"), "$3500.00")
        self.assertEqual(br.format_money(1234.5), "$1234.50")

    def test_no_thousands_separator(self):
        # `$${parseFloat(x).toFixed(2)}` never inserts a comma.
        self.assertEqual(br.format_money(12000), "$12000.00")
        self.assertNotIn(",", br.format_money(12000))

    def test_zero_and_null_are_zero_dollars_not_the_sentinel(self):
        # The action writes '$0.00' when a line item EXISTS with a zero amount,
        # and '$ -' only when the line item is absent. Conflating them would
        # hide a cancelled channel.
        self.assertEqual(br.format_money(0), "$0.00")
        self.assertEqual(br.format_money("0"), "$0.00")
        self.assertEqual(br.format_money(None), "$0.00")
        self.assertEqual(br.format_money(""), "$0.00")

    def test_unparseable_amount_is_zero_not_a_crash(self):
        self.assertEqual(br.format_money("abc"), "$0.00")

    def test_sentinel_is_distinct_from_zero(self):
        self.assertNotEqual(br.NOT_PURCHASED, br.format_money(0))


class SheetParsing(unittest.TestCase):
    def test_parses_a_clean_property(self):
        rows = [HEADER] + _sheet_rows("u1", "Citria", {"Paid Search Ads": "$3500.00"})
        actual, drift = br.parse_sheet(rows)
        self.assertEqual(drift, [])
        self.assertEqual(actual["u1"]["Paid Search Ads"], "$3500.00")
        self.assertEqual(len(actual["u1"]), br.EXPECTED_ROWS_PER_PROPERTY)

    def test_blank_uuid_rows_are_ignored(self):
        rows = [HEADER] + [["", "", "", ""]] + _sheet_rows("u1", "Citria")
        actual, drift = br.parse_sheet(rows)
        self.assertEqual(list(actual), ["u1"])
        self.assertEqual(drift, [])

    def test_short_rows_do_not_raise(self):
        rows = [HEADER, ["u1", "Citria"]]
        actual, drift = br.parse_sheet(rows)
        # One recognised-label-less row → unknown channel, not a crash.
        self.assertTrue(any(d["kind"] == br.KIND_UNKNOWN_CHANNEL for d in drift))
        self.assertEqual(actual, {})

    def test_duplicate_rows_are_reported(self):
        """The fingerprint of the delete/append race."""
        rows = [HEADER] + _sheet_rows("u1", "Citria")
        rows.append(["u1", "Citria", "Geofence", "$500.00"])
        _, drift = br.parse_sheet(rows)
        dupes = [d for d in drift if d["kind"] == br.KIND_DUPLICATE_ROW]
        self.assertEqual(len(dupes), 1)
        self.assertEqual(dupes[0]["channel"], "Geofence")

    def test_missing_rows_are_reported_as_row_count_drift(self):
        """A property left with fewer than 10 rows — what a timeout between
        the delete and the append produces."""
        partial = _sheet_rows("u1", "Citria")[:4]
        _, drift = br.parse_sheet([HEADER] + partial)
        counts = [d for d in drift if d["kind"] == br.KIND_ROW_COUNT]
        self.assertEqual(len(counts), 1)
        self.assertEqual(counts[0]["found"], 4)
        self.assertEqual(counts[0]["expected"], br.EXPECTED_ROWS_PER_PROPERTY)

    def test_unknown_channel_label_is_reported(self):
        rows = [HEADER] + _sheet_rows("u1", "Citria")
        rows.append(["u1", "Citria", "Crypto Ads", "$99.00"])
        _, drift = br.parse_sheet(rows)
        unknown = [d for d in drift if d["kind"] == br.KIND_UNKNOWN_CHANNEL]
        self.assertEqual(unknown[0]["channel"], "Crypto Ads")

    def test_whitespace_is_stripped_but_sentinels_are_not_normalised(self):
        rows = [HEADER] + _sheet_rows("u1", "Citria", {"Geofence": "  $500.00  "})
        actual, _ = br.parse_sheet(rows)
        self.assertEqual(actual["u1"]["Geofence"], "$500.00")

    def test_a_different_sentinel_spelling_is_not_silently_accepted(self):
        # '$-' is not '$ -'. If the writer changes its sentinel we want to see it.
        rows = [HEADER] + _sheet_rows("u1", "Citria", {"Geofence": "$-"})
        actual, _ = br.parse_sheet(rows)
        drift = br.diff(_expected("u1", "Citria"), actual)
        self.assertTrue(any(d["kind"] == br.KIND_VALUE_MISMATCH for d in drift))


class Diffing(unittest.TestCase):
    def test_matching_state_produces_no_drift(self):
        exp = _expected("u1", "Citria", {"Paid Search Ads": "$3500.00"})
        actual, _ = br.parse_sheet(
            [HEADER] + _sheet_rows("u1", "Citria", {"Paid Search Ads": "$3500.00"}))
        self.assertEqual(br.diff(exp, actual), [])

    def test_value_mismatch_carries_both_sides_and_the_deal(self):
        """This is the 8/1 incident's signature: HubSpot moved, sheet didn't."""
        exp = _expected("u1", "Citria", {"Paid Search Ads": "$4000.00"})
        actual, _ = br.parse_sheet(
            [HEADER] + _sheet_rows("u1", "Citria", {"Paid Search Ads": "$3500.00"}))
        drift = br.diff(exp, actual)
        self.assertEqual(len(drift), 1)
        d = drift[0]
        self.assertEqual(d["kind"], br.KIND_VALUE_MISMATCH)
        self.assertEqual(d["sheet_value"], "$3500.00")
        self.assertEqual(d["expected_value"], "$4000.00")
        self.assertEqual(d["channel"], "Paid Search Ads")
        # The deal is what a human needs to act — without it the report is a
        # riddle rather than a work item.
        self.assertEqual(d["deal_id"], "d1")
        self.assertEqual(d["closedate"], "2026-08-01T00:00:00Z")

    def test_property_absent_from_the_sheet_entirely(self):
        drift = br.diff(_expected("u1", "Citria"), {})
        self.assertEqual(drift[0]["kind"], br.KIND_MISSING_PROPERTY)
        self.assertIn("expected_budgets", drift[0])

    def test_missing_single_channel(self):
        labels = [l for _, l in br.BUDGET_CHANNELS][:-1]
        actual, _ = br.parse_sheet(
            [HEADER] + _sheet_rows("u1", "Citria", channels=labels))
        drift = br.diff(_expected("u1", "Citria"), actual)
        missing = [d for d in drift if d["kind"] == br.KIND_MISSING_CHANNEL]
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]["channel"], br.BUDGET_CHANNELS[-1][1])

    def test_orphan_property_in_the_sheet_is_reported_not_corrected(self):
        actual, _ = br.parse_sheet([HEADER] + _sheet_rows("ghost", "Old Property"))
        drift = br.diff({}, actual)
        self.assertEqual(len(drift), 1)
        self.assertEqual(drift[0]["kind"], br.KIND_ORPHAN_PROPERTY)
        self.assertEqual(drift[0]["uuid"], "ghost")

    def test_cancelled_channel_zero_vs_absent_are_different_drift(self):
        """A channel cancelled to $0.00 must not read as 'never purchased'."""
        exp = _expected("u1", "Citria", {"Geofence": "$0.00"})
        actual, _ = br.parse_sheet(
            [HEADER] + _sheet_rows("u1", "Citria"))  # Geofence == '$ -'
        drift = br.diff(exp, actual)
        self.assertEqual(len(drift), 1)
        self.assertEqual(drift[0]["sheet_value"], br.NOT_PURCHASED)
        self.assertEqual(drift[0]["expected_value"], "$0.00")

    def test_multiple_properties_are_independent(self):
        exp = {**_expected("u1", "A", {"Geofence": "$100.00"}),
               **_expected("u2", "B", {"Geofence": "$200.00"}, deal_id="d2")}
        rows = ([HEADER]
                + _sheet_rows("u1", "A", {"Geofence": "$100.00"})
                + _sheet_rows("u2", "B", {"Geofence": "$999.00"}))
        actual, _ = br.parse_sheet(rows)
        drift = br.diff(exp, actual)
        self.assertEqual(len(drift), 1)
        self.assertEqual(drift[0]["uuid"], "u2")


class WonDealSelection(unittest.TestCase):
    def test_closedwon_stage_is_won(self):
        self.assertTrue(br._is_won({"properties": {"dealstage": "closedwon"}}))

    def test_pipeline_specific_won_stage_is_won(self):
        self.assertTrue(br._is_won({"properties": {"dealstage": "1389723181-won"}}))

    def test_open_stage_is_not_won(self):
        self.assertFalse(br._is_won({"properties": {"dealstage": "presentationscheduled"}}))
        self.assertFalse(br._is_won({"properties": {"dealstage": "closedlost"}}))

    def test_sort_key_prefers_closedate_and_orders_chronologically(self):
        older = {"properties": {"closedate": "2026-07-01T00:00:00Z"}}
        newer = {"properties": {"closedate": "2026-08-01T00:00:00Z"}}
        self.assertLess(br._close_sort_key(older), br._close_sort_key(newer))

    def test_sort_key_falls_back_to_createdate(self):
        d = {"properties": {"createdate": "2026-06-01T00:00:00Z"}}
        self.assertEqual(br._close_sort_key(d), "2026-06-01T00:00:00Z")

    def test_missing_dates_sort_last_rather_than_raising(self):
        self.assertEqual(br._close_sort_key({"properties": {}}), "")


class ChannelRegistry(unittest.TestCase):
    def test_labels_carry_no_asterisk(self):
        """Fluency joins on these strings; the action writes the catalog name
        with the asterisk stripped."""
        for _, label in br.BUDGET_CHANNELS:
            self.assertNotIn("*", label)

    def test_product_ids_are_unique(self):
        ids = [pid for pid, _ in br.BUDGET_CHANNELS]
        self.assertEqual(len(ids), len(set(ids)))

    def test_labels_are_unique(self):
        labels = [l for _, l in br.BUDGET_CHANNELS]
        self.assertEqual(len(labels), len(set(labels)))

    def test_ids_match_the_product_catalog(self):
        """Guards against the two lists drifting apart."""
        import product_catalog
        catalog = set(product_catalog.CHANNEL_PRODUCT_MAP.values())
        for pid, label in br.BUDGET_CHANNELS:
            self.assertIn(pid, catalog, f"{label} ({pid}) is not in the catalog")

    def test_ctv_is_excluded_by_default(self):
        """Sold and quoted, but not in the action's BUDGET_NAMES. Expecting it
        would report a false missing channel on every property."""
        self.assertNotIn("42010615327", [pid for pid, _ in br.BUDGET_CHANNELS])


if __name__ == "__main__":
    unittest.main()


class MoneyComparison(unittest.TestCase):
    """Found by the first live reconcile, 2026-08-13.

    gspread's get_all_values() returns DISPLAYED values, and the budget column
    carries a currency number format — so Sheets hands back '$1,000.00' for a
    cell written as '$1000.00'. Raw string comparison reported false drift on
    every property with a budget of $1,000 or more: 29 of that run's 70
    'value mismatches' were this and nothing else.
    """

    def test_thousands_separator_does_not_count_as_drift(self):
        self.assertTrue(br.same_money("$1,000.00", "$1000.00"))
        self.assertTrue(br.same_money("$12,345.67", "$12345.67"))

    def test_genuinely_different_amounts_still_differ(self):
        self.assertFalse(br.same_money("$1,700.00", "$1200.00"))

    def test_not_purchased_stays_distinct_from_zero(self):
        """'$ -' means the property does not buy the channel; '$0.00' means the
        line item exists and is zeroed. Collapsing them hides real drift."""
        self.assertFalse(br.same_money(br.NOT_PURCHASED, "$0.00"))

    def test_surrounding_whitespace_is_ignored(self):
        self.assertTrue(br.same_money(" $500.00 ", "$500.00"))

    def test_diff_no_longer_reports_formatted_values_as_drift(self):
        expected = {"u1": {"company_id": "1", "account_name": "A",
                           "deal_id": "d", "deal_name": "d", "closedate": "",
                           "budgets": {l: br.NOT_PURCHASED
                                       for _, l in br.BUDGET_CHANNELS}}}
        label = br.BUDGET_CHANNELS[0][1]
        expected["u1"]["budgets"][label] = "$2060.00"
        actual = {"u1": {l: br.NOT_PURCHASED for _, l in br.BUDGET_CHANNELS}}
        actual["u1"][label] = "$2,060.00"          # what Sheets returns
        self.assertEqual(br.diff(expected, actual), [])
