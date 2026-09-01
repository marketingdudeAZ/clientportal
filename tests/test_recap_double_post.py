"""A re-fired webhook must not post the client-visible note twice.

ClickUp re-fires taskStatusUpdated for the same task — three fires on one
ticket is normal in the observed production data — and the receiver runs each
fire in its own background thread. The `recap-posted` tag is the idempotency
key, but it is only written AFTER the note is posted, and everything expensive
(model call, profile proposals, PDF) sits in between. Two threads could both
pass the check at the top and both post.

That was tolerable when it was one duplicate note an AM notices. It is not
tolerable while a backfill replays a thousand tickets alongside a live feed.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

import clickup_recap  # noqa: E402


def _task(tags):
    return {
        "id": "abc123",
        "name": "Olmsted Savannah",
        "status": {"status": "complete", "type": "closed"},
        "tags": [{"name": t} for t in tags],
        "date_done": "1788217260537",
        "custom_fields": [],
    }


COMPANY = {"id": "555", "properties": {"name": "Olmsted Savannah", "uuid": "u-1"}}


class TheTagIsRecheckedBeforePosting(unittest.TestCase):
    def setUp(self):
        self.p = []
        for target, kw in (
            ("clickup_recap.ticket_recap.infer_ticket_type", {"return_value": "general"}),
            ("clickup_recap.match_company_for_ticket", {"return_value": (COMPANY, "url:domain")}),
            ("clickup_recap.clickup_client.get_comments", {"return_value": []}),
            ("clickup_recap.ticket_recap.generate_recap",
             {"return_value": {"note": "a real recap", "needs_review": False}}),
            ("clickup_recap.clickup_client.add_tag", {"return_value": True}),
            ("clickup_recap._emit_matched", {}),
        ):
            patcher = mock.patch(target, **kw)
            patcher.start()
            self.p.append(patcher)

    def tearDown(self):
        for patcher in self.p:
            patcher.stop()

    def test_it_does_not_post_when_another_run_tagged_it_mid_flight(self):
        """First read is untagged; by the time we would post, the tag is there."""
        with mock.patch("clickup_recap.clickup_client.get_task",
                        side_effect=[_task([]), _task(["recap-posted"])]), \
                mock.patch("clickup_recap.ticket_recap_writer.post_recap_to_company") as post:
            res = clickup_recap.process_completed_task("abc123")
        post.assert_not_called()
        self.assertIn("raced", res.get("skipped", ""))

    def test_it_still_posts_the_normal_single_run(self):
        """The guard must not break the ordinary path."""
        with mock.patch("clickup_recap.clickup_client.get_task",
                        side_effect=[_task([]), _task([])]), \
                mock.patch("clickup_recap.ticket_recap_writer.post_recap_to_company",
                           return_value={"note_id": "n1"}) as post:
            res = clickup_recap.process_completed_task("abc123")
        post.assert_called_once()
        self.assertEqual(res.get("posted", {}).get("note_id"), "n1")

    def test_the_first_check_still_short_circuits_an_already_tagged_task(self):
        with mock.patch("clickup_recap.clickup_client.get_task",
                        return_value=_task(["recap-posted"])), \
                mock.patch("clickup_recap.ticket_recap_writer.post_recap_to_company") as post:
            res = clickup_recap.process_completed_task("abc123")
        post.assert_not_called()
        self.assertEqual(res.get("skipped"), "already processed")


if __name__ == "__main__":
    unittest.main()
