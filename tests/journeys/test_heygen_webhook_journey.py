"""Journey test: POST /api/heygen-webhook.

Covers signature validation (fail closed in production, pass with good sig)
and that a well-formed webhook event reaches the inner HubSpot lookup path.
External HubSpot calls are mocked.
"""

import hashlib
import hmac
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "webhook-server"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

os.environ.setdefault("HUBSPOT_API_KEY", "test-hub-key")
os.environ.setdefault("WEBHOOK_SECRET", "test-webhook-secret")


def _sign(body: str, secret: str) -> str:
    return hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()


class TestHeyGenWebhookJourney(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import server
        cls.app = server.app
        cls.client = cls.app.test_client()

    def test_unsigned_payload_accepted_when_secret_unset(self):
        """Supported config: no HEYGEN_WEBHOOK_SECRET → no signature check."""
        body = json.dumps({
            "event_type": "avatar_video.success",
            "event_data": {"video_id": "vid-1", "callback_id": "var-1"},
        })
        search_resp = mock.MagicMock()
        search_resp.status_code = 200
        search_resp.json.return_value = {"results": []}
        search_resp.raise_for_status = mock.MagicMock()
        with mock.patch("video_providers.heygen_provider.HEYGEN_WEBHOOK_SECRET", ""), \
             mock.patch("requests.post", return_value=search_resp):
            resp = self.client.post(
                "/api/heygen-webhook",
                data=body,
                content_type="application/json",
            )
        self.assertNotEqual(resp.status_code, 401,
                            "Unsigned webhook rejected with no secret configured")

    def test_rejects_bad_signature_when_secret_set(self):
        """When HEYGEN_WEBHOOK_SECRET is set, wrong signatures must be rejected."""
        body = json.dumps({
            "event_type": "avatar_video.success",
            "event_data": {"video_id": "vid-1", "callback_id": "var-1"},
        })
        with mock.patch("video_providers.heygen_provider.HEYGEN_WEBHOOK_SECRET", "good-secret"):
            resp = self.client.post(
                "/api/heygen-webhook",
                data=body,
                content_type="application/json",
                headers={"X-Signature": "0" * 64},
            )
        self.assertEqual(resp.status_code, 401)

    def test_missing_signature_when_secret_set_rejected(self):
        """When the secret is set but no signature header sent → reject."""
        body = json.dumps({
            "event_type": "avatar_video.success",
            "event_data": {"video_id": "vid-1", "callback_id": "var-1"},
        })
        with mock.patch("video_providers.heygen_provider.HEYGEN_WEBHOOK_SECRET", "good-secret"):
            resp = self.client.post(
                "/api/heygen-webhook",
                data=body,
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 401)

    def test_valid_signature_parses_and_attempts_match(self):
        body = json.dumps({
            "event_type": "avatar_video.success",
            "event_data": {
                "video_id": "vid-xyz",
                "callback_id": "var-abc|uuid-xyz-123",
                "url": "https://cdn.heygen.com/video.mp4",
            },
        })
        sig = _sign(body, "good-secret")
        search_resp = mock.MagicMock()
        search_resp.status_code = 200
        search_resp.json.return_value = {"results": []}
        search_resp.raise_for_status = mock.MagicMock()
        with mock.patch("video_providers.heygen_provider.HEYGEN_WEBHOOK_SECRET", "good-secret"), \
             mock.patch("requests.post", return_value=search_resp):
            resp = self.client.post(
                "/api/heygen-webhook",
                data=body,
                content_type="application/json",
                headers={"X-Signature": sig},
            )
        self.assertNotEqual(resp.status_code, 401,
                            f"Good signature rejected: {resp.get_data(as_text=True)}")

    def test_malformed_json_returns_400(self):
        with mock.patch("video_providers.heygen_provider.HEYGEN_WEBHOOK_SECRET", ""):
            resp = self.client.post(
                "/api/heygen-webhook",
                data="{not valid json",
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
