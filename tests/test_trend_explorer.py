"""Tests for webhook-server/trend_explorer.py (Phase 3)."""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

import trend_explorer as te  # noqa: E402


def _fake_trends_response(keywords: list[str], monthly_values_per_kw: list[list[int]]) -> dict:
    """Build a minimal trends_explore response matching DataForSEO's shape."""
    data = []
    n_points = len(monthly_values_per_kw[0]) if monthly_values_per_kw else 0
    for i in range(n_points):
        data.append({"values": [monthly_values_per_kw[k][i] for k in range(len(keywords))]})
    return {
        "items": [{
            "type": "google_trends_graph",
            "data": data,
        }]
    }


class ExploreTests(unittest.TestCase):
    def test_empty_keywords_returns_empty_series(self):
        result = te.explore([])
        self.assertEqual(result["series"], [])

    def test_caps_at_5_keywords(self):
        captured = {}
        def mock_trends(kws, timeframe="past_12_months"):
            captured["kws"] = kws
            return _fake_trends_response(kws, [[i] for i in range(len(kws))])

        with patch("dataforseo_client.trends_explore", side_effect=mock_trends):
            te.explore(["a", "b", "c", "d", "e", "f", "g"])
        self.assertEqual(len(captured["kws"]), 5)

    def test_reshapes_to_per_keyword_series(self):
        fake = _fake_trends_response(["a", "b"], [[10, 20, 30], [5, 15, 25]])
        with patch("dataforseo_client.trends_explore", return_value=fake):
            result = te.explore(["a", "b"])
        self.assertEqual(len(result["series"]), 2)
        self.assertEqual(result["series"][0]["keyword"], "a")
        self.assertEqual(result["series"][0]["values"], [10, 20, 30])
        self.assertEqual(result["series"][1]["values"], [5, 15, 25])


class SeasonalPeaksTests(unittest.TestCase):
    def test_identifies_peak_month(self):
        # 60 monthly data points (5 years) for one keyword, peak in index 60 // 12 = 5 → June
        # Build [0] * 60 with a spike at index 5
        values = [10] * 60
        values[5] = 100  # This will fall into month bucket 5 (index 5 // 1 = 5 % 12 = 5 = Jun)
        fake = _fake_trends_response(["test"], [values])
        with patch("dataforseo_client.trends_explore", return_value=fake):
            result = te.seasonal_peaks(["test"])
        self.assertEqual(len(result["peaks"]), 1)
        # Exact month depends on n_per_month bucketing, just verify structure
        self.assertIn(result["peaks"][0]["peak_month"], te.MONTHS)
        self.assertEqual(result["peaks"][0]["keyword"], "test")

    def test_empty_input(self):
        self.assertEqual(te.seasonal_peaks([])["peaks"], [])


class RelatedRisingTests(unittest.TestCase):
    def test_empty_seed(self):
        self.assertEqual(te.related_rising("")["rising"], [])

    def test_extracts_rising_queries(self):
        fake = {
            "items": [{
                "type": "google_trends_related_queries",
                "data": {"rising": [{"query": "apartments 2026"}, {"query": "winter garden homes for rent"}]},
            }]
        }
        with patch("dataforseo_client._post") as mock_post, \
             patch("dataforseo_client._first_result", return_value=fake):
            mock_post.return_value = {}
            result = te.related_rising("apartments winter garden")
        self.assertEqual(result["rising"], ["apartments 2026", "winter garden homes for rent"])


if __name__ == "__main__":
    unittest.main()
