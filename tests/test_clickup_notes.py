"""ClickUp work → HubSpot company notes loop.

External I/O (ClickUp API, HubSpot company search, note write) is mocked.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

import clickup_notes as cn


def _task(name="SEO — March deliverable", status="in progress",
          property_field=("Property Name", "10X Tarpon Springs"),
          assignees=("Amy",), list_name="SEO Queue"):
    custom_fields = []
    if property_field:
        custom_fields.append({"name": property_field[0], "value": property_field[1]})
    return {
        "id": "task-1",
        "name": name,
        "status": {"status": status},
        "assignees": [{"username": a} for a in assignees],
        "list": {"name": list_name},
        "url": "https://app.clickup.com/t/task-1",
        "custom_fields": custom_fields,
    }


class TestResolveCompany(unittest.TestCase):
    def setUp(self):
        cn.reset_dedup()

    def test_single_match_resolves(self):
        with mock.patch("property_brief._search_companies_by_name",
                        return_value=[{"id": "C-99"}]):
            self.assertEqual(cn.resolve_company_id(_task()), "C-99")

    def test_no_match_returns_none(self):
        with mock.patch("property_brief._search_companies_by_name", return_value=[]):
            self.assertIsNone(cn.resolve_company_id(_task()))

    def test_ambiguous_match_returns_none(self):
        with mock.patch("property_brief._search_companies_by_name",
                        return_value=[{"id": "C-1"}, {"id": "C-2"}]):
            self.assertIsNone(cn.resolve_company_id(_task()))

    def test_falls_back_to_task_name_when_no_property_field(self):
        with mock.patch("property_brief._search_companies_by_name",
                        return_value=[{"id": "C-7"}]) as m:
            cn.resolve_company_id(_task(name="Sur Club", property_field=None))
            m.assert_called_once_with("Sur Club")


class TestFormatNote(unittest.TestCase):
    def test_includes_key_fields(self):
        body = cn.format_note(_task(), "complete")
        self.assertIn("Digital work update", body)
        self.assertIn("SEO — March deliverable", body)
        self.assertIn("complete", body)
        self.assertIn("SEO Queue", body)
        self.assertIn("Amy", body)
        self.assertIn("clickup.com/t/task-1", body)


class TestHandleEvent(unittest.TestCase):
    def setUp(self):
        cn.reset_dedup()

    def test_non_status_event_ignored(self):
        r = cn.handle_event({"event": "taskUpdated", "task_id": "task-1"})
        self.assertEqual(r["status"], "ignored")

    def test_missing_task_id(self):
        r = cn.handle_event({"event": "taskStatusUpdated"})
        self.assertEqual(r["status"], "error")

    def test_happy_path_posts_note(self):
        with mock.patch("clickup_client.get_task", return_value=_task(status="complete")), \
             mock.patch("property_brief._search_companies_by_name", return_value=[{"id": "C-5"}]), \
             mock.patch("clickup_notes.add_company_note", return_value="note-1") as note:
            r = cn.handle_event({"event": "taskStatusUpdated", "task_id": "task-1"})
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["company_id"], "C-5")
        self.assertEqual(r["note_id"], "note-1")
        note.assert_called_once()

    def test_dedup_same_status_posts_once(self):
        with mock.patch("clickup_client.get_task", return_value=_task(status="complete")), \
             mock.patch("property_brief._search_companies_by_name", return_value=[{"id": "C-5"}]), \
             mock.patch("clickup_notes.add_company_note", return_value="note-1") as note:
            cn.handle_event({"event": "taskStatusUpdated", "task_id": "task-1"})
            r2 = cn.handle_event({"event": "taskStatusUpdated", "task_id": "task-1"})
        self.assertEqual(r2["status"], "skipped")
        self.assertEqual(note.call_count, 1)

    def test_no_company_match_skips_without_note(self):
        with mock.patch("clickup_client.get_task", return_value=_task()), \
             mock.patch("property_brief._search_companies_by_name", return_value=[]), \
             mock.patch("clickup_notes.add_company_note") as note:
            r = cn.handle_event({"event": "taskStatusUpdated", "task_id": "task-1"})
        self.assertEqual(r["status"], "skipped")
        note.assert_not_called()


class TestWebhookRoute(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("HUBSPOT_API_KEY", "test")
        os.environ.setdefault("WEBHOOK_SECRET", "test")
        import server
        cls.client = server.app.test_client()

    def setUp(self):
        cn.reset_dedup()

    def test_route_processes_event(self):
        with mock.patch("clickup_client.get_task", return_value=_task(status="complete")), \
             mock.patch("property_brief._search_companies_by_name", return_value=[{"id": "C-5"}]), \
             mock.patch("clickup_notes.add_company_note", return_value="note-9"):
            resp = self.client.post("/webhooks/clickup/task-activity",
                                    json={"event": "taskStatusUpdated", "task_id": "task-1"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["status"], "ok")


if __name__ == "__main__":
    unittest.main()
