"""Unit tests for the VideoProvider abstraction and HeyGen payload shape.

These tests mock out all outbound HTTP so they run without Creatify or HeyGen
credentials. They cover the contract both providers must honor plus the
HeyGen-specific scene plan → v2 payload translation and webhook parsing.
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

# Make `webhook-server/` importable so we can reach video_providers.*
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "webhook-server"))


class TestProviderFactory(unittest.TestCase):
    def test_known_providers_registered(self):
        from video_providers import PROVIDERS
        self.assertIn("creatify", PROVIDERS)
        self.assertIn("heygen", PROVIDERS)

    def test_normalize_provider_name_unknown_falls_back(self):
        from video_providers import normalize_provider_name
        self.assertEqual(normalize_provider_name("unknown"), "creatify")

    def test_normalize_provider_name_case_insensitive(self):
        from video_providers import normalize_provider_name
        self.assertEqual(normalize_provider_name("HEYGEN"), "heygen")
        self.assertEqual(normalize_provider_name("  Creatify  "), "creatify")

    def test_get_provider_returns_instance(self):
        from video_providers import get_provider, CreatifyProvider, HeyGenProvider
        self.assertIsInstance(get_provider("creatify"), CreatifyProvider)
        self.assertIsInstance(get_provider("heygen"), HeyGenProvider)


class TestCreatifyProvider(unittest.TestCase):
    def test_describe_shape(self):
        from video_providers import CreatifyProvider
        d = CreatifyProvider().describe()
        self.assertEqual(d["name"], "creatify")
        self.assertFalse(d["supports_scene_plan"])
        self.assertTrue(d["always_renders_avatar"])

    def test_build_variants_requires_creds(self):
        from video_providers import CreatifyProvider, ProviderError
        with patch("video_providers.creatify_provider.CREATIFY_API_ID", ""), \
             patch("video_providers.creatify_provider.CREATIFY_API_KEY", ""):
            with self.assertRaises(ProviderError):
                CreatifyProvider().build_variants_for_brief(
                    brief={"script": "A test script of sufficient length to pass validation."},
                    property_url="https://example.com",
                    tier="Starter",
                )

    def test_webhook_parser_normalizes(self):
        from video_providers import CreatifyProvider
        payload = {
            "id": "job-abc",
            "status": "done",
            "video_output": "https://cdn.example/video.mp4",
            "video_thumbnail": "https://cdn.example/thumb.jpg",
            "failed_reason": None,
        }
        parsed = CreatifyProvider().normalize_webhook(payload)
        self.assertEqual(parsed["job_id"], "job-abc")
        self.assertEqual(parsed["status"], "done")
        self.assertEqual(parsed["video_url"], "https://cdn.example/video.mp4")
        self.assertEqual(parsed["thumbnail_url"], "https://cdn.example/thumb.jpg")


class TestHeyGenProvider(unittest.TestCase):
    def setUp(self):
        # Patch HEYGEN_API_KEY so the provider passes the configured check.
        self._api_patch = patch(
            "video_providers.heygen_provider.HEYGEN_API_KEY", "test-key"
        )
        self._api_patch.start()
        # Make a fresh provider per test.
        from video_providers import HeyGenProvider
        self.provider = HeyGenProvider()

    def tearDown(self):
        self._api_patch.stop()

    def test_describe_shape(self):
        d = self.provider.describe()
        self.assertEqual(d["name"], "heygen")
        self.assertTrue(d["supports_scene_plan"])
        self.assertFalse(d["always_renders_avatar"])

    def test_build_generate_payload_has_no_avatar(self):
        from video_providers.heygen_provider import HeyGenProvider
        scenes = [
            {
                "duration_s": 4,
                "asset_url": "https://cdn.example/exterior.jpg",
                "asset_type": "image",
                "voiceover_text": "Welcome home.",
                "on_screen_text": "Scottsdale Living",
            },
            {
                "duration_s": 6,
                "asset_url": "https://cdn.example/pool.mp4",
                "asset_type": "video",
                "voiceover_text": "Resort-style amenities.",
                "on_screen_text": "",
            },
        ]
        payload = HeyGenProvider._build_generate_payload(
            scenes=scenes,
            voice_id="voice-123",
            aspect_ratio="9:16",
            webhook_url="https://hooks.example/heygen",
            callback_id="var-1",
            script_fallback="Welcome home. Resort-style amenities.",
        )

        # Top-level shape
        self.assertEqual(payload["dimension"], {"width": 1080, "height": 1920})
        self.assertEqual(payload["callback_url"], "https://hooks.example/heygen")
        self.assertEqual(payload["callback_id"], "var-1")
        self.assertFalse(payload["test"])

        # Every scene must be avatar-free with a voice + background
        self.assertEqual(len(payload["video_inputs"]), 2)
        for s in payload["video_inputs"]:
            self.assertEqual(s["character"], {"type": "none"})
            self.assertEqual(s["voice"]["voice_id"], "voice-123")
            self.assertEqual(s["voice"]["type"], "text")
            self.assertIn(s["background"]["type"], ("image", "video"))
            self.assertTrue(s["background"]["url"].startswith("https://"))
            self.assertEqual(s["background"]["fit"], "cover")

        # First scene has overlay; second does not
        self.assertEqual(payload["video_inputs"][0]["text_overlay"]["text"], "Scottsdale Living")
        self.assertNotIn("text_overlay", payload["video_inputs"][1])

        # Image vs video correctly derived from asset_type
        self.assertEqual(payload["video_inputs"][0]["background"]["type"], "image")
        self.assertEqual(payload["video_inputs"][1]["background"]["type"], "video")

    def test_aspect_ratio_mapping(self):
        from video_providers.heygen_provider import HeyGenProvider
        scenes = [{"asset_url": "https://x/y.jpg", "asset_type": "image",
                   "voiceover_text": "Hi", "on_screen_text": ""}]

        p_square = HeyGenProvider._build_generate_payload(
            scenes=scenes, voice_id="v", aspect_ratio="1:1",
            webhook_url=None, callback_id="x", script_fallback="Hi",
        )
        self.assertEqual(p_square["dimension"], {"width": 1080, "height": 1080})

        p_wide = HeyGenProvider._build_generate_payload(
            scenes=scenes, voice_id="v", aspect_ratio="16:9",
            webhook_url=None, callback_id="x", script_fallback="Hi",
        )
        self.assertEqual(p_wide["dimension"], {"width": 1920, "height": 1080})

    def test_fallback_scene_plan_from_script(self):
        from video_providers.heygen_provider import HeyGenProvider
        script = (
            "Welcome home. Luxury apartments in the heart of Scottsdale. "
            "Resort-style pool. Pet-friendly community. Schedule your tour today."
        )
        media = [
            "https://cdn.example/a.jpg",
            "https://cdn.example/b.mp4",
            "https://cdn.example/c.png",
        ]
        scenes = HeyGenProvider._fallback_scene_plan(script, media)
        self.assertEqual(len(scenes), 3)
        self.assertEqual(scenes[1]["asset_type"], "video")   # .mp4
        self.assertEqual(scenes[0]["asset_type"], "image")
        self.assertEqual(scenes[2]["asset_type"], "image")
        for s in scenes:
            self.assertTrue(s["voiceover_text"])

    def test_webhook_done_event(self):
        payload = {
            "event_type": "avatar_video.success",
            "event_data": {
                "video_id": "vid-xyz",
                "callback_id": "var-1",
                "url": "https://cdn.heygen.com/video.mp4",
                "gif_url": "https://cdn.heygen.com/thumb.gif",
            },
        }
        # Ensure no secret is configured so we skip the signature check
        with patch("video_providers.heygen_provider.HEYGEN_WEBHOOK_SECRET", ""):
            result = self.provider.normalize_webhook(payload)
        self.assertEqual(result["job_id"], "vid-xyz")
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["video_url"], "https://cdn.heygen.com/video.mp4")
        self.assertEqual(result["thumbnail_url"], "https://cdn.heygen.com/thumb.gif")
        self.assertEqual(result["variant_id"], "var-1")
        self.assertEqual(result["property_uuid"], "")

    def test_webhook_decodes_property_uuid_from_callback(self):
        """Verify callback_id "variant_id|property_uuid" round-trips back out."""
        payload = {
            "event_type": "avatar_video.success",
            "event_data": {
                "video_id": "vid-1",
                "callback_id": "var-abc|uuid-xyz-123",
                "url": "https://cdn.heygen.com/video.mp4",
            },
        }
        with patch("video_providers.heygen_provider.HEYGEN_WEBHOOK_SECRET", ""):
            result = self.provider.normalize_webhook(payload)
        self.assertEqual(result["variant_id"], "var-abc")
        self.assertEqual(result["property_uuid"], "uuid-xyz-123")

    def test_webhook_signature_mismatch_raises(self):
        import hashlib
        import hmac
        from video_providers import ProviderError

        body = '{"event_type":"avatar_video.success","event_data":{"video_id":"vid-1"}}'
        good_sig = hmac.new(b"secret", body.encode(), hashlib.sha256).hexdigest()

        with patch("video_providers.heygen_provider.HEYGEN_WEBHOOK_SECRET", "secret"):
            # Good signature — must succeed
            ok = self.provider.normalize_webhook(
                {"event_type": "avatar_video.success", "event_data": {"video_id": "vid-1"}},
                headers={"X-Signature": good_sig, "_raw_body": body},
            )
            self.assertEqual(ok["job_id"], "vid-1")

            # Bad signature — must raise
            with self.assertRaises(ProviderError):
                self.provider.normalize_webhook(
                    {"event_type": "avatar_video.success", "event_data": {"video_id": "vid-1"}},
                    headers={"X-Signature": "deadbeef", "_raw_body": body},
                )

    def test_get_job_status_maps_heygen_states(self):
        fake_session = MagicMock()
        fake_resp = MagicMock()
        fake_resp.ok = True
        fake_resp.json.return_value = {
            "data": {
                "status": "completed",
                "video_url": "https://cdn.heygen.com/final.mp4",
                "thumbnail_url": "https://cdn.heygen.com/poster.jpg",
                "duration": 17.5,
            }
        }
        fake_session.get.return_value = fake_resp
        self.provider._session_obj = fake_session

        st = self.provider.get_job_status("vid-77")
        self.assertEqual(st["status"], "done")
        self.assertEqual(st["video_url"], "https://cdn.heygen.com/final.mp4")
        self.assertEqual(st["thumbnail_url"], "https://cdn.heygen.com/poster.jpg")

        # Processing state
        fake_resp.json.return_value = {"data": {"status": "processing"}}
        st = self.provider.get_job_status("vid-78")
        self.assertEqual(st["status"], "running")

        # Failed state surfaces the reason
        fake_resp.json.return_value = {"data": {"status": "failed", "error": "bad asset url"}}
        st = self.provider.get_job_status("vid-79")
        self.assertEqual(st["status"], "failed")
        self.assertEqual(st["failed_reason"], "bad asset url")


class TestSceneplanValidator(unittest.TestCase):
    def test_drops_invalid_and_sanitizes(self):
        from video_pipeline_config import validate_scene_plan
        plan = [
            {"asset_url": "not-a-url", "voiceover_text": "skip"},
            {"asset_url": "https://cdn.example/a.jpg",
             "voiceover_text": "Starting at $1,200 per month.",
             "on_screen_text": "Luxury Living"},
            {"asset_url": "https://cdn.example/b.mp4",
             "voiceover_text": "Resort pool.",
             "duration_s": 4},
        ]
        out = validate_scene_plan(plan)
        self.assertEqual(len(out["plan"]), 2, "non-URL scene should be dropped")
        # Pricing stripped from scene 0 in the cleaned plan
        self.assertNotIn("$", out["plan"][0]["voiceover_text"])
        # Video auto-detected from extension
        self.assertEqual(out["plan"][1]["asset_type"], "video")
        self.assertTrue(any("pricing" in e for e in out["errors"]))

    def test_caps_at_max_scenes(self):
        from video_pipeline_config import validate_scene_plan, MAX_SCENES
        plan = [
            {"asset_url": f"https://cdn.example/{i}.jpg", "voiceover_text": "x"}
            for i in range(MAX_SCENES + 3)
        ]
        out = validate_scene_plan(plan)
        self.assertEqual(len(out["plan"]), MAX_SCENES)
        self.assertTrue(any("truncated" in e for e in out["errors"]))


if __name__ == "__main__":
    unittest.main()
