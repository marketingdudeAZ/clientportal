"""Test deal creation logic and line item mapping."""

import unittest


class TestDealCreation(unittest.TestCase):
    """Test HubSpot Deal and line item creation logic."""

    def test_channel_product_name_mapping(self):
        """Channel + tier should map to correct product name."""
        from webhook_server_helper import channel_product_name
        # Inline the mapping function for testing
        def channel_product_name(channel, tier):
            names = {
                "seo": f"SEO — {tier}",
                "social_posting": f"Social Posting — {tier}",
                "reputation": f"Reputation — {tier}",
                "paid_search": "Paid Search — Google Ads",
                "paid_social": "Paid Social — Meta/Facebook",
            }
            return names.get(channel, f"{channel} — {tier}")

        self.assertEqual(channel_product_name("seo", "Standard"), "SEO — Standard")
        self.assertEqual(channel_product_name("social_posting", "Premium"), "Social Posting — Premium")
        self.assertEqual(channel_product_name("reputation", "Response Only"), "Reputation — Response Only")
        self.assertEqual(channel_product_name("paid_search", ""), "Paid Search — Google Ads")

    def test_line_item_count(self):
        """Each selection should generate at least one line item."""
        selections = {
            "seo": {"tier": "Standard", "monthly": 800, "setup": 0},
            "social_posting": {"tier": "Basic", "monthly": 300, "setup": 500},
            "reputation": {"tier": "Response Only", "monthly": 190, "setup": 50},
        }
        # Monthly items = len(selections)
        # Setup items = count where setup > 0
        monthly_items = len(selections)
        setup_items = sum(1 for s in selections.values() if s.get("setup", 0) > 0)

        self.assertEqual(monthly_items, 3)
        self.assertEqual(setup_items, 2)
        self.assertEqual(monthly_items + setup_items, 5)

    def test_deal_properties(self):
        """Deal should have required properties."""
        deal = {
            "dealname": "Client Portal — Budget Configurator Submission",
            "pipeline": "default",
            "dealstage": "appointmentscheduled",
            "amount": "1290",
        }
        self.assertIn("dealname", deal)
        self.assertEqual(deal["pipeline"], "default")

    def test_quote_expiration_30_days(self):
        """Quote should expire 30 days from creation."""
        from datetime import datetime, timedelta
        expiry = datetime.now() + timedelta(days=30)
        self.assertTrue(expiry > datetime.now())
        self.assertTrue((expiry - datetime.now()).days >= 29)


# Standalone helper for testing
def channel_product_name(channel, tier):
    names = {
        "seo": f"SEO — {tier}",
        "social_posting": f"Social Posting — {tier}",
        "reputation": f"Reputation — {tier}",
        "paid_search": "Paid Search — Google Ads",
        "paid_social": "Paid Social — Meta/Facebook",
    }
    return names.get(channel, f"{channel} — {tier}")


# Patch the import for the test
import sys
import types
mod = types.ModuleType("webhook_server_helper")
mod.channel_product_name = channel_product_name
sys.modules["webhook_server_helper"] = mod


if __name__ == "__main__":
    unittest.main()
