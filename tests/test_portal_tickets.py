"""Tests for the portal ticket page (per-type ClickUp forms).

Covers the pure logic (status mapping, live form-schema shaping, prefill
filtering, drop-down/currency coercion), the create flow (custom-field payload
+ mapping record), and the list-id discovery-by-name helper.

All external I/O — ClickUp, HubSpot, BigQuery — is mocked. BigQuery is left
unconfigured so the mapping store no-ops (its graceful-degradation path).
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

# webhook-server/ must win for `import config` — it holds the app config
# (and the PORTAL_TICKET_* symbols). Root is appended last as a fallback.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("HUBSPOT_API_KEY", "test-key")
os.environ.setdefault("CLICKUP_API_KEY", "test-key")
# Configure a couple of ticket-type list ids so they appear in the picker.
os.environ["CLICKUP_LIST_GENERAL"] = "901-general"
os.environ["CLICKUP_LIST_CAMPAIGN_REVIEW"] = "901-review"
os.environ["CLICKUP_LIST_DISPO_CANCEL"] = "901-dispo"  # internal audience

import clickup_client  # noqa: E402
import portal_tickets  # noqa: E402


def _fields():
    """A representative ClickUp list field definition set."""
    return [
        {"id": "f_url", "name": "Property URL", "type": "url", "required": False},
        {"id": "f_uuid", "name": "uuid", "type": "short_text", "required": False},
        {"id": "f_pri", "name": "Priority", "type": "drop_down", "required": True,
         "type_config": {"options": [
             {"id": "opt-low", "name": "Low", "orderindex": 0},
             {"id": "opt-high", "name": "High", "orderindex": 1},
         ]}},
        {"id": "f_budget", "name": "New Budget", "type": "currency", "required": False,
         "type_config": {}},
        {"id": "f_details", "name": "Details", "type": "text", "required": False},
    ]


class StatusMapping(unittest.TestCase):
    def test_known_statuses_map_to_client_labels(self):
        self.assertEqual(portal_tickets.client_status("pending pm approval"), "In progress")
        self.assertEqual(portal_tickets.client_status("TO DO"), "Open")
        self.assertEqual(portal_tickets.client_status("complete"), "Done")

    def test_unknown_status_falls_through_titlecased(self):
        # Never leak a raw internal slug — title-case it instead.
        self.assertEqual(portal_tickets.client_status("waiting_on_vendor".replace("_", " ")),
                         "Waiting On Vendor")

    def test_blank_status_defaults_open(self):
        self.assertEqual(portal_tickets.client_status(""), "Open")


class FormSchema(unittest.TestCase):
    def test_prefill_fields_are_filtered_out(self):
        with mock.patch.object(clickup_client, "get_list_fields", return_value=_fields()):
            schema = portal_tickets.form_schema("901-general")
        names = [f["name"] for f in schema]
        self.assertNotIn("Property URL", names)   # prefilled → hidden
        self.assertNotIn("uuid", names)           # prefilled → hidden
        self.assertIn("Priority", names)
        self.assertIn("Details", names)

    def test_dropdown_options_and_input_kind_shaped(self):
        with mock.patch.object(clickup_client, "get_list_fields", return_value=_fields()):
            schema = portal_tickets.form_schema("901-general")
        pri = next(f for f in schema if f["name"] == "Priority")
        self.assertEqual(pri["input"], "select")
        self.assertEqual([o["label"] for o in pri["options"]], ["Low", "High"])
        details = next(f for f in schema if f["name"] == "Details")
        self.assertEqual(details["input"], "textarea")


class ConfiguredTypes(unittest.TestCase):
    def test_only_configured_client_types_show(self):
        types = portal_tickets.configured_types(include_internal=False)
        keys = {t["key"] for t in types}
        self.assertIn("general", keys)
        self.assertIn("campaign_review", keys)
        self.assertNotIn("dispo_cancel", keys)     # internal audience, hidden
        self.assertNotIn("rebrand", keys)          # no list id configured

    def test_internal_types_show_with_flag(self):
        types = portal_tickets.configured_types(include_internal=True)
        self.assertIn("dispo_cancel", {t["key"] for t in types})


class CoerceAndCreate(unittest.TestCase):
    def setUp(self):
        self.created = {}

        def _fake_create_task(list_id, name, **kw):
            self.created = {"list_id": list_id, "name": name, **kw}
            return {"id": "task-1", "name": name, "url": "https://app.clickup.com/t/task-1",
                    "status": {"status": "to do"}, "date_created": "1720000000000"}

        self.patchers = [
            # Patch the module attribute directly so the test doesn't depend on
            # whether some earlier-imported test set CLICKUP_API_KEY before
            # config froze it (the full-suite import-order gotcha).
            mock.patch.object(clickup_client, "CLICKUP_API_KEY", "test-key"),
            mock.patch.object(clickup_client, "get_list_fields", return_value=_fields()),
            mock.patch.object(clickup_client, "create_task", side_effect=_fake_create_task),
            mock.patch("hubspot_client.get_company",
                       return_value={"website": "https://maple.example.com", "uuid": "u-1", "name": "Maple"}),
        ]
        for p in self.patchers:
            p.start()

    def tearDown(self):
        for p in self.patchers:
            p.stop()

    def test_create_builds_custom_fields_and_records_mapping(self):
        with mock.patch.object(portal_tickets, "_record_mapping") as rec:
            body, status = portal_tickets.create_ticket(
                "cid-42", "general",
                subject="Update our hours",
                fields={"f_pri": "High", "f_budget": "500", "f_details": "Please update"},
                submitted_by="user@rpmliving.com",
                property_uuid="u-1",
            )
        self.assertEqual(status, 201)
        self.assertTrue(body["ok"])
        self.assertEqual(self.created["list_id"], "901-general")

        cf = {c["id"]: c["value"] for c in self.created["custom_fields"]}
        # drop-down label resolved to option id
        self.assertEqual(cf["f_pri"], "opt-high")
        # currency coerced to float
        self.assertEqual(cf["f_budget"], 500.0)
        # prefill landed on the property fields the requester never saw
        self.assertEqual(cf["f_url"], "https://maple.example.com")
        self.assertEqual(cf["f_uuid"], "u-1")
        # mapping recorded exactly once with the new task id
        rec.assert_called_once()
        self.assertEqual(rec.call_args[0][0], "task-1")

    def test_shaped_ticket_has_client_status(self):
        with mock.patch.object(portal_tickets, "_record_mapping"):
            body, _ = portal_tickets.create_ticket(
                "cid-42", "general", subject="x", fields={}, property_uuid="u-1")
        self.assertEqual(body["ticket"]["status"], "Open")   # "to do" → Open
        self.assertEqual(body["ticket"]["type_label"], "General Ticket")


class CreateGuards(unittest.TestCase):
    def test_unknown_type_is_400(self):
        body, status = portal_tickets.create_ticket("cid", "nope", subject="x", fields={})
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])

    def test_unconfigured_type_is_503(self):
        # 'rebrand' has no list id set in this test env.
        body, status = portal_tickets.create_ticket("cid", "rebrand", subject="x", fields={})
        self.assertEqual(status, 503)


class Discovery(unittest.TestCase):
    def test_matches_lists_to_types_by_name_and_alias(self):
        lists = [
            {"id": "L1", "name": "Digital Marketing Review", "space": "S", "folder": None},
            {"id": "L2", "name": "General Ticket", "space": "S", "folder": None},
            {"id": "L3", "name": "Some Unrelated List", "space": "S", "folder": None},
        ]
        with mock.patch.object(clickup_client, "discover_workspace_lists", return_value=lists):
            out = portal_tickets.discover_list_ids()
        matched = {m["key"]: m["list_id"] for m in out["matched"]}
        # exact label match
        self.assertEqual(matched["general"], "L2")
        # alias: registry label is "Digital Marketing Review" (campaign_review)
        self.assertEqual(matched["campaign_review"], "L1")
        self.assertIn("CLICKUP_LIST_GENERAL=L2", out["env_block"])


if __name__ == "__main__":
    unittest.main()
