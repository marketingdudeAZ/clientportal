"""Tests for webhook-server/content_brief_writer.py (Phase 2).

Mocks the Anthropic client — no live Claude API calls in tests.
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

import content_brief_writer as cbw  # noqa: E402


VALID_BRIEF_JSON = json.dumps({
    "h1": "Apartments in Winter Garden, FL: A 2026 Renter's Guide",
    "meta_description": "Find luxury apartments in Winter Garden — pet-friendly, near Disney, with spacious floor plans.",
    "outline": [
        {
            "h2": "What to Expect Renting in Winter Garden",
            "h3_list": ["Neighborhoods at a glance", "Average rent ranges"],
            "target_entities": ["Disney World", "Winter Garden Village"],
            "paa_answered": ["Is Winter Garden a good place to live?"],
        },
        {
            "h2": "Top Amenities Renters Look For",
            "h3_list": ["Pet-friendly policies", "Pool and fitness center standards"],
            "target_entities": ["dog park", "24-hour gym"],
            "paa_answered": ["What amenities do luxury apartments in Winter Garden offer?"],
        },
    ],
    "target_word_count": 1800,
    "internal_link_targets": ["amenities page", "floor plans page"],
    "schema_types": ["ApartmentComplex", "FAQPage"],
    "geo_optimization_notes": "Answers PAA directly in H2s; adds FAQPage schema for AI Overview citation.",
})


def _mock_anthropic_response(text: str):
    fake = MagicMock()
    fake.content = [MagicMock(text=text)]
    client = MagicMock()
    client.messages.create.return_value = fake
    return client


def _sample_cluster() -> dict:
    return {
        "hub_keyword":    "apartments winter garden fl",
        "spokes":         ["luxury apartments winter garden", "apartments in winter garden"],
        "property_name":  "Muse at Winter Garden",
        "property_domain": "musewintergarden.com",
        "market":         "Orlando",
        "top_serp_urls":  ["https://comp1.com/a", "https://comp2.com/b"],
        "competitor_headings": [
            {"url": "https://comp1.com/a", "h1": "Apts WG", "h2s": ["Pool", "Pets"]},
        ],
        "paa_questions": ["Is Winter Garden safe?", "How much is rent?"],
        "related_searches": ["wg apartments 2 bed", "apartments kissimmee"],
        "competitor_entities": ["Disney World", "SR-429", "Clermont"],
        "existing_tracked_keywords": ["luxury apartments winter garden"],
    }


class BuildUserMessageTests(unittest.TestCase):
    def test_includes_hub_keyword_spokes_paa_entities(self):
        msg = cbw._build_user_message(_sample_cluster())
        self.assertIn("HUB KEYWORD: apartments winter garden fl", msg)
        self.assertIn("luxury apartments winter garden", msg)
        self.assertIn("Is Winter Garden safe?", msg)
        self.assertIn("Disney World", msg)
        self.assertIn("Muse at Winter Garden", msg)

    def test_handles_missing_spokes(self):
        c = _sample_cluster()
        c["spokes"] = []
        msg = cbw._build_user_message(c)
        self.assertIn("stand-alone topic", msg)


class GenerateBriefTests(unittest.TestCase):
    def test_valid_json_response_parsed_correctly(self):
        with patch("anthropic.Anthropic") as mock_cls, \
             patch.object(cbw, "ANTHROPIC_API_KEY", "fake-key", create=False):
            # The real import chain resolves ANTHROPIC_API_KEY via config — we patch config here instead.
            pass
        with patch("anthropic.Anthropic") as mock_cls, \
             patch("content_brief_writer._build_user_message") as mock_build:
            mock_cls.return_value = _mock_anthropic_response(VALID_BRIEF_JSON)
            mock_build.return_value = "fake prompt"
            # config.ANTHROPIC_API_KEY is set via config.py import; patch the value
            with patch("config.ANTHROPIC_API_KEY", "sk-test"):
                brief = cbw.generate_brief(_sample_cluster())
        self.assertEqual(brief["h1"], "Apartments in Winter Garden, FL: A 2026 Renter's Guide")
        self.assertEqual(len(brief["outline"]), 2)
        self.assertEqual(brief["target_word_count"], 1800)

    def test_tolerates_markdown_fences(self):
        raw = "```json\n" + VALID_BRIEF_JSON + "\n```"
        with patch("anthropic.Anthropic") as mock_cls, \
             patch("config.ANTHROPIC_API_KEY", "sk-test"):
            mock_cls.return_value = _mock_anthropic_response(raw)
            brief = cbw.generate_brief(_sample_cluster())
        self.assertEqual(brief["h1"], "Apartments in Winter Garden, FL: A 2026 Renter's Guide")

    def test_raises_on_malformed_json(self):
        with patch("anthropic.Anthropic") as mock_cls, \
             patch("config.ANTHROPIC_API_KEY", "sk-test"):
            mock_cls.return_value = _mock_anthropic_response("this is not json at all")
            with self.assertRaises(ValueError):
                cbw.generate_brief(_sample_cluster())

    def test_raises_when_required_keys_missing(self):
        bad = json.dumps({"h1": "x", "meta_description": "y"})  # no outline / word count
        with patch("anthropic.Anthropic") as mock_cls, \
             patch("config.ANTHROPIC_API_KEY", "sk-test"):
            mock_cls.return_value = _mock_anthropic_response(bad)
            with self.assertRaises(ValueError):
                cbw.generate_brief(_sample_cluster())

    def test_raises_without_api_key(self):
        with patch("config.ANTHROPIC_API_KEY", ""):
            # Re-import the module-level reference
            from importlib import reload
            reload(cbw)
            with self.assertRaises(RuntimeError):
                cbw.generate_brief(_sample_cluster())


if __name__ == "__main__":
    unittest.main()
