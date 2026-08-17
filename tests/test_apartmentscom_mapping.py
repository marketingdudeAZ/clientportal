"""Tests for scripts/suggest_apartmentscom_mapping.py — the match tiers and
the suggestion/action derivation. Pure logic; no network."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import suggest_apartmentscom_mapping as sm  # noqa: E402


def _company(cid, name, addr="", city="", state="", uuid="u-" , mapped=""):
    return {
        "id": cid, "name": name, "address": addr, "city": city, "state": state,
        "zip": "", "uuid": (uuid + cid if uuid else ""),
        "apartmentscom_property_id": mapped, "apartmentscom_listing_id": "",
    }


def _listing(pid, name, addr="", city="", state="", lid="L"):
    return {
        "costar_property_id": pid, "costar_listing_id": lid + pid, "name": name,
        "address": addr, "city": city, "state": state, "postal_code": "",
    }


class TestRoster(unittest.TestCase):
    def test_build_roster_dedupes_by_property(self):
        s1 = {"items": [{"costar_property_id": 1, "costar_listing_id": 9,
                         "property_name": "A", "address": "1 St", "city": "X",
                         "state": "TX"}]}
        s2 = {"items": [{"costar_property_id": 1, "property_name": "A dup"},
                        {"costar_property_id": 2, "property_name": "B"}]}
        roster = sm.build_listing_roster([s1, s2])
        self.assertEqual(len(roster), 2)
        by_id = {r["costar_property_id"]: r for r in roster}
        self.assertEqual(by_id["1"]["name"], "A")  # first wins


class TestMatchTiers(unittest.TestCase):
    def test_exact_name_with_geo(self):
        companies = [_company("100", "Riverfront Apartments", "123 Main St", "Atlanta", "GA")]
        lst = _listing("50", "Riverfront Apartments", "123 Main St", "Atlanta", "GA")
        m = sm.match_listing_to_companies(lst, companies)
        self.assertEqual(m["match_type"], "exact_name")
        self.assertEqual(m["score"], 1.0)
        self.assertEqual(m["company"]["id"], "100")

    def test_name_collision_is_ambiguous(self):
        companies = [
            _company("1", "Newport", "100 A St", "Nashville", "TN"),
            _company("2", "Newport", "200 B St", "Nashville", "TN"),
        ]
        lst = _listing("50", "Newport", "", "Nashville", "TN")
        m = sm.match_listing_to_companies(lst, companies)
        self.assertEqual(m["match_type"], "ambiguous_name")

    def test_address_tier(self):
        # Different word order/suffix so exact_name misses, but same street
        # number + state + token overlap → address tier.
        companies = [_company("7", "Midtown Lofts", "500 Oak Ave", "Dallas", "TX")]
        lst = _listing("50", "The Lofts at Midtown", "500 Oak Ave", "Dallas", "TX")
        m = sm.match_listing_to_companies(lst, companies)
        self.assertEqual(m["match_type"], "address")

    def test_fuzzy_tier_word_order(self):
        # Token sets equal but string differs and no street number → fuzzy.
        companies = [_company("8", "Sunset Ridge", "Main St", "Dallas", "TX")]
        lst = _listing("50", "Ridge Sunset", "Main St", "Dallas", "TX")
        m = sm.match_listing_to_companies(lst, companies)
        self.assertEqual(m["match_type"], "fuzzy")
        self.assertGreaterEqual(m["score"], 0.9)

    def test_no_match(self):
        companies = [_company("9", "Completely Different", "1 St", "Miami", "FL")]
        lst = _listing("50", "Zzz Unknown Place", "999 Nowhere", "Boise", "ID")
        m = sm.match_listing_to_companies(lst, companies)
        self.assertEqual(m["match_type"], "none")


class TestSuggestActions(unittest.TestCase):
    def test_commit_eligible(self):
        companies = [_company("100", "Riverfront Apartments", "123 Main St", "Atlanta", "GA")]
        rows = sm.suggest([_listing("50", "Riverfront Apartments", "123 Main St", "Atlanta", "GA")], companies)
        self.assertEqual(rows[0]["action"], "commit_eligible")

    def test_match_but_no_uuid(self):
        companies = [_company("100", "Riverfront Apartments", "123 Main St", "Atlanta", "GA", uuid="")]
        rows = sm.suggest([_listing("50", "Riverfront Apartments", "123 Main St", "Atlanta", "GA")], companies)
        self.assertEqual(rows[0]["action"], "match_but_no_uuid")

    def test_already_mapped_same(self):
        companies = [_company("100", "Riverfront Apartments", "123 Main St", "Atlanta", "GA", mapped="50")]
        rows = sm.suggest([_listing("50", "Riverfront Apartments", "123 Main St", "Atlanta", "GA")], companies)
        self.assertEqual(rows[0]["action"], "already_mapped_same")

    def test_already_mapped_different(self):
        companies = [_company("100", "Riverfront Apartments", "123 Main St", "Atlanta", "GA", mapped="999")]
        rows = sm.suggest([_listing("50", "Riverfront Apartments", "123 Main St", "Atlanta", "GA")], companies)
        self.assertEqual(rows[0]["action"], "already_mapped_DIFFERENT_review")

    def test_fuzzy_is_review_only(self):
        companies = [_company("8", "Sunset Ridge", "Main St", "Dallas", "TX")]
        rows = sm.suggest([_listing("50", "Ridge Sunset", "Main St", "Dallas", "TX")], companies)
        self.assertEqual(rows[0]["action"], "review")


if __name__ == "__main__":
    unittest.main()
