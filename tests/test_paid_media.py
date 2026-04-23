"""Tests for webhook-server/paid_media.py."""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

import paid_media as pm  # noqa: E402


def _fake_hs_response(props):
    """Build a minimal HubSpot response object stub."""
    class R:
        status_code = 200
        def raise_for_status(self): pass
        def json(self):
            return {"properties": props}
    return R()


class TestTargeting(unittest.TestCase):
    @patch("paid_media.requests.get")
    def test_returns_neighborhoods_and_radius(self, mock_get):
        mock_get.return_value = _fake_hs_response({
            "name": "Aurora Heights",
            "city": "Dallas",
            "state": "TX",
            "neighborhoods_to_target": "Deep Ellum, Uptown",
            "landmarks_near_the_property": "Klyde Warren Park",
            "paid_media_radius_miles": "20",
        })
        out = pm.targeting_coverage("123", platform="meta")
        self.assertEqual(out["neighborhoods"], ["Deep Ellum", "Uptown"])
        self.assertEqual(out["radius_miles"], 20.0)
        self.assertTrue(out["radius_ok"])
        self.assertEqual(out["min_radius_miles"], 15)

    @patch("paid_media.requests.get")
    def test_flags_under_minimum_radius(self, mock_get):
        mock_get.return_value = _fake_hs_response({
            "name": "X", "paid_media_radius_miles": "5",
            "neighborhoods_to_target": "", "landmarks_near_the_property": "",
            "city": "", "state": "",
        })
        out = pm.targeting_coverage("123", platform="meta")
        self.assertFalse(out["radius_ok"])
        self.assertIn("15", out["radius_message"])

    @patch("paid_media.requests.get")
    def test_no_keyword_fields_returned(self, mock_get):
        mock_get.return_value = _fake_hs_response({"name": "X", "city": "", "state": ""})
        out = pm.targeting_coverage("123")
        self.assertNotIn("keywords", out)
        self.assertNotIn("keyword", out)


class TestAudienceNarrative(unittest.TestCase):
    @patch("paid_media.requests.get")
    def test_scrubs_protected_class_bullets(self, mock_get):
        mock_get.return_value = _fake_hs_response({
            "name": "X",
            "city": "Austin",
            "property_voice_and_tone": "Warm, welcoming",
            "what_makes_this_property_unique_": "Targeting young Christian families",  # should be scrubbed
            "brand_adjectives": "modern, clean",
            "additional_selling_points": "Pet-friendly policy, bike storage",
            "overarching_goals": "Lease-up stabilization by Q4",
        })
        out = pm.audience_narrative("123")
        bullet_bodies = " ".join(b["body"] for b in out["bullets"])
        self.assertNotIn("Christian", bullet_bodies)
        self.assertGreaterEqual(out["scrubbed_count"], 1)

    @patch("paid_media.requests.get")
    def test_keeps_clean_bullets(self, mock_get):
        mock_get.return_value = _fake_hs_response({
            "name": "X",
            "city": "Austin",
            "property_voice_and_tone": "Warm, welcoming",
            "what_makes_this_property_unique_": "Rooftop pool, walkable to downtown",
            "brand_adjectives": "modern, clean",
            "additional_selling_points": "Pet-friendly policy",
            "overarching_goals": "Lease-up stabilization",
        })
        out = pm.audience_narrative("123")
        self.assertGreaterEqual(len(out["bullets"]), 3)
        self.assertEqual(out["scrubbed_count"], 0)


class TestCreative(unittest.TestCase):
    @patch("paid_media.requests.get")
    def test_parses_newline_separated_taglines(self, mock_get):
        mock_get.return_value = _fake_hs_response({
            "name": "X",
            "property_tag_lines": "\"Home, elevated\"\n'Your Midtown move-in'",
            "onsite_upcoming_events": "Pool party July 15\nResident mixer Aug 1",
            "additional_selling_points": "Pet-friendly",
        })
        out = pm.creative_and_offers("123")
        self.assertIn("Home, elevated", out["active_taglines"])
        self.assertIn("Your Midtown move-in", out["active_taglines"])
        self.assertEqual(len(out["seasonal_angles"]), 2)


if __name__ == "__main__":
    unittest.main()
