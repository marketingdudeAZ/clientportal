"""Tests for webhook-server/ils_research.py — provider detection, extraction,
graceful failure, prompt formatting."""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

import ils_research as ils  # noqa: E402


class TestProviderDetection(unittest.TestCase):
    def test_apartments_com_detected(self):
        self.assertEqual(
            ils.detect_provider("https://www.apartments.com/aurora-heights-phoenix-az/abc/"),
            "apartments_com",
        )

    def test_zillow_detected(self):
        self.assertEqual(
            ils.detect_provider("https://www.zillow.com/apartments/phoenix-az/aurora/abc/"),
            "zillow",
        )

    def test_subdomain_detected(self):
        self.assertEqual(ils.detect_provider("https://m.apartments.com/x/"), "apartments_com")

    def test_unknown_returns_none(self):
        self.assertIsNone(ils.detect_provider("https://example.com/listing/"))

    def test_empty_returns_none(self):
        self.assertIsNone(ils.detect_provider(""))


class TestJsonLdExtraction(unittest.TestCase):
    def test_extracts_well_formed_block(self):
        html = """<html><head>
        <script type="application/ld+json">
        {"@type": "ApartmentComplex", "name": "Aurora Heights", "ratingValue": 4.2}
        </script></head></html>"""
        blocks = ils._extract_jsonld(html)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["name"], "Aurora Heights")

    def test_handles_trailing_comma(self):
        # apartments.com sometimes ships JSON-LD with trailing commas
        html = """<script type="application/ld+json">
        {"name": "X", "amenities": ["pool", "gym",]}
        </script>"""
        blocks = ils._extract_jsonld(html)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["amenities"], ["pool", "gym"])

    def test_no_blocks_returns_empty(self):
        self.assertEqual(ils._extract_jsonld("<html>nothing here</html>"), [])

    def test_multiple_blocks_collected(self):
        html = """
        <script type="application/ld+json">{"a": 1}</script>
        <script type="application/ld+json">{"b": 2}</script>
        """
        self.assertEqual(len(ils._extract_jsonld(html)), 2)


class TestStripHtml(unittest.TestCase):
    def test_drops_script_and_style(self):
        html = "<html><head><style>x{}</style></head><body>visible<script>alert(1)</script></body></html>"
        text = ils._strip_html(html)
        self.assertNotIn("alert", text)
        self.assertNotIn("x{}", text)
        self.assertIn("visible", text)

    def test_collapses_whitespace(self):
        html = "<p>hello   world\n\n   foo</p>"
        text = ils._strip_html(html)
        self.assertEqual(text, "hello world foo")


class TestResearchE2E(unittest.TestCase):
    @patch("ils_research._claude_extract")
    @patch("ils_research._fetch_html")
    def test_happy_path_dict_input(self, mock_fetch, mock_extract):
        mock_fetch.return_value = "<html><body>ok</body></html>"
        mock_extract.return_value = {
            "property_name": "Aurora",
            "amenities": ["pool", "gym"],
            "review_excerpts": [
                {"text": "the pool was closed three times in February", "rating": 2, "date": "2026-03"},
                {"text": "great place", "rating": 5, "date": None},  # too generic, but kept
            ],
            "rating": 4.1,
        }
        result = ils.research_ils_listings({
            "apartments_com": "https://www.apartments.com/x/abc/",
        })
        self.assertEqual(result["providers"], ["apartments_com"])
        self.assertIn("pool", result["merged_amenities"])
        self.assertEqual(result["ratings"], {"apartments_com": 4.1})
        self.assertGreaterEqual(len(result["merged_review_quotes"]), 1)

    @patch("ils_research._claude_extract")
    @patch("ils_research._fetch_html")
    def test_happy_path_list_input(self, mock_fetch, mock_extract):
        mock_fetch.return_value = "<html>ok</html>"
        mock_extract.return_value = {"amenities": ["pool"], "review_excerpts": []}
        result = ils.research_ils_listings([
            "https://www.zillow.com/apartments/x/y/",
            "https://www.apartments.com/a/b/",
        ])
        # Both providers detected and processed
        self.assertEqual(set(result["providers"]), {"zillow", "apartments_com"})

    @patch("ils_research._fetch_html")
    def test_fetch_failure_returns_error_not_raise(self, mock_fetch):
        mock_fetch.return_value = ""  # blocked
        result = ils.research_ils_listings({"apartments_com": "https://www.apartments.com/x/"})
        self.assertEqual(result["providers"], [])
        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("fetch_blocked_or_failed", list(result["errors"].values()))

    def test_empty_input_returns_empty_shape(self):
        result = ils.research_ils_listings({})
        self.assertEqual(result["providers"], [])
        self.assertEqual(result["merged_amenities"], [])
        self.assertEqual(result["errors"], {})

    @patch("ils_research._claude_extract")
    @patch("ils_research._fetch_html")
    def test_dedupes_amenities_across_providers(self, mock_fetch, mock_extract):
        mock_fetch.return_value = "<html>ok</html>"
        mock_extract.side_effect = [
            {"amenities": ["Pool", "Gym"], "review_excerpts": []},
            {"amenities": ["pool", "Dog Park"], "review_excerpts": []},
        ]
        result = ils.research_ils_listings([
            "https://www.apartments.com/x/",
            "https://www.zillow.com/y/",
        ])
        amen_lc = [a.lower() for a in result["merged_amenities"]]
        self.assertEqual(amen_lc.count("pool"), 1)
        self.assertIn("gym", amen_lc)
        self.assertIn("dog park", amen_lc)

    @patch("ils_research._claude_extract")
    @patch("ils_research._fetch_html")
    def test_short_review_text_skipped(self, mock_fetch, mock_extract):
        mock_fetch.return_value = "<html>ok</html>"
        mock_extract.return_value = {
            "amenities": [],
            "review_excerpts": [
                {"text": "ok", "rating": 3},  # too short
                {"text": "Specific review with enough detail to be useful here", "rating": 4},
            ],
        }
        result = ils.research_ils_listings({"apartments_com": "https://www.apartments.com/x/"})
        # Only the long-enough quote survives
        self.assertEqual(len(result["merged_review_quotes"]), 1)


class TestFormatForPrompt(unittest.TestCase):
    def test_empty_returns_empty_string(self):
        self.assertEqual(ils.format_for_prompt({}), "")
        self.assertEqual(ils.format_for_prompt({"providers": []}), "")

    def test_includes_quotes_and_amenities(self):
        data = {
            "providers": ["apartments_com"],
            "by_provider": {"apartments_com": {
                "unit_mix": [{"type": "1BR", "rent_low": 1500, "rent_high": 2000}],
                "concession_text": "1 month free",
                "walk_score": 87,
            }},
            "merged_amenities": ["pool", "gym", "dog park"],
            "merged_review_quotes": ["Specific complaint about parking enforcement here"],
            "ratings": {"apartments_com": 4.1},
            "errors": {},
        }
        out = ils.format_for_prompt(data)
        self.assertIn("ILS LISTING RESEARCH", out)
        self.assertIn("apartments_com: 4.1/5", out)
        self.assertIn("pool", out)
        self.assertIn("Specific complaint about parking", out)
        self.assertIn("$1500-$2000", out)
        self.assertIn("1 month free", out)
        self.assertIn("Walk Score (apartments_com): 87", out)


if __name__ == "__main__":
    unittest.main()
