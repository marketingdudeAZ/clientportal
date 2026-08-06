"""Tests for clerk_auth — Clerk session JWT verification (ADR 0002)."""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
os.environ.setdefault("HUBSPOT_API_KEY", "test-key")

import clerk_auth as ca  # noqa: E402


class ClerkAuthTests(unittest.TestCase):

    def setUp(self):
        ca._email_cache.clear()

    def test_no_header(self):
        self.assertIsNone(ca.verify_bearer(""))
        self.assertIsNone(ca.verify_bearer("Basic abc"))

    def test_malformed_token(self):
        self.assertIsNone(ca.verify_bearer("Bearer not-a-jwt"))
        self.assertIsNone(ca.verify_bearer("Bearer a.b"))  # 2 segments

    def test_no_config_returns_none(self):
        with mock.patch.dict(os.environ, {"CLERK_PUBLISHABLE_KEY": "", "CLERK_JWKS_URL": ""}):
            self.assertIsNone(ca.verify_bearer("Bearer a.b.c"))

    def test_frontend_domain_decodes_publishable_key(self):
        import base64
        domain = "star-example-1.clerk.accounts.dev"
        pk = "pk_test_" + base64.b64encode((domain + "$").encode()).decode().rstrip("=")
        with mock.patch.dict(os.environ, {"CLERK_PUBLISHABLE_KEY": pk}):
            self.assertEqual(ca._frontend_api_domain(), domain)
            self.assertEqual(ca._jwks_url(), f"https://{domain}/.well-known/jwks.json")

    def test_verified_token_resolves_email(self):
        claims = {"sub": "user_123", "exp": 9999999999}
        fake_key = mock.Mock(); fake_key.key = "k"
        fake_client = mock.Mock(); fake_client.get_signing_key_from_jwt.return_value = fake_key
        with mock.patch.object(ca, "_get_jwk_client", return_value=fake_client), \
             mock.patch("jwt.decode", return_value=claims), \
             mock.patch.object(ca, "_email_for_user", return_value="director@rpmliving.com"):
            ident = ca.verify_bearer("Bearer x.y.z")
        self.assertEqual(ident, {"user_id": "user_123", "email": "director@rpmliving.com"})

    def test_email_claim_short_circuits_lookup(self):
        claims = {"sub": "user_9", "email": "AM@RPMLiving.com"}
        fake_key = mock.Mock(); fake_key.key = "k"
        fake_client = mock.Mock(); fake_client.get_signing_key_from_jwt.return_value = fake_key
        with mock.patch.object(ca, "_get_jwk_client", return_value=fake_client), \
             mock.patch("jwt.decode", return_value=claims), \
             mock.patch.object(ca, "_email_for_user") as lookup:
            ident = ca.verify_bearer("Bearer x.y.z")
        self.assertEqual(ident["email"], "am@rpmliving.com")
        lookup.assert_not_called()

    def test_bad_signature_returns_none(self):
        fake_client = mock.Mock()
        fake_client.get_signing_key_from_jwt.side_effect = Exception("bad sig")
        with mock.patch.object(ca, "_get_jwk_client", return_value=fake_client):
            self.assertIsNone(ca.verify_bearer("Bearer x.y.z"))

    def test_verified_but_no_email_returns_none(self):
        claims = {"sub": "user_5"}
        fake_key = mock.Mock(); fake_key.key = "k"
        fake_client = mock.Mock(); fake_client.get_signing_key_from_jwt.return_value = fake_key
        with mock.patch.object(ca, "_get_jwk_client", return_value=fake_client), \
             mock.patch("jwt.decode", return_value=claims), \
             mock.patch.object(ca, "_email_for_user", return_value=""):
            self.assertIsNone(ca.verify_bearer("Bearer x.y.z"))


if __name__ == "__main__":
    unittest.main()
