"""Tests for webhook-server/apartmentscom_client.py — parsing, normalization,
error mapping, date helpers."""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

import apartmentscom_client as ac  # noqa: E402


SAMPLE_RESPONSE = {
    "propertyManagementCompany": "ABC Property Management",
    "recordDate": "2026-04-01",
    "items": [
        {
            "propertyId": 12345,
            "listingId": 67890,
            "propertyName": "Riverfront Apartments",
            "address": "123 Main St",
            "city": "Atlanta",
            "state": "GA",
            "postalCode": "30303",
            "country": "US",
            "adPackage": "Diamond",
            "searchResultImpressions": 1250,
            "detailsPageImpressions": 310,
            "totalImpressions": 1560,
            "totalMediaViews": 210,
            "hdVideoViews": 22,
            "3dTourViews": 18,
            "propertyMapViews": 37,
            "totalLeads": 12,
            "phoneLeads": 4,
            "emailLeads": 3,
            "propertyWebsiteLeads": 2,
            "requestToTourLeads": 2,
            "requestToApplyLeads": 1,
            "unitApplicationLeads": 0,
        }
    ],
    "message": None,
}


def _fake_resp(status=200, json_body=None, text=""):
    resp = MagicMock()
    resp.status_code = status
    resp.content = b"x" if (json_body is not None or text) else b""
    resp.json.return_value = json_body if json_body is not None else {}
    resp.text = text
    resp.raise_for_status.return_value = None
    return resp


class TestNormalize(unittest.TestCase):
    def test_normalize_item_maps_all_fields(self):
        item = ac.normalize_item(SAMPLE_RESPONSE["items"][0])
        self.assertEqual(item["costar_property_id"], 12345)
        self.assertEqual(item["costar_listing_id"], 67890)
        self.assertEqual(item["property_name"], "Riverfront Apartments")
        self.assertEqual(item["ad_package"], "Diamond")
        self.assertEqual(item["search_result_impressions"], 1250)
        self.assertEqual(item["total_impressions"], 1560)
        self.assertEqual(item["tour_3d_views"], 18)
        self.assertEqual(item["total_leads"], 12)
        self.assertEqual(item["unit_application_leads"], 0)

    def test_normalize_is_case_insensitive(self):
        # Docs schema table uses PascalCase; sample uses camelCase.
        item = ac.normalize_item({"PropertyId": 1, "TotalLeads": "9"})
        self.assertEqual(item["costar_property_id"], 1)
        self.assertEqual(item["total_leads"], 9)  # coerced to int

    def test_missing_metric_is_none(self):
        item = ac.normalize_item({"propertyId": 1})
        self.assertIsNone(item["total_leads"])


class TestFetch(unittest.TestCase):
    @patch.dict(os.environ, {"APARTMENTSCOM_API_KEY": "k"})
    @patch("apartmentscom_client.requests.post")
    def test_fetch_success(self, post):
        post.return_value = _fake_resp(200, SAMPLE_RESPONSE)
        out = ac.fetch_daily_summary("2026-04-01")
        self.assertEqual(out["record_date"], "2026-04-01")
        self.assertEqual(out["pmc"], "ABC Property Management")
        self.assertEqual(len(out["items"]), 1)
        self.assertEqual(len(out["raw_items"]), 1)
        # date passed through as JSON body
        _, kwargs = post.call_args
        self.assertEqual(kwargs["json"], {"date": "2026-04-01"})
        self.assertEqual(kwargs["headers"]["X-PMC-API-KEY"], "k")

    @patch.dict(os.environ, {"APARTMENTSCOM_API_KEY": "k"})
    @patch("apartmentscom_client.requests.post")
    def test_no_date_omits_body(self, post):
        post.return_value = _fake_resp(200, {"items": []})
        ac.fetch_daily_summary()
        _, kwargs = post.call_args
        self.assertEqual(kwargs["json"], {})

    @patch.dict(os.environ, {"APARTMENTSCOM_API_KEY": "k"})
    @patch("apartmentscom_client.requests.post")
    def test_empty_items(self, post):
        post.return_value = _fake_resp(200, {"recordDate": "2026-04-02", "items": []})
        out = ac.fetch_daily_summary("2026-04-02")
        self.assertEqual(out["items"], [])

    @patch.dict(os.environ, {"APARTMENTSCOM_API_KEY": "k"})
    @patch("apartmentscom_client.requests.post")
    def test_401_raises_auth(self, post):
        post.return_value = _fake_resp(401)
        with self.assertRaises(ac.ApartmentsComAuthError):
            ac.fetch_daily_summary()

    @patch.dict(os.environ, {"APARTMENTSCOM_API_KEY": "k"})
    @patch("apartmentscom_client.requests.post")
    def test_400_raises_bad_date(self, post):
        post.return_value = _fake_resp(400, text="Date must be a past date.")
        with self.assertRaises(ac.ApartmentsComBadDateError):
            ac.fetch_daily_summary("2099-01-01")

    @patch.dict(os.environ, {"APARTMENTSCOM_API_KEY": "k"})
    @patch("apartmentscom_client.requests.post")
    def test_429_raises_rate_limit(self, post):
        post.return_value = _fake_resp(429)
        with self.assertRaises(ac.ApartmentsComRateLimitError):
            ac.fetch_daily_summary("2026-04-01")

    @patch.dict(os.environ, {}, clear=True)
    def test_unconfigured_raises(self):
        with self.assertRaises(ac.ApartmentsComError):
            ac.fetch_daily_summary()


class TestDateHelpers(unittest.TestCase):
    def test_backfill_dates_caps_at_90(self):
        dates = ac.backfill_dates(200)
        self.assertEqual(len(dates), 90)

    def test_backfill_dates_newest_first(self):
        from datetime import date
        dates = ac.backfill_dates(3, end=date(2026, 4, 10))
        self.assertEqual(dates, ["2026-04-10", "2026-04-09", "2026-04-08"])


if __name__ == "__main__":
    unittest.main()
