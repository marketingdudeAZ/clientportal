"""Tests for webhook-server/entity_audit.py (Phase 2)."""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

import entity_audit  # noqa: E402


def _fake_page_result(entities: list[dict], schema: list[dict] | None = None) -> dict:
    """Build a minimal onpage_content_parsing response."""
    return {
        "items": [
            {
                "page_content": {"entities": entities},
                "meta":         {"schema": schema or []},
            }
        ]
    }


class ExtractEntitiesTests(unittest.TestCase):
    def test_returns_empty_on_api_error(self):
        with patch("dataforseo_client.onpage_content_parsing", side_effect=RuntimeError("boom")):
            self.assertEqual(entity_audit.extract_entities("https://x.com"), [])

    def test_normalizes_field_names(self):
        raw = [
            {"name": "Disney World", "type": "LOCATION", "salience": 0.8, "mentions": 3},
            {"text": "pool",         "entity_type": "AMENITY", "salience": 0.4, "count": 5},
        ]
        with patch("dataforseo_client.onpage_content_parsing") as mock:
            mock.return_value = _fake_page_result(raw)
            out = entity_audit.extract_entities("https://x.com")
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["name"], "Disney World")
        self.assertEqual(out[0]["type"], "LOCATION")
        self.assertEqual(out[1]["name"], "pool")
        self.assertEqual(out[1]["type"], "AMENITY")
        self.assertEqual(out[1]["mentions"], 5)


class AuditPageTests(unittest.TestCase):
    def test_diff_surfaces_entities_in_2plus_competitors(self):
        """Entity appears on 2 competitors but not target → surfaced as gap."""
        calls = {
            "https://mine.com":     _fake_page_result([
                {"name": "Disney World", "type": "LOCATION", "salience": 0.7}
            ]),
            "https://comp1.com":    _fake_page_result([
                {"name": "Disney World", "type": "LOCATION", "salience": 0.7},
                {"name": "Universal",    "type": "LOCATION", "salience": 0.6},
                {"name": "SeaWorld",     "type": "LOCATION", "salience": 0.5},
            ]),
            "https://comp2.com":    _fake_page_result([
                {"name": "Universal",    "type": "LOCATION", "salience": 0.55},
            ]),
        }
        def _fake(url):
            return calls.get(url, {})

        with patch("dataforseo_client.onpage_content_parsing", side_effect=_fake):
            result = entity_audit.audit_page(
                "https://mine.com",
                ["https://comp1.com", "https://comp2.com"],
            )
        gap_names = [g["name"] for g in result["gaps"]]
        self.assertIn("Universal", gap_names)       # 2 competitors mention it, mine does not
        self.assertNotIn("SeaWorld", gap_names)     # only 1 competitor mentions it
        self.assertNotIn("Disney World", gap_names) # mine already has it

    def test_gaps_sorted_by_avg_salience_desc(self):
        calls = {
            "https://mine.com":  _fake_page_result([]),
            "https://c1.com":    _fake_page_result([
                {"name": "A", "type": "T", "salience": 0.9},
                {"name": "B", "type": "T", "salience": 0.3},
            ]),
            "https://c2.com":    _fake_page_result([
                {"name": "A", "type": "T", "salience": 0.8},
                {"name": "B", "type": "T", "salience": 0.4},
            ]),
        }
        def _fake(url):
            return calls.get(url, {})

        with patch("dataforseo_client.onpage_content_parsing", side_effect=_fake):
            result = entity_audit.audit_page("https://mine.com", ["https://c1.com", "https://c2.com"])
        self.assertEqual([g["name"] for g in result["gaps"]], ["A", "B"])


class RecommendSchemaTests(unittest.TestCase):
    def test_returns_all_types_when_api_fails(self):
        with patch("dataforseo_client.onpage_content_parsing", side_effect=RuntimeError("boom")):
            out = entity_audit.recommend_schema("https://x.com")
        self.assertEqual(out["present"], [])
        self.assertEqual(out["missing"], entity_audit.APARTMENT_SCHEMA_TYPES)
        self.assertIn("ApartmentComplex", out["templates"])

    def test_omits_present_types_from_recommendations(self):
        """If page already has ApartmentComplex schema, don't recommend it."""
        with patch("dataforseo_client.onpage_content_parsing") as mock:
            mock.return_value = _fake_page_result(
                [],
                schema=[{"@type": "ApartmentComplex"}, {"@type": "BreadcrumbList"}],
            )
            out = entity_audit.recommend_schema("https://x.com")
        self.assertIn("ApartmentComplex", out["present"])
        self.assertIn("BreadcrumbList", out["present"])
        self.assertNotIn("ApartmentComplex", out["missing"])
        self.assertNotIn("BreadcrumbList", out["missing"])
        # FAQPage should still be missing
        self.assertIn("FAQPage", out["missing"])


if __name__ == "__main__":
    unittest.main()
