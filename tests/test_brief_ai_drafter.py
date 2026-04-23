"""Tests for webhook-server/brief_ai_drafter.py.

No live API calls — the Anthropic client is mocked.
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

import brief_ai_drafter as drafter  # noqa: E402


VALID_DRAFT_JSON = json.dumps({
    "property_voice_and_tone":           {"value": "Warm, confident, neighborhood-led.", "confidence": 0.8},
    "neighborhoods_to_target":           {"value": "Midtown, East Village", "confidence": 0.9},
    "landmarks_near_the_property":       {"value": "Washington Square Park", "confidence": 0.7},
    "property_tag_lines":                {"value": "Your Midtown move-in\nHome, elevated", "confidence": 0.6},
    "what_makes_this_property_unique_":  {"value": "Rooftop lounge + 24/7 concierge.", "confidence": 0.8},
    "brand_adjectives":                  {"value": "modern, warm, elevated", "confidence": 0.7},
    "additional_selling_points":         {"value": "Pet-friendly, bike storage.", "confidence": 0.5},
    "overarching_goals":                 {"value": None, "confidence": 0.1},
    "primary_competitors":               {"value": "The Pinnacle, The Grove", "confidence": 0.6},
    "units_offered":                     {"value": "Studio, 1 Bed, 2 Bed", "confidence": 0.9},
})


class TestNormalizeDomain(unittest.TestCase):
    def test_strips_scheme_and_www(self):
        self.assertEqual(drafter.normalize_domain("https://www.example.com/page"), "example.com")

    def test_strips_path_and_query(self):
        self.assertEqual(drafter.normalize_domain("http://example.com/foo?q=1"), "example.com")

    def test_bare_domain_passes(self):
        self.assertEqual(drafter.normalize_domain("example.com"), "example.com")

    def test_empty_returns_empty(self):
        self.assertEqual(drafter.normalize_domain(""), "")
        self.assertEqual(drafter.normalize_domain(None), "")


class TestDraftBrief(unittest.TestCase):
    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"})
    @patch("anthropic.Anthropic")
    def test_returns_shape_matching_hubspot_property_names(self, MockAnthropic):
        client = MockAnthropic.return_value
        msg = MagicMock()
        msg.content = [MagicMock(type="text", text=VALID_DRAFT_JSON)]
        client.messages.create.return_value = msg

        # Bypass network scrape.
        with patch.object(drafter, "scrape_site_text", return_value="Welcome to Example Apartments"):
            result = drafter.draft_brief("example.com")

        for field in drafter.DRAFTABLE_FIELDS:
            self.assertIn(field, result)
            self.assertIn("value", result[field])
            self.assertIn("confidence", result[field])

    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"})
    @patch("anthropic.Anthropic")
    def test_confidence_is_clamped_to_0_1(self, MockAnthropic):
        client = MockAnthropic.return_value
        msg = MagicMock()
        bad = json.dumps({
            "property_voice_and_tone": {"value": "x", "confidence": 5.0},
            "neighborhoods_to_target": {"value": "y", "confidence": -0.3},
        })
        msg.content = [MagicMock(type="text", text=bad)]
        client.messages.create.return_value = msg
        with patch.object(drafter, "scrape_site_text", return_value=""):
            result = drafter.draft_brief("example.com")
        self.assertEqual(result["property_voice_and_tone"]["confidence"], 1.0)
        self.assertEqual(result["neighborhoods_to_target"]["confidence"], 0.0)

    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"})
    @patch("anthropic.Anthropic")
    def test_attaches_pdf_document_blocks(self, MockAnthropic):
        client = MockAnthropic.return_value
        msg = MagicMock()
        msg.content = [MagicMock(type="text", text=VALID_DRAFT_JSON)]
        client.messages.create.return_value = msg
        with patch.object(drafter, "scrape_site_text", return_value=""):
            drafter.draft_brief(
                "example.com",
                deck_pdf_bytes=b"%PDF-1.4 fake deck",
                rfp_pdf_bytes=b"%PDF-1.4 fake rfp",
            )
        call = client.messages.create.call_args
        user_content = call.kwargs["messages"][0]["content"]
        doc_blocks = [b for b in user_content if b.get("type") == "document"]
        self.assertEqual(len(doc_blocks), 2)
        titles = {b.get("title") for b in doc_blocks}
        self.assertEqual(titles, {"Pitch Deck", "RFP"})

    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"})
    @patch("anthropic.Anthropic")
    def test_system_prompt_uses_cache_control(self, MockAnthropic):
        client = MockAnthropic.return_value
        msg = MagicMock()
        msg.content = [MagicMock(type="text", text=VALID_DRAFT_JSON)]
        client.messages.create.return_value = msg
        with patch.object(drafter, "scrape_site_text", return_value=""):
            drafter.draft_brief("example.com")
        call = client.messages.create.call_args
        system = call.kwargs["system"]
        self.assertIsInstance(system, list)
        self.assertEqual(system[0].get("cache_control", {}).get("type"), "ephemeral")

    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"})
    @patch("anthropic.Anthropic")
    def test_raises_on_malformed_json(self, MockAnthropic):
        client = MockAnthropic.return_value
        msg = MagicMock()
        msg.content = [MagicMock(type="text", text="not JSON, not even a brace")]
        client.messages.create.return_value = msg
        with patch.object(drafter, "scrape_site_text", return_value=""):
            with self.assertRaises(drafter.BriefDrafterError):
                drafter.draft_brief("example.com")


if __name__ == "__main__":
    unittest.main()
