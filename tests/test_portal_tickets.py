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
        self.assertEqual(portal_tickets.client_status("TO DO"), "Open")
        self.assertEqual(portal_tickets.client_status("complete"), "Done")
        self.assertEqual(portal_tickets.client_status("in progress"), "In progress")

    def test_awaiting_the_requester_is_not_shown_as_in_progress(self):
        """The one status where the requester can act. Showing it as
        'In progress' makes them wait on us while we wait on them."""
        self.assertEqual(portal_tickets.client_status("pending pm approval"),
                         "Needs your approval")
        self.assertEqual(portal_tickets.client_status("Pending PM Approval"),
                         "Needs your approval")

    def test_unknown_status_falls_through_titlecased(self):
        # Never leak a raw internal slug — title-case it instead.
        self.assertEqual(portal_tickets.client_status("waiting_on_vendor".replace("_", " ")),
                         "Waiting On Vendor")

    def test_blank_status_defaults_open(self):
        self.assertEqual(portal_tickets.client_status(""), "Open")


class FormSchema(unittest.TestCase):
    def test_prefill_fields_are_filtered_out_when_we_can_fill_them(self):
        prefill = {"Property URL": "https://x.com", "uuid": "u-1"}
        with mock.patch.object(clickup_client, "get_list_fields", return_value=_fields()):
            schema = portal_tickets.form_schema("901-general", prefill)
        names = [f["name"] for f in schema]
        self.assertNotIn("Property URL", names)   # resolved → hidden
        self.assertNotIn("uuid", names)           # resolved → hidden
        self.assertIn("Priority", names)
        self.assertIn("Details", names)

    def test_an_unresolvable_prefill_field_is_RENDERED_not_hidden(self):
        """The Account Manager class of bug: the mapping pointed at a HubSpot
        property that does not exist, prefill swallowed the failure, and the
        field ended up neither on the form nor on the task — permanently blank
        on every ticket, silently. Strictly worse than the ClickUp form."""
        prefill = {"uuid": "u-1"}                  # Property URL did NOT resolve
        with mock.patch.object(clickup_client, "get_list_fields", return_value=_fields()):
            schema = portal_tickets.form_schema("901-general", prefill)
        names = [f["name"] for f in schema]
        self.assertIn("Property URL", names)
        self.assertNotIn("uuid", names)

    def test_no_prefill_map_hides_nothing(self):
        """Safe direction: show a field we would have filled, rather than hide
        one nobody fills."""
        with mock.patch.object(clickup_client, "get_list_fields", return_value=_fields()):
            schema = portal_tickets.form_schema("901-general")
        self.assertIn("Property URL", [f["name"] for f in schema])

    def test_form_schema_returns_none_when_clickup_will_not_say(self):
        with mock.patch.object(clickup_client, "get_list_fields", return_value=None):
            self.assertIsNone(portal_tickets.form_schema("901-general"))

    def test_a_genuinely_fieldless_list_is_empty_not_none(self):
        with mock.patch.object(clickup_client, "get_list_fields", return_value=[]):
            self.assertEqual(portal_tickets.form_schema("901-general"), [])

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

    def test_mapped_values_are_not_duplicated_into_the_description(self):
        """Regression: `extra` is keyed by field IDs and the applied set used to
        be keyed by field NAMES, so the two key spaces never intersected and
        every value the requester typed was echoed into the description behind
        a raw ClickUp field uuid."""
        with mock.patch.object(portal_tickets, "_record_mapping"):
            portal_tickets.create_ticket(
                "cid-42", "general",
                subject="Update our hours",
                fields={"f_pri": "High", "f_details": "Please update the pool photos"},
                submitted_by="user@rpmliving.com",
                property_uuid="u-1",
            )
        desc = self.created["description"]
        self.assertNotIn("f_pri", desc)
        self.assertNotIn("f_details", desc)
        self.assertNotIn("Please update the pool photos", desc)
        # The provenance stamp the recap matches on must survive.
        self.assertIn("hubspot_company=cid-42", desc)
        self.assertIn("uuid=u-1", desc)
        self.assertIn("user@rpmliving.com", desc)

    def test_unmapped_freetext_still_surfaces_under_its_field_label(self):
        with mock.patch.object(portal_tickets, "_record_mapping"):
            portal_tickets.create_ticket(
                "cid-42", "general", subject="x",
                fields={"not_a_clickup_field": "ring the doorbell twice"},
                property_uuid="u-1",
            )
        self.assertIn("ring the doorbell twice", self.created["description"])

    def test_filing_emits_a_loop_event(self):
        import loop_ticket_events
        with mock.patch.object(portal_tickets, "_record_mapping"), \
                mock.patch.object(loop_ticket_events, "record_ticket_filed") as ev:
            portal_tickets.create_ticket(
                "cid-42", "general", subject="x", fields={},
                submitted_by="user@rpmliving.com", property_uuid="u-1")
        ev.assert_called_once()
        self.assertEqual(ev.call_args.kwargs["task_id"], "task-1")
        self.assertEqual(ev.call_args.kwargs["ticket_type"], "general")

    def test_a_failing_loop_event_never_fails_the_ticket(self):
        import loop_ticket_events
        with mock.patch.object(portal_tickets, "_record_mapping"), \
                mock.patch.object(loop_ticket_events, "record_ticket_filed",
                                  side_effect=RuntimeError("BQ down")):
            body, status = portal_tickets.create_ticket(
                "cid-42", "general", subject="x", fields={}, property_uuid="u-1")
        self.assertEqual(status, 201)
        self.assertTrue(body["ok"])


class InternalAudienceGuard(unittest.TestCase):
    """A portal user must not be able to file into an internal list just by
    knowing its key — `dispo_cancel` governs whether a property gets cancelled."""

    def test_client_caller_cannot_file_an_internal_type(self):
        body, status = portal_tickets.create_ticket(
            "cid-42", "dispo_cancel", subject="cancel us", fields={}, internal=False)
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])

    def test_response_is_indistinguishable_from_an_unknown_type(self):
        # Otherwise the error message itself enumerates the internal types.
        internal_body, internal_status = portal_tickets.create_ticket(
            "cid-42", "dispo_cancel", subject="x", fields={}, internal=False)
        unknown_body, unknown_status = portal_tickets.create_ticket(
            "cid-42", "no_such_type", subject="x", fields={}, internal=False)
        self.assertEqual(internal_status, unknown_status)
        self.assertEqual(internal_body, unknown_body)

    def test_default_is_closed(self):
        # Callers that forget the flag get the safe behaviour.
        _, status = portal_tickets.create_ticket(
            "cid-42", "dispo_cancel", subject="x", fields={})
        self.assertEqual(status, 400)

    def test_internal_caller_may_file_an_internal_type(self):
        def _fake_create_task(list_id, name, **kw):
            return {"id": "task-9", "name": name, "url": "u",
                    "status": {"status": "to do"}, "date_created": "1720000000000"}

        with mock.patch.object(clickup_client, "CLICKUP_API_KEY", "test-key"), \
                mock.patch.object(clickup_client, "get_list_fields", return_value=_fields()), \
                mock.patch.object(clickup_client, "create_task", side_effect=_fake_create_task), \
                mock.patch("hubspot_client.get_company", return_value={}), \
                mock.patch.object(portal_tickets, "_record_mapping"), \
                mock.patch.object(portal_tickets, "_emit_filed"):
            body, status = portal_tickets.create_ticket(
                "cid-42", "dispo_cancel", subject="x", fields={}, internal=True)
        self.assertEqual(status, 201)
        self.assertTrue(body["ok"])


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

    def test_internal_list_ids_are_split_out_of_the_paste_block(self):
        """The env block is pasted into Render as one step. Emitting the
        internal lists alongside the client ones is what turns that paste into
        a live authorization hole."""
        lists = [
            {"id": "L2", "name": "General Ticket", "space": "S", "folder": None},
            {"id": "L9", "name": "Dispo / Cancel", "space": "S", "folder": None},
            {"id": "L8", "name": "New Business", "space": "S", "folder": None},
        ]
        with mock.patch.object(clickup_client, "discover_workspace_lists", return_value=lists):
            out = portal_tickets.discover_list_ids()
        self.assertIn("CLICKUP_LIST_GENERAL=L2", out["env_block"])
        self.assertNotIn("DISPO", out["env_block"])
        self.assertNotIn("NEW_BUSINESS", out["env_block"])
        self.assertIn("CLICKUP_LIST_DISPO_CANCEL=L9", out["env_block_internal"])
        self.assertIn("CLICKUP_LIST_NEW_BUSINESS=L8", out["env_block_internal"])


if __name__ == "__main__":
    unittest.main()


class ListTickets(unittest.TestCase):
    """The status surface. Previously untested, and its failure mode —
    silently rendering a partial list as complete — is the worst one available
    here: a requester concludes their request was never filed."""

    def _refs(self, n=3):
        return [{"task_id": f"t{i}", "ticket_type": "general",
                 "submitted_by": "cm@rpmliving.com",
                 "created_at": f"2026-08-0{i+1}T12:00:00+00:00"} for i in range(n)]

    def _task(self, tid):
        return {"id": tid, "name": f"Subject {tid}", "status": {"status": "to do"},
                "date_created": "1754049600000", "url": f"https://clickup.com/t/{tid}"}

    def test_every_mapping_row_produces_a_row(self):
        with mock.patch.object(portal_tickets, "_read_mappings", return_value=self._refs(3)), \
             mock.patch.object(clickup_client, "get_task",
                               side_effect=lambda t: self._task(t) if t != "t1" else None):
            out = portal_tickets.list_tickets("c1")
        self.assertEqual(len(out), 3)
        self.assertEqual(sum(1 for t in out if t.get("unresolved")), 1)

    def test_a_resolvable_task_is_shaped_normally(self):
        with mock.patch.object(portal_tickets, "_read_mappings", return_value=self._refs(1)), \
             mock.patch.object(clickup_client, "get_task", side_effect=self._task):
            out = portal_tickets.list_tickets("c1")
        self.assertFalse(out[0]["unresolved"])
        self.assertEqual(out[0]["status"], "Open")
        self.assertTrue(out[0]["url"])

    def test_placeholder_carries_the_stored_filing_date_and_no_url(self):
        """We wrote the mapping row, so we know when it was filed even when
        ClickUp won't tell us its state."""
        with mock.patch.object(portal_tickets, "_read_mappings", return_value=self._refs(1)), \
             mock.patch.object(clickup_client, "get_task", return_value=None):
            out = portal_tickets.list_tickets("c1")
        self.assertTrue(out[0]["unresolved"])
        self.assertIsNotNone(out[0]["created_ts"])
        self.assertIsNone(out[0]["url"])
        self.assertEqual(out[0]["status"], "Status unavailable")

    def test_duplicate_mapping_rows_render_once(self):
        refs = self._refs(1) * 2
        with mock.patch.object(portal_tickets, "_read_mappings", return_value=refs), \
             mock.patch.object(clickup_client, "get_task", side_effect=self._task):
            out = portal_tickets.list_tickets("c1")
        self.assertEqual(len(out), 1)

    def test_tasks_are_fetched_concurrently_not_serially(self):
        import time as _t
        def slow(tid):
            _t.sleep(0.2)
            return self._task(tid)
        with mock.patch.object(portal_tickets, "_read_mappings", return_value=self._refs(8)), \
             mock.patch.object(clickup_client, "get_task", side_effect=slow):
            start = _t.monotonic()
            out = portal_tickets.list_tickets("c1")
            elapsed = _t.monotonic() - start
        self.assertEqual(len(out), 8)
        self.assertLess(elapsed, 1.2, "8 × 0.2s ran serially — the fan-out is not working")

    def test_the_wall_clock_budget_bounds_the_call(self):
        """The regression test for the 500-second page load."""
        import time as _t
        with mock.patch.object(portal_tickets, "_read_mappings", return_value=self._refs(4)), \
             mock.patch.object(portal_tickets, "_FETCH_BUDGET", 0.2), \
             mock.patch.object(clickup_client, "get_task",
                               side_effect=lambda t: (_t.sleep(3), self._task(t))[1]):
            start = _t.monotonic()
            out = portal_tickets.list_tickets("c1")
            elapsed = _t.monotonic() - start
        self.assertLess(elapsed, 1.5)
        self.assertTrue(all(t["unresolved"] for t in out))

    def test_a_raising_get_task_becomes_a_placeholder_not_a_500(self):
        def boom(tid):
            raise RuntimeError("clickup exploded")
        with mock.patch.object(portal_tickets, "_read_mappings", return_value=self._refs(2)), \
             mock.patch.object(clickup_client, "get_task", side_effect=boom):
            out = portal_tickets.list_tickets("c1")
        self.assertEqual(len(out), 2)
        self.assertTrue(all(t["unresolved"] for t in out))

    def test_no_mappings_never_touches_clickup(self):
        with mock.patch.object(portal_tickets, "_read_mappings", return_value=[]), \
             mock.patch.object(clickup_client, "get_task") as g:
            self.assertEqual(portal_tickets.list_tickets("c1"), [])
        g.assert_not_called()


class RegistryTypes(unittest.TestCase):
    def test_every_client_type_appears_even_unconfigured(self):
        types = portal_tickets.types_with_schema()
        keys = {t["key"] for t in types}
        self.assertIn("rebrand", keys)       # no CLICKUP_LIST_* set for it
        rebrand = next(t for t in types if t["key"] == "rebrand")
        self.assertFalse(rebrand["available"])
        self.assertEqual(rebrand["reason_code"], "not_configured")
        self.assertTrue(rebrand["reason"])

    def test_a_throttled_schema_marks_the_type_unavailable_not_fieldless(self):
        with mock.patch.object(clickup_client, "get_list_fields", return_value=None):
            types = portal_tickets.types_with_schema()
        general = next(t for t in types if t["key"] == "general")
        self.assertFalse(general["available"])
        self.assertEqual(general["reason_code"], "schema_unavailable")
        self.assertEqual(general["fields"], [])

    def test_a_genuinely_fieldless_list_is_still_available(self):
        with mock.patch.object(clickup_client, "get_list_fields", return_value=[]):
            types = portal_tickets.types_with_schema()
        general = next(t for t in types if t["key"] == "general")
        self.assertTrue(general["available"])
        self.assertEqual(general["fields"], [])

    def test_internal_types_are_OMITTED_not_marked_unavailable(self):
        """Listing them would enumerate dispo_cancel and new_business to every
        portal user — the disclosure create_ticket's identical-error branch
        exists to prevent."""
        with mock.patch.object(clickup_client, "get_list_fields", return_value=[]):
            keys = {t["key"] for t in portal_tickets.types_with_schema()}
        self.assertNotIn("dispo_cancel", keys)
        self.assertNotIn("new_business", keys)

    def test_internal_caller_sees_internal_types(self):
        with mock.patch.object(clickup_client, "get_list_fields", return_value=[]):
            keys = {t["key"] for t in portal_tickets.types_with_schema(include_internal=True)}
        self.assertIn("dispo_cancel", keys)

    def test_order_preserves_registry_order(self):
        with mock.patch.object(clickup_client, "get_list_fields", return_value=[]):
            types = portal_tickets.types_with_schema()
        self.assertEqual([t["order"] for t in types], sorted(t["order"] for t in types))

    def test_form_url_is_none_without_a_configured_base(self):
        """form_slug is a slug, not a URL. A dead 'use the form instead' link
        strands the requester twice."""
        with mock.patch.object(clickup_client, "get_list_fields", return_value=[]):
            types = portal_tickets.types_with_schema()
        self.assertTrue(all(t["form_url"] is None for t in types))
