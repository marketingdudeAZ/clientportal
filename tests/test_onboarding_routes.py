"""Tests for webhook-server/routes/onboarding.py — name derivation + endpoints."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

from routes.onboarding import derive_rpm_name  # noqa: E402


class TestNameDerivation(unittest.TestCase):
    def test_simple_name(self):
        self.assertEqual(derive_rpm_name("jane.smith@rpmliving.com"), "Jane Smith")

    def test_capitalized_input_normalized(self):
        self.assertEqual(derive_rpm_name("Jane.Smith@RPMLiving.com"), "Jane Smith")

    def test_hyphenated_last_name(self):
        self.assertEqual(derive_rpm_name("anna.smith-jones@rpmliving.com"), "Anna Smith-Jones")

    def test_wrong_domain_returns_empty(self):
        self.assertEqual(derive_rpm_name("jane.smith@gmail.com"), "")

    def test_no_dot_returns_empty(self):
        # We require first.last format — single-token usernames don't have a parseable name
        self.assertEqual(derive_rpm_name("janesmith@rpmliving.com"), "")

    def test_empty_string_returns_empty(self):
        self.assertEqual(derive_rpm_name(""), "")


if __name__ == "__main__":
    unittest.main()
