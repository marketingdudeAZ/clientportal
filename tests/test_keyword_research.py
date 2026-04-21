"""Tests for webhook-server/keyword_research.py (Phase 3)."""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

import keyword_research as kr  # noqa: E402


class ExpandSeedTests(unittest.TestCase):
    def test_normalizes_fields(self):
        raw = [
            {"keyword": "apartments winter garden",
             "keyword_info": {"search_volume": 600, "cpc": 2.4, "monthly_searches": [{"search_volume": 500}]*12},
             "keyword_properties": {"keyword_difficulty": 25},
             "serp_info": {"serp_item_types": ["organic", "people_also_ask"]},
             "search_intent_info": {"main_intent": "commercial"}},
        ]
        with patch("dataforseo_client.keyword_ideas") as mock:
            mock.return_value = raw
            out = kr.expand_seed(["apartments winter garden"])
        self.assertEqual(out[0]["keyword"], "apartments winter garden")
        self.assertEqual(out[0]["volume"], 600)
        self.assertEqual(out[0]["difficulty"], 25)
        self.assertEqual(out[0]["intent"], "commercial")
        self.assertIn("organic", out[0]["serp_features"])

    def test_respects_limit_arg(self):
        with patch("dataforseo_client.keyword_ideas") as mock:
            mock.return_value = []
            kr.expand_seed(["x"], limit=50)
        kwargs = mock.call_args.kwargs or {}
        self.assertEqual(kwargs.get("limit"), 50)


class EnrichDifficultyTests(unittest.TestCase):
    def test_chunks_input_over_batch_max(self):
        keywords = [f"kw-{i}" for i in range(2500)]  # 3 batches of 1000
        call_count = {"n": 0}

        def mock_kd(chunk, location_code=None):
            call_count["n"] += 1
            return [{"keyword": c, "keyword_difficulty": 30} for c in chunk]

        with patch("dataforseo_client.bulk_keyword_difficulty", side_effect=mock_kd):
            out = kr.enrich_difficulty(keywords, batch_max=1000)
        self.assertEqual(call_count["n"], 3)
        self.assertEqual(len(out), 2500)

    def test_empty_input(self):
        self.assertEqual(kr.enrich_difficulty([]), [])


class CompetitorGapTests(unittest.TestCase):
    def test_filters_by_kd_and_volume(self):
        raw = [
            {"keyword": "keep-me",    "keyword_data": {"keyword_info": {"search_volume": 500, "cpc": 1.5},
                                                       "keyword_properties": {"keyword_difficulty": 40}}},
            {"keyword": "too-hard",   "keyword_data": {"keyword_info": {"search_volume": 1000, "cpc": 2},
                                                       "keyword_properties": {"keyword_difficulty": 80}}},
            {"keyword": "too-obscure","keyword_data": {"keyword_info": {"search_volume": 5,   "cpc": 0.5},
                                                       "keyword_properties": {"keyword_difficulty": 15}}},
        ]
        with patch("dataforseo_client.domain_intersection") as mock:
            mock.return_value = raw
            out = kr.competitor_gap("mine.com", "comp.com")
        names = [r["keyword"] for r in out]
        self.assertEqual(names, ["keep-me"])

    def test_sorted_by_volume_desc(self):
        raw = [
            {"keyword": "low",  "keyword_data": {"keyword_info": {"search_volume": 100, "cpc": 1},
                                                  "keyword_properties": {"keyword_difficulty": 30}}},
            {"keyword": "high", "keyword_data": {"keyword_info": {"search_volume": 900, "cpc": 1},
                                                  "keyword_properties": {"keyword_difficulty": 30}}},
        ]
        with patch("dataforseo_client.domain_intersection") as mock:
            mock.return_value = raw
            out = kr.competitor_gap("mine.com", "comp.com")
        self.assertEqual(out[0]["keyword"], "high")


class SaveToTrackedTests(unittest.TestCase):
    def test_inserts_and_publishes_once(self):
        with patch("hubdb_helpers.insert_row") as mock_ins, \
             patch("hubdb_helpers.publish") as mock_pub, \
             patch("config.HUBDB_SEO_KEYWORDS_TABLE_ID", "123", create=False), \
             patch("seo_dashboard.invalidate"):
            # Also patch the module-level import target
            with patch("keyword_research.HUBDB_SEO_KEYWORDS_TABLE_ID", "123", create=True):
                pass  # keyword_research imports from config directly — patching the import won't help
            # Use the indirect patch via config
            mock_ins.return_value = "row-1"
            count = kr.save_to_tracked("prop-uuid", [
                {"keyword": "a"},
                {"keyword": "b"},
                {"keyword": "  "},  # skipped (blank)
            ])
        self.assertEqual(count, 2)
        self.assertEqual(mock_ins.call_count, 2)
        self.assertEqual(mock_pub.call_count, 1)

    def test_returns_zero_when_table_not_configured(self):
        with patch("config.HUBDB_SEO_KEYWORDS_TABLE_ID", None, create=False):
            count = kr.save_to_tracked("prop-uuid", [{"keyword": "a"}])
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
