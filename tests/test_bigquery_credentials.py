"""Credential handling for bigquery_client.

The failure this guards against is silent: with credentials supplied as raw JSON
in an env var (how the host provides them) the old path-only code reported "not
configured", portal_tickets swallowed the write, and the portal showed
"No open requests" forever with no error anywhere.
"""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

import bigquery_client as bq  # noqa: E402


_FAKE_SA = {
    "type": "service_account",
    "project_id": "rpm-test",
    "private_key_id": "abc123",
    "client_email": "test@rpm-test.iam.gserviceaccount.com",
}


class ServiceAccountResolution(unittest.TestCase):
    def test_raw_json_in_env_is_accepted(self):
        """The host supplies env vars, not files. This is the case that broke."""
        with mock.patch.object(bq, "BIGQUERY_SERVICE_ACCOUNT_JSON",
                               json.dumps(_FAKE_SA)):
            self.assertEqual(bq._sa_info()["client_email"], _FAKE_SA["client_email"])

    def test_leading_whitespace_around_json_is_tolerated(self):
        with mock.patch.object(bq, "BIGQUERY_SERVICE_ACCOUNT_JSON",
                               "\n  " + json.dumps(_FAKE_SA) + "  \n"):
            self.assertIsNotNone(bq._sa_info())

    def test_a_file_path_still_works(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(_FAKE_SA, fh)
            path = fh.name
        try:
            with mock.patch.object(bq, "BIGQUERY_SERVICE_ACCOUNT_JSON", path):
                self.assertEqual(bq._sa_info()["project_id"], "rpm-test")
        finally:
            os.unlink(path)

    def test_unset_is_none(self):
        with mock.patch.object(bq, "BIGQUERY_SERVICE_ACCOUNT_JSON", ""):
            self.assertIsNone(bq._sa_info())

    def test_the_env_template_placeholder_is_none(self):
        with mock.patch.object(bq, "BIGQUERY_SERVICE_ACCOUNT_JSON",
                               "path/to/service_account.json"):
            self.assertIsNone(bq._sa_info())

    def test_a_missing_file_is_none_not_a_raise(self):
        with mock.patch.object(bq, "BIGQUERY_SERVICE_ACCOUNT_JSON",
                               "/nonexistent/sa.json"):
            self.assertIsNone(bq._sa_info())

    def test_malformed_json_is_none_not_a_raise(self):
        with mock.patch.object(bq, "BIGQUERY_SERVICE_ACCOUNT_JSON", "{not json"):
            self.assertIsNone(bq._sa_info())


class ConfiguredFlagAgreesWithTheClient(unittest.TestCase):
    """These two disagreeing is how the silent-empty-list bug reappears."""

    def test_json_in_env_reports_configured(self):
        with mock.patch.object(bq, "BIGQUERY_SERVICE_ACCOUNT_JSON",
                               json.dumps(_FAKE_SA)), \
             mock.patch.object(bq, "BIGQUERY_PROJECT_ID", "rpm-test"):
            self.assertTrue(bq.is_bigquery_configured())

    def test_no_project_id_reports_unconfigured(self):
        with mock.patch.object(bq, "BIGQUERY_SERVICE_ACCOUNT_JSON",
                               json.dumps(_FAKE_SA)), \
             mock.patch.object(bq, "BIGQUERY_PROJECT_ID", ""):
            self.assertFalse(bq.is_bigquery_configured())

    def test_no_credentials_reports_unconfigured(self):
        with mock.patch.object(bq, "BIGQUERY_SERVICE_ACCOUNT_JSON", ""), \
             mock.patch.object(bq, "BIGQUERY_PROJECT_ID", "rpm-test"):
            self.assertFalse(bq.is_bigquery_configured())

    def test_configured_implies_sa_info_resolves(self):
        """The invariant, stated directly: whenever is_bigquery_configured() is
        True, _get_client() must be able to obtain credentials."""
        for value in (json.dumps(_FAKE_SA), "", "path/to/service_account.json"):
            with mock.patch.object(bq, "BIGQUERY_SERVICE_ACCOUNT_JSON", value), \
                 mock.patch.object(bq, "BIGQUERY_PROJECT_ID", "rpm-test"):
                if bq.is_bigquery_configured():
                    self.assertIsNotNone(bq._sa_info(), f"disagreed for {value!r}")


if __name__ == "__main__":
    unittest.main()
