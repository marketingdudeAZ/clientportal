"""Tests for Phase 2 /api/content/* Flask routes."""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

import server  # noqa: E402


def _auth_headers():
    return {"X-Portal-Email": "portal@rpmliving.com"}


class TierGatingTests(unittest.TestCase):
    """Verify content routes respect tier gating (Standard+)."""

    def setUp(self):
        self.client = server.app.test_client()

    def test_clusters_basic_blocked(self):
        with patch("seo_entitlement.get_seo_tier") as mock_tier:
            mock_tier.return_value = "Basic"
            r = self.client.get(
                "/api/content/clusters?company_id=123&property_uuid=456",
                headers=_auth_headers(),
            )
        self.assertEqual(r.status_code, 403)
        body = r.get_json()
        self.assertEqual(body["feature"], "content_clusters")

    def test_clusters_standard_allowed(self):
        with patch("seo_entitlement.get_seo_tier") as mock_tier, \
             patch("content_planner.cluster_keywords") as mock_cluster:
            mock_tier.return_value = "Standard"
            mock_cluster.return_value = []
            r = self.client.get(
                "/api/content/clusters?company_id=123&property_uuid=456",
                headers=_auth_headers(),
            )
        self.assertEqual(r.status_code, 200)

    def test_briefs_basic_blocked(self):
        with patch("seo_entitlement.get_seo_tier") as mock_tier:
            mock_tier.return_value = "Basic"
            r = self.client.get(
                "/api/content/briefs?company_id=123&property_uuid=456",
                headers=_auth_headers(),
            )
        self.assertEqual(r.status_code, 403)

    def test_decay_teaser_for_basic(self):
        """Basic tier gets top 3 decay rows + upgrade message."""
        full_rows = [{"url": f"u{i}", "avg_drop": 10, "affected_keywords_count": 3,
                      "affected_keywords": [], "priority": "medium"} for i in range(5)]
        with patch("seo_entitlement.get_seo_tier") as mock_tier, \
             patch("hubdb_helpers.read_rows") as mock_read:
            mock_tier.return_value = "Basic"
            mock_read.return_value = full_rows
            r = self.client.get(
                "/api/content/decay?company_id=123&property_uuid=456",
                headers=_auth_headers(),
            )
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(len(body["rows"]), 3)
        self.assertTrue(body["teaser"])
        self.assertEqual(body["total"], 5)
        self.assertEqual(body["upgrade_required"], "Premium")

    def test_decay_full_list_for_premium(self):
        full_rows = [{"url": f"u{i}", "avg_drop": 10, "affected_keywords_count": 3,
                      "affected_keywords": [], "priority": "medium"} for i in range(5)]
        with patch("seo_entitlement.get_seo_tier") as mock_tier, \
             patch("hubdb_helpers.read_rows") as mock_read:
            mock_tier.return_value = "Premium"
            mock_read.return_value = full_rows
            r = self.client.get(
                "/api/content/decay?company_id=123&property_uuid=456",
                headers=_auth_headers(),
            )
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(len(body["rows"]), 5)
        self.assertFalse(body["teaser"])


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.client = server.app.test_client()

    def test_missing_email_401(self):
        r = self.client.get("/api/content/clusters?company_id=123&property_uuid=456")
        self.assertEqual(r.status_code, 401)

    def test_missing_company_id_400(self):
        r = self.client.get("/api/content/clusters?property_uuid=456", headers=_auth_headers())
        self.assertEqual(r.status_code, 400)

    def test_missing_property_uuid_400(self):
        r = self.client.get("/api/content/clusters?company_id=123", headers=_auth_headers())
        self.assertEqual(r.status_code, 400)


class ClusterCacheTests(unittest.TestCase):
    def setUp(self):
        self.client = server.app.test_client()
        server._CONTENT_CLUSTER_CACHE.clear()

    def test_clusters_cache_hit_skips_rebuild(self):
        """Second call within 7d should NOT re-invoke cluster_keywords."""
        with patch("seo_entitlement.get_seo_tier") as mock_tier, \
             patch("content_planner.cluster_keywords") as mock_cluster:
            mock_tier.return_value = "Standard"
            mock_cluster.return_value = [{"hub_keyword": "test", "spokes": [], "total_volume": 100,
                                           "current_coverage_pct": 0.5, "avg_difficulty": 20}]
            r1 = self.client.get(
                "/api/content/clusters?company_id=123&property_uuid=caches-me",
                headers=_auth_headers(),
            )
            r2 = self.client.get(
                "/api/content/clusters?company_id=123&property_uuid=caches-me",
                headers=_auth_headers(),
            )
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(mock_cluster.call_count, 1)  # only built once


if __name__ == "__main__":
    unittest.main()
