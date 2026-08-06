"""The video script's brief comes from the Property Profile.

Two properties matter more than the field mapping itself:

  1. Override-wins is honored — because the values route through
     `community_brief.resolve_value()` and are not re-derived here.
  2. `internal=True` fields NEVER reach ad copy. The ICP field is explicitly
     allowed to discuss age and family status for internal strategy; a leak
     into a generated housing ad is a Fair Housing problem, not a cosmetic one.
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "webhook-server"))

import community_brief as cb  # noqa: E402
import video_brief as vb  # noqa: E402


def _props(**overrides):
    """A Property Profile with enough substance to be worth generating from."""
    base = {
        "fluency_voice_tier": "lifestyle",
        "fluency_differentiators": "Rooftop dog run\nCold-plunge spa",
        "fluency_property_amenities": "Resort pool\nCoworking lounge",
        "fluency_taglines": "Live above it all",
        "fluency_neighborhood": "South Congress",
    }
    base.update(overrides)
    return base


class TestOverrideWins(unittest.TestCase):
    def test_override_beats_resolved(self):
        brief = vb.build_video_brief(_props(
            fluency_voice_tier="value",
            fluency_voice_tier_override="luxury",
        ))
        self.assertEqual(brief.voice_tier, "luxury")

    def test_whitespace_only_override_is_not_an_edit(self):
        """The drift bug the canonical resolver exists to prevent: a blank
        override must fall through to the resolved value, not blank the field."""
        brief = vb.build_video_brief(_props(
            fluency_voice_tier="lifestyle",
            fluency_voice_tier_override="   ",
        ))
        self.assertEqual(brief.voice_tier, "lifestyle")

    def test_resolver_is_not_reimplemented(self):
        """build_video_brief must go through community_brief.resolve_value so
        the portal, Fluency, and video can never disagree about a value."""
        calls = []
        original = cb.resolve_value

        def spy(props, resolved_key, override_key):
            calls.append((resolved_key, override_key))
            return original(props, resolved_key, override_key)

        cb.resolve_value = spy
        try:
            vb.build_video_brief(_props())
        finally:
            cb.resolve_value = original
        self.assertTrue(calls, "build_video_brief bypassed resolve_value()")


class TestInternalFieldsExcluded(unittest.TestCase):
    """internal=True fields must never appear in anything sent to an ad."""

    def test_icp_never_reaches_the_brief(self):
        icp = "Ages 25-34, mostly young couples without children, high income"
        brief = vb.build_video_brief(_props(fluency_target_resident=icp))

        self.assertNotIn(icp, brief.facts)
        self.assertNotIn(icp, repr(brief.brief))
        for value in brief.brief.values():
            self.assertNotIn("without children", str(value).lower())

    def test_budget_and_ops_fields_excluded(self):
        brief = vb.build_video_brief(_props(
            fluency_marketing_budget="$42,000/mo",
            fluency_pms="Yardi",
            fluency_residents_dislike="Thin walls, slow elevators",
            fluency_client_expectations="Agency may not update copy",
        ))
        for leaked in ("42,000", "Yardi", "Thin walls", "may not update copy"):
            self.assertNotIn(leaked, brief.facts)

    def test_audience_comes_from_motivations_not_icp(self):
        brief = vb.build_video_brief(_props(
            fluency_target_resident="Young families with kids",
            fluency_motivations_considerations_override="Walkability, short commute, room to work from home",
        ))
        self.assertEqual(
            brief.brief["target_audience"],
            "Walkability, short commute, room to work from home",
        )
        self.assertNotIn("kids", brief.brief["target_audience"].lower())

    def test_ad_copy_fields_excludes_tables_and_documents(self):
        fields = cb.ad_copy_fields(_props(
            fluency_tracking_table='[{"source":"Yelp","tracking_number":"555"}]',
        ))
        for field, _value in fields:
            self.assertNotIn(field.type, ("tracking_table", "documents"))
            self.assertFalse(field.internal)


class TestThinProfile(unittest.TestCase):
    def test_empty_profile_is_thin(self):
        self.assertTrue(vb.build_video_brief({}).is_thin)

    def test_single_field_is_thin(self):
        brief = vb.build_video_brief({"fluency_taglines": "Live above it all"})
        self.assertTrue(brief.is_thin)

    def test_populated_profile_is_not_thin(self):
        self.assertFalse(vb.build_video_brief(_props()).is_thin)

    def test_internal_only_profile_is_still_thin(self):
        """A profile full of internal context has nothing to advertise with."""
        brief = vb.build_video_brief({
            "fluency_target_resident": "Ages 25-34",
            "fluency_marketing_budget": "$42,000",
            "fluency_pms": "Yardi",
        })
        self.assertTrue(brief.is_thin)


class TestVoiceAndGuardrails(unittest.TestCase):
    def test_voice_guidance_reflects_tier(self):
        self.assertIn("Restrained", vb.voice_guidance("luxury"))
        self.assertIn("Practical", vb.voice_guidance("value"))

    def test_multiselect_voice_tier_combines(self):
        guidance = vb.voice_guidance("lifestyle\nluxury")
        self.assertIn("Aspirational", guidance)
        self.assertIn("Restrained", guidance)

    def test_unknown_tier_yields_no_guidance(self):
        self.assertEqual(vb.voice_guidance("brutalist"), "")
        self.assertEqual(vb.voice_guidance(""), "")

    def test_forbidden_phrases_are_split(self):
        brief = vb.build_video_brief(_props(
            fluency_forbidden_phrases="luxury living at its finest\nsafe neighborhood",
        ))
        self.assertEqual(
            brief.forbidden_phrases,
            ["luxury living at its finest", "safe neighborhood"],
        )


if __name__ == "__main__":
    unittest.main()
