"""Tests for webhook-server/auth.py — HMAC request signature verification."""

import sys
import os
import time
from unittest import mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Mock config before importing auth
with mock.patch.dict(os.environ, {
    "HUBSPOT_API_KEY": "test-key",
    "WEBHOOK_SECRET": "test-webhook-secret-key-32bytes!",
}):
    from auth import generate_request_signature, verify_request_signature


class TestRequestSignature:
    def test_generate_is_deterministic(self):
        sig1 = generate_request_signature("user@test.com", "1234567890", "secret")
        sig2 = generate_request_signature("user@test.com", "1234567890", "secret")
        assert sig1 == sig2

    def test_different_emails_different_sigs(self):
        sig1 = generate_request_signature("a@test.com", "1234567890", "secret")
        sig2 = generate_request_signature("b@test.com", "1234567890", "secret")
        assert sig1 != sig2

    def test_different_timestamps_different_sigs(self):
        sig1 = generate_request_signature("user@test.com", "1234567890", "secret")
        sig2 = generate_request_signature("user@test.com", "1234567891", "secret")
        assert sig1 != sig2

    def test_different_secrets_different_sigs(self):
        sig1 = generate_request_signature("user@test.com", "1234567890", "secret1")
        sig2 = generate_request_signature("user@test.com", "1234567890", "secret2")
        assert sig1 != sig2

    def test_email_normalized(self):
        sig1 = generate_request_signature("User@Test.com", "1234567890", "secret")
        sig2 = generate_request_signature("user@test.com", "1234567890", "secret")
        assert sig1 == sig2


class TestVerifySignature:
    SECRET = "test-verify-secret"

    def test_valid_signature(self):
        ts = str(int(time.time()))
        sig = generate_request_signature("user@test.com", ts, self.SECRET)
        assert verify_request_signature("user@test.com", ts, sig, self.SECRET) is True

    def test_invalid_signature(self):
        ts = str(int(time.time()))
        assert verify_request_signature("user@test.com", ts, "bad-sig", self.SECRET) is False

    def test_expired_timestamp(self):
        ts = str(int(time.time()) - 600)  # 10 minutes ago
        sig = generate_request_signature("user@test.com", ts, self.SECRET)
        assert verify_request_signature("user@test.com", ts, sig, self.SECRET) is False

    def test_invalid_timestamp(self):
        sig = generate_request_signature("user@test.com", "not-a-number", self.SECRET)
        assert verify_request_signature("user@test.com", "not-a-number", sig, self.SECRET) is False

    def test_wrong_email(self):
        ts = str(int(time.time()))
        sig = generate_request_signature("user@test.com", ts, self.SECRET)
        assert verify_request_signature("other@test.com", ts, sig, self.SECRET) is False
