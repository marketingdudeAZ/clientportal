"""End-to-end behavior of video_generator.generate_videos.

The point of these tests is that no hop fails silently. Before this, a
provider error escaped into the caller's daemon thread and left the company
pinned at "Processing" forever with an empty portal. Every case below asserts
both the returned reason AND that a terminal cycle status reached HubSpot.

All provider, HubSpot, and LLM calls are mocked — no credits are spent.
"""

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "webhook-server"))

import video_generator as vg  # noqa: E402

SCRIPT = ("POV: you just found your next place. Sunlit corner windows. "
          "A rooftop that actually gets used. Book a tour.")

ASSETS = [
    {"file_url": f"https://cdn.example.com/photo{i}.jpg", "asset_name": f"Photo {i}",
     "category": "Photography", "subcategory": "Interior", "file_type": "jpg",
     "description": ""}
    for i in range(5)
]

PROFILE = {
    "fluency_voice_tier": "lifestyle",
    "fluency_differentiators": "Rooftop dog run\nCold-plunge spa",
    "fluency_property_amenities": "Resort pool\nCoworking lounge",
    "fluency_taglines": "Live above it all",
}


def _variant(idx=0, status="pending"):
    return {
        "variant_index": idx, "variant_id": f"var-{idx}", "provider": "heygen",
        "heygen_video_id": f"vid-{idx}", "status": status,
        "voice_id": "v1", "voice_name": "Ella", "voice_gender": "female",
        "aspect_ratio": "9:16", "duration_seconds": 15, "title": f"Variant {idx + 1}",
    }


class _Harness:
    """Patches every outbound edge of generate_videos."""

    def __init__(self, *, assets=None, variants=None, provider_error=None,
                 script=SCRIPT, store_ok=True, profile=None):
        self.assets = ASSETS if assets is None else assets
        self.variants = [_variant(0), _variant(1)] if variants is None else variants
        self.provider_error = provider_error
        self.script = script
        self.store_ok = store_ok
        self.profile = PROFILE if profile is None else profile
        self.writes = []          # every patch_company payload, in order
        self.provider_calls = []  # provider submissions

    def __enter__(self):
        provider = mock.MagicMock()
        if self.provider_error:
            provider.build_variants_for_brief.side_effect = self.provider_error
        else:
            def _submit(**kwargs):
                self.provider_calls.append(kwargs)
                return [dict(v) for v in self.variants]
            provider.build_variants_for_brief.side_effect = _submit

        def _patch_company(company_id, properties):
            self.writes.append(properties)
            if not self.store_ok:
                raise RuntimeError("HubSpot 500")
            return {}

        hubspot_client = mock.MagicMock()
        hubspot_client.patch_company.side_effect = _patch_company

        self._stack = [
            mock.patch.object(vg, "fetch_property_assets", return_value=self.assets),
            mock.patch.object(vg, "get_provider", return_value=provider),
            mock.patch.object(vg, "generate_script_with_assets", return_value={
                "script": self.script, "media_urls": [], "media_plan": [],
            }),
            mock.patch.dict(sys.modules, {"hubspot_client": hubspot_client}),
            mock.patch("community_brief.load_company_state", return_value=self.profile),
            # Keep the Fair Housing nuance pass off the network.
            mock.patch("config.ANTHROPIC_API_KEY", ""),
        ]
        for p in self._stack:
            p.start()
        self.provider = provider
        return self

    def __exit__(self, *exc):
        for p in reversed(self._stack):
            p.stop()
        return False

    @property
    def statuses(self):
        return [w.get("video_cycle_status") for w in self.writes if "video_cycle_status" in w]


def _run(**kwargs):
    defaults = dict(
        property_uuid="uuid-123", hs_object_id="hs-999", property_name="The Mabry",
        tier="Starter", brief={}, property_url="https://themabry.com",
    )
    defaults.update(kwargs)
    return vg.generate_videos(**defaults)


class TestHappyPath(unittest.TestCase):
    def test_variants_submitted_and_stored(self):
        with _Harness() as h:
            result = _run()
        self.assertTrue(result.ok, result.user_message)
        self.assertEqual(result.cycle_status, vg.CYCLE_PROCESSING)
        self.assertEqual(len(result.variants), 2)
        self.assertIn("video_variants_json", h.writes[-1])

    def test_compliance_record_rides_on_each_variant(self):
        """A reviewer looking at this variant months later must be able to see
        whether the Fair Housing pass ran or only degraded."""
        with _Harness():
            result = _run()
        for v in result.variants:
            self.assertIn("compliance", v)
            self.assertEqual(v["compliance"]["fair_housing"], "degraded")
            self.assertTrue(v["compliance"]["fair_housing_degraded"])

    def test_script_reaches_the_provider_and_the_variants(self):
        with _Harness() as h:
            result = _run()
        self.assertEqual(h.provider_calls[0]["brief"]["script"], SCRIPT)
        self.assertEqual(result.variants[0]["script"], SCRIPT)

    def test_result_still_iterates_as_a_variant_list(self):
        """Back-compat for callers written against the old list return type."""
        with _Harness():
            result = _run()
        self.assertEqual(len(list(result)), 2)
        self.assertEqual(len(result), 2)


class TestNotEnoughPhotos(unittest.TestCase):
    def test_below_minimum_stops_before_the_provider(self):
        with _Harness(assets=ASSETS[:1]) as h:
            result = _run()
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "needs_photos")
        self.assertEqual(result.cycle_status, vg.CYCLE_NEEDS_PHOTOS)
        h.provider.build_variants_for_brief.assert_not_called()

    def test_terminal_status_is_written_to_hubspot(self):
        with _Harness(assets=[]) as h:
            _run()
        self.assertIn(vg.CYCLE_NEEDS_PHOTOS, h.statuses)
        self.assertNotIn(vg.CYCLE_PROCESSING, h.statuses)

    def test_message_names_the_shortfall(self):
        with _Harness(assets=ASSETS[:1]):
            result = _run()
        self.assertIn("1 usable photo", result.user_message)
        self.assertEqual(result.asset_count, 1)

    def test_placeholder_urls_do_not_count_as_photos(self):
        """Providers reject placeholders like `property_website_imagery`, so
        they are not usable photos no matter how many of them there are."""
        junk = [{"file_url": "property_website_imagery", "file_type": "jpg",
                 "asset_name": "", "category": "", "subcategory": "", "description": ""}
                for _ in range(9)]
        with _Harness(assets=junk):
            result = _run()
        self.assertEqual(result.reason, "needs_photos")
        self.assertEqual(result.asset_count, 0)


class TestThinProfile(unittest.TestCase):
    def test_thin_profile_stops_when_flag_on(self):
        with mock.patch.dict(os.environ, {"VIDEO_BRIEF_FROM_PROFILE": "true"}), \
             _Harness(profile={}) as h:
            result = _run()
        self.assertEqual(result.reason, "needs_profile")
        self.assertEqual(result.cycle_status, vg.CYCLE_NEEDS_PROFILE)
        h.provider.build_variants_for_brief.assert_not_called()

    def test_thin_profile_ignored_when_flag_off(self):
        """Off by default — the caller's brief is still used."""
        with mock.patch.dict(os.environ, {"VIDEO_BRIEF_FROM_PROFILE": "false"}), \
             _Harness(profile={}):
            result = _run()
        self.assertTrue(result.ok)

    def test_profile_values_reach_the_script_prompt(self):
        with mock.patch.dict(os.environ, {"VIDEO_BRIEF_FROM_PROFILE": "true"}), \
             _Harness() as h:
            _run(brief={"voice_tone": "from-the-browser"})
            call = vg.generate_script_with_assets.call_args.kwargs
        # The Property Profile is the source of truth the portal promises,
        # so it wins over whatever the browser assembled.
        self.assertEqual(call["brief"]["voice_tone"], "lifestyle")
        self.assertIn("Rooftop dog run", call["profile_facts"])
        self.assertIn("Aspirational", call["voice_guidance"])
        self.assertTrue(h.provider_calls)

    def test_profile_load_failure_falls_back_to_supplied_brief(self):
        with mock.patch.dict(os.environ, {"VIDEO_BRIEF_FROM_PROFILE": "true"}), \
             _Harness() as h:
            with mock.patch("community_brief.load_company_state",
                            side_effect=RuntimeError("HubSpot down")):
                result = _run(brief={"voice_tone": "from-the-browser"})
        self.assertTrue(result.ok)
        self.assertTrue(h.provider_calls)


class TestFairHousingBlocks(unittest.TestCase):
    BAD = ("Adults-only building with a rooftop that actually gets used. "
           "Sunlit corner windows. Book a tour.")

    def test_violation_stops_before_the_provider(self):
        with _Harness(script=self.BAD) as h:
            result = _run()
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "fair_housing")
        self.assertEqual(result.cycle_status, vg.CYCLE_BLOCKED_COMPLIANCE)
        h.provider.build_variants_for_brief.assert_not_called()

    def test_violations_are_reported_on_the_result(self):
        with _Harness(script=self.BAD):
            result = _run()
        self.assertTrue(result.compliance["violations"])
        self.assertEqual(result.compliance["fair_housing"], "blocked")

    def test_terminal_status_written(self):
        with _Harness(script=self.BAD) as h:
            _run()
        self.assertIn(vg.CYCLE_BLOCKED_COMPLIANCE, h.statuses)

    def test_pricing_is_stripped_before_the_provider_sees_it(self):
        priced = "Studios from $1,299. Sunlit corner windows. Book a tour today."
        with _Harness(script=priced) as h:
            result = _run()
        self.assertTrue(result.ok)
        submitted = h.provider_calls[0]["brief"]["script"]
        self.assertNotIn("$1,299", submitted)


class TestProviderFailures(unittest.TestCase):
    def test_provider_exception_lands_on_failed(self):
        with _Harness(provider_error=RuntimeError("HeyGen 500")) as h:
            result = _run()
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "provider_failed")
        self.assertIn(vg.CYCLE_FAILED, h.statuses)

    def test_user_message_from_provider_error_is_surfaced(self):
        from video_providers import ProviderError
        err = ProviderError("boom", user_message="No property assets were available.")
        with _Harness(provider_error=err):
            result = _run()
        self.assertEqual(result.user_message, "No property assets were available.")

    def test_no_variants_returned_is_a_failure(self):
        with _Harness(variants=[]) as h:
            result = _run()
        self.assertFalse(result.ok)
        self.assertIn(vg.CYCLE_FAILED, h.statuses)

    def test_all_variants_errored_is_a_failure(self):
        with _Harness(variants=[_variant(0, status="error"), _variant(1, status="error")]):
            result = _run()
        self.assertFalse(result.ok)
        self.assertEqual(result.cycle_status, vg.CYCLE_FAILED)

    def test_partial_submission_still_proceeds(self):
        with _Harness(variants=[_variant(0), _variant(1, status="error")]):
            result = _run()
        self.assertTrue(result.ok)


class TestStoreFailure(unittest.TestCase):
    def test_failed_store_is_reported_not_swallowed(self):
        """The provider is already rendering but nothing points at those jobs —
        that is a failure, not a success with a log line."""
        with _Harness(store_ok=False):
            result = _run()
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "store_failed")
        self.assertIn("couldn't save", result.user_message)


class TestUsableMediaUrls(unittest.TestCase):
    def test_only_http_urls_are_usable(self):
        assets = [
            {"file_url": "https://cdn/a.jpg"},
            {"file_url": "http://cdn/b.jpg"},
            {"file_url": "property_website_imagery"},
            {"file_url": ""},
            {},
        ]
        self.assertEqual(
            vg.usable_media_urls(assets),
            ["https://cdn/a.jpg", "http://cdn/b.jpg"],
        )


if __name__ == "__main__":
    unittest.main()
