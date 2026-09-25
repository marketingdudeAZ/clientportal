"""The four shadow-tab misses from Tara's 2026-09-24 review, as regressions.

  * Icon on Broadway — Management End Date entered wrong (end = start =
    2026-04-30); a new build closed 2026-09-19. Must stay in the sync.
  * Strata — returning property: ended 2025-05-27, new build closed
    2026-09-23 on the same record. Must come back.
  * Ridgecrest / Luna Villa — DISPO deals closed 2026-09-21. Every channel
    must go to $0.00, matching what the live action writes.
  * Account names — rebrands reach the sheet only when BUDGET_SYNC_NAMES is on.

Offline: HubSpot and the sheet are stubbed.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

import budget_reconcile as rec  # noqa: E402
import budget_sync as bs  # noqa: E402

LABELS = [l for _, l in rec.BUDGET_CHANNELS]
PID_SEARCH, LABEL_SEARCH = rec.BUDGET_CHANNELS[0]
HEADER = ["UUID", "Account Name", "Budget Group", "Budget"]


def _company(cid, ended=False, end=""):
    return {"id": cid, "name": f"co{cid}", "management_ended": ended, "management_end": end}


def _deal(did, name, close, stage="closedwon"):
    return {"id": did, "properties": {"dealname": name, "dealstage": stage,
                                      "closedate": close, "createdate": close}}


def _expected_for(companies, deals, deal_to_company, line_items):
    """Run expected_budgets() against stubbed HubSpot data."""
    import spend_sheet
    with mock.patch.object(spend_sheet, "_get_managed_companies",
                           lambda include_ended=False: [c for c in companies
                                                        if include_ended or not c["management_ended"]]), \
         mock.patch.object(spend_sheet, "_get_deal_associations", lambda ids: deal_to_company), \
         mock.patch.object(spend_sheet, "_batch_read_deals", lambda ids: deals), \
         mock.patch.object(rec, "_line_items_by_product", lambda ids: line_items), \
         mock.patch.object(rec, "_uuids_for", lambda ids: {i: f"u{i}" for i in ids}), \
         mock.patch.object(rec, "_company_names", lambda ids: {i: f"Name {i}" for i in ids}):
        return rec.expected_budgets()


class DispoAndReturning(unittest.TestCase):
    def test_wrong_end_date_with_a_later_deal_stays_active(self):
        # Icon on Broadway: end 2026-04-30, PMax build closed 2026-09-19.
        exp = _expected_for([_company("1", ended=True, end="2026-04-30")],
                            {"d1": _deal("d1", "Icon - PMax New Build", "2026-09-19T00:00:00Z")},
                            {"d1": "1"}, {"d1": {PID_SEARCH: 2500}})
        self.assertIn("u1", exp)
        self.assertEqual(exp["u1"]["budgets"][LABEL_SEARCH], "$2500.00")
        self.assertTrue(exp["u1"]["returning"])
        self.assertFalse(exp["u1"]["dispo"])

    def test_returning_property_comes_back(self):
        # Strata: ended 2025-05-27, new build 2026-09-23 on the same record.
        exp = _expected_for([_company("2", ended=True, end="2025-05-27")],
                            {"d2": _deal("d2", "Strata - New Account Build - 09/16/2026",
                                         "2026-09-23T00:00:00Z")},
                            {"d2": "2"}, {"d2": {PID_SEARCH: 3000}})
        self.assertEqual(exp["u2"]["budgets"][LABEL_SEARCH], "$3000.00")

    def test_ended_with_nothing_after_the_end_date_stays_excluded(self):
        exp = _expected_for([_company("3", ended=True, end="2026-06-01")],
                            {"d3": _deal("d3", "Old - Budget Change", "2026-05-01T00:00:00Z")},
                            {"d3": "3"}, {"d3": {PID_SEARCH: 900}})
        self.assertNotIn("u3", exp)

    def test_dispo_zeroes_every_channel(self):
        # Ridgecrest: DISPO closed 2026-09-21 (= its end date) → all $0.00.
        exp = _expected_for([_company("4", ended=True, end="2026-09-21")],
                            {"old": _deal("old", "Ridgecrest - Budget Change - 12/5/25", "2025-12-31T00:00:00Z"),
                             "dispo": _deal("dispo", "Ridgecrest - DISPO - September, 2026", "2026-09-21T00:00:00Z")},
                            {"old": "4", "dispo": "4"}, {"old": {PID_SEARCH: 3500}})
        self.assertTrue(exp["u4"]["dispo"])
        self.assertEqual(set(exp["u4"]["budgets"].values()), {"$0.00"})

    def test_dispo_on_a_still_managed_property_also_zeroes(self):
        exp = _expected_for([_company("5")],
                            {"d5": _deal("d5", "Luna Villa - Dispo - September 2026", "2026-09-21T00:00:00Z")},
                            {"d5": "5"}, {})
        self.assertEqual(set(exp["u5"]["budgets"].values()), {"$0.00"})

    def test_a_later_normal_deal_beats_an_older_dispo(self):
        exp = _expected_for([_company("6", ended=True, end="2025-05-27")],
                            {"a": _deal("a", "X - DISPO 5.27.25", "2025-05-27T00:00:00Z"),
                             "b": _deal("b", "X - New Account Build", "2026-09-23T00:00:00Z")},
                            {"a": "6", "b": "6"}, {"b": {PID_SEARCH: 100}})
        self.assertFalse(exp["u6"]["dispo"])
        self.assertEqual(exp["u6"]["budgets"][LABEL_SEARCH], "$100.00")

    def test_dispo_word_match_is_whole_word(self):
        for name, want in [("R - DISPO - Sept", True), ("L - Dispo - Sept", True),
                           ("X - Disposition", True), ("Disposable budget", False)]:
            self.assertEqual(rec._is_dispo({"properties": {"dealname": name}}), want, name)

    def test_end_digital_services_counts_as_a_dispo(self):
        self.assertTrue(rec._is_dispo({"properties": {"dealname": "Enclave - End Digital Services - 10/13"}}))

    def test_an_old_deal_after_the_end_date_is_not_returning(self):
        # Abby Court: end 2024-04-09, last deal 2024-04-17 — gone, not returning.
        self.assertFalse(rec._closed_after({"properties": {"closedate": "2024-04-17"}}, "2024-04-09"))

    def test_unparseable_dates_never_count_as_after(self):
        self.assertFalse(rec._closed_after({"properties": {"closedate": ""}}, "2026-01-01"))
        self.assertFalse(rec._closed_after({"properties": {"closedate": "2026-09-01"}}, ""))
        with mock.patch.object(rec, "RETURNING_WINDOW_DAYS", 100000):
            self.assertTrue(rec._closed_after({"properties": {"closedate": "2026-09-01"}}, "1745971200000"))


def _rows(*blocks):
    out = [HEADER]
    for uuid, name, vals in blocks:
        for label in LABELS:
            out.append([uuid, name, label, vals.get(label, rec.NOT_PURCHASED)])
    return out


def _exp(uuid, name, vals, **flags):
    return {uuid: {"company_id": uuid, "account_name": name, "deal_id": "d", "deal_name": "n",
                   "closedate": "", "budgets": {l: vals.get(l, rec.NOT_PURCHASED) for l in LABELS},
                   "dispo": flags.get("dispo", False), "returning": flags.get("returning", False)}}


class PlanningDispoAndNames(unittest.TestCase):
    def test_dispo_zeroes_existing_rows(self):
        rows = _rows(("R", "Ridgecrest", {LABEL_SEARCH: "$3500.00"}))
        actual, _ = rec.parse_sheet(rows)
        p = bs.plan(_exp("R", "Ridgecrest", {l: "$0.00" for l in LABELS}, dispo=True),
                    actual, bs.index_rows(rows))
        self.assertIn(("D2", "$0.00"), p["updates"])
        self.assertEqual(p["appends"], [])

    def test_dispo_never_appends_a_missing_property(self):
        p = bs.plan(_exp("GONE", "Gone", {l: "$0.00" for l in LABELS}, dispo=True), {}, {})
        self.assertEqual(p["appends"], [])
        self.assertEqual(rec.diff(_exp("GONE", "Gone", {}, dispo=True), {}), [])

    def test_renames_are_planned_for_every_row_of_the_property(self):
        rows = _rows(("S", "Arris Seahaven", {}))
        actual, _ = rec.parse_sheet(rows)
        p = bs.plan(_exp("S", "Oasis at Seahaven", {}), actual, bs.index_rows(rows),
                    names=rec.sheet_names(rows))
        self.assertEqual(len(p["name_updates"]), len(LABELS))
        self.assertEqual(p["renamed"], [{"uuid": "S", "from": "Arris Seahaven", "to": "Oasis at Seahaven"}])

    def test_no_names_passed_means_no_renames(self):
        rows = _rows(("S", "Arris Seahaven", {}))
        actual, _ = rec.parse_sheet(rows)
        p = bs.plan(_exp("S", "Oasis at Seahaven", {}), actual, bs.index_rows(rows))
        self.assertEqual(p["name_updates"], [])

    def test_names_are_not_written_when_the_flag_is_off(self):
        ws = mock.Mock()
        with mock.patch.object(bs, "SYNC_NAMES", False):
            bs._apply(ws, {"updates": [], "appends": [], "name_updates": [("B2", "New")]})
        ws.batch_update.assert_not_called()

    def test_names_are_written_when_the_flag_is_on(self):
        ws = mock.Mock()
        with mock.patch.object(bs, "SYNC_NAMES", True):
            bs._apply(ws, {"updates": [], "appends": [], "name_updates": [("B2", "New")]})
        ws.batch_update.assert_called_once()

    def test_rename_ceiling_aborts_when_names_are_on(self):
        renamed = [{"uuid": str(i), "from": "a", "to": "b"} for i in range(30)]
        with mock.patch.object(bs, "SYNC_NAMES", True), \
             mock.patch.object(bs, "MAX_NAME_PROPERTIES", 25), \
             mock.patch.object(bs, "MIN_EXPECTED_PROPERTIES", 0):
            with self.assertRaises(bs.SyncAborted):
                bs._preflight({"x": {}}, {}, {"updates": [], "appends": [], "renamed": renamed})

    def test_rename_ceiling_ignored_when_names_are_off(self):
        renamed = [{"uuid": str(i), "from": "a", "to": "b"} for i in range(30)]
        with mock.patch.object(bs, "SYNC_NAMES", False), \
             mock.patch.object(bs, "MIN_EXPECTED_PROPERTIES", 0):
            bs._preflight({"x": {"budgets": {l: rec.NOT_PURCHASED for l in LABELS}}}, {},
                          {"updates": [], "appends": [], "renamed": renamed})


if __name__ == "__main__":
    unittest.main()


class FailClosed(unittest.TestCase):
    """A HubSpot batch that can't be read must stop the run, never blank budgets."""

    def test_persistent_failure_raises(self):
        import requests
        with mock.patch.object(requests, "post", side_effect=requests.ConnectionError("reset")), \
             mock.patch("time.sleep"):
            with self.assertRaises(rec.HubSpotIncomplete):
                rec._line_items_by_product(["1", "2"])

    def test_transient_failure_is_retried(self):
        import requests
        ok = mock.Mock(status_code=200, json=lambda: {"results": []})
        with mock.patch.object(requests, "post", side_effect=[requests.ConnectionError("reset"), ok]), \
             mock.patch("time.sleep"):
            self.assertEqual(rec._line_items_by_product(["1"]), {})

    def test_rate_limit_is_retried_then_fails_closed(self):
        import requests
        limited = mock.Mock(status_code=429, text="slow down")
        with mock.patch.object(requests, "post", return_value=limited), mock.patch("time.sleep"):
            with self.assertRaises(rec.HubSpotIncomplete):
                rec._line_items_by_product(["1"])
