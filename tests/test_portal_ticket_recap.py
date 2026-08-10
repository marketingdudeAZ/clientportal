"""The recap must match portal-created tickets on the mapping table.

The whole value proposition of filing in the portal is the recap note that
lands on the company record when the work completes. Before this, the recap
re-derived the company from the ticket's website domain — so any property with
a stale or missing `website` produced no recap at all, silently, for a
requester who had just been told the portal was the better door.

External I/O (ClickUp, HubSpot, BigQuery) is mocked throughout.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("HUBSPOT_API_KEY", "test-key")
os.environ.setdefault("CLICKUP_API_KEY", "test-key")

import clickup_recap  # noqa: E402
import portal_tickets  # noqa: E402


def _task(task_id="task-1", tags=("portal",)):
    return {
        "id": task_id,
        "name": "Update the pool photos",
        "tags": [{"name": t} for t in tags],
        "status": {"status": "complete", "type": "closed"},
        "custom_fields": [],
    }


class MatchViaPortalMapping(unittest.TestCase):
    def test_mapping_row_wins_and_never_hits_the_domain_search(self):
        with mock.patch.object(portal_tickets, "mapping_for_task",
                               return_value={"company_id": "cid-42",
                                             "property_uuid": "u-1"}), \
                mock.patch("hubspot_client.get_company",
                           return_value={"name": "Maple", "uuid": "u-1"}), \
                mock.patch.object(clickup_recap, "_search") as search:
            company, method = clickup_recap.match_company_for_ticket(_task())

        self.assertEqual(method, "portal_mapping")
        self.assertEqual(company["id"], "cid-42")
        self.assertEqual(company["properties"]["uuid"], "u-1")
        # The exact lookup must short-circuit the guessing entirely.
        search.assert_not_called()

    def test_property_with_no_website_still_matches(self):
        """The exact failure that produced no recap: nothing to derive from."""
        with mock.patch.object(portal_tickets, "mapping_for_task",
                               return_value={"company_id": "cid-7"}), \
                mock.patch("hubspot_client.get_company",
                           return_value={"name": "No Website LLC", "uuid": "u-7"}):
            company, method = clickup_recap.match_company_for_ticket(_task())
        self.assertEqual(method, "portal_mapping")
        self.assertEqual(company["id"], "cid-7")

    def test_non_portal_task_falls_through_to_the_existing_search(self):
        with mock.patch.object(portal_tickets, "mapping_for_task", return_value=None), \
                mock.patch.object(clickup_recap, "_search",
                                  return_value=[{"id": "cid-9",
                                                 "properties": {"uuid": "u-9"}}]):
            company, method = clickup_recap.match_company_for_ticket(
                _task(tags=()) | {"name": "Some Property"})
        self.assertEqual(company["id"], "cid-9")
        self.assertNotEqual(method, "portal_mapping")

    def test_a_broken_mapping_lookup_degrades_instead_of_raising(self):
        with mock.patch.object(portal_tickets, "mapping_for_task",
                               side_effect=RuntimeError("BigQuery unavailable")), \
                mock.patch.object(clickup_recap, "_search", return_value=[]):
            company, method = clickup_recap.match_company_for_ticket(_task())
        self.assertIsNone(company)
        self.assertEqual(method, "no confident uuid match")


class UnmatchedPortalTaskIsEscalated(unittest.TestCase):
    """A portal-created task that can't be matched is a defect, not a miss —
    we wrote the mapping row ourselves. The usual cause is that the mapping
    write silently no-op'd because BigQuery isn't configured, which is
    invisible from every other surface."""

    def _run(self, tags):
        with mock.patch.object(clickup_recap.clickup_client, "get_task",
                               return_value=_task(tags=tags)), \
                mock.patch.object(clickup_recap.ticket_recap, "infer_ticket_type",
                                  return_value="creative"), \
                mock.patch.object(clickup_recap, "match_company_for_ticket",
                                  return_value=(None, "no confident uuid match")), \
                mock.patch.object(clickup_recap, "_emit_unmatched") as emit:
            result = clickup_recap.process_completed_task("task-1")
        return result, emit

    def test_portal_created_miss_is_flagged_and_recorded(self):
        with self.assertLogs("clickup_recap", level="ERROR") as logs:
            result, emit = self._run(tags=("portal",))
        self.assertTrue(result["portal_created"])
        self.assertIn("PORTAL-created", "\n".join(logs.output))
        emit.assert_called_once_with("task-1", "no confident uuid match", True)

    def test_externally_filed_miss_stays_an_ordinary_skip(self):
        result, emit = self._run(tags=())
        self.assertFalse(result["portal_created"])
        emit.assert_called_once_with("task-1", "no confident uuid match", False)


class MatchEmitsFunnelEvent(unittest.TestCase):
    def test_successful_match_records_the_method(self):
        import loop_ticket_events
        with mock.patch.object(loop_ticket_events, "record_recap_matched") as ev:
            clickup_recap._emit_matched(
                "task-1", {"id": "cid-42", "properties": {"uuid": "u-1"}}, "portal_mapping")
        ev.assert_called_once()
        self.assertEqual(ev.call_args.kwargs["method"], "portal_mapping")
        self.assertEqual(ev.call_args[0][0], "u-1")

    def test_event_failure_never_breaks_the_recap(self):
        import loop_ticket_events
        with mock.patch.object(loop_ticket_events, "record_recap_matched",
                               side_effect=RuntimeError("BQ down")):
            clickup_recap._emit_matched("task-1", {"id": "c", "properties": {}}, "x")


if __name__ == "__main__":
    unittest.main()
