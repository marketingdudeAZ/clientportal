"""Deal creation tests — exercise the real deal_creator module.

Previous version of this file mocked the module under test and imported a
fake `webhook_server_helper` that doesn't exist. These tests hit the actual
`deal_creator.create_deal_with_line_items` and `deal_creator._channel_product_name`
with `requests` mocked out so we assert the HubSpot wire format.
"""

import os
import sys
import unittest
from unittest import mock

# Make webhook-server/ importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# deal_creator reads HUBSPOT_API_KEY from config at import time.
with mock.patch.dict(os.environ, {"HUBSPOT_API_KEY": "test-key"}):
    import deal_creator  # noqa: E402


def _resp(body, status_code=200):
    m = mock.MagicMock()
    m.status_code = status_code
    m.json.return_value = body
    m.raise_for_status = mock.MagicMock()
    return m


class TestChannelProductName(unittest.TestCase):
    def test_seo_tier(self):
        self.assertEqual(deal_creator._channel_product_name("seo", "Standard"),
                         "SEO — Standard")

    def test_social_posting_tier(self):
        self.assertEqual(deal_creator._channel_product_name("social_posting", "Premium"),
                         "Social Posting — Premium")

    def test_reputation_tier(self):
        self.assertEqual(deal_creator._channel_product_name("reputation", "Response Only"),
                         "Reputation — Response Only")

    def test_paid_search_ignores_tier(self):
        self.assertEqual(deal_creator._channel_product_name("paid_search", ""),
                         "Paid Search — Google Ads")

    def test_paid_social_ignores_tier(self):
        self.assertEqual(deal_creator._channel_product_name("paid_social", "anything"),
                         "Paid Social — Meta/Facebook")

    def test_unknown_channel_falls_back(self):
        self.assertEqual(deal_creator._channel_product_name("mystery", "Gold"),
                         "mystery — Gold")


class TestCreateDealWithLineItems(unittest.TestCase):
    """End-to-end exercise of the real deal_creator with requests mocked."""

    def setUp(self):
        # Map of endpoint suffix -> response body, so each HTTP call resolves
        # deterministically.
        self._post = mock.patch("deal_creator.requests.post").start()
        self._put = mock.patch("deal_creator.requests.put").start()

        # Counter so each line-item POST gets a unique id.
        self._line_item_counter = iter(range(1000, 2000))

        def post_side_effect(url, **kwargs):
            if "/deals" in url:
                return _resp({"id": "deal-42"})
            if "/line_items" in url:
                return _resp({"id": f"li-{next(self._line_item_counter)}"})
            return _resp({})

        def put_side_effect(url, **kwargs):
            return _resp({})

        self._post.side_effect = post_side_effect
        self._put.side_effect = put_side_effect

    def tearDown(self):
        mock.patch.stopall()

    def test_returns_deal_id(self):
        deal_id = deal_creator.create_deal_with_line_items(
            company_id="comp-1",
            selections={
                "seo": {"tier": "Standard", "monthly": 800, "setup": 0},
            },
            totals={"monthly": 800, "setup": 0},
        )
        self.assertEqual(deal_id, "deal-42")

    def test_deal_body_shape(self):
        deal_creator.create_deal_with_line_items(
            company_id="comp-1",
            selections={"seo": {"tier": "Standard", "monthly": 800, "setup": 0}},
            totals={"monthly": 800, "setup": 0},
        )
        # First POST is the deal itself
        deal_call = self._post.call_args_list[0]
        url = deal_call.args[0] if deal_call.args else deal_call.kwargs.get("url")
        body = deal_call.kwargs["json"]
        self.assertIn("/crm/v3/objects/deals", url)
        self.assertEqual(body["properties"]["dealname"],
                         "Client Portal — Budget Configurator Submission")
        self.assertEqual(body["properties"]["pipeline"], "default")
        self.assertEqual(body["properties"]["dealstage"], "appointmentscheduled")
        self.assertEqual(body["properties"]["amount"], "800")

    def test_associates_deal_with_company(self):
        deal_creator.create_deal_with_line_items(
            company_id="comp-1",
            selections={"seo": {"tier": "Standard", "monthly": 800, "setup": 0}},
            totals={"monthly": 800, "setup": 0},
        )
        # First PUT is the deal->company association
        assoc_url = self._put.call_args_list[0].args[0]
        self.assertIn("/deals/deal-42/associations/companies/comp-1", assoc_url)
        self.assertIn("deal_to_company", assoc_url)

    def test_monthly_only_produces_one_line_item(self):
        deal_creator.create_deal_with_line_items(
            company_id="c1",
            selections={"seo": {"tier": "Standard", "monthly": 800, "setup": 0}},
            totals={"monthly": 800, "setup": 0},
        )
        line_item_posts = [c for c in self._post.call_args_list
                           if "/line_items" in (c.args[0] if c.args else "")]
        self.assertEqual(len(line_item_posts), 1)
        props = line_item_posts[0].kwargs["json"]["properties"]
        self.assertEqual(props["name"], "SEO — Standard")
        self.assertEqual(props["price"], "800")
        self.assertEqual(props["recurringbillingfrequency"], "monthly")

    def test_setup_fee_adds_second_line_item(self):
        deal_creator.create_deal_with_line_items(
            company_id="c1",
            selections={"social_posting": {"tier": "Basic", "monthly": 300, "setup": 500}},
            totals={"monthly": 300, "setup": 500},
        )
        line_item_posts = [c for c in self._post.call_args_list
                           if "/line_items" in (c.args[0] if c.args else "")]
        self.assertEqual(len(line_item_posts), 2)
        names = [p.kwargs["json"]["properties"]["name"] for p in line_item_posts]
        self.assertIn("Social Posting — Basic", names)
        self.assertIn("Social Posting — Basic — Setup Fee", names)

    def test_multiple_selections_each_get_line_items(self):
        deal_creator.create_deal_with_line_items(
            company_id="c1",
            selections={
                "seo":             {"tier": "Standard",      "monthly": 800, "setup": 0},
                "social_posting":  {"tier": "Basic",         "monthly": 300, "setup": 500},
                "reputation":      {"tier": "Response Only", "monthly": 190, "setup": 50},
            },
            totals={"monthly": 1290, "setup": 550},
        )
        line_item_posts = [c for c in self._post.call_args_list
                           if "/line_items" in (c.args[0] if c.args else "")]
        # 3 monthly + 2 setup (seo has no setup fee)
        self.assertEqual(len(line_item_posts), 5)

    def test_each_line_item_is_associated_to_deal(self):
        deal_creator.create_deal_with_line_items(
            company_id="c1",
            selections={
                "seo":            {"tier": "Standard", "monthly": 800, "setup": 0},
                "social_posting": {"tier": "Basic",    "monthly": 300, "setup": 500},
            },
            totals={"monthly": 1100, "setup": 500},
        )
        # line_item -> deal associations go through PUT. Skip the deal->company one.
        li_assoc = [c for c in self._put.call_args_list
                    if "/line_items/" in c.args[0] and "line_item_to_deal" in c.args[0]]
        # 2 monthly + 1 setup = 3 associations
        self.assertEqual(len(li_assoc), 3)


class TestProductMapLookup(unittest.TestCase):
    """When a product is in the catalog, the line item should include hs_product_id."""

    def test_hs_product_id_attached_when_mapped(self):
        with mock.patch("deal_creator.requests.post") as post, \
             mock.patch("deal_creator.requests.put") as put, \
             mock.patch.dict(deal_creator.PRODUCT_MAP, {"SEO — Standard": "prod-999"}):
            post.side_effect = [
                _resp({"id": "deal-1"}),
                _resp({"id": "li-1"}),
            ]
            put.return_value = _resp({})
            deal_creator.create_deal_with_line_items(
                company_id="c1",
                selections={"seo": {"tier": "Standard", "monthly": 800, "setup": 0}},
                totals={"monthly": 800, "setup": 0},
            )
            line_item_body = post.call_args_list[1].kwargs["json"]
            self.assertEqual(line_item_body["properties"]["hs_product_id"], "prod-999")


if __name__ == "__main__":
    unittest.main()
