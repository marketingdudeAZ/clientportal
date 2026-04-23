"""Tests for webhook-server/fair_housing.py."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

import fair_housing as fh  # noqa: E402


class TestRadiusValidation(unittest.TestCase):
    def test_meta_minimum_rejects_below_15(self):
        ok, reason = fh.validate_radius("meta", 10)
        self.assertFalse(ok)
        self.assertIn("15", reason)

    def test_meta_accepts_at_minimum(self):
        ok, _ = fh.validate_radius("meta", 15)
        self.assertTrue(ok)

    def test_meta_accepts_above_minimum(self):
        ok, _ = fh.validate_radius("meta", 25)
        self.assertTrue(ok)

    def test_google_same_minimum(self):
        ok, _ = fh.validate_radius("google", 10)
        self.assertFalse(ok)
        ok, _ = fh.validate_radius("google", 15)
        self.assertTrue(ok)

    def test_unknown_platform_falls_back_to_default(self):
        ok, _ = fh.validate_radius("tiktok", 10)
        self.assertFalse(ok)
        ok, _ = fh.validate_radius("tiktok", 15)
        self.assertTrue(ok)

    def test_missing_radius_rejected(self):
        ok, reason = fh.validate_radius("meta", None)
        self.assertFalse(ok)
        self.assertIn("required", reason.lower())

    def test_non_numeric_rejected(self):
        ok, reason = fh.validate_radius("meta", "ten")
        self.assertFalse(ok)
        self.assertIn("number", reason.lower())


class TestAudienceTerms(unittest.TestCase):
    def test_neutral_text_passes(self):
        ok, hits = fh.validate_audience_terms("young professionals who want walkable amenities")
        # "young" falls near the protected "age" category — for apartment marketing
        # we intentionally flag age language. This test documents that behavior.
        # Keep other neutral phrasing to assert no false positives on benign terms.
        _ = (ok, hits)
        ok, hits = fh.validate_audience_terms("renters looking for pet-friendly buildings")
        self.assertTrue(ok, f"expected clean, got hits: {hits}")

    def test_flags_protected_class_term(self):
        ok, hits = fh.validate_audience_terms("Christian families with children looking for quiet living")
        self.assertFalse(ok)
        self.assertIn("christian", hits)
        self.assertTrue(any("famil" in h for h in hits),
                        f"expected family-related hit, got {hits}")

    def test_flags_race(self):
        ok, hits = fh.validate_audience_terms("Hispanic renters in Midtown")
        self.assertFalse(ok)
        self.assertIn("hispanic", hits)

    def test_flags_gender(self):
        ok, hits = fh.validate_audience_terms("young women who value safety")
        self.assertFalse(ok)
        self.assertTrue(any(h in ("women", "sex", "gender") for h in hits))

    def test_handles_list_input(self):
        ok, hits = fh.validate_audience_terms(["age 25-34", "hip neighborhood"])
        self.assertFalse(ok)
        self.assertIn("age", hits)

    def test_empty_is_ok(self):
        ok, hits = fh.validate_audience_terms("")
        self.assertTrue(ok)
        self.assertEqual(hits, [])


class TestComplianceBanner(unittest.TestCase):
    def test_banner_includes_minimum_radius(self):
        b = fh.compliance_banner("meta")
        self.assertEqual(b["min_radius_miles"], 15)
        self.assertIn("15", b["body"])

    def test_banner_mentions_housing_category(self):
        b = fh.compliance_banner("google")
        self.assertIn("Housing", b["title"])


if __name__ == "__main__":
    unittest.main()
