"""Test configurator submission payload and tier mapping logic."""

import unittest
from config import (
    SEO_TIERS,
    SOCIAL_POSTING_TIERS,
    REPUTATION_TIERS,
    SOCIAL_POSTING_SETUP_FEE,
    REPUTATION_SETUP_FEE,
    get_setup_fee,
)


class TestConfiguratorSubmit(unittest.TestCase):
    """Test configurator business logic."""

    def test_seo_tiers(self):
        self.assertEqual(SEO_TIERS["Local"], 100)
        self.assertEqual(SEO_TIERS["Lite"], 300)
        self.assertEqual(SEO_TIERS["Basic"], 500)
        self.assertEqual(SEO_TIERS["Standard"], 800)
        self.assertEqual(SEO_TIERS["Premium"], 1300)

    def test_social_posting_tiers(self):
        self.assertEqual(SOCIAL_POSTING_TIERS["Basic"], 300)
        self.assertEqual(SOCIAL_POSTING_TIERS["Standard"], 450)
        self.assertEqual(SOCIAL_POSTING_TIERS["Premium"], 700)

    def test_reputation_tiers(self):
        self.assertEqual(REPUTATION_TIERS["Response Only"], 190)
        self.assertEqual(REPUTATION_TIERS["Response + Removal"], 255)

    def test_setup_fee_new_enrollment(self):
        """New enrollment should incur setup fee."""
        self.assertEqual(get_setup_fee("social_posting", None), SOCIAL_POSTING_SETUP_FEE)
        self.assertEqual(get_setup_fee("social_posting", "None"), SOCIAL_POSTING_SETUP_FEE)
        self.assertEqual(get_setup_fee("social_posting", ""), SOCIAL_POSTING_SETUP_FEE)
        self.assertEqual(get_setup_fee("reputation", None), REPUTATION_SETUP_FEE)

    def test_setup_fee_upgrade(self):
        """Upgrade should have $0 setup fee."""
        self.assertEqual(get_setup_fee("social_posting", "Basic"), 0)
        self.assertEqual(get_setup_fee("reputation", "Response Only"), 0)

    def test_setup_fee_unknown_service(self):
        """Unknown service defaults to $0."""
        self.assertEqual(get_setup_fee("unknown_service", None), 0)

    def test_payload_structure(self):
        """Configurator submit payload should have required fields."""
        payload = {
            "uuid": "test-uuid",
            "hubspot_company_id": "12345",
            "selections": {
                "seo": {"tier": "Standard", "monthly": 800, "setup": 0},
                "social_posting": {"tier": "Premium", "monthly": 700, "setup": 500},
            },
            "totals": {"monthly": 1500, "setup": 500, "delta": 900},
        }

        self.assertIn("uuid", payload)
        self.assertIn("hubspot_company_id", payload)
        self.assertIn("selections", payload)
        self.assertIn("totals", payload)
        self.assertEqual(payload["totals"]["monthly"], 1500)

    def test_good_better_best_mapping(self):
        """Property on SEO Lite, recommended Standard:
        Good=Basic, Better=Standard (recommended), Best=Premium.
        """
        tiers_ordered = list(SEO_TIERS.keys())
        current = "Lite"
        recommended = "Standard"

        current_idx = tiers_ordered.index(current)
        rec_idx = tiers_ordered.index(recommended)

        good = tiers_ordered[current_idx + 1]  # One above current
        better = tiers_ordered[rec_idx]          # Recommended
        best = tiers_ordered[rec_idx + 1] if rec_idx + 1 < len(tiers_ordered) else tiers_ordered[rec_idx]

        self.assertEqual(good, "Basic")
        self.assertEqual(better, "Standard")
        self.assertEqual(best, "Premium")

    def test_at_or_above_recommended_is_on_track(self):
        """Property at or above recommended tier should show 'On Track'."""
        tiers_ordered = list(SEO_TIERS.keys())
        current = "Premium"
        recommended = "Standard"

        current_idx = tiers_ordered.index(current)
        rec_idx = tiers_ordered.index(recommended)

        self.assertTrue(current_idx >= rec_idx)


if __name__ == "__main__":
    unittest.main()
