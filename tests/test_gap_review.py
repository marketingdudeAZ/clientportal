"""Tests for webhook-server/gap_review.py — completeness, validators, typos."""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

import gap_review as gr  # noqa: E402


class TestValidators(unittest.TestCase):
    def test_concession_dollar_sign_passes(self):
        ok, val, _ = gr._validate_concession("$1,500")
        self.assertTrue(ok)
        self.assertEqual(val, "1500")

    def test_concession_zero_passes(self):
        ok, val, _ = gr._validate_concession("0")
        self.assertTrue(ok)
        self.assertEqual(val, "0")

    def test_concession_none_string_passes(self):
        ok, _, _ = gr._validate_concession("None")
        self.assertTrue(ok)

    def test_concession_prose_fails(self):
        # Free-form prose should fail validation and trigger a gap question
        ok, _, reason = gr._validate_concession("two months free")
        # "two months free" contains no digits → should fail
        self.assertFalse(ok)
        self.assertTrue(reason)

    def test_percent_in_range(self):
        ok, val, _ = gr._validate_percent("87%")
        self.assertTrue(ok)
        self.assertEqual(val, "87")

    def test_percent_out_of_range(self):
        ok, _, reason = gr._validate_percent("150")
        self.assertFalse(ok)
        self.assertIn("range", reason)

    def test_rpm_email_valid(self):
        ok, val, _ = gr._validate_rpm_email("Jane.Smith@RPMLiving.com")
        self.assertTrue(ok)
        self.assertEqual(val, "jane.smith@rpmliving.com")

    def test_rpm_email_wrong_domain(self):
        ok, _, _ = gr._validate_rpm_email("jane.smith@gmail.com")
        self.assertFalse(ok)

    def test_rpm_email_hyphenated_last_name(self):
        ok, _, _ = gr._validate_rpm_email("anna.smith-jones@rpmliving.com")
        self.assertTrue(ok)


class TestTypoDetection(unittest.TestCase):
    def test_clean_text_no_flags(self):
        self.assertEqual(gr.detect_typos("Beautiful luxury apartments in downtown Phoenix."), [])

    def test_duplicated_word_caught(self):
        flags = gr.detect_typos("This is is a test sentence")
        self.assertTrue(any("duplicated" in f["reason"] for f in flags))

    def test_excessive_whitespace_caught(self):
        flags = gr.detect_typos("Hello    world this is bad")
        self.assertTrue(any("whitespace" in f["reason"] for f in flags))

    def test_repeated_letter_caught(self):
        flags = gr.detect_typos("This apartment is greaaaaat")
        self.assertTrue(any("repeated" in f["reason"] for f in flags))


class TestReviewIntake(unittest.TestCase):
    @patch("gap_review.score_slop")
    def test_complete_payload_no_gaps(self, mock_slop):
        mock_slop.return_value = {"slop_score": 0.1, "reason": "specific"}
        payload = {
            "property_name":           "Aurora Heights",
            "neighborhoods":           ["Midtown", "Downtown"],
            "unit_types":              ["Studio", "1BR"],
            "primary_competitors":     ["The Pinnacle"],
            "current_concession":      "$1500",
            "current_occupancy_pct":   "92%",
            "top_resident_complaint":  "Pool maintenance has been slow this quarter — three closures since February.",
            "brand_archetype":         "The Caregiver",
            "community_manager_email": "jane.smith@rpmliving.com",
            "regional_manager_email":  "bob.jones@rpmliving.com",
        }
        review = gr.review_intake(payload)
        self.assertEqual(review["completeness"], 1.0)
        self.assertEqual(review["gap_questions"], [])
        self.assertEqual(review["validation_errors"], {})

    @patch("gap_review.score_slop")
    def test_missing_field_creates_gap(self, mock_slop):
        mock_slop.return_value = {"slop_score": 0.1, "reason": "ok"}
        payload = {"property_name": "Aurora Heights"}
        review = gr.review_intake(payload)
        # Many fields missing → many gap questions
        self.assertGreater(len(review["gap_questions"]), 5)
        self.assertLess(review["completeness"], 0.5)

    @patch("gap_review.score_slop")
    def test_invalid_forced_fact_creates_gap(self, mock_slop):
        mock_slop.return_value = {"slop_score": 0.1, "reason": "ok"}
        payload = {
            "property_name":          "X",
            "neighborhoods":          ["Y"],
            "unit_types":             ["Z"],
            "primary_competitors":    ["W"],
            "current_concession":     "two months free",   # not parseable
            "current_occupancy_pct":  "92%",
            "top_resident_complaint": "specific complaint with detail",
            "brand_archetype":        "Sage",
            "community_manager_email":"jane.doe@rpmliving.com",
            "regional_manager_email": "bob.jones@gmail.com",   # wrong domain
        }
        review = gr.review_intake(payload)
        self.assertIn("current_concession", review["validation_errors"])
        gap_fields = [q["field"] for q in review["gap_questions"]]
        self.assertIn("current_concession", gap_fields)
        # regional manager domain is wrong → also flagged
        self.assertIn("regional_manager_email", review["validation_errors"])

    @patch("gap_review.score_slop")
    def test_high_slop_creates_gap(self, mock_slop):
        mock_slop.return_value = {"slop_score": 0.9, "reason": "generic marketing copy"}
        payload = {
            "property_name":          "X",
            "neighborhoods":          ["Y"],
            "unit_types":             ["Z"],
            "primary_competitors":    ["W"],
            "current_concession":     "0",
            "current_occupancy_pct":  "92",
            "top_resident_complaint": "Our luxurious community provides best-in-class amenities and exceptional living experiences.",
            "brand_archetype":        "Sage",
            "community_manager_email":"jane.doe@rpmliving.com",
            "regional_manager_email": "bob.jones@rpmliving.com",
        }
        review = gr.review_intake(payload)
        gap_fields = [q["field"] for q in review["gap_questions"]]
        self.assertIn("top_resident_complaint", gap_fields)
        self.assertGreaterEqual(review["ai_slop_score"], 0.7)


class TestSlopClassifier(unittest.TestCase):
    def test_short_text_skips_classifier(self):
        # Short answers should bypass the API call entirely
        result = gr.score_slop("field", "yes")
        self.assertEqual(result["slop_score"], 0.0)


if __name__ == "__main__":
    unittest.main()
