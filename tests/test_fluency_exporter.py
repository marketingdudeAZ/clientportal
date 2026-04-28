"""Tests for webhook-server/fluency_exporter.py — match-type syntax + CSV shape."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

import fluency_exporter as fe  # noqa: E402


class TestKeywordFormatting(unittest.TestCase):
    def test_exact_uses_pipes_not_brackets(self):
        # Fluency-specific quirk — exact match is |kw| not [kw]
        self.assertEqual(fe.format_keyword("downtown apartments", "exact"),
                         "|downtown apartments|")

    def test_phrase_uses_quotes(self):
        self.assertEqual(fe.format_keyword("luxury living", "phrase"),
                         '"luxury living"')

    def test_broad_no_decoration(self):
        self.assertEqual(fe.format_keyword("studio", "broad"), "studio")

    def test_default_match_type_is_phrase(self):
        # Per Fluency docs, unspecified match type = phrase
        self.assertEqual(fe.format_keyword("loft", ""), '"loft"')

    def test_negative_keyword(self):
        self.assertEqual(fe.format_keyword("cheap", "phrase", negative=True),
                         '-"cheap"')

    def test_negative_exact(self):
        self.assertEqual(fe.format_keyword("free", "exact", negative=True),
                         "-|free|")


class TestCsvBuilders(unittest.TestCase):
    """Verify CSV output shape matches Fluency Bulk Manage expectations."""

    def setUp(self):
        self.payload = {
            "property_uuid": "abc-123",
            "keywords": [
                {
                    "keyword_formatted": "|downtown apartments|",
                    "keyword_raw":       "downtown apartments",
                    "match_type":        "exact",
                    "ad_group":          "transactional",
                    "priority":          "high",
                    "intent":            "transactional",
                    "negative":          False,
                    "cpc_low":            1.20,
                    "cpc_high":           3.50,
                },
            ],
            "variables": [
                {"name": "brand_primary", "value": "#1A2B3C", "type": "color", "approved": True},
            ],
            "tags": [
                {"name": "lifecycle", "value": "lease_up"},
            ],
            "assets": [
                {
                    "role": "logo_square", "variable_name": "{{logo_square}}",
                    "url": "https://hubspot.cdn/logo.png",
                    "width": 1200, "height": 1200,
                },
            ],
        }

    def test_keywords_csv_has_header_and_row(self):
        out = fe.CsvExporter._build_keywords_csv(self.payload).decode()
        lines = out.strip().split("\n")
        # header + 1 data row
        self.assertEqual(len(lines), 2)
        self.assertIn("Property UUID", lines[0])
        self.assertIn("Match Type", lines[0])
        self.assertIn("|downtown apartments|", lines[1])

    def test_variables_csv_writes_approved_as_TRUE(self):
        out = fe.CsvExporter._build_variables_csv(self.payload).decode()
        self.assertIn("TRUE", out)
        self.assertIn("brand_primary", out)
        self.assertIn("#1A2B3C", out)

    def test_tags_csv_shape(self):
        out = fe.CsvExporter._build_tags_csv(self.payload).decode()
        self.assertIn("lifecycle", out)
        self.assertIn("lease_up", out)

    def test_assets_csv_shape(self):
        out = fe.CsvExporter._build_assets_csv(self.payload).decode()
        self.assertIn("logo_square", out)
        self.assertIn("hubspot.cdn/logo.png", out)
        self.assertIn("1200", out)


class TestExporterE2E(unittest.TestCase):
    @patch("fluency_exporter.assemble_payload")
    def test_csv_exporter_writes_to_dropzone(self, mock_assemble):
        mock_assemble.return_value = {
            "property_uuid": "u-1",
            "keywords":  [{"keyword_formatted": '"a"', "keyword_raw": "a",
                           "match_type": "phrase", "ad_group": "g", "priority": "high",
                           "intent": "info", "negative": False, "cpc_low": 0, "cpc_high": 0}],
            "variables": [],
            "tags":      [],
            "assets":    [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            exporter = fe.CsvExporter(dropzone_path=tmp)
            result = exporter.export_blueprint("u-1")
            self.assertEqual(result["transport"], "csv")
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["keywords_count"], 1)
            # 4 files written: keywords, variables, tags, assets
            self.assertEqual(len(result["written"]), 4)
            for path in result["written"]:
                self.assertTrue(os.path.exists(path))

    def test_api_exporter_raises_until_phase_2(self):
        exporter = fe.ApiExporter(api_key="dummy")
        with self.assertRaises(NotImplementedError):
            exporter.export_blueprint("u-1")


class TestFactory(unittest.TestCase):
    def test_no_api_key_returns_csv_exporter(self):
        # Default — no FLUENCY_API_KEY env var set
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FLUENCY_API_KEY", None)
            exporter = fe.get_exporter()
            self.assertIsInstance(exporter, fe.CsvExporter)

    def test_api_key_present_returns_api_exporter(self):
        with patch.dict(os.environ, {"FLUENCY_API_KEY": "test-key"}):
            exporter = fe.get_exporter()
            self.assertIsInstance(exporter, fe.ApiExporter)


if __name__ == "__main__":
    unittest.main()
