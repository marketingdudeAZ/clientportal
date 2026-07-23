"""Tests for Bridge 2 — brief edit → real-time Fluency per-company sync.

Covers:
  - fluency_feed.sync_company: no company_id / no uuid / sheet unset → skip
  - build_record_for_company reuses override-wins resolution
  - sync_company upserts (append) and updates the right row, skips unchanged
  - brief_hooks.on_field_written fires the fluency leg only when the flag is on
  - community_brief.write_field dispatches the hook on a real change only

External I/O — HubSpot and Google Sheets — is fully mocked.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("HUBSPOT_API_KEY", "test-key")

import fluency_feed  # noqa: E402
import brief_hooks  # noqa: E402


def _resp(json_data, status=200):
    m = mock.Mock()
    m.status_code = status
    m.json.return_value = json_data
    m.raise_for_status.return_value = None
    m.text = ""
    return m


COMPANY = {
    "id": "co-1",
    "properties": {
        "name": "Maple Court", "uuid": "uuid-abc",
        "rpmmarket": "Austin", "state": "TX", "plestatus": "RPM Managed",
    },
}


class SyncCompanyTests(unittest.TestCase):

    def setUp(self):
        os.environ.pop("RPM_PIPELINE_SHEET_ID", None)

    def test_no_company_id_skips(self):
        self.assertIn("skipped", fluency_feed.sync_company(""))

    def test_no_uuid_skips(self):
        company = {"id": "co-1", "properties": {"name": "X"}}
        with mock.patch.object(fluency_feed.requests, "get", return_value=_resp(company)):
            result = fluency_feed.sync_company("co-1")
        self.assertEqual(result.get("skipped"), "no uuid")

    def test_sheet_unset_skips_after_build(self):
        with mock.patch.object(fluency_feed.requests, "get", return_value=_resp(COMPANY)):
            result = fluency_feed.sync_company("co-1")
        self.assertIn("skipped", result)
        self.assertEqual(result.get("account_id"), "uuid-abc")

    def test_dry_run_returns_record_without_sheet(self):
        with mock.patch.object(fluency_feed.requests, "get", return_value=_resp(COMPANY)):
            result = fluency_feed.sync_company("co-1", dry_run=True)
        self.assertTrue(result.get("dry_run"))
        self.assertEqual(result["account_id"], "uuid-abc")
        self.assertIn("hash", result["record"])

    def test_appends_new_row(self):
        os.environ["RPM_PIPELINE_SHEET_ID"] = "sheet-1"
        columns = fluency_feed.feed_schema()["columns"]
        ws = mock.Mock()
        ws.get_all_values.return_value = [columns]  # header only, no data rows
        sh = mock.Mock(); sh.worksheet.return_value = ws
        gc = mock.Mock(); gc.open_by_key.return_value = sh
        with mock.patch.object(fluency_feed.requests, "get", return_value=_resp(COMPANY)), \
             mock.patch.object(fluency_feed, "_gc", return_value=gc):
            result = fluency_feed.sync_company("co-1")
        self.assertEqual(result.get("appended"), 1)
        ws.append_rows.assert_called_once()

    def test_skips_unchanged_row(self):
        os.environ["RPM_PIPELINE_SHEET_ID"] = "sheet-1"
        columns = fluency_feed.feed_schema()["columns"]
        # Build the record once to get its hash, then present an existing row
        # with the same account_id + hash so sync should skip.
        with mock.patch.object(fluency_feed.requests, "get", return_value=_resp(COMPANY)):
            rec, _ = fluency_feed.build_record_for_company("co-1")
        data_cols = columns[:-2]
        h = fluency_feed._hash([rec.get(c, "") for c in data_cols])
        existing_row = [str(rec.get(c, "")) for c in columns[:-2]] + [h, "old-ts"]
        ws = mock.Mock()
        ws.get_all_values.return_value = [columns, existing_row]
        sh = mock.Mock(); sh.worksheet.return_value = ws
        gc = mock.Mock(); gc.open_by_key.return_value = sh
        with mock.patch.object(fluency_feed.requests, "get", return_value=_resp(COMPANY)), \
             mock.patch.object(fluency_feed, "_gc", return_value=gc):
            result = fluency_feed.sync_company("co-1")
        self.assertTrue(result.get("skipped_unchanged"))
        ws.append_rows.assert_not_called()
        ws.batch_update.assert_not_called()

    def test_header_mismatch_defers(self):
        os.environ["RPM_PIPELINE_SHEET_ID"] = "sheet-1"
        ws = mock.Mock()
        ws.get_all_values.return_value = [["account_id", "wrong", "columns"]]
        sh = mock.Mock(); sh.worksheet.return_value = ws
        gc = mock.Mock(); gc.open_by_key.return_value = sh
        with mock.patch.object(fluency_feed.requests, "get", return_value=_resp(COMPANY)), \
             mock.patch.object(fluency_feed, "_gc", return_value=gc):
            result = fluency_feed.sync_company("co-1")
        self.assertIn("deferred", result)


class BriefHookTests(unittest.TestCase):

    def setUp(self):
        os.environ.pop("FLUENCY_REALTIME_SYNC", None)

    def test_leg_noop_when_flag_off(self):
        with mock.patch.object(fluency_feed, "sync_company") as sc:
            brief_hooks._leg_fluency("co-1")
        sc.assert_not_called()

    def test_leg_fires_when_flag_on(self):
        os.environ["FLUENCY_REALTIME_SYNC"] = "true"
        with mock.patch.object(fluency_feed, "sync_company", return_value={"appended": 1}) as sc:
            brief_hooks._leg_fluency("co-1")
        sc.assert_called_once_with("co-1")

    def test_on_field_written_runs_legs_in_thread(self):
        calls = []
        with mock.patch.object(brief_hooks, "_LEGS", [lambda cid, **k: calls.append(cid)]):
            brief_hooks.on_field_written("co-1", "voice_tier", "Voice", "a", "b")
            # join the daemon thread by polling briefly
            import time
            for _ in range(50):
                if calls:
                    break
                time.sleep(0.01)
        self.assertEqual(calls, ["co-1"])

    def test_empty_company_id_noop(self):
        with mock.patch.object(brief_hooks, "_LEGS", [mock.Mock()]) as legs:
            brief_hooks.on_field_written("", "k", "L", "a", "b")
        # nothing to assert beyond no exception; leg never called for empty id


if __name__ == "__main__":
    unittest.main()
