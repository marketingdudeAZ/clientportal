"""Tests for Phase 3 /api/keywords/* + /api/trends/* Flask routes."""
from __future__ import annotations

import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

import server  # noqa: E402


def _auth():
    return {"X-Portal-Email": "portal@rpmliving.com"}


class IdeasRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = server.app.test_client()

    def test_requires_seed(self):
        with patch("seo_entitlement.get_seo_tier", return_value="Basic"):
            r = self.client.get("/api/keywords/ideas?company_id=1&property_uuid=2", headers=_auth())
        self.assertEqual(r.status_code, 400)

    def test_local_tier_blocked(self):
        with patch("seo_entitlement.get_seo_tier", return_value="Local"):
            r = self.client.get(
                "/api/keywords/ideas?company_id=1&property_uuid=2&seed=foo",
                headers=_auth(),
            )
        self.assertEqual(r.status_code, 403)

    def test_basic_tier_allowed(self):
        with patch("seo_entitlement.get_seo_tier", return_value="Basic"), \
             patch("keyword_research.expand_seed", return_value=[{"keyword": "x", "volume": 100, "difficulty": 30, "intent": "commercial", "cpc": 1.5, "serp_features": [], "monthly_volumes": []}]):
            r = self.client.get(
                "/api/keywords/ideas?company_id=1&property_uuid=2&seed=foo",
                headers=_auth(),
            )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["count"], 1)


class DifficultyRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = server.app.test_client()

    def test_rejects_too_many_keywords(self):
        from config import KEYWORD_DIFFICULTY_BATCH_MAX
        huge = ["kw"] * (KEYWORD_DIFFICULTY_BATCH_MAX * 10 + 1)
        with patch("seo_entitlement.get_seo_tier", return_value="Basic"):
            r = self.client.post(
                "/api/keywords/difficulty",
                json={"company_id": "1", "property_uuid": "2", "keywords": huge},
                headers=_auth(),
            )
        self.assertEqual(r.status_code, 400)

    def test_accepts_valid_list(self):
        with patch("seo_entitlement.get_seo_tier", return_value="Basic"), \
             patch("keyword_research.enrich_difficulty", return_value=[{"keyword": "a", "difficulty": 20}]):
            r = self.client.post(
                "/api/keywords/difficulty",
                json={"company_id": "1", "property_uuid": "2", "keywords": ["a", "b"]},
                headers=_auth(),
            )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.get_json()["results"]), 1)


class SaveRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = server.app.test_client()

    def test_requires_keywords_list(self):
        with patch("seo_entitlement.get_seo_tier", return_value="Basic"):
            r = self.client.post(
                "/api/keywords/save",
                json={"company_id": "1", "property_uuid": "2"},
                headers=_auth(),
            )
        self.assertEqual(r.status_code, 400)

    def test_bulk_save_invokes_helper(self):
        with patch("seo_entitlement.get_seo_tier", return_value="Basic"), \
             patch("keyword_research.save_to_tracked", return_value=3) as mock_save:
            r = self.client.post(
                "/api/keywords/save",
                json={"company_id": "1", "property_uuid": "2",
                      "keywords": [{"keyword": "a"}, {"keyword": "b"}, {"keyword": "c"}]},
                headers=_auth(),
            )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["saved"], 3)
        mock_save.assert_called_once()


class TrendsGatingTests(unittest.TestCase):
    def setUp(self):
        self.client = server.app.test_client()

    def test_trends_basic_blocked(self):
        with patch("seo_entitlement.get_seo_tier", return_value="Basic"):
            r = self.client.get(
                "/api/trends/explore?company_id=1&property_uuid=2&keywords=foo",
                headers=_auth(),
            )
        self.assertEqual(r.status_code, 403)

    def test_trends_standard_allowed(self):
        with patch("seo_entitlement.get_seo_tier", return_value="Standard"), \
             patch("trend_explorer.explore", return_value={"series": [], "timeframe": "past_12_months"}):
            r = self.client.get(
                "/api/trends/explore?company_id=1&property_uuid=2&keywords=foo",
                headers=_auth(),
            )
        self.assertEqual(r.status_code, 200)


if __name__ == "__main__":
    unittest.main()
