"""Tests for Bridge 1 — self-checkout Deal → ClickUp fulfillment task.

Covers:
  - config gate (no list env / no token → skip, not error)
  - happy path (creates a task, stamps the deal)
  - durable dedup (deal already stamped → skip, no second task)
  - in-process TTL claim (double delivery → one task)
  - status-slug fallback (bad status → retry without status)
  - task create failure releases the claim so a retry can succeed

External I/O — HubSpot and ClickUp — is fully mocked.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("HUBSPOT_API_KEY", "test-key")
os.environ.setdefault("CLICKUP_API_KEY", "test-key")
os.environ["CLICKUP_LIST_FULFILLMENT"] = "900900900"

import clickup_client  # noqa: E402
import fulfillment_task as ft  # noqa: E402


def _resp(json_data, status=200):
    m = mock.Mock()
    m.status_code = status
    m.json.return_value = json_data
    m.raise_for_status.return_value = None
    m.text = ""
    return m


class DealMocks:
    """Route HubSpot deal GET/PATCH by URL; ClickUp create is mocked separately."""

    def __init__(self, stamp=""):
        self.stamp = stamp
        self.patches = []

    def get(self, url, **kw):
        if "/crm/v3/objects/deals/" in url:
            return _resp({"properties": {ft.TASK_ID_PROP: self.stamp}})
        raise AssertionError(f"unexpected GET {url}")

    def patch(self, url, json=None, **kw):
        self.patches.append((url, json))
        return _resp({})


class FulfillmentTaskTests(unittest.TestCase):

    def setUp(self):
        ft._recent.clear()
        os.environ["CLICKUP_LIST_FULFILLMENT"] = "900900900"
        os.environ.pop("CLICKUP_FULFILLMENT_STATUS", None)

    _DEFAULT = object()

    def _run(self, mocks, *, create_result=_DEFAULT, deal_id="deal-1", company_id="co-1", **kw):
        if create_result is self._DEFAULT:
            create_result = {"id": "task-77", "url": "https://app.clickup.com/t/task-77"}
        with mock.patch.object(ft.requests, "get", side_effect=mocks.get), \
             mock.patch.object(ft.requests, "patch", side_effect=mocks.patch), \
             mock.patch.object(clickup_client, "create_task", return_value=create_result) as ct:
            result = ft.create_for_deal(deal_id, company_id, **kw)
        return result, ct

    # ── config gate ──────────────────────────────────────────────────────

    def test_no_list_env_skips(self):
        os.environ.pop("CLICKUP_LIST_FULFILLMENT", None)
        result = ft.create_for_deal("deal-1", "co-1")
        self.assertEqual(result, {"skipped": "list env unset"})

    def test_no_deal_id_skips(self):
        result = ft.create_for_deal("", "co-1")
        self.assertIn("skipped", result)

    # ── happy path ───────────────────────────────────────────────────────

    def test_creates_task_and_stamps_deal(self):
        mocks = DealMocks(stamp="")
        result, ct = self._run(
            mocks, channel="Paid Search", amount=1500.0,
            property_name="Maple Court", property_uuid="uuid-abc",
            launch_date="2026-07-10",
        )
        self.assertEqual(result["task_id"], "task-77")
        ct.assert_called_once()
        # deal got stamped with the task id
        self.assertTrue(any(ft.TASK_ID_PROP in (p or {}).get("properties", {})
                            for _u, p in mocks.patches))
        # task name + description carry the channel and links
        _args, kwargs = ct.call_args
        self.assertIn("Maple Court", _args[1])
        self.assertIn("Paid Search", kwargs["description"])
        self.assertIn("deal-1", kwargs["description"])

    # ── dedup ────────────────────────────────────────────────────────────

    def test_already_stamped_deal_skips(self):
        mocks = DealMocks(stamp="task-existing")
        result, ct = self._run(mocks)
        self.assertEqual(result.get("skipped"), "task already exists")
        ct.assert_not_called()

    def test_in_process_claim_blocks_double(self):
        mocks = DealMocks(stamp="")
        self._run(mocks)                      # first claims + creates
        result, ct = self._run(mocks)         # second, within TTL
        self.assertEqual(result.get("skipped"), "claimed in-process")
        ct.assert_not_called()

    # ── status fallback + failure ────────────────────────────────────────

    def test_status_slug_fallback_retries(self):
        os.environ["CLICKUP_FULFILLMENT_STATUS"] = "bogus"
        mocks = DealMocks(stamp="")
        # first create returns None (bad status), retry returns a task
        with mock.patch.object(ft.requests, "get", side_effect=mocks.get), \
             mock.patch.object(ft.requests, "patch", side_effect=mocks.patch), \
             mock.patch.object(clickup_client, "create_task",
                               side_effect=[None, {"id": "task-9", "url": "u"}]) as ct:
            result = ft.create_for_deal("deal-1", "co-1")
        self.assertEqual(result["task_id"], "task-9")
        self.assertEqual(ct.call_count, 2)

    def test_create_failure_releases_claim(self):
        mocks = DealMocks(stamp="")
        result, _ct = self._run(mocks, create_result=None)
        self.assertEqual(result, {"error": "task create failed"})
        self.assertNotIn("deal-1", ft._recent)  # claim released for retry


if __name__ == "__main__":
    unittest.main()
