"""Integration tests for /api/seo/* routes.

Stubs DataForSEO and HubDB so the Flask test client can exercise real route
code and assert gating, validation, and response shape.
"""

import sys
import os
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

from server import app


class _SeoBase(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    def _headers(self, email="client@example.com"):
        return {"X-Portal-Email": email}


class TestEntitlementRoute(_SeoBase):
    def test_missing_email_401(self):
        r = self.client.get("/api/seo/entitlement?company_id=123")
        self.assertEqual(r.status_code, 401)

    def test_missing_company_id_400(self):
        r = self.client.get("/api/seo/entitlement", headers=self._headers())
        self.assertEqual(r.status_code, 400)

    @patch("seo_entitlement.get_seo_tier", return_value="Standard")
    def test_returns_feature_map(self, _):
        r = self.client.get("/api/seo/entitlement?company_id=123", headers=self._headers())
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body["tier"], "Standard")
        self.assertTrue(body["features"]["dashboard"])
        self.assertTrue(body["features"]["ai_mentions"])
        self.assertFalse(body["features"]["content_decay"])  # premium only


class TestDashboardRoute(_SeoBase):
    @patch("seo_entitlement.get_seo_tier", return_value=None)
    def test_no_tier_403(self, _):
        r = self.client.get(
            "/api/seo/dashboard?company_id=123&property_uuid=abc",
            headers=self._headers(),
        )
        self.assertEqual(r.status_code, 403)

    @patch("seo_entitlement.get_seo_tier", return_value="Local")
    @patch("seo_dashboard.build_dashboard", return_value={"keywords": [], "summary": {}, "organic_trend": [], "competitors": []})
    def test_happy_path(self, _build, _tier):
        r = self.client.get(
            "/api/seo/dashboard?company_id=123&property_uuid=abc",
            headers=self._headers(),
        )
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body["tier"], "Local")
        self.assertIn("keywords", body)


class TestKeywordsRoute(_SeoBase):
    @patch("seo_entitlement.get_seo_tier", return_value="Local")
    @patch("hubdb_helpers.read_rows", return_value=[{"id": "1", "keyword": "phoenix apartments"}])
    def test_get_rows(self, _read, _tier):
        r = self.client.get(
            "/api/seo/keywords?company_id=123&property_uuid=abc",
            headers=self._headers(),
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.get_json()["rows"]), 1)

    @patch("seo_entitlement.get_seo_tier", return_value="Local")
    def test_post_blocked_at_local_tier(self, _tier):
        """Local tier can read keywords but not write — write needs Basic+."""
        r = self.client.post(
            "/api/seo/keywords",
            json={"company_id": "123", "property_uuid": "abc", "keyword": "x"},
            headers=self._headers(),
        )
        self.assertEqual(r.status_code, 403)

    @patch("seo_entitlement.get_seo_tier", return_value="Standard")
    @patch("hubdb_helpers.insert_row", return_value="row-42")
    @patch("hubdb_helpers.publish", return_value=True)
    @patch("seo_dashboard.invalidate")
    def test_post_creates_row(self, _inv, _pub, _ins, _tier):
        r = self.client.post(
            "/api/seo/keywords",
            json={"company_id": "123", "property_uuid": "abc", "keyword": "luxury apartments phoenix"},
            headers=self._headers(),
        )
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body["status"], "created")
        self.assertEqual(body["id"], "row-42")

    @patch("seo_entitlement.get_seo_tier", return_value="Standard")
    def test_post_requires_keyword(self, _tier):
        r = self.client.post(
            "/api/seo/keywords",
            json={"company_id": "123", "property_uuid": "abc"},
            headers=self._headers(),
        )
        self.assertEqual(r.status_code, 400)


class TestAiMentionsRoute(_SeoBase):
    @patch("seo_entitlement.get_seo_tier", return_value="Lite")
    def test_lite_tier_blocked(self, _tier):
        r = self.client.get(
            "/api/seo/ai-mentions?company_id=123&property_uuid=abc",
            headers=self._headers(),
        )
        self.assertEqual(r.status_code, 403)

    @patch("seo_entitlement.get_seo_tier", return_value="Basic")
    @patch("ai_mentions.get_latest_snapshot", return_value={"composite_index": 42, "by_engine": {}, "history": []})
    def test_basic_tier_reads_snapshot(self, _snap, _tier):
        r = self.client.get(
            "/api/seo/ai-mentions?company_id=123&property_uuid=abc",
            headers=self._headers(),
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["composite_index"], 42)


if __name__ == "__main__":
    unittest.main()
