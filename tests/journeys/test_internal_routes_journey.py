"""Journey test: the four routes guarded by @require_internal_key.

Does not drive the full refresh pipeline — that would need BigQuery and
DataForSEO mocks. Just proves the auth decorator is applied correctly:
wrong / missing key returns 401, correct key is allowed through.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "webhook-server"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

os.environ.setdefault("HUBSPOT_API_KEY", "test-hub-key")
os.environ.setdefault("WEBHOOK_SECRET", "test-webhook-secret")
os.environ["INTERNAL_API_KEY"] = "internal-secret"


INTERNAL_ROUTES = [
    ("/api/internal/seo-refresh-property", {"company_id": "c1"}),
    ("/api/internal/sync-properties-to-bq", {}),
    ("/api/red-light/ingest-csv", None),  # body is raw CSV
]


class TestInternalKeyGuard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import server
        cls.app = server.app
        cls.client = cls.app.test_client()

    def _missing_key_returns_401(self, route, body):
        resp = self.client.post(route, json=body) if body is not None \
            else self.client.post(route, data="id\n1")
        self.assertEqual(resp.status_code, 401, f"{route} accepted missing key")

    def _wrong_key_returns_401(self, route, body):
        headers = {"X-Internal-Key": "nope"}
        resp = self.client.post(route, json=body, headers=headers) if body is not None \
            else self.client.post(route, data="id\n1", headers=headers)
        self.assertEqual(resp.status_code, 401, f"{route} accepted wrong key")

    def test_all_routes_reject_missing_key(self):
        for route, body in INTERNAL_ROUTES:
            with self.subTest(route=route):
                self._missing_key_returns_401(route, body)

    def test_all_routes_reject_wrong_key(self):
        for route, body in INTERNAL_ROUTES:
            with self.subTest(route=route):
                self._wrong_key_returns_401(route, body)

    def test_correct_key_passes_auth_layer(self):
        """With the correct key, the request must get past auth. We mock the
        handler's downstream call so we don't need BigQuery / DataForSEO."""
        headers = {"X-Internal-Key": "internal-secret"}
        with mock.patch("seo_refresh_cron.refresh_ranks", return_value=5), \
             mock.patch("seo_refresh_cron.refresh_ai_mentions",
                        return_value={"composite_index": 0.42}), \
             mock.patch("seo_refresh_cron.refresh_onpage", return_value=88), \
             mock.patch("seo_refresh_cron._meets_tier", return_value=False), \
             mock.patch("requests.get") as get_req:
            # Mock the HubSpot company lookup that happens before refresh.
            get_req.return_value.status_code = 200
            get_req.return_value.json.return_value = {
                "properties": {
                    "domain": "example.com",
                    "city": "Phoenix",
                    "name": "Test",
                    "uuid": "u-1",
                },
            }
            get_req.return_value.raise_for_status = mock.MagicMock()
            resp = self.client.post(
                "/api/internal/seo-refresh-property",
                json={"company_id": "c1"},
                headers=headers,
            )
            # Either 200 (happy) or 500 (inner pipeline failed); what we
            # specifically DON'T want is 401.
            self.assertNotEqual(resp.status_code, 401,
                                "Correct internal key got rejected by auth")


class TestRedLightRunDualAuth(unittest.TestCase):
    """/api/red-light/run accepts EITHER X-Portal-Email OR X-Internal-Key."""

    @classmethod
    def setUpClass(cls):
        import server
        cls.client = server.app.test_client()

    def test_neither_credential_returns_401(self):
        resp = self.client.post("/api/red-light/run",
                                json={"property_uuid": "p-1"})
        self.assertEqual(resp.status_code, 401)

    def test_portal_email_allows_through(self):
        with mock.patch("red_light_ingest.run_single_property",
                        return_value={"status": "ok"}):
            resp = self.client.post(
                "/api/red-light/run",
                json={"property_uuid": "p-1"},
                headers={"X-Portal-Email": "kyle@rpm.test"},
            )
        self.assertEqual(resp.status_code, 200)

    def test_internal_key_allows_through(self):
        with mock.patch("red_light_ingest.run_single_property",
                        return_value={"status": "ok"}):
            resp = self.client.post(
                "/api/red-light/run",
                json={"property_uuid": "p-1"},
                headers={"X-Internal-Key": "internal-secret"},
            )
        self.assertEqual(resp.status_code, 200)


if __name__ == "__main__":
    unittest.main()
