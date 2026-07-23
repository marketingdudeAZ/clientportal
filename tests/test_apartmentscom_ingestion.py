"""Tests for webhook-server/apartmentscom_ingestion.py — row building,
BQ write, Loop event emission. External deps (BQ, Loop) are mocked."""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

import apartmentscom_ingestion as ing  # noqa: E402


def _summary():
    return {
        "pmc": "ABC Property Management",
        "record_date": "2026-04-01",
        "message": None,
        "items": [
            {
                "costar_property_id": 12345,
                "costar_listing_id": 67890,
                "property_name": "Riverfront Apartments",
                "address": "123 Main St",
                "city": "Atlanta",
                "state": "GA",
                "postal_code": "30303",
                "country": "US",
                "ad_package": "Diamond",
                "search_result_impressions": 1250,
                "details_page_impressions": 310,
                "total_impressions": 1560,
                "total_media_views": 210,
                "hd_video_views": 22,
                "tour_3d_views": 18,
                "property_map_views": 37,
                "total_leads": 12,
                "phone_leads": 4,
                "email_leads": 3,
                "property_website_leads": 2,
                "request_to_tour_leads": 2,
                "request_to_apply_leads": 1,
                "unit_application_leads": 0,
            }
        ],
        "raw_items": [{"propertyId": 12345, "listingId": 67890, "totalLeads": 12}],
    }


class TestIngestDate(unittest.TestCase):
    def test_builds_rows_and_writes(self):
        fake_bq = MagicMock()
        fake_lw = MagicMock()
        with patch.object(ing.ac, "fetch_daily_summary", return_value=_summary()), \
             patch.dict(sys.modules, {"bigquery_client": fake_bq, "loop_writer": fake_lw}), \
             patch("config.BIGQUERY_APARTMENTSCOM_DAILY_TABLE", "apartmentscom_ils_daily", create=True):
            result = ing.ingest_date("2026-04-01")

        self.assertEqual(result["listings"], 1)
        self.assertEqual(result["rows_written"], 1)
        self.assertEqual(result["total_impressions"], 1560)
        self.assertEqual(result["total_leads"], 12)

        # BQ insert called with enriched rows
        fake_bq.insert_rows.assert_called_once()
        table, rows = fake_bq.insert_rows.call_args[0]
        self.assertEqual(table, "apartmentscom_ils_daily")
        row = rows[0]
        self.assertEqual(row["record_date"], "2026-04-01")
        self.assertEqual(row["pmc"], "ABC Property Management")
        self.assertIn("ingested_at", row)
        self.assertIn("raw_payload", row)
        self.assertIn("67890", row["raw_payload"])

        # Loop run event emitted once, ops/job
        fake_lw.record.assert_called_once()
        kwargs = fake_lw.record.call_args.kwargs
        self.assertEqual(kwargs["stage"], "ops")
        self.assertEqual(kwargs["event_type"], "job")
        self.assertEqual(kwargs["payload"]["record_date"], "2026-04-01")
        self.assertEqual(kwargs["payload"]["total_leads"], 12)

    def test_empty_day_writes_nothing_but_emits(self):
        empty = _summary()
        empty["items"] = []
        empty["raw_items"] = []
        fake_bq = MagicMock()
        fake_lw = MagicMock()
        with patch.object(ing.ac, "fetch_daily_summary", return_value=empty), \
             patch.dict(sys.modules, {"bigquery_client": fake_bq, "loop_writer": fake_lw}), \
             patch("config.BIGQUERY_APARTMENTSCOM_DAILY_TABLE", "apartmentscom_ils_daily", create=True):
            result = ing.ingest_date("2026-04-01")

        self.assertEqual(result["rows_written"], 0)
        fake_bq.insert_rows.assert_not_called()
        fake_lw.record.assert_called_once()  # still records the run

    def test_loop_failure_does_not_block(self):
        fake_bq = MagicMock()
        fake_lw = MagicMock()
        fake_lw.record.side_effect = RuntimeError("loop down")
        with patch.object(ing.ac, "fetch_daily_summary", return_value=_summary()), \
             patch.dict(sys.modules, {"bigquery_client": fake_bq, "loop_writer": fake_lw}), \
             patch("config.BIGQUERY_APARTMENTSCOM_DAILY_TABLE", "apartmentscom_ils_daily", create=True):
            result = ing.ingest_date("2026-04-01")  # must not raise
        self.assertEqual(result["rows_written"], 1)


if __name__ == "__main__":
    unittest.main()
