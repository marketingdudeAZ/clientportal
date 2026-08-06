"""Journey: POST /api/video-approve — approved videos land in the Files library.

This is the hop the portal copy promises ("they save straight to your Files
library") and the one that was broken: the HubDB row was written with the
HubSpot company id in the `property_uuid` column. Client uploads and the video
generator both key that table on the RPM property uuid, so approved videos
landed under a key nothing reads and were invisible in Files.

External HubSpot / HubDB calls are mocked.
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


def _variant(idx=0, **over):
    v = {
        "variant_id": f"var-{idx}",
        "title": f"Variant {idx + 1}",
        "status": "pending_review",
        "video_url": f"https://cdn.heygen.com/v{idx}.mp4",
        "poster_url": f"https://cdn.heygen.com/v{idx}.jpg",
        "provider": "heygen",
    }
    v.update(over)
    return v


class _HubSpot:
    """Stand-in for the HubSpot + HubDB calls video_approve makes."""

    def __init__(self, variants, uuid=PROPERTY_UUID, hubdb_ok=True, publish_ok=True):
        self.variants = variants
        self.uuid = uuid
        self.hubdb_ok = hubdb_ok
        self.publish_ok = publish_ok
        self.hubdb_rows = []      # every row written to the Files library
        self.company_patches = []

    def get(self, url, **kw):
        resp = mock.MagicMock(ok=True, status_code=200)
        resp.json.return_value = {"properties": {
            "video_variants_json": json.dumps(self.variants),
            "video_cycle_month": "2026-08",
            "uuid": self.uuid,
        }}
        return resp

    def patch(self, url, **kw):
        self.company_patches.append(kw.get("json", {}).get("properties", {}))
        return mock.MagicMock(ok=True, status_code=200)

    def post(self, url, **kw):
        if "/draft/publish" in url:
            return mock.MagicMock(ok=self.publish_ok, status_code=200 if self.publish_ok else 500,
                                  text="publish failed")
        # HubDB row create
        self.hubdb_rows.append(kw.get("json", {}).get("values", {}))
        resp = mock.MagicMock(ok=self.hubdb_ok,
                              status_code=201 if self.hubdb_ok else 400,
                              text="bad row")
        resp.json.return_value = {"id": f"row-{len(self.hubdb_rows)}"}
        return resp


class VideoApproveJourney(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import server
        cls.client = server.app.test_client()

    def _approve(self, hs, variant_ids="all"):
        with mock.patch("requests.get", side_effect=hs.get), \
             mock.patch("requests.patch", side_effect=hs.patch), \
             mock.patch("requests.post", side_effect=hs.post):
            return self.client.post(
                "/api/video-approve",
                json={"company_id": COMPANY_ID, "variant_ids": variant_ids},
                headers={"X-Portal-Email": "pm@example.com"},
            )

    # ── the bug ────────────────────────────────────────────────────────────

    def test_files_row_is_keyed_by_property_uuid_not_company_id(self):
        hs = _HubSpot([_variant(0)])
        resp = self._approve(hs)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(hs.hubdb_rows), 1)
        self.assertEqual(hs.hubdb_rows[0]["property_uuid"], PROPERTY_UUID)
        self.assertNotEqual(hs.hubdb_rows[0]["property_uuid"], COMPANY_ID)

    def test_row_carries_the_rendered_video_and_poster(self):
        hs = _HubSpot([_variant(0)])
        self._approve(hs)
        row = hs.hubdb_rows[0]
        self.assertEqual(row["file_url"], "https://cdn.heygen.com/v0.mp4")
        self.assertEqual(row["thumbnail_url"], "https://cdn.heygen.com/v0.jpg")
        self.assertEqual(row["file_type"], "mp4")

    def test_file_type_follows_the_actual_url(self):
        """/api/property-assets filters on file_type — a wrong value hides the
        asset from the library it was just saved into."""
        hs = _HubSpot([_variant(0, video_url="https://cdn.heygen.com/v0.mov")])
        self._approve(hs)
        self.assertEqual(hs.hubdb_rows[0]["file_type"], "mov")

    # ── idempotency ────────────────────────────────────────────────────────

    def test_reapproving_does_not_duplicate_the_files_entry(self):
        variants = [_variant(0)]
        first = _HubSpot(variants)
        self._approve(first)
        self.assertEqual(len(first.hubdb_rows), 1)

        # Second call sees the variant as the first call persisted it.
        persisted = json.loads(first.company_patches[-1]["video_variants_json"])
        self.assertTrue(persisted[0].get("asset_row_id"))

        second = _HubSpot(persisted)
        resp = self._approve(second)
        self.assertEqual(len(second.hubdb_rows), 0,
                         "re-approval created a duplicate Files entry")
        body = resp.get_json()
        self.assertEqual(body["saved_to_files"], 0)
        self.assertEqual(body["approved"], 0, "nothing changed status on re-approval")

    def test_retry_after_a_filing_failure_files_the_video(self):
        """A variant approved but not filed has no asset_row_id, so the next
        approve must retry it rather than skip it as already done."""
        failed = _HubSpot([_variant(0)], hubdb_ok=False)
        self._approve(failed)
        persisted = json.loads(failed.company_patches[-1]["video_variants_json"])
        self.assertEqual(persisted[0]["status"], "approved")
        self.assertIsNone(persisted[0].get("asset_row_id"))

        retry = _HubSpot(persisted)
        body = self._approve(retry).get_json()
        self.assertEqual(len(retry.hubdb_rows), 1)
        self.assertEqual(body["saved_to_files"], 1)

    def test_approving_a_second_variant_files_only_that_one(self):
        v0 = _variant(0, status="approved", asset_row_id="row-1")
        v1 = _variant(1)
        hs = _HubSpot([v0, v1])
        self._approve(hs)
        self.assertEqual(len(hs.hubdb_rows), 1)
        self.assertEqual(hs.hubdb_rows[0]["file_url"], "https://cdn.heygen.com/v1.mp4")

    # ── real failure modes instead of silent ones ──────────────────────────

    def test_missing_uuid_reports_a_failure_rather_than_filing_wrong(self):
        hs = _HubSpot([_variant(0)], uuid="")
        resp = self._approve(hs)
        body = resp.get_json()

        self.assertEqual(len(hs.hubdb_rows), 0)
        self.assertEqual(body["saved_to_files"], 0)
        self.assertTrue(body["filing_failures"])
        self.assertIn("uuid", body["filing_failures"][0]["error"])

    def test_variant_without_a_rendered_video_is_reported(self):
        hs = _HubSpot([_variant(0, video_url="", poster_url="")])
        body = self._approve(hs).get_json()
        self.assertEqual(body["saved_to_files"], 0)
        self.assertIn("no rendered video", body["filing_failures"][0]["error"])

    def test_hubdb_write_failure_is_reported(self):
        hs = _HubSpot([_variant(0)], hubdb_ok=False)
        body = self._approve(hs).get_json()
        self.assertEqual(body["saved_to_files"], 0)
        self.assertTrue(body["filing_failures"])

    def test_failed_write_does_not_mark_the_variant_as_filed(self):
        """Otherwise the next approve would skip it and the video is lost."""
        hs = _HubSpot([_variant(0)], hubdb_ok=False)
        self._approve(hs)
        persisted = json.loads(hs.company_patches[-1]["video_variants_json"])
        self.assertIsNone(persisted[0].get("asset_row_id"))

    def test_publish_failure_is_reported(self):
        hs = _HubSpot([_variant(0)], publish_ok=False)
        body = self._approve(hs).get_json()
        self.assertEqual(body["saved_to_files"], 1)
        self.assertTrue(any("publish" in f["error"] for f in body["filing_failures"]))

    # ── cycle status ───────────────────────────────────────────────────────

    def test_all_approved_sets_cycle_approved(self):
        hs = _HubSpot([_variant(0), _variant(1)])
        body = self._approve(hs).get_json()
        self.assertEqual(body["cycle_status"], "Approved")

    def test_partial_approval_stays_pending_review(self):
        hs = _HubSpot([_variant(0), _variant(1)])
        body = self._approve(hs, variant_ids=["var-0"]).get_json()
        self.assertEqual(body["cycle_status"], "Pending Review")


if __name__ == "__main__":
    unittest.main()
