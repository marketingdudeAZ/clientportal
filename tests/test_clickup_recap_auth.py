"""Auth tests for the ticket-recap webhook receiver.

CLICKUP_WEBHOOK_SECRET is a comma-separated list (ClickUp mints one secret per
native webhook), and this endpoint writes client-visible notes — so unlike the
property-brief receiver, an unset secret must REJECT, never fail open.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

import config  # noqa: E402
import clickup_recap  # noqa: E402


def _sig(secret: str, raw: bytes) -> str:
    return hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


class VerifyWebhookAuth(unittest.TestCase):
    RAW = b'{"task_id": "abc123"}'

    def _verify(self, secret_env, signature="", token=""):
        with mock.patch.object(config, "CLICKUP_WEBHOOK_SECRET", secret_env):
            return clickup_recap.verify_webhook_auth(self.RAW, signature, token)

    def test_no_secret_configured_rejects_everything(self):
        self.assertFalse(self._verify("", signature=_sig("x", self.RAW)))
        self.assertFalse(self._verify("", token="anything"))
        self.assertFalse(self._verify("", signature="", token=""))
        self.assertFalse(self._verify(" , ,", token="anything"))  # only blanks

    def test_single_secret_hmac(self):
        self.assertTrue(self._verify("s1", signature=_sig("s1", self.RAW)))
        self.assertFalse(self._verify("s1", signature=_sig("wrong", self.RAW)))

    def test_multi_secret_hmac_any_entry_verifies(self):
        env = "brief-secret, recap-secret"
        self.assertTrue(self._verify(env, signature=_sig("brief-secret", self.RAW)))
        self.assertTrue(self._verify(env, signature=_sig("recap-secret", self.RAW)))
        self.assertFalse(self._verify(env, signature=_sig("other", self.RAW)))

    def test_token_matches_any_entry(self):
        env = "brief-secret,recap-secret"
        self.assertTrue(self._verify(env, token="recap-secret"))
        self.assertTrue(self._verify(env, token="brief-secret"))
        self.assertFalse(self._verify(env, token="nope"))

    def test_signature_takes_precedence_over_token(self):
        # A present-but-wrong signature must fail even if the token is valid —
        # a signed request that doesn't verify is never rescued by a token.
        self.assertFalse(self._verify("s1", signature=_sig("wrong", self.RAW), token="s1"))

    def test_whitespace_around_entries_tolerated(self):
        self.assertTrue(self._verify("  s1 ,  s2  ", token="s2"))
        self.assertTrue(self._verify("  s1 ,  s2  ", signature=_sig("s1", self.RAW)))


if __name__ == "__main__":
    unittest.main()
