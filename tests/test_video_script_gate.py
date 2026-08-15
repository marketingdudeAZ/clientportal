"""The compliance gate on generated video ad copy.

The load-bearing assertion here is the honesty one: when the Fair Housing
nuance pass cannot run, the result must say `degraded` — never `passed`.
Reporting a check that did not happen is worse than reporting no check.
"""

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "webhook-server"))

import config  # noqa: E402
import fair_housing_gate  # noqa: E402
import video_script_gate as gate  # noqa: E402

CLEAN = (
    "POV: you just found your next place. Sunlit corner windows. "
    "A rooftop that actually gets used. Book a tour."
)


def _no_llm():
    """Force the Fair Housing LLM pass to be unavailable.

    Patches the config attribute rather than os.environ: config binds its
    values at import time, so an env patch would only take effect if this
    test module happened to be the first to import it (see tests/conftest.py
    on order-dependent config freezing).
    """
    return mock.patch.object(config, "ANTHROPIC_API_KEY", "")


def _llm_available():
    return mock.patch.object(config, "ANTHROPIC_API_KEY", "sk-test")


class TestPricingRule(unittest.TestCase):
    def test_pricing_is_stripped_and_reported(self):
        with _no_llm():
            result = gate.gate_script(
                "Studios starting at $1,299. A rooftop that actually gets used. Book a tour."
            )
        self.assertTrue(result.ok)
        self.assertNotIn("$1,299", result.script)
        self.assertNotIn("starting at", result.script.lower())
        self.assertTrue(result.pricing_removed)

    def test_too_short_script_is_blocked(self):
        with _no_llm():
            result = gate.gate_script("Nice.")
        self.assertFalse(result.ok)
        self.assertEqual(result.fair_housing, "blocked")

    def test_short_script_does_not_claim_a_fair_housing_pass(self):
        """It was rejected before the Fair Housing pass ran — say so."""
        with _no_llm():
            result = gate.gate_script("Nice.")
        self.assertNotEqual(result.fair_housing, "passed")
        self.assertTrue(result.degraded)


class TestFairHousing(unittest.TestCase):
    def test_hard_pattern_blocks_without_an_llm(self):
        """HARD_PATTERNS must hold even when the nuance pass is unavailable."""
        with _no_llm():
            result = gate.gate_script(
                "A quiet building, adults-only, with a rooftop that actually gets used. Book a tour."
            )
        self.assertFalse(result.ok)
        self.assertTrue(result.violations)
        self.assertEqual(result.fair_housing, "blocked")

    def test_familial_status_steering_blocked(self):
        with _no_llm():
            result = gate.gate_script(
                "Perfect for young professionals who want a rooftop that gets used. Book a tour."
            )
        self.assertFalse(result.ok)
        self.assertTrue(any("familial" in v["protected_class"] for v in result.violations))

    def test_ordinary_amenity_copy_is_not_flagged(self):
        with _no_llm():
            result = gate.gate_script(CLEAN)
        self.assertTrue(result.ok)
        self.assertEqual(result.violations, [])

    def test_blocking_can_be_disabled(self):
        with _no_llm(), mock.patch.dict(os.environ, {"VIDEO_FAIR_HOUSING_BLOCK": "false"}):
            result = gate.gate_script("No kids. A rooftop that actually gets used. Book a tour.")
        self.assertTrue(result.ok)
        self.assertTrue(result.violations, "violations must still be reported when not blocking")
        self.assertEqual(result.fair_housing, "violations")


class TestDegradationHonesty(unittest.TestCase):
    """Never claim a check that did not happen."""

    def test_missing_api_key_reports_degraded_not_passed(self):
        with _no_llm():
            result = gate.gate_script(CLEAN)
        self.assertTrue(result.ok)
        self.assertTrue(result.degraded)
        self.assertEqual(result.fair_housing, "degraded")
        self.assertNotEqual(result.fair_housing, "passed")
        self.assertIn("ANTHROPIC_API_KEY", result.degraded_reason)

    def test_llm_error_reports_degraded(self):
        with _llm_available(), \
             mock.patch("anthropic.Anthropic", side_effect=RuntimeError("boom")):
            result = gate.gate_script(CLEAN)
        self.assertTrue(result.degraded)
        self.assertEqual(result.fair_housing, "degraded")
        self.assertIn("boom", result.degraded_reason)

    def test_describe_checks_surfaces_degradation(self):
        with _no_llm():
            result = gate.gate_script(CLEAN)
        described = result.describe_checks()
        self.assertIn("fair_housing=degraded", described)
        self.assertNotIn("fair_housing=passed", described)

    def test_completed_llm_pass_reports_passed(self):
        fake = {"compliant": True, "violations": [], "checked": True,
                "llm_checked": True, "degraded": False, "degraded_reason": ""}
        with mock.patch.object(fair_housing_gate, "check_fair_housing", return_value=fake):
            result = gate.gate_script(CLEAN)
        self.assertFalse(result.degraded)
        self.assertEqual(result.fair_housing, "passed")

    def test_gate_unavailable_blocks_by_default(self):
        with mock.patch.object(fair_housing_gate, "check_fair_housing",
                               side_effect=ImportError("module gone")):
            result = gate.gate_script(CLEAN)
        self.assertFalse(result.ok)
        self.assertTrue(result.degraded)
        self.assertEqual(result.fair_housing, "blocked")


class TestFairHousingGateReturnShape(unittest.TestCase):
    """fair_housing_gate itself must report whether the nuance pass ran."""

    def test_degraded_flag_when_no_api_key(self):
        with _no_llm():
            out = fair_housing_gate.check_fair_housing([{"field": "f", "text": CLEAN}])
        self.assertTrue(out["degraded"])
        self.assertFalse(out["llm_checked"])
        self.assertTrue(out["checked"], "back-compat key must survive")

    def test_empty_input_is_not_degraded(self):
        out = fair_housing_gate.check_fair_housing([])
        self.assertFalse(out["degraded"])
        self.assertTrue(out["compliant"])


class TestForbiddenPhrases(unittest.TestCase):
    def test_property_specific_phrase_is_reported(self):
        with _no_llm():
            result = gate.gate_script(
                "Luxury living at its finest. A rooftop that actually gets used. Book a tour.",
                forbidden_phrases=["luxury living at its finest"],
            )
        self.assertEqual(result.forbidden_used, ["luxury living at its finest"])

    def test_match_is_case_insensitive(self):
        with _no_llm():
            result = gate.gate_script(CLEAN + " Home Sweet Home.",
                                      forbidden_phrases=["home sweet home"])
        self.assertEqual(result.forbidden_used, ["home sweet home"])

    def test_absent_phrase_not_reported(self):
        with _no_llm():
            result = gate.gate_script(CLEAN, forbidden_phrases=["home sweet home"])
        self.assertEqual(result.forbidden_used, [])


class TestScenePlanGate(unittest.TestCase):
    def _plan(self, overlay="Rooftop that gets used"):
        return [
            {"duration_s": 2, "asset_url": "https://cdn/a.jpg", "asset_type": "image",
             "voiceover_text": "Sunlit corner windows all day long.",
             "on_screen_text": overlay},
        ]

    def test_overlay_pricing_is_stripped_in_place(self):
        plan = self._plan(overlay="From $1,299")
        with _no_llm():
            result = gate.gate_scene_plan(plan)
        self.assertNotIn("$1,299", plan[0]["on_screen_text"])
        self.assertTrue(result.pricing_removed)

    def test_overlay_fair_housing_violation_blocks(self):
        plan = self._plan(overlay="Adults-only building")
        with _no_llm():
            result = gate.gate_scene_plan(plan)
        self.assertFalse(result.ok)
        self.assertTrue(result.violations)

    def test_clean_plan_passes(self):
        with _no_llm():
            result = gate.gate_scene_plan(self._plan())
        self.assertTrue(result.ok)

    def test_empty_plan_is_ok(self):
        self.assertTrue(gate.gate_scene_plan([]).ok)

    def test_short_overlays_are_not_rejected_for_length(self):
        """Overlays are ≤7 words by design — the whole-script minimum must not
        apply to them, or every correct scene plan gets blocked."""
        plan = self._plan(overlay="Rooftop")
        plan[0]["voiceover_text"] = "Light all day."
        with _no_llm():
            result = gate.gate_scene_plan(plan)
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(plan[0]["on_screen_text"], "Rooftop")

    def test_short_fragment_still_screened_for_fair_housing(self):
        """Skipping the length rule must not skip the compliance rule."""
        with _no_llm():
            result = gate.gate_script("No kids", enforce_min_length=False)
        self.assertFalse(result.ok)
        self.assertTrue(result.violations)


if __name__ == "__main__":
    unittest.main()
