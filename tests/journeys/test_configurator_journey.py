"""End-to-end journey test: POST /api/configurator-submit.

Covers HMAC validation, payload routing to deal_creator / quote_generator /
notifier, and error surfaces. All outbound HubSpot calls are mocked.
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


def _sign(body: bytes) -> str:
    secret = os.environ["WEBHOOK_SECRET"].encode()
    digest = hmac.new(secret, body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


class TestConfiguratorSubmitJourney(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import server
        cls.app = server.app
        cls.client = cls.app.test_client()

    def _payload(self):
        return {
            "uuid": "prop-uuid-1",
            "hubspot_company_id": "company-123",
            "selections": {
                "seo": {"tier": "Standard", "monthly": 800, "setup": 0},
            },
            "totals": {"monthly": 800, "setup": 0},
        }

    def test_rejects_unsigned_request(self):
        resp = self.client.post(
            "/api/configurator-submit",
            data=json.dumps(self._payload()),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 401)

    def test_rejects_wrong_signature(self):
        body = json.dumps(self._payload()).encode()
        resp = self.client.post(
            "/api/configurator-submit",
            data=body,
            content_type="application/json",
            headers={"X-Hub-Signature-256": "sha256=" + "0" * 64},
        )
        self.assertEqual(resp.status_code, 401)

    def test_valid_signature_runs_full_pipeline(self):
        body = json.dumps(self._payload()).encode()
        sig = _sign(body)
        with mock.patch("deal_creator.create_deal_with_line_items",
                        return_value="deal-99") as create_deal, \
             mock.patch("quote_generator.generate_and_send_quote",
                        return_value="quote-99") as gen_quote, \
             mock.patch("notifier.notify_am",
                        return_value="task-99") as notify:
            resp = self.client.post(
                "/api/configurator-submit",
                data=body,
                content_type="application/json",
                headers={"X-Hub-Signature-256": sig},
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["deal_id"], "deal-99")
        self.assertEqual(data["quote_id"], "quote-99")
        self.assertEqual(data["task_id"], "task-99")
        create_deal.assert_called_once_with(
            "company-123",
            {"seo": {"tier": "Standard", "monthly": 800, "setup": 0}},
            {"monthly": 800, "setup": 0},
        )
        gen_quote.assert_called_once_with("deal-99", "company-123")
        notify.assert_called_once()

    def test_missing_required_fields_returns_400(self):
        body = json.dumps({"selections": {}, "totals": {}}).encode()
        resp = self.client.post(
            "/api/configurator-submit",
            data=body,
            content_type="application/json",
            headers={"X-Hub-Signature-256": _sign(body)},
        )
        self.assertEqual(resp.status_code, 400)

    def test_downstream_failure_returns_500_with_detail(self):
        body = json.dumps(self._payload()).encode()
        with mock.patch("deal_creator.create_deal_with_line_items",
                        side_effect=RuntimeError("HubSpot 503")):
            resp = self.client.post(
                "/api/configurator-submit",
                data=body,
                content_type="application/json",
                headers={"X-Hub-Signature-256": _sign(body)},
            )
        self.assertEqual(resp.status_code, 500)
        self.assertIn("HubSpot 503", resp.get_json().get("detail", ""))


if __name__ == "__main__":
    unittest.main()
