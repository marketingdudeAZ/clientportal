"""Tier gating and feature entitlement checks for SEO portal."""

import sys
import os
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

from seo_entitlement import (
    _amount_to_tier,
    get_seo_tier,
    has_feature,
    meets_tier,
)


class TestAmountToTier(unittest.TestCase):
    def test_exact(self):
        self.assertEqual(_amount_to_tier(100), "Local")
        self.assertEqual(_amount_to_tier(300), "Lite")
        self.assertEqual(_amount_to_tier(500), "Basic")
        self.assertEqual(_amount_to_tier(800), "Standard")
        self.assertEqual(_amount_to_tier(1300), "Premium")

    def test_near(self):
        self.assertEqual(_amount_to_tier(150), "Local")
        self.assertEqual(_amount_to_tier(250), "Lite")
        self.assertEqual(_amount_to_tier(450), "Basic")
        self.assertEqual(_amount_to_tier(1100), "Premium")

    def test_zero(self):
        self.assertIsNone(_amount_to_tier(0))
        self.assertIsNone(_amount_to_tier(-5))


class TestMeetsTier(unittest.TestCase):
    def test_higher_passes(self):
        self.assertTrue(meets_tier("Premium", "Basic"))
        self.assertTrue(meets_tier("Standard", "Basic"))
        self.assertTrue(meets_tier("Basic", "Basic"))

    def test_lower_fails(self):
        self.assertFalse(meets_tier("Local", "Basic"))
        self.assertFalse(meets_tier("Lite", "Standard"))

    def test_none_fails(self):
        self.assertFalse(meets_tier(None, "Local"))


class TestHasFeature(unittest.TestCase):
    def test_dashboard_local(self):
        self.assertTrue(has_feature("Local", "dashboard"))

    def test_ai_mentions_needs_basic(self):
        self.assertFalse(has_feature("Lite", "ai_mentions"))
        self.assertTrue(has_feature("Basic", "ai_mentions"))

    def test_content_briefs_needs_standard(self):
        self.assertFalse(has_feature("Basic", "content_briefs"))
        self.assertTrue(has_feature("Standard", "content_briefs"))

    def test_content_decay_premium_only(self):
        self.assertFalse(has_feature("Standard", "content_decay"))
        self.assertTrue(has_feature("Premium", "content_decay"))


class TestGetSeoTier(unittest.TestCase):
    def test_no_company_id(self):
        self.assertIsNone(get_seo_tier(""))

    @patch("seo_entitlement._latest_deal_id", return_value=None)
    @patch("seo_entitlement._company_seo_budget", return_value=None)
    def test_neither_signal(self, _b, _d):
        self.assertIsNone(get_seo_tier("123"))

    @patch("seo_entitlement._latest_deal_id", return_value="deal-1")
    @patch("seo_entitlement._seo_line_item_amount", return_value=800)
    def test_sku_wins(self, _a, _d):
        self.assertEqual(get_seo_tier("123"), "Standard")

    @patch("seo_entitlement._latest_deal_id", return_value=None)
    @patch("seo_entitlement._company_seo_budget", return_value=1300)
    def test_budget_fallback(self, _b, _d):
        self.assertEqual(get_seo_tier("123"), "Premium")


if __name__ == "__main__":
    unittest.main()
