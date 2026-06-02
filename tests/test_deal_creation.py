"""Deal creation tests — exercise the real deal_creator module.

Aligned to the new IO process Kyle's docs lock in (2026-05-08):
  - Deal name format:  "<Property> - <Type> - MM/DD/YYYY"
  - All 13 default digital SKUs on every deal (driven by product_catalog)
  - Line items reference catalog products by hs_product_id, not by
    invented name. HubSpot resolves name/SKU from the product itself.
  - Setup-fee line items still get created when selections include setup.
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
    import product_catalog  # noqa: E402


def _resp(body, status_code=200):
    m = mock.MagicMock()
    m.status_code = status_code
    m.json.return_value = body
    m.raise_for_status = mock.MagicMock()
    return m


class TestProductCatalog(unittest.TestCase):
    """The catalog lookup + SEO tier price parser."""

    def test_known_channel_returns_product_id(self):
        self.assertEqual(product_catalog.hs_product_id("paid_search"), "1828410484")
        self.assertEqual(product_catalog.hs_product_id("seo"), "29987927375")
        self.assertEqual(product_catalog.hs_product_id("management_fee"), "3995554730")

    def test_unknown_channel_returns_empty(self):
        self.assertEqual(product_catalog.hs_product_id("nonexistent"), "")

    def test_seo_price_parsed_from_tier_label(self):
        self.assertEqual(product_catalog._seo_price("Local - $100"), 100.0)
        self.assertEqual(product_catalog._seo_price("Lite - $300"), 300.0)
        self.assertEqual(product_catalog._seo_price("Basic - $500"), 500.0)
        self.assertEqual(product_catalog._seo_price("Standard - $800"), 800.0)
        self.assertEqual(product_catalog._seo_price("Premium - $1,300"), 1300.0)

    def test_seo_price_zero_for_empty_or_unparseable(self):
        self.assertEqual(product_catalog._seo_price(""), 0.0)
        self.assertEqual(product_catalog._seo_price("None"), 0.0)
        self.assertEqual(product_catalog._seo_price("New Channel"), 0.0)

    def test_management_fee_is_20_percent_of_asterisked(self):
        # Premier-at-Morton-Ranch shape: $4000 search + $2000 pmax + $500 social
        sel = {
            "paid_search": {"tier": "x", "monthly": 4000, "setup": 0},
            "pmax":        {"tier": "x", "monthly": 2000, "setup": 0},
            "paid_social": {"tier": "x", "monthly":  500, "setup": 0},
        }
        # 6500 * 0.20 = 1300
        self.assertEqual(product_catalog.compute_management_fee(sel), 1300.0)

    def test_management_fee_zero_when_no_paid_spend(self):
        # The $250 floor only applies WHEN there's paid spend. No paid
        # services selected → no management fee at all.
        self.assertEqual(product_catalog.compute_management_fee({}), 0.0)
        self.assertEqual(
            product_catalog.compute_management_fee(
                {"seo": {"tier": "Standard - $800", "monthly": 0, "setup": 0}}
            ),
            0.0,
        )

    def test_management_fee_excludes_seo_from_calc(self):
        # SEO is NOT asterisked — its $800 must NOT be in the mgmt fee base.
        # 1000 * 0.20 = 200, but the $250 floor lifts it to 250.
        sel = {
            "paid_search": {"tier": "x", "monthly": 1000, "setup": 0},
            "seo":         {"tier": "Standard - $800", "monthly": 0, "setup": 0},
        }
        self.assertEqual(product_catalog.compute_management_fee(sel),
                         product_catalog.MANAGEMENT_FEE_MIN)

    def test_management_fee_floor_is_250(self):
        # 20% of $1000 = $200, below floor — clamped to $250.
        sel = {"paid_search": {"tier": "x", "monthly": 1000, "setup": 0}}
        self.assertEqual(product_catalog.compute_management_fee(sel), 250.0)
        # 20% of $1250 = $250 exactly — at the floor.
        sel = {"paid_search": {"tier": "x", "monthly": 1250, "setup": 0}}
        self.assertEqual(product_catalog.compute_management_fee(sel), 250.0)
        # 20% of $1300 = $260, above floor — uses the calc.
        sel = {"paid_search": {"tier": "x", "monthly": 1300, "setup": 0}}
        self.assertEqual(product_catalog.compute_management_fee(sel), 260.0)

    def test_default_line_items_include_management_fee_in_position(self):
        sel = {
            "paid_search": {"tier": "x", "monthly": 3500, "setup": 0},
            "pmax":        {"tier": "x", "monthly": 2000, "setup": 0},
        }
        items = product_catalog.build_default_line_items(sel)
        mgmt_items = [i for i in items if i["channel"] == "management_fee"]
        self.assertEqual(len(mgmt_items), 1)
        # 5500 * 0.20 = 1100
        self.assertEqual(mgmt_items[0]["price"], 1100.0)

    def test_default_line_items_includes_all_13(self):
        items = product_catalog.build_default_line_items({})
        # 12 SKUs + management_fee = 13 line items
        self.assertEqual(len(items), 13)
        channels = {i["channel"] for i in items}
        for required in ("seo", "paid_search", "paid_social", "pmax", "display",
                         "geofence", "retargeting", "tiktok", "programmatic",
                         "demand_gen", "youtube", "ctv", "management_fee"):
            self.assertIn(required, channels)
        # Every entry has a product id
        for i in items:
            self.assertTrue(i["hs_product_id"], f"missing pid for {i['channel']}")

    def test_default_line_items_zero_for_inactive_channels(self):
        items = product_catalog.build_default_line_items({})
        for i in items:
            self.assertEqual(i["price"], 0.0)

    def test_default_line_items_uses_selection_prices(self):
        sel = {
            "paid_search": {"tier": "New Channel", "monthly": 3500, "setup": 0},
            "pmax":        {"tier": "New Channel", "monthly": 2000, "setup": 0},
            "seo":         {"tier": "Standard - $800", "monthly": 0, "setup": 0},
        }
        items = product_catalog.build_default_line_items(sel)
        by_channel = {i["channel"]: i["price"] for i in items}
        self.assertEqual(by_channel["paid_search"], 3500.0)
        self.assertEqual(by_channel["pmax"], 2000.0)
        self.assertEqual(by_channel["seo"], 800.0)         # parsed from tier label
        self.assertEqual(by_channel["paid_social"], 0.0)   # not in selections
        # 5500 paid spend (3500 + 2000) * 20% = 1100 mgmt fee.
        self.assertEqual(by_channel["management_fee"], 1100.0)


class TestNonDefaultChannelLineItems(unittest.TestCase):
    """Selected channels outside the fixed 13 (e.g. Email Drip) must still
    produce a line item — the bug the test team hit where Email Drip was
    silently dropped."""

    def test_selected_email_drip_is_appended_when_product_id_configured(self):
        with mock.patch.dict(product_catalog.CHANNEL_PRODUCT_MAP,
                             {"email_drip": "55555"}):
            items = product_catalog.build_default_line_items({
                "email_drip": {"tier": "New Build", "monthly": 125, "setup": 225},
            })
        by_channel = {i["channel"]: i for i in items}
        self.assertIn("email_drip", by_channel)
        self.assertEqual(by_channel["email_drip"]["hs_product_id"], "55555")
        self.assertEqual(by_channel["email_drip"]["price"], 125.0)
        # The 13 defaults are still there; email_drip is the 14th.
        self.assertEqual(len(items), 14)

    def test_selected_channel_without_product_id_is_skipped_not_crashing(self):
        # A selected channel with no configured product id (website_hosting
        # has no catalog product) should be skipped (logged), not raise, and
        # not appear as a bare line item.
        items = product_catalog.build_default_line_items({
            "website_hosting": {"tier": "", "monthly": 125, "setup": 0},
        })
        self.assertNotIn("website_hosting", {i["channel"] for i in items})
        self.assertEqual(len(items), 13)

    def test_price_parsed_from_tier_label_for_non_seo_channel(self):
        with mock.patch.dict(product_catalog.CHANNEL_PRODUCT_MAP,
                             {"email_drip": "55555"}):
            items = product_catalog.build_default_line_items({
                "email_drip": {"tier": "New Build - $125", "monthly": 0, "setup": 0},
            })
        by_channel = {i["channel"]: i for i in items}
        self.assertEqual(by_channel["email_drip"]["price"], 125.0)


class TestCreateDealWithLineItems(unittest.TestCase):
    """End-to-end exercise of the real deal_creator with requests mocked."""

    def setUp(self):
        self._post = mock.patch("deal_creator.requests.post").start()
        self._put = mock.patch("deal_creator.requests.put").start()
        self._line_item_counter = iter(range(1000, 9999))

        def post_side_effect(url, **kwargs):
            if "/deals" in url and "/associations" not in url:
                return _resp({"id": "deal-42"})
            if "/line_items" in url:
                return _resp({"id": f"li-{next(self._line_item_counter)}"})
            return _resp({})

        self._post.side_effect = post_side_effect
        self._put.return_value = _resp({})

        # Strip test-mode so default-pipeline assertions pass.
        os.environ.pop("PROPERTY_BRIEF_TEST_MODE", None)

    def tearDown(self):
        mock.patch.stopall()

    def _line_item_post_calls(self):
        return [c for c in self._post.call_args_list
                if "/line_items" in (c.args[0] if c.args else "")]

    def test_returns_deal_id(self):
        deal_id = deal_creator.create_deal_with_line_items(
            company_id="comp-1",
            selections={"seo": {"tier": "Standard - $800", "monthly": 0, "setup": 0}},
            totals={"monthly": 800, "setup": 0},
            property_name="Test Property",
        )
        self.assertEqual(deal_id, "deal-42")

    def test_deal_name_format(self):
        deal_creator.create_deal_with_line_items(
            company_id="comp-1",
            selections={},
            totals={"monthly": 800, "setup": 0},
            property_name="Vitri",
            deal_type="New Account Build",
        )
        body = self._post.call_args_list[0].kwargs["json"]
        # Format: "Vitri - New Account Build - MM/DD/YYYY"
        name = body["properties"]["dealname"]
        self.assertTrue(name.startswith("Vitri - New Account Build - "), f"got {name!r}")
        # date suffix is MM/DD/YYYY (10 chars after the dash-space)
        self.assertRegex(name, r"^Vitri - New Account Build - \d{2}/\d{2}/\d{4}$")

    def test_deal_name_falls_back_when_property_name_missing(self):
        deal_creator.create_deal_with_line_items(
            company_id="c", selections={}, totals={"monthly": 0, "setup": 0},
        )
        body = self._post.call_args_list[0].kwargs["json"]
        self.assertIn("Unnamed Property", body["properties"]["dealname"])

    def test_clickup_ticket_id_stamped_on_deal(self):
        deal_creator.create_deal_with_line_items(
            company_id="c", selections={}, totals={"monthly": 0, "setup": 0},
            clickup_ticket_id="abc123", property_name="X",
        )
        body = self._post.call_args_list[0].kwargs["json"]
        self.assertEqual(body["properties"]["clickup_ticket_id"], "abc123")

    def test_owner_id_set_on_deal_when_provided(self):
        deal_creator.create_deal_with_line_items(
            company_id="c", selections={}, totals={"monthly": 0, "setup": 0},
            property_name="X", owner_id="71900211",
        )
        body = self._post.call_args_list[0].kwargs["json"]
        self.assertEqual(body["properties"]["hubspot_owner_id"], "71900211")

    def test_owner_id_omitted_when_empty(self):
        deal_creator.create_deal_with_line_items(
            company_id="c", selections={}, totals={"monthly": 0, "setup": 0},
            property_name="X", owner_id="",
        )
        body = self._post.call_args_list[0].kwargs["json"]
        self.assertNotIn("hubspot_owner_id", body["properties"])

    def test_associates_deal_with_company(self):
        deal_creator.create_deal_with_line_items(
            company_id="comp-1", selections={}, totals={"monthly": 0, "setup": 0},
            property_name="X",
        )
        assoc_url = self._put.call_args_list[0].args[0]
        self.assertIn("/deals/deal-42/associations/companies/comp-1", assoc_url)
        self.assertIn("deal_to_company", assoc_url)

    def test_creates_all_13_default_line_items_even_when_selections_empty(self):
        deal_creator.create_deal_with_line_items(
            company_id="c", selections={}, totals={"monthly": 0, "setup": 0},
            property_name="X",
        )
        line_items = self._line_item_post_calls()
        self.assertEqual(len(line_items), 13)
        # Every line item references a product by hs_product_id
        for c in line_items:
            props = c.kwargs["json"]["properties"]
            self.assertTrue(props.get("hs_product_id"), "line item missing hs_product_id")
            self.assertNotIn("name", props, "line item should NOT carry an invented name")

    def test_line_item_prices_match_selections(self):
        deal_creator.create_deal_with_line_items(
            company_id="c",
            selections={
                "paid_search": {"tier": "New Channel", "monthly": 3500, "setup": 0},
                "pmax":        {"tier": "New Channel", "monthly": 2000, "setup": 0},
                "seo":         {"tier": "Standard - $800", "monthly": 0, "setup": 0},
            },
            totals={"monthly": 5500, "setup": 0},
            property_name="X",
        )
        line_items = self._line_item_post_calls()
        prices_by_pid = {
            c.kwargs["json"]["properties"]["hs_product_id"]:
                c.kwargs["json"]["properties"]["price"]
            for c in line_items
        }
        # Paid Search Ads (1828410484) = 3500
        self.assertEqual(prices_by_pid["1828410484"], "3500.0")
        # Google Ads Performance Max (1992302863) = 2000
        self.assertEqual(prices_by_pid["1992302863"], "2000.0")
        # SEO Package (29987927375) = 800 (parsed from "Standard - $800")
        self.assertEqual(prices_by_pid["29987927375"], "800.0")
        # Management Fee (3995554730) = 1100 (20% of 5500 paid)
        self.assertEqual(prices_by_pid["3995554730"], "1100.0")
        # Inactive channels at 0
        # Geofence (1828397328)
        self.assertEqual(prices_by_pid["1828397328"], "0.0")

    def test_deal_amount_equals_line_item_sum(self):
        # Bug we shipped earlier: deal.amount was set from totals.monthly,
        # which excluded SEO and Mgmt Fee. Now amount must equal the
        # sum of every line item price.
        deal_creator.create_deal_with_line_items(
            company_id="c",
            selections={
                "paid_search": {"tier": "x", "monthly": 3500, "setup": 0},
                "pmax":        {"tier": "x", "monthly": 2000, "setup": 0},
                "seo":         {"tier": "Standard - $800", "monthly": 0, "setup": 0},
            },
            totals={"monthly": 5500, "setup": 0},
            property_name="X",
        )
        deal_body = self._post.call_args_list[0].kwargs["json"]
        # 3500 paid_search + 2000 pmax + 800 seo + 1100 mgmt = 7400
        self.assertEqual(deal_body["properties"]["amount"], "7400.0")

    def test_each_line_item_associated_to_deal(self):
        deal_creator.create_deal_with_line_items(
            company_id="c", selections={}, totals={"monthly": 0, "setup": 0},
            property_name="X",
        )
        li_assoc = [c for c in self._put.call_args_list
                    if "/line_items/" in c.args[0] and "line_item_to_deal" in c.args[0]]
        self.assertEqual(len(li_assoc), 13)

    def test_setup_fee_creates_extra_line_items(self):
        deal_creator.create_deal_with_line_items(
            company_id="c",
            selections={
                "paid_search": {"tier": "New Channel", "monthly": 3500, "setup": 500},
            },
            totals={"monthly": 3500, "setup": 500},
            property_name="X",
        )
        line_items = self._line_item_post_calls()
        # 13 default + 1 setup
        self.assertEqual(len(line_items), 14)
        # Last one should be the setup item — has a `name` instead of hs_product_id
        last = line_items[-1].kwargs["json"]["properties"]
        self.assertIn("Setup Fee", last["name"])
        self.assertEqual(last["price"], "500.0")
        self.assertEqual(last["hs_sku"], "Paid_Search_Ads")

    def test_email_drip_setup_uses_catalog_product(self):
        deal_creator.create_deal_with_line_items(
            company_id="c",
            selections={
                "email_drip": {"tier": "New Build", "monthly": 125, "setup": 225},
            },
            totals={"monthly": 125, "setup": 225},
            property_name="X",
        )
        line_items = self._line_item_post_calls()
        last = line_items[-1].kwargs["json"]["properties"]
        # Setup line item references the real "Email Drip Campaign Setup"
        # product, not an ad-hoc name.
        self.assertEqual(last["hs_product_id"], "2948989326")
        self.assertEqual(last["price"], "225.0")
        self.assertNotIn("name", last)


class TestPropertyBriefTestMode(unittest.TestCase):
    """When PROPERTY_BRIEF_TEST_MODE=true, deals route to the test pipeline."""

    def _run_with_env(self, env: dict) -> dict:
        with mock.patch.dict(os.environ, env, clear=False), \
             mock.patch("deal_creator.requests.post") as post, \
             mock.patch("deal_creator.requests.put") as put:
            counter = iter(range(1000, 9999))

            def post_side_effect(url, **kwargs):
                if "/deals" in url and "/associations" not in url:
                    return _resp({"id": "deal-1"})
                if "/line_items" in url:
                    return _resp({"id": f"li-{next(counter)}"})
                return _resp({})
            post.side_effect = post_side_effect
            put.return_value = _resp({})
            deal_creator.create_deal_with_line_items(
                company_id="c1",
                selections={"seo": {"tier": "Standard - $800", "monthly": 0, "setup": 0}},
                totals={"monthly": 800, "setup": 0},
                property_name="Test Property",
            )
            return post.call_args_list[0].kwargs["json"]["properties"]

    def test_default_pipeline_when_test_mode_absent(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PROPERTY_BRIEF_TEST_MODE", None)
            props = self._run_with_env({})
        self.assertEqual(props["pipeline"], "default")
        self.assertEqual(props["dealstage"], "appointmentscheduled")
        self.assertNotIn("[TEST]", props["dealname"])

    def test_test_pipeline_when_test_mode_on(self):
        props = self._run_with_env({
            "PROPERTY_BRIEF_TEST_MODE": "true",
            "HUBSPOT_TEST_PIPELINE_ID": "898123078",
            "HUBSPOT_TEST_PIPELINE_FIRST_STAGE_ID": "1356833043",
        })
        self.assertEqual(props["pipeline"], "898123078")
        self.assertEqual(props["dealstage"], "1356833043")
        self.assertIn("[TEST]", props["dealname"])

    def test_test_mode_falls_back_to_default_when_pipeline_id_missing(self):
        props = self._run_with_env({
            "PROPERTY_BRIEF_TEST_MODE": "true",
            "HUBSPOT_TEST_PIPELINE_ID": "",
        })
        self.assertEqual(props["pipeline"], "default")
        self.assertEqual(props["dealstage"], "appointmentscheduled")


if __name__ == "__main__":
    unittest.main()
