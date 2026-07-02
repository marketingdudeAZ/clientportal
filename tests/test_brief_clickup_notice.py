"""Tests for Bridge 3 — brief change → ClickUp notice for fulfillment.

Covers:
  - gating: disabled by default / no token → skip
  - comments on an existing stamped task (creative_transition_task_id)
  - baseline sentinel stamp is ignored (not a real task)
  - creates a task on CLICKUP_LIST_BRIEF_UPDATES when no stamped task
  - no target → skip
  - the leg is registered in brief_hooks

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

import clickup_client  # noqa: E402
import brief_change_notifier as bcn  # noqa: E402
import brief_hooks  # noqa: E402


def _resp(props):
    m = mock.Mock()
    m.status_code = 200
    m.json.return_value = {"properties": props}
    m.text = ""
    return m


class BriefClickupNoticeTests(unittest.TestCase):

    def setUp(self):
        os.environ["BRIEF_CLICKUP_NOTICE"] = "true"
        os.environ.pop("CLICKUP_LIST_BRIEF_UPDATES", None)

    def test_disabled_skips(self):
        os.environ["BRIEF_CLICKUP_NOTICE"] = "false"
        self.assertEqual(bcn.notify("co-1")["skipped"], "disabled")

    def test_comments_on_stamped_task(self):
        props = {"name": "Maple Court", "creative_transition_task_id": "task-55"}
        with mock.patch.object(bcn.requests, "get", return_value=_resp(props)), \
             mock.patch.object(clickup_client, "post_comment", return_value=True) as pc:
            result = bcn.notify("co-1", field_label="Voice Tier",
                                old_value="Warm", new_value="Bold", edited_by="k@rpm.com")
        self.assertTrue(result["commented"])
        self.assertEqual(result["task_id"], "task-55")
        text = pc.call_args[0][1]
        self.assertIn("Voice Tier", text)
        self.assertIn("Bold", text)
        self.assertIn("k@rpm.com", text)

    def test_baseline_sentinel_ignored_falls_through(self):
        props = {"name": "Maple Court", "creative_transition_task_id": "baseline-pre-2026-06-12"}
        with mock.patch.object(bcn.requests, "get", return_value=_resp(props)), \
             mock.patch.object(clickup_client, "post_comment") as pc:
            result = bcn.notify("co-1", field_label="X")
        pc.assert_not_called()
        self.assertIn("skipped", result)  # no list configured → no target

    def test_creates_task_when_list_set_and_no_stamp(self):
        os.environ["CLICKUP_LIST_BRIEF_UPDATES"] = "list-9"
        props = {"name": "Maple Court", "creative_transition_task_id": ""}
        with mock.patch.object(bcn.requests, "get", return_value=_resp(props)), \
             mock.patch.object(clickup_client, "create_task",
                               return_value={"id": "task-new"}) as ct:
            result = bcn.notify("co-1", field_label="Amenities", new_value="Pool")
        self.assertTrue(result["created"])
        self.assertEqual(result["task_id"], "task-new")
        ct.assert_called_once()

    def test_no_token_skips(self):
        with mock.patch.object(clickup_client, "CLICKUP_API_KEY", ""):
            self.assertIn("skipped", bcn.notify("co-1"))

    def test_leg_registered_in_hooks(self):
        self.assertIn(brief_hooks._leg_clickup_notice, brief_hooks._LEGS)


if __name__ == "__main__":
    unittest.main()
