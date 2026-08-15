"""Journey: the HeyGen webhook moves a cycle off "Processing", and
/api/video-regenerate re-renders on the variant's OWN provider.

Both were places a variant could get stranded:

  - a cycle where every variant failed stayed at "Processing" forever, so the
    portal spun with nothing to show;
  - regenerate always submitted to Creatify, so a HeyGen variant came back
    with a creatify_job_id while still tagged provider="heygen". The poller
    looks up heygen_video_id, finds none, and never touches it again.

External HubSpot / provider calls are mocked — no provider credits spent.
"""

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "webhook-server"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

os.environ.setdefault("HUBSPOT_API_KEY", "test-hub-key")

COMPANY_ID = "hs-999"
PROPERTY_UUID = "uuid-abc-123"

GOOD_SCRIPT = ("POV: you just found your next place. Sunlit corner windows. "
               "A rooftop that actually gets used. Book a tour.")


def _heygen_variant(idx=0, status="pending"):
    return {
        "variant_id": f"var-{idx}", "provider": "heygen",
        "heygen_video_id": f"vid-{idx}", "status": status,
        "aspect_ratio": "9:16", "duration_seconds": 15,
        "voice_id": "d84493795278413d8d7a2dc6c9026318",
        "voice_name": "Dynamic Derek", "title": f"Variant {idx + 1}",
        "property_uuid": PROPERTY_UUID, "revision_count": 0,
        "scene_plan": [
            {"duration_s": 3, "asset_url": "https://cdn/a.jpg", "asset_type": "image",
             "voiceover_text": "Sunlit corner windows.", "on_screen_text": "Light all day"},
            {"duration_s": 3, "asset_url": "https://cdn/b.jpg", "asset_type": "image",
             "voiceover_text": "A rooftop that gets used.", "on_screen_text": "Rooftop"},
        ],
    }


class TestCycleStatusRule(unittest.TestCase):
    """_video_cycle_status_from is the single derivation both the webhook and
    the auto-poller use."""

    @classmethod
    def setUpClass(cls):
        import server
        cls.derive = staticmethod(server._video_cycle_status_from)

    def test_anything_rendering_stays_processing(self):
        self.assertEqual(self.derive([
            {"status": "pending_review"}, {"status": "pending"},
        ]), "Processing")

    def test_all_failed_becomes_failed_not_processing(self):
        self.assertEqual(self.derive([
            {"status": "failed"}, {"status": "error"},
        ]), "Failed")

    def test_all_approved_becomes_approved(self):
        self.assertEqual(self.derive([
            {"status": "approved"}, {"status": "approved"},
        ]), "Approved")

    def test_mixed_terminal_becomes_pending_review(self):
        self.assertEqual(self.derive([
            {"status": "pending_review"}, {"status": "failed"},
        ]), "Pending Review")

    def test_empty_list_stays_processing(self):
        self.assertEqual(self.derive([]), "Processing")

    def test_revision_keeps_the_status_video_revise_set(self):
        """A variant awaiting revision isn't rendering — don't push the cycle
        back to "Processing" and lose what the client asked for."""
        self.assertEqual(self.derive([
            {"status": "revision_in_progress"}, {"status": "pending_review"},
        ]), "Revision Requested")

    def test_a_still_rendering_variant_outranks_a_revision(self):
        self.assertEqual(self.derive([
            {"status": "revision_in_progress"}, {"status": "pending"},
        ]), "Processing")


class TestHeyGenWebhookStatus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import server
        cls.client = server.app.test_client()

    def _post(self, variants, *, event="avatar_video.success", write_ok=True):
        body = json.dumps({
            "event_type": event,
            "event_data": {
                "video_id": "vid-0",
                "callback_id": f"var-0|{PROPERTY_UUID}",
                "url": "https://cdn.heygen.com/v0.mp4",
            },
        })
        patches = []

        def _get(url, **kw):
            resp = mock.MagicMock(ok=True, status_code=200)
            resp.json.return_value = {"properties": {
                "video_variants_json": json.dumps(variants),
            }}
            return resp

        def _post_hs(url, **kw):
            resp = mock.MagicMock(ok=True, status_code=200)
            resp.json.return_value = {"results": [{
                "id": COMPANY_ID,
                "properties": {"video_variants_json": json.dumps(variants),
                               "uuid": PROPERTY_UUID},
            }]}
            return resp

        def _patch(url, **kw):
            patches.append(kw.get("json", {}).get("properties", {}))
            return mock.MagicMock(ok=write_ok, status_code=200 if write_ok else 500,
                                  text="hubspot down")

        with mock.patch("video_providers.heygen_provider.HEYGEN_WEBHOOK_SECRET", ""), \
             mock.patch("requests.get", side_effect=_get), \
             mock.patch("requests.post", side_effect=_post_hs), \
             mock.patch("requests.patch", side_effect=_patch):
            resp = self.client.post("/api/heygen-webhook", data=body,
                                    content_type="application/json")
        return resp, patches

    def test_success_lands_the_video_and_moves_to_pending_review(self):
        resp, patches = self._post([_heygen_variant(0)])
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["written"])
        self.assertEqual(patches[-1]["video_cycle_status"], "Pending Review")

        stored = json.loads(patches[-1]["video_variants_json"])
        self.assertEqual(stored[0]["status"], "pending_review")
        self.assertEqual(stored[0]["video_url"], "https://cdn.heygen.com/v0.mp4")

    def test_sole_variant_failing_moves_cycle_off_processing(self):
        resp, patches = self._post([_heygen_variant(0)], event="avatar_video.fail")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(patches[-1]["video_cycle_status"], "Failed")

    def test_failure_with_a_sibling_still_rendering_stays_processing(self):
        resp, patches = self._post(
            [_heygen_variant(0), _heygen_variant(1)], event="avatar_video.fail")
        self.assertEqual(patches[-1]["video_cycle_status"], "Processing")

    def test_hubspot_write_failure_is_not_reported_as_success(self):
        """HeyGen retries on non-2xx — which is what we want when the render
        landed but our record of it did not."""
        resp, _ = self._post([_heygen_variant(0)], write_ok=False)
        self.assertEqual(resp.status_code, 502)
        self.assertFalse(resp.get_json()["written"])

    def test_require_signature_flag_rejects_unsigned(self):
        body = json.dumps({"event_type": "avatar_video.success",
                           "event_data": {"video_id": "v", "callback_id": "c"}})
        with mock.patch("video_providers.heygen_provider.HEYGEN_WEBHOOK_SECRET", ""), \
             mock.patch.dict(os.environ, {"HEYGEN_WEBHOOK_REQUIRE_SIGNATURE": "true"}):
            resp = self.client.post("/api/heygen-webhook", data=body,
                                    content_type="application/json")
        self.assertEqual(resp.status_code, 401)

    def test_unsigned_still_accepted_by_default(self):
        """Turning signing on must stay a config flip, not a deploy."""
        with mock.patch.dict(os.environ, {"HEYGEN_WEBHOOK_REQUIRE_SIGNATURE": "false"}):
            resp, _ = self._post([_heygen_variant(0)])
        self.assertEqual(resp.status_code, 200)


class TestRegenerateRouting(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import server
        cls.client = server.app.test_client()

    def _regenerate(self, variant, body_extra=None, heygen_resp=None):
        variants = [variant]
        patches = []

        def _get(url, **kw):
            resp = mock.MagicMock(ok=True, status_code=200)
            resp.json.return_value = {"properties": {
                "name": "The Mabry", "domain": "themabry.com",
                "video_variants_json": json.dumps(variants),
                "video_cycle_history_json": "[]",
            }}
            return resp

        def _patch(url, **kw):
            patches.append(kw.get("json", {}).get("properties", {}))
            return mock.MagicMock(ok=True, status_code=200)

        payload = {"company_id": COMPANY_ID, "variant_id": variant["variant_id"],
                   "script": GOOD_SCRIPT}
        payload.update(body_extra or {})

        heygen_post = mock.MagicMock(return_value=heygen_resp
                                     or {"data": {"video_id": "vid-new"}})
        creatify_job = mock.MagicMock(return_value={"id": "creatify-new"})

        with mock.patch("requests.get", side_effect=_get), \
             mock.patch("requests.patch", side_effect=_patch), \
             mock.patch("config.ANTHROPIC_API_KEY", ""), \
             mock.patch("video_providers.heygen_provider.HEYGEN_API_KEY", "hg-key"), \
             mock.patch("video_providers.HeyGenProvider._post", heygen_post), \
             mock.patch("creatify_client.create_video_job", creatify_job):
            resp = self.client.post("/api/video-regenerate", json=payload,
                                    headers={"X-Portal-Email": "pm@example.com"})
        return resp, patches, heygen_post, creatify_job

    def test_heygen_variant_goes_to_heygen(self):
        resp, patches, heygen_post, creatify_job = self._regenerate(_heygen_variant(0))

        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        self.assertEqual(resp.get_json()["provider"], "heygen")
        heygen_post.assert_called_once()
        creatify_job.assert_not_called()

    def test_heygen_job_id_lands_on_the_field_the_poller_reads(self):
        _resp, patches, _hp, _cj = self._regenerate(_heygen_variant(0))
        stored = json.loads(patches[-1]["video_variants_json"])[0]

        self.assertEqual(stored["heygen_video_id"], "vid-new")
        self.assertEqual(stored["provider"], "heygen")
        self.assertIsNone(stored.get("creatify_job_id"))

    def test_creatify_variant_still_goes_to_creatify(self):
        variant = _heygen_variant(0)
        variant.update({"provider": "creatify", "creatify_job_id": "old-job"})
        variant.pop("heygen_video_id")
        _resp, _p, heygen_post, creatify_job = self._regenerate(variant)

        creatify_job.assert_called_once()
        heygen_post.assert_not_called()

    def test_heygen_voice_is_accepted(self):
        """The Creatify-only catalog rejected every HeyGen voice id."""
        resp, _p, _hp, _cj = self._regenerate(
            _heygen_variant(0),
            body_extra={"voice_id": "8552d4c72f6448009910edf84b93b0f6"},  # Energetic Ella
        )
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))

    def test_unknown_voice_is_still_rejected(self):
        resp, _p, _hp, _cj = self._regenerate(
            _heygen_variant(0), body_extra={"voice_id": "not-a-real-voice"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("approved list", resp.get_json()["error"])

    def test_edited_script_is_fair_housing_screened(self):
        resp, _p, _hp, creatify_job = self._regenerate(
            _heygen_variant(0),
            body_extra={"script": "Adults-only building. Sunlit corner windows. Book a tour."},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertTrue(resp.get_json()["violations"])
        creatify_job.assert_not_called()

    def test_pricing_is_stripped_from_the_edited_script(self):
        _resp, patches, _hp, _cj = self._regenerate(
            _heygen_variant(0),
            body_extra={"script": "Studios from $1,299. Sunlit corner windows. Book a tour."},
        )
        stored = json.loads(patches[-1]["video_variants_json"])[0]
        self.assertNotIn("$1,299", stored["script"])

    def test_compliance_record_is_written_onto_the_variant(self):
        _resp, patches, _hp, _cj = self._regenerate(_heygen_variant(0))
        stored = json.loads(patches[-1]["video_variants_json"])[0]
        self.assertEqual(stored["compliance"]["fair_housing"], "degraded")


if __name__ == "__main__":
    unittest.main()
