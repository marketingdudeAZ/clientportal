"""Tests for webhook-server/content_planner.py (Phase 2).

Mocks DataForSEO + HubDB + BigQuery boundaries so no network calls happen.
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

import content_planner  # noqa: E402


def _fake_serp(urls: list[str]) -> dict:
    """Build a minimal serp_organic_advanced() result with the given organic URLs."""
    return {"items": [{"type": "organic", "url": u} for u in urls]}


class ClusterKeywordsTests(unittest.TestCase):
    def test_cluster_overlap_threshold_met(self):
        """Two keywords sharing 4 URLs cluster together."""
        shared = ["https://a.com", "https://b.com", "https://c.com", "https://d.com"]
        serp_cache = {
            "winter garden apartments":      _fake_serp(shared + ["https://e.com"]),
            "apartments in winter garden":   _fake_serp(shared + ["https://f.com"]),
        }
        with patch("content_planner.read_rows", create=True), \
             patch("hubdb_helpers.read_rows") as mock_read:
            mock_read.return_value = [
                {"keyword": "winter garden apartments",    "volume": 500, "difficulty": 35},
                {"keyword": "apartments in winter garden", "volume": 300, "difficulty": 30},
            ]
            clusters = content_planner.cluster_keywords("test-uuid", _serp_cache=serp_cache)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["hub_keyword"], "winter garden apartments")  # higher volume
        self.assertEqual(clusters[0]["spokes"], ["apartments in winter garden"])
        self.assertEqual(clusters[0]["total_volume"], 800)

    def test_cluster_overlap_threshold_not_met(self):
        """Two keywords sharing only 3 URLs stay separate."""
        serp_cache = {
            "luxury apartments":  _fake_serp(["https://a.com", "https://b.com", "https://c.com", "https://x.com"]),
            "pet friendly rent":  _fake_serp(["https://a.com", "https://b.com", "https://c.com", "https://y.com"]),
        }
        with patch("hubdb_helpers.read_rows") as mock_read:
            mock_read.return_value = [
                {"keyword": "luxury apartments",  "volume": 400, "difficulty": 50},
                {"keyword": "pet friendly rent",  "volume": 200, "difficulty": 35},
            ]
            clusters = content_planner.cluster_keywords("test-uuid", _serp_cache=serp_cache)
        # Each keyword is its own cluster
        self.assertEqual(len(clusters), 2)
        hubs = {c["hub_keyword"] for c in clusters}
        self.assertEqual(hubs, {"luxury apartments", "pet friendly rent"})

    def test_no_keywords_returns_empty(self):
        with patch("hubdb_helpers.read_rows") as mock_read:
            mock_read.return_value = []
            self.assertEqual(content_planner.cluster_keywords("test-uuid"), [])

    def test_coverage_pct_uses_current_position(self):
        """Keywords with position <= 10 count toward coverage."""
        serp_cache = {
            "a": _fake_serp(["u1", "u2", "u3", "u4"]),
            "b": _fake_serp(["u1", "u2", "u3", "u4"]),
        }
        with patch("hubdb_helpers.read_rows") as mock_read:
            mock_read.return_value = [
                {"keyword": "a", "volume": 100, "position": 5},   # ranking top 10
                {"keyword": "b", "volume": 50,  "position": 25},  # not ranking
            ]
            clusters = content_planner.cluster_keywords("test-uuid", _serp_cache=serp_cache)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["current_coverage_pct"], 0.5)


class SemanticGapsTests(unittest.TestCase):
    def test_filters_by_difficulty_and_volume(self):
        """Rows with KD > 40 or volume < 50 are dropped."""
        mock_rows = [
            # kept
            {"keyword": "apartments winter garden fl", "keyword_data": {
                "keyword_info": {"search_volume": 500, "cpc": 2.10},
                "keyword_properties": {"keyword_difficulty": 25}}},
            # too hard — dropped
            {"keyword": "apartments orlando", "keyword_data": {
                "keyword_info": {"search_volume": 2000, "cpc": 3.00},
                "keyword_properties": {"keyword_difficulty": 65}}},
            # too low-vol — dropped
            {"keyword": "obscure long tail", "keyword_data": {
                "keyword_info": {"search_volume": 10, "cpc": 0.50},
                "keyword_properties": {"keyword_difficulty": 15}}},
        ]
        with patch("dataforseo_client.domain_intersection") as mock_di:
            mock_di.return_value = mock_rows
            gaps = content_planner.semantic_gaps("mine.com", ["competitor.com"])
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["keyword"], "apartments winter garden fl")

    def test_sorted_by_volume_desc(self):
        mock_rows = [
            {"keyword": "low vol",  "keyword_data": {"keyword_info": {"search_volume": 100, "cpc": 1},
                                                     "keyword_properties": {"keyword_difficulty": 20}}},
            {"keyword": "high vol", "keyword_data": {"keyword_info": {"search_volume": 900, "cpc": 1},
                                                     "keyword_properties": {"keyword_difficulty": 20}}},
            {"keyword": "mid vol",  "keyword_data": {"keyword_info": {"search_volume": 400, "cpc": 1},
                                                     "keyword_properties": {"keyword_difficulty": 20}}},
        ]
        with patch("dataforseo_client.domain_intersection") as mock_di:
            mock_di.return_value = mock_rows
            gaps = content_planner.semantic_gaps("mine.com", ["comp.com"])
        self.assertEqual([g["keyword"] for g in gaps], ["high vol", "mid vol", "low vol"])


class DetectDecayTests(unittest.TestCase):
    def test_flags_url_with_enough_affected_keywords(self):
        """URL with 3 keywords each dropping 5+ positions gets flagged."""
        history = [
            # Keyword A: position 3 -> 9 (drop of 6)
            {"url": "https://mine.com/p1", "keyword": "kw-a", "position": 3, "fetched_at": "2026-03-21T00:00:00Z"},
            {"url": "https://mine.com/p1", "keyword": "kw-a", "position": 9, "fetched_at": "2026-04-20T00:00:00Z"},
            # Keyword B: 5 -> 12 (drop 7)
            {"url": "https://mine.com/p1", "keyword": "kw-b", "position": 5, "fetched_at": "2026-03-21T00:00:00Z"},
            {"url": "https://mine.com/p1", "keyword": "kw-b", "position": 12, "fetched_at": "2026-04-20T00:00:00Z"},
            # Keyword C: 1 -> 8 (drop 7)
            {"url": "https://mine.com/p1", "keyword": "kw-c", "position": 1, "fetched_at": "2026-03-21T00:00:00Z"},
            {"url": "https://mine.com/p1", "keyword": "kw-c", "position": 8, "fetched_at": "2026-04-20T00:00:00Z"},
        ]
        with patch("bigquery_client.get_seo_rank_history") as mock_bq, \
             patch("bigquery_client.is_bigquery_configured") as mock_cfg:
            mock_cfg.return_value = True
            mock_bq.return_value = history
            result = content_planner.detect_decay("test-uuid", threshold=5, min_affected=3)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["url"], "https://mine.com/p1")
        self.assertEqual(result[0]["affected_keywords_count"], 3)

    def test_skips_url_with_only_2_affected(self):
        history = [
            {"url": "https://mine.com/p2", "keyword": "kw-a", "position": 3, "fetched_at": "2026-03-21T00:00:00Z"},
            {"url": "https://mine.com/p2", "keyword": "kw-a", "position": 9, "fetched_at": "2026-04-20T00:00:00Z"},
            {"url": "https://mine.com/p2", "keyword": "kw-b", "position": 5, "fetched_at": "2026-03-21T00:00:00Z"},
            {"url": "https://mine.com/p2", "keyword": "kw-b", "position": 12, "fetched_at": "2026-04-20T00:00:00Z"},
        ]
        with patch("bigquery_client.get_seo_rank_history") as mock_bq, \
             patch("bigquery_client.is_bigquery_configured") as mock_cfg:
            mock_cfg.return_value = True
            mock_bq.return_value = history
            result = content_planner.detect_decay("test-uuid", threshold=5, min_affected=3)
        self.assertEqual(result, [])

    def test_returns_empty_when_bq_unconfigured(self):
        with patch("bigquery_client.is_bigquery_configured") as mock_cfg:
            mock_cfg.return_value = False
            self.assertEqual(content_planner.detect_decay("test-uuid"), [])

    def test_priority_buckets(self):
        """avg_drop >= 15 -> high, 10-14 -> medium, else low."""
        def _hist(drops):
            rows = []
            for i, d in enumerate(drops):
                rows.append({"url": "u", "keyword": f"kw-{i}", "position": 1,       "fetched_at": "2026-03-21"})
                rows.append({"url": "u", "keyword": f"kw-{i}", "position": 1 + d,   "fetched_at": "2026-04-20"})
            return rows

        with patch("bigquery_client.is_bigquery_configured", return_value=True):
            with patch("bigquery_client.get_seo_rank_history", return_value=_hist([20, 18, 16])):
                r = content_planner.detect_decay("u", threshold=5, min_affected=3)
                self.assertEqual(r[0]["priority"], "high")
            with patch("bigquery_client.get_seo_rank_history", return_value=_hist([12, 11, 10])):
                r = content_planner.detect_decay("u", threshold=5, min_affected=3)
                self.assertEqual(r[0]["priority"], "medium")
            with patch("bigquery_client.get_seo_rank_history", return_value=_hist([6, 7, 5])):
                r = content_planner.detect_decay("u", threshold=5, min_affected=3)
                self.assertEqual(r[0]["priority"], "low")


if __name__ == "__main__":
    unittest.main()
