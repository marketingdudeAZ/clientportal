"""Contract tests for webhook-server/hubdb_helpers.py.

Writes raise HubDBError on failure; read failures return []; missing
table_id returns the appropriate sentinel (not an error).
"""

import os
import sys
import unittest
from unittest import mock

import requests as requests_module

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("HUBSPOT_API_KEY", "test")
os.environ.setdefault("WEBHOOK_SECRET", "test")

import hubdb_helpers  # noqa: E402
from hubdb_helpers import (  # noqa: E402
    HubDBError, read_rows, insert_row, update_row, delete_row, publish,
)


def _ok_response(body=None, status=200):
    m = mock.MagicMock()
    m.status_code = status
    m.json.return_value = body or {}
    m.raise_for_status = mock.MagicMock()
    return m


def _http_error(status=400, body=""):
    """Build a HTTPError with a response body attached, like requests raises."""
    resp = mock.MagicMock()
    resp.status_code = status
    resp.text = body
    err = requests_module.HTTPError(f"{status} error")
    err.response = resp
    return err


class TestReadRows(unittest.TestCase):
    @mock.patch("hubdb_helpers.requests.get")
    def test_returns_rows_with_flattened_select_columns(self, get):
        get.return_value = _ok_response({
            "results": [
                {"id": "row-1", "values": {
                    "name": "A",
                    "status": {"name": "Active", "id": "1"},
                }}
            ]
        })
        rows = read_rows("t-1")
        self.assertEqual(rows, [{"id": "row-1", "name": "A", "status": "Active"}])

    def test_missing_table_id_returns_empty_list(self):
        self.assertEqual(read_rows(""), [])
        self.assertEqual(read_rows(None), [])

    @mock.patch("hubdb_helpers.requests.get")
    def test_http_failure_logs_and_returns_empty(self, get):
        get.return_value.raise_for_status.side_effect = _http_error(500, "internal")
        self.assertEqual(read_rows("t-1"), [])

    @mock.patch("hubdb_helpers.requests.get")
    def test_filter_translates_to_col__eq_query_string(self, get):
        get.return_value = _ok_response({"results": []})
        read_rows("t-1", filters={"property_uuid": "p1"})
        url = get.call_args.args[0]
        self.assertIn("property_uuid__eq=p1", url)


class TestInsertRow(unittest.TestCase):
    @mock.patch("hubdb_helpers.requests.post")
    def test_returns_row_id_on_success(self, post):
        post.return_value = _ok_response({"id": "row-42"})
        self.assertEqual(insert_row("t-1", {"keyword": "x"}), "row-42")

    def test_missing_table_id_returns_none_without_raising(self):
        self.assertIsNone(insert_row("", {"k": "v"}))
        self.assertIsNone(insert_row(None, {"k": "v"}))

    @mock.patch("hubdb_helpers.requests.post")
    def test_http_failure_raises_hubdb_error_with_response_body(self, post):
        post.return_value.raise_for_status.side_effect = _http_error(
            400, '{"message":"DATETIME expects millis not ISO"}'
        )
        with self.assertRaises(HubDBError) as ctx:
            insert_row("t-1", {"when": "2026-04-24"})
        self.assertIn("DATETIME expects millis", str(ctx.exception))


class TestUpdateRow(unittest.TestCase):
    @mock.patch("hubdb_helpers.requests.patch")
    def test_returns_true_on_success(self, patch_req):
        patch_req.return_value = _ok_response({})
        self.assertTrue(update_row("t-1", "row-1", {"status": "done"}))

    def test_missing_ids_return_false_without_raising(self):
        self.assertFalse(update_row("", "r", {"k": "v"}))
        self.assertFalse(update_row("t", "", {"k": "v"}))

    @mock.patch("hubdb_helpers.requests.patch")
    def test_http_failure_raises_hubdb_error(self, patch_req):
        patch_req.return_value.raise_for_status.side_effect = _http_error(404, "not found")
        with self.assertRaises(HubDBError):
            update_row("t-1", "row-1", {"k": "v"})


class TestDeleteRow(unittest.TestCase):
    @mock.patch("hubdb_helpers.requests.delete")
    def test_success_returns_true(self, delete_req):
        delete_req.return_value = _ok_response({}, status=204)
        self.assertTrue(delete_row("t-1", "r-1"))

    def test_missing_ids_return_false(self):
        self.assertFalse(delete_row("", "r"))
        self.assertFalse(delete_row("t", ""))

    @mock.patch("hubdb_helpers.requests.delete")
    def test_http_failure_raises(self, delete_req):
        delete_req.return_value.raise_for_status.side_effect = _http_error(500, "boom")
        with self.assertRaises(HubDBError):
            delete_row("t-1", "r-1")


class TestPublish(unittest.TestCase):
    @mock.patch("hubdb_helpers.requests.post")
    def test_success_returns_true(self, post):
        post.return_value = _ok_response({})
        self.assertTrue(publish("t-1"))

    def test_missing_table_id_returns_false(self):
        self.assertFalse(publish(""))
        self.assertFalse(publish(None))

    @mock.patch("hubdb_helpers.requests.post")
    def test_http_failure_raises(self, post):
        post.return_value.raise_for_status.side_effect = _http_error(502, "bad gateway")
        with self.assertRaises(HubDBError) as ctx:
            publish("t-1")
        self.assertIn("bad gateway", str(ctx.exception))


class TestErrorMessagePreservesResponseBody(unittest.TestCase):
    """Regression test: the whole point of the raise-on-error contract is that
    the HubSpot error body (especially DATETIME schema errors) shows up in logs.
    """

    @mock.patch("hubdb_helpers.requests.post")
    def test_datetime_error_visible_in_exception_message(self, post):
        hubspot_body = (
            '{"status":"error","message":"Property values were not valid",'
            '"correlationId":"abc-123",'
            '"category":"VALIDATION_ERROR",'
            '"errors":[{"message":"Invalid DATETIME \'2026-04-24T16:18:00Z\'","in":"values.scanned_at"}]}'
        )
        post.return_value.raise_for_status.side_effect = _http_error(400, hubspot_body)
        with self.assertRaises(HubDBError) as ctx:
            insert_row("ai-mentions-table", {"scanned_at": "2026-04-24T16:18:00Z"})
        msg = str(ctx.exception)
        self.assertIn("Invalid DATETIME", msg)
        self.assertIn("values.scanned_at", msg)


if __name__ == "__main__":
    unittest.main()
