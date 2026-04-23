"""Tests for webhook-server/keyword_classifier.py."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

import keyword_classifier as kc  # noqa: E402


def _kw(**overrides):
    base = {
        "keyword":            "midtown 1 bedroom apartments",
        "volume":             260,
        "difficulty":         40,
        "competition_index":  55,
        "cpc_low":            1.10,
        "cpc_high":           2.80,
        "intent":             "commercial",
        "source_neighborhood":"Midtown",
    }
    base.update(overrides)
    return base


class TestClassifier(unittest.TestCase):
    def test_competitor_brand_is_paid_only(self):
        rows = [_kw(keyword="the pinnacle apartments reviews")]
        result = kc.classify(rows, competitor_brands=["The Pinnacle"])
        self.assertEqual(result[0]["label"], kc.LABEL_PAID)
        self.assertIn("Competitor", result[0]["reason"])

    def test_property_brand_is_seo_target(self):
        rows = [_kw(keyword="aurora heights apartments")]
        result = kc.classify(rows, property_brand="Aurora Heights")
        self.assertEqual(result[0]["label"], kc.LABEL_SEO)
        self.assertEqual(result[0]["priority"], "high")

    def test_low_volume_is_low_priority_seo(self):
        rows = [_kw(volume=3)]
        result = kc.classify(rows)
        self.assertEqual(result[0]["label"], kc.LABEL_SEO)
        self.assertEqual(result[0]["priority"], "low")

    def test_high_kd_with_commercial_cpc_is_paid_only(self):
        rows = [_kw(difficulty=80, competition_index=80, cpc_high=3.50)]
        result = kc.classify(rows)
        self.assertEqual(result[0]["label"], kc.LABEL_PAID)

    def test_informational_longtail_is_seo(self):
        rows = [_kw(
            keyword="what neighborhoods are safe in midtown",
            difficulty=35, intent="informational", cpc_high=0.40,
        )]
        result = kc.classify(rows)
        self.assertEqual(result[0]["label"], kc.LABEL_SEO)

    def test_mixed_signal_is_both(self):
        rows = [_kw(difficulty=55, competition_index=55, volume=500, cpc_high=2.00)]
        result = kc.classify(rows)
        self.assertEqual(result[0]["label"], kc.LABEL_BOTH)

    def test_empty_keyword_does_not_crash(self):
        rows = [_kw(keyword="")]
        result = kc.classify(rows)
        self.assertEqual(len(result), 1)

    def test_input_is_not_mutated(self):
        rows = [_kw()]
        snapshot = dict(rows[0])
        kc.classify(rows)
        self.assertEqual(rows[0], snapshot)


if __name__ == "__main__":
    unittest.main()
