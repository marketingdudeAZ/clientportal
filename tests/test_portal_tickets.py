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
        {"id": "f_code", "name": "Property Code", "type": "short_text", "required": False},
        {"id": "f_name", "name": "Property Name", "type": "short_text", "required": False},
        {"id": "f_pri", "name": "Priority", "type": "drop_down", "required": True,
         "type_config": {"options": [
             {"id": "opt-low", "name": "Low", "orderindex": 0},
             {"id": "opt-high", "name": "High", "orderindex": 1},
         ]}},
        {"id": "f_budget", "name": "New Budget", "type": "currency", "required": False,
         "type_config": {}},
        {"id": "f_details", "name": "Details", "type": "text", "required": False},
    ]


def _creative_fields():
    """The Creative + Ad Copy Updates list, as ClickUp actually returns it.

    Names are verbatim from list 901111120522 on 2026-08-17, including the
    trailing asterisk on "Market*" and the LOWERCASE `z_` prefix — a
    case-sensitive "hide Z_*" filter would miss that field entirely.
    """
    return [
        {"id": "cu_market", "name": "Market*", "type": "drop_down",
         "type_config": {"options": [{"id": "m-dal", "name": "Dallas"}]}},
        {"id": "cu_channels", "name": "Channels / Ads Impacted", "type": "labels",
         "type_config": {"options": [{"id": "ch-ps", "name": "Paid Search Ads"},
                                     {"id": "ch-disp", "name": "Display"}]}},
        {"id": "cu_am", "name": "Account Manager", "type": "drop_down",
         "type_config": {"options": [{"id": "am-dane", "name": "Dane"}]}},
        {"id": "cu_due", "name": "Requested Due Date", "type": "date"},
        {"id": "cu_code", "name": "Property Code", "type": "short_text"},
        {"id": "cu_special", "name": "What is the new/updated special?", "type": "text"},
        {"id": "cu_other", "name": "Is there any other information we need to know?",
         "type": "text"},
        {"id": "cu_qa", "name": "QA Status", "type": "drop_down", "type_config": {}},
        {"id": "cu_url", "name": "Property URL", "type": "url"},
        {"id": "cu_by", "name": "z_Requested By", "type": "users"},
        {"id": "cu_end", "name": "Special / Promotion End Date", "type": "date"},
        {"id": "cu_sp", "name": "SharePoint Path to New Image / Photo", "type": "url"},
        {"id": "cu_prog", "name": "Task Progress", "type": "automatic_progress"},
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
            schema = portal_tickets.form_schema("_no_manifest", "901-general", prefill)
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
            schema = portal_tickets.form_schema("_no_manifest", "901-general", prefill)
        names = [f["name"] for f in schema]
        self.assertIn("Property URL", names)
        self.assertNotIn("uuid", names)

    def test_no_prefill_map_hides_nothing(self):
        """Safe direction: show a field we would have filled, rather than hide
        one nobody fills."""
        with mock.patch.object(clickup_client, "get_list_fields", return_value=_fields()):
            schema = portal_tickets.form_schema("_no_manifest", "901-general")
        self.assertIn("Property URL", [f["name"] for f in schema])

    def test_form_schema_returns_none_when_clickup_will_not_say(self):
        with mock.patch.object(clickup_client, "get_list_fields", return_value=None):
            self.assertIsNone(portal_tickets.form_schema("_no_manifest", "901-general"))

    def test_a_genuinely_fieldless_list_is_empty_not_none(self):
        with mock.patch.object(clickup_client, "get_list_fields", return_value=[]):
            self.assertEqual(portal_tickets.form_schema("_no_manifest", "901-general"), [])

    def test_dropdown_options_and_input_kind_shaped(self):
        with mock.patch.object(clickup_client, "get_list_fields", return_value=_fields()):
            schema = portal_tickets.form_schema("_no_manifest", "901-general")
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
                       return_value={"website": "https://maple.example.com", "uuid": "u-1",
                                     "name": "Maple", "property_code": "MPL001"}),
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
        # prefill landed on the property fields the requester never saw.
        # Driven by the MANIFEST: `general` declares Property Code and Property
        # Name as `known`, so those are what get stamped — not every field that
        # happens to look like identity.
        self.assertEqual(cf["f_code"], "MPL001")
        self.assertEqual(cf["f_name"], "Maple")
        # mapping recorded exactly once with the new task id
        rec.assert_called_once()
        self.assertEqual(rec.call_args[0][0], "task-1")

    def test_shaped_ticket_has_client_status(self):
        with mock.patch.object(portal_tickets, "_record_mapping"):
            body, _ = portal_tickets.create_ticket(
                "cid-42", "general", subject="x", fields={}, property_uuid="u-1",
                submitted_by="user@rpmliving.com")
        self.assertEqual(body["ticket"]["status"], "Open")   # "to do" → Open
        self.assertEqual(body["ticket"]["type_label"], "General Ticket")
        # Scope doc §4 lists submitted-by among the columns the tracker shows.
        self.assertEqual(body["ticket"]["submitted_by"], "user@rpmliving.com")


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


class DiscoveryByForm(unittest.TestCase):
    """Name matching picked the wrong list against the real workspace.

    `creative_ad_copy` (label "Ad Updates: Photos & New Specials") resolved by
    loose contains-match to a list named "Creative" in another space's docs
    folder, while its form actually files into "Creative + Ad Copy Updates".
    Every ad-update ticket in the pilot would have landed somewhere nobody
    triages, and the portal would have looked like it worked.
    """

    @staticmethod
    def _form_view(list_id, name="Some Form"):
        return {"id": "v1", "name": name, "type": "form",
                "parent": {"id": list_id, "type": 6}}

    def test_form_parent_beats_the_name_match_and_the_disagreement_is_reported(self):
        # Only the decoy is in the workspace walk, so the name match can only
        # land on it — the shape the live workspace had before the alias fix.
        lists = [{"id": "WRONG", "name": "Creative", "space": "Paid Media", "folder": "Docs"}]
        views = {"8cjaf2c-2751": self._form_view("RIGHT")}
        with mock.patch.object(clickup_client, "discover_workspace_lists", return_value=lists), \
             mock.patch.object(clickup_client, "get_view", side_effect=lambda v: views.get(v)), \
             mock.patch.object(clickup_client, "get_list",
                               return_value={"name": "Creative + Ad Copy Updates",
                                             "archived": False}):
            out = portal_tickets.discover_list_ids()
        matched = {m["key"]: m for m in out["matched"]}
        self.assertEqual(matched["creative_ad_copy"]["list_id"], "RIGHT")
        self.assertEqual(matched["creative_ad_copy"]["source"], "form")
        self.assertIn("CLICKUP_LIST_CREATIVE_AD_COPY=RIGHT", out["env_block"])
        self.assertNotIn("WRONG", out["env_block"])
        conflict = [c for c in out["conflicts"] if c["key"] == "creative_ad_copy"]
        self.assertEqual(len(conflict), 1)
        self.assertEqual(conflict[0]["name_list_id"], "WRONG")

    def test_archived_list_never_reaches_the_env_block(self):
        """An archived list still accepts tasks — into a list nobody opens.
        campaign_review's registered form pointed at exactly that."""
        views = {"8cjaf2c-68111": self._form_view("ARCH")}
        with mock.patch.object(clickup_client, "discover_workspace_lists", return_value=[]), \
             mock.patch.object(clickup_client, "get_view", side_effect=lambda v: views.get(v)), \
             mock.patch.object(clickup_client, "get_list",
                               return_value={"name": "[OLD] - Campaign Performance Review",
                                             "archived": True}):
            out = portal_tickets.discover_list_ids()
        matched = {m["key"]: m for m in out["matched"]}
        self.assertTrue(matched["campaign_review"]["archived"])
        self.assertNotIn("CAMPAIGN_REVIEW", out["env_block"])
        self.assertTrue(any("campaign_review" in w for w in out["warnings"]))

    def test_unreachable_form_falls_back_to_the_name_match(self):
        """ClickUp being down must not silently blank the discovery output."""
        lists = [{"id": "L2", "name": "General Ticket", "space": "S", "folder": None}]
        with mock.patch.object(clickup_client, "discover_workspace_lists", return_value=lists), \
             mock.patch.object(clickup_client, "get_view", return_value=None):
            out = portal_tickets.discover_list_ids()
        matched = {m["key"]: m for m in out["matched"]}
        self.assertEqual(matched["general"]["list_id"], "L2")
        self.assertEqual(matched["general"]["source"], "name")
        self.assertIn("CLICKUP_LIST_GENERAL=L2", out["env_block"])

    def test_a_type_resolvable_by_neither_reports_why(self):
        with mock.patch.object(clickup_client, "discover_workspace_lists", return_value=[]), \
             mock.patch.object(clickup_client, "get_view", return_value=None):
            out = portal_tickets.discover_list_ids()
        self.assertEqual(out["env_block"], "")
        self.assertTrue(all(u.get("reason") for u in out["unmatched_types"]))


class TypesRoute(unittest.TestCase):
    """The picker payload also tells the form what we pre-fill for the requester."""

    def _client(self):
        from flask import Flask
        from routes.portal_tickets import portal_tickets_bp
        app = Flask(__name__)
        app.register_blueprint(portal_tickets_bp)
        return app.test_client()

    def test_types_payload_names_the_prefilled_property_fields(self):
        # The route now sits behind require_access (the pilot mechanism), so
        # patch the gate at the seam the blueprint imports it through — same
        # reason as test_portal_ticket_auth._access: reaching the real gate
        # pulls in hubdb_helpers and turns this into an integration test.
        with mock.patch("routes.portal_tickets.require_access", return_value=None), \
             mock.patch.object(portal_tickets, "types_with_schema", return_value=[]):
            resp = self._client().get("/api/portal-tickets/types",
                                      headers={"X-Portal-Email": "pm@rpmliving.com"})
        self.assertEqual(resp.status_code, 200)
        prefill = resp.get_json()["prefill_fields"]
        self.assertIn("Property URL", prefill)
        self.assertIn("Account Manager", prefill)
        # uuid is internal plumbing — never shown to a client.
        self.assertNotIn("uuid", [p.lower() for p in prefill])

    def test_types_requires_auth(self):
        resp = self._client().get("/api/portal-tickets/types")
        self.assertEqual(resp.status_code, 401)


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

    def test_the_row_reports_status_and_who_filed_it(self):
        """`submitted_by` comes from OUR mapping row, not from ClickUp — the
        requester is a portal user, who may not exist as a ClickUp member."""
        with mock.patch.object(portal_tickets, "_read_mappings", return_value=self._refs(1)), \
             mock.patch.object(clickup_client, "get_task",
                               side_effect=lambda t: {**self._task(t),
                                                      "status": {"status": "in progress"}}):
            out = portal_tickets.list_tickets("c1", property_uuid="u-1")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["status"], "In progress")
        self.assertEqual(out[0]["type_label"], "General Ticket")
        self.assertEqual(out[0]["submitted_by"], "cm@rpmliving.com")

    def test_a_placeholder_still_names_who_filed_it(self):
        """An unresolvable task is exactly when "who filed this" is the only
        thing we can still tell the requester."""
        with mock.patch.object(portal_tickets, "_read_mappings", return_value=self._refs(1)), \
             mock.patch.object(clickup_client, "get_task", return_value=None):
            out = portal_tickets.list_tickets("c1")
        self.assertTrue(out[0]["unresolved"])
        self.assertEqual(out[0]["submitted_by"], "cm@rpmliving.com")

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

    def test_a_fieldless_list_is_now_DRIFT_for_a_manifest_type(self):
        """This used to assert "available with no fields", which was right when
        the form was whatever ClickUp happened to return. Now that every type
        declares the fields it needs, a list with none of them is a list the
        manifest does not describe — and offering the form anyway collects a
        request with nothing on it."""
        with mock.patch.object(clickup_client, "get_list_fields", return_value=[]):
            types = portal_tickets.types_with_schema()
        general = next(t for t in types if t["key"] == "general")
        self.assertFalse(general["available"])
        self.assertEqual(general["reason_code"], "manifest_drift")
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


if __name__ == "__main__":
    unittest.main()


class ManifestForm(unittest.TestCase):
    """Phase 1 — the manifest decides what is asked; ClickUp resolves the ids.

    `creative_ad_copy` rendered ZERO of its five real fields, so every special
    submitted through the portal arrived with no special, no channels and no
    end date. These pin the fix and the two ways it could quietly rot: the
    space-wide field pool leaking back in, and the manifest drifting from the
    list it names.
    """

    def setUp(self):
        os.environ["CLICKUP_LIST_CREATIVE_AD_COPY"] = "901-creative"
        self.addCleanup(os.environ.pop, "CLICKUP_LIST_CREATIVE_AD_COPY", None)

    def _schema(self, defs=None):
        with mock.patch.object(clickup_client, "get_list_fields",
                               return_value=_creative_fields() if defs is None else defs):
            return portal_tickets.form_schema("creative_ad_copy", "901-creative")

    def _entry(self, resolved=True):
        """`resolved` mirrors a property whose identity fields all come back
        from HubSpot. Without one, `known` fields render as inputs rather than
        vanishing — see test_an_unfillable_known_field_becomes_an_input."""
        prefill = {"Property Code": "42831", "Property URL": "https://x.com",
                   "Account Manager": "Dane", "Market*": "Dallas",
                   "z_Requested By": "cm@rpmliving.com"} if resolved else {}
        with mock.patch.object(clickup_client, "get_list_fields",
                               return_value=_creative_fields()), \
             mock.patch.object(portal_tickets, "_prefill_values", return_value=prefill):
            types = portal_tickets.types_with_schema(company_id="c1")
        return next(t for t in types if t["key"] == "creative_ad_copy")

    def test_it_renders_exactly_the_five_ask_fields_in_manifest_order(self):
        self.assertEqual([f["name"] for f in self._schema()], [
            "What is the new/updated special?",
            "Channels / Ads Impacted",
            "Special / Promotion End Date",
            "SharePoint Path to New Image / Photo",
            "Is there any other information we need to know?",
        ])

    def test_internal_and_identity_fields_are_not_renderable(self):
        """The union dump leaked ops fields to clients. `known` never renders,
        and z_Requested By is lowercase — a `Z_` prefix filter would miss it."""
        names = [f["name"] for f in self._schema()]
        for hidden in ("QA Status", "Task Progress", "z_Requested By",
                       "Property Code", "Property URL", "Account Manager",
                       "Market*", "Requested Due Date"):
            self.assertNotIn(hidden, names)

    def test_field_ids_and_options_still_come_from_clickup(self):
        """The anti-drift goal survives: editing a dropdown's options in
        ClickUp must still reach the portal with no redeploy."""
        channels = next(f for f in self._schema() if f["name"] == "Channels / Ads Impacted")
        self.assertEqual(channels["id"], "cu_channels")
        self.assertEqual(channels["input"], "multiselect")
        self.assertEqual([o["label"] for o in channels["options"]],
                         ["Paid Search Ads", "Display"])

    def test_requiredness_comes_from_the_manifest_not_clickup(self):
        """ClickUp reports required=False for every list field — requiredness
        lives on the form view, which the API will not show us."""
        by_name = {f["name"]: f for f in self._schema()}
        self.assertTrue(by_name["What is the new/updated special?"]["required"])
        self.assertFalse(by_name["SharePoint Path to New Image / Photo"]["required"])

    def test_the_special_field_carries_its_rule_to_the_browser(self):
        special = next(f for f in self._schema()
                       if f["name"] == "What is the new/updated special?")
        self.assertEqual(special["maxlength"], 90)
        self.assertEqual(special["validate"], "no_caps_no_punct_except_period")
        self.assertTrue(special["help"])

    def test_sections_sla_and_notes_reach_the_picker_payload(self):
        entry = self._entry()
        self.assertTrue(entry["available"])
        self.assertEqual([s["title"] for s in entry["sections"]],
                         ["Creative + Ad Copy Information"])
        self.assertTrue(entry["sla"])
        self.assertTrue(entry["notes"])
        self.assertEqual(entry["name_pattern"],
                         "[Property Name] - Creative / Ad Copy Update")

    def test_a_section_of_only_known_fields_is_not_an_empty_heading(self):
        titles = [s["title"] for s in self._entry()["sections"]]
        self.assertNotIn("Property Information", titles)

    def test_the_identity_we_attach_is_returned_for_the_summary_card(self):
        """Shown as a read-only card, not as disabled inputs — a disabled input
        still reads as "a field to deal with"."""
        ident = self._entry()["identity"]
        self.assertEqual(ident["Property Code"], "42831")
        self.assertEqual(ident["Account Manager"], "Dane")
        # uuid and the requester are plumbing, not something to show a client.
        self.assertNotIn("z_Requested By", ident)

    def test_an_unfillable_known_field_becomes_an_input(self):
        """The Account Manager bug, generically: a `known` field whose source
        breaks used to vanish from the form AND the task, so it was blank on
        every ticket forever. Two are live today — regional_manager_email and
        marketing_rvp_email exist on the schema but are populated on nothing."""
        entry = self._entry(resolved=False)
        names = [f["name"] for f in entry["fields"]]
        self.assertIn("Property Code", names)
        degraded = next(f for f in entry["fields"] if f["name"] == "Property Code")
        self.assertTrue(degraded["degraded_known"])
        self.assertFalse(degraded["required"])   # never block on our own gap
        self.assertIn("couldn\'t find", degraded["help"])

    def test_flat_fields_still_ship_for_the_cached_frontend(self):
        """The HubSpot CDN serves the portal template for up to 10 hours while
        the API deploys in minutes, so the old renderer — which reads the flat
        `fields` list and knows nothing of sections — must keep working."""
        entry = self._entry()
        self.assertEqual([f["name"] for f in entry["fields"]],
                         [f["name"] for s in entry["sections"] for f in s["fields"]])

    def test_drift_on_a_REQUIRED_field_takes_the_type_offline(self):
        """Half a form is worse than no form: the requester submits something
        that looks complete and isn't."""
        defs = [f for f in _creative_fields() if f["name"] != "Channels / Ads Impacted"]
        with mock.patch.object(clickup_client, "get_list_fields", return_value=defs):
            types = portal_tickets.types_with_schema()
        entry = next(t for t in types if t["key"] == "creative_ad_copy")
        self.assertFalse(entry["available"])
        self.assertEqual(entry["reason_code"], "manifest_drift")

    def test_drift_on_an_optional_field_only_drops_that_field(self):
        defs = [f for f in _creative_fields()
                if f["name"] != "SharePoint Path to New Image / Photo"]
        names = [f["name"] for f in self._schema(defs)]
        self.assertNotIn("SharePoint Path to New Image / Photo", names)
        self.assertIn("Channels / Ads Impacted", names)

    def test_a_type_with_no_manifest_is_untouched(self):
        with mock.patch.object(clickup_client, "get_list_fields", return_value=_fields()):
            schema = portal_tickets.form_schema("_no_manifest", "901-general")
        self.assertIn("Priority", [f["name"] for f in schema])


class SpecialCopyRule(unittest.TestCase):
    """90 characters, no capitals, periods only. Enforced server-side because
    the browser is advice — the API is reachable without the page."""

    RULE = {"maxlength": 90, "validate": "no_caps_no_punct_except_period"}
    FIELD = {"id": "cu_special", "name": "What is the new/updated special?",
             "type": "text"}

    def _coerce(self, value):
        return portal_tickets._coerce(self.FIELD, value, self.RULE)

    def test_a_conforming_special_passes_through(self):
        self.assertEqual(self._coerce("2 bedrooms starting at $1500 through july 15th."),
                         "2 bedrooms starting at $1500 through july 15th.")

    def test_capitals_are_rejected(self):
        with self.assertRaises(portal_tickets.TicketValidation) as ctx:
            self._coerce("2 Bedrooms starting at $1500")
        self.assertIn("capital", ctx.exception.message.lower())
        self.assertEqual(ctx.exception.field, "What is the new/updated special?")

    def test_punctuation_other_than_periods_is_rejected(self):
        with self.assertRaises(portal_tickets.TicketValidation):
            self._coerce("2 bedrooms free, half off!")

    def test_over_90_characters_is_rejected(self):
        with self.assertRaises(portal_tickets.TicketValidation) as ctx:
            self._coerce("a" * 91)
        self.assertIn("90", ctx.exception.message)

    def test_the_rule_does_not_apply_to_fields_without_it(self):
        self.assertEqual(portal_tickets._coerce(self.FIELD, "SHOUTING, loudly!"),
                         "SHOUTING, loudly!")

    def test_create_rejects_a_bad_special_with_400_and_names_the_field(self):
        os.environ["CLICKUP_LIST_CREATIVE_AD_COPY"] = "901-creative"
        self.addCleanup(os.environ.pop, "CLICKUP_LIST_CREATIVE_AD_COPY", None)
        with mock.patch.object(clickup_client, "CLICKUP_API_KEY", "test-key"), \
             mock.patch.object(clickup_client, "get_list_fields",
                               return_value=_creative_fields()), \
             mock.patch.object(clickup_client, "create_task") as create, \
             mock.patch("hubspot_client.get_company", return_value={}):
            body, status = portal_tickets.create_ticket(
                "cid-42", "creative_ad_copy", subject="x",
                fields={"cu_special": "Half Off, Now!"}, property_uuid="u-1")
        self.assertEqual(status, 400)
        self.assertEqual(body["field"], "What is the new/updated special?")
        create.assert_not_called()


class ManifestMatchesLiveClickUp(unittest.TestCase):
    """The drift test. Runs against LIVE ClickUp when a key is available, and
    skips otherwise — a manifest that names a field the list does not have is
    exactly the failure this pattern trades for the old whole-list dump, so it
    has to be checked against the real thing, not a fixture."""

    def setUp(self):
        if not os.getenv("CLICKUP_API_KEY") or os.getenv("CLICKUP_API_KEY") == "test-key":
            self.skipTest("no live ClickUp key")

    def test_every_manifest_name_resolves_on_that_types_own_list(self):
        from config import PORTAL_TICKET_FORMS
        clickup_client.clear_field_cache()
        problems = []
        for type_key, manifest in PORTAL_TICKET_FORMS.items():
            # Resolve through the form view, NOT through CLICKUP_LIST_* env.
            # This module pins fake list ids at import so the picker has
            # something to show, and the whole point of this test is to check
            # the manifest against the list that type's form actually files
            # into — which is also what makes it work on CI, where none of the
            # real env vars are set.
            t = portal_tickets._type_by_key(type_key)
            list_id, why = portal_tickets._list_by_form(t.get("form_slug", "") if t else "")
            if not list_id:
                problems.append(f"{type_key}: form did not resolve to a list — {why}")
                continue
            defs = clickup_client.get_list_fields(list_id)
            if defs is None:
                self.skipTest(f"ClickUp would not return fields for {list_id}")
            live = {(d.get("name") or "").strip().lower(): d for d in defs}
            for sec in manifest.get("sections") or []:
                for entry in sec.get("fields") or []:
                    name = (entry.get("name") or "").strip()
                    if name.lower() not in live:
                        problems.append(f"{type_key}: {name!r} is not on list {list_id}")
            # Informational: a live REQUIRED field nobody asks for. ClickUp
            # reports requiredness at the list level as False for everything
            # (it lives on the form view), so this is near-always empty today —
            # it exists to catch the day that changes.
            missing_required = [
                (d.get("name") or "") for d in defs
                if d.get("required") and (d.get("name") or "").strip().lower() not in {
                    (e.get("name") or "").strip().lower()
                    for s in manifest.get("sections") or [] for e in s.get("fields") or []
                }
            ]
            if missing_required:
                print(f"\n[drift] {type_key}: live REQUIRED fields absent from the "
                      f"manifest: {missing_required}")
        self.assertEqual(problems, [], "\n".join(problems))


class DuplicateFieldNames(unittest.TestCase):
    """Duplicate names are the NORMAL case on these lists, not an edge case.

    Every paid channel exists twice on the same list — a `currency` variant for
    new-build budgets and a `drop_down` variant for add/remove/change — and the
    General list carries FOUR fields called "Market" with different regional
    option sets. A name→def dict silently keeps whichever came last, which is a
    wrong-field-id write that nothing downstream can detect.
    """

    DEFS = [
        {"id": "cur", "name": "Paid Search", "type": "currency"},
        {"id": "sel", "name": "Paid Search", "type": "drop_down", "type_config": {}},
        {"id": "m1", "name": "Market", "type": "drop_down", "type_config": {}},
        {"id": "m2", "name": "Market", "type": "drop_down", "type_config": {}},
        {"id": "solo", "name": "Campaign Focus", "type": "text"},
    ]

    def _pick(self, entry):
        by = portal_tickets._defs_by_name(self.DEFS)
        return portal_tickets._pick_def(entry, by.get(entry["name"].lower()) or [])

    def test_clickup_type_separates_the_channel_twins(self):
        self.assertEqual(self._pick({"name": "Paid Search",
                                     "clickup_type": "currency"})["id"], "cur")
        self.assertEqual(self._pick({"name": "Paid Search",
                                     "clickup_type": "drop_down"})["id"], "sel")

    def test_an_unqualified_duplicate_resolves_to_nothing(self):
        """Not to whichever came last. Half the channel budgets would land on
        the wrong field and the task would look correctly filled in."""
        self.assertIsNone(self._pick({"name": "Paid Search"}))

    def test_duplicates_identical_in_type_need_an_explicit_id(self):
        """The four "Market" fields differ only by id and option set."""
        self.assertIsNone(self._pick({"name": "Market", "clickup_type": "drop_down"}))
        self.assertEqual(self._pick({"name": "Market", "field_id": "m2"})["id"], "m2")

    def test_a_unique_name_needs_no_qualifier(self):
        self.assertEqual(self._pick({"name": "Campaign Focus"})["id"], "solo")

    def test_an_ambiguous_REQUIRED_field_is_drift_not_a_coin_flip(self):
        by = portal_tickets._defs_by_name(self.DEFS)
        with self.assertRaises(portal_tickets.ManifestDrift):
            portal_tickets._resolve_manifest_field(
                {"name": "Paid Search", "tier": "ask", "required": True}, by, "t")

    def test_prefill_never_writes_to_an_ambiguous_name(self):
        """The form posts field IDS so it is unaffected; prefill posts NAMES,
        and writing to a coin-flip field is worse than leaving it blank."""
        with mock.patch.object(clickup_client, "get_list_fields", return_value=self.DEFS):
            built = portal_tickets._build_custom_fields(
                "L", {}, {"Paid Search": "500", "Campaign Focus": "lease-ups"})
        self.assertIsNotNone(built)
        payload = {c["id"] for c in built[0]}
        self.assertIn("solo", payload)
        self.assertNotIn("cur", payload)
        self.assertNotIn("sel", payload)


class EveryTypeHasAManifest(unittest.TestCase):
    def test_all_eight_registry_types_are_covered(self):
        """A type without a manifest falls back to rendering the whole space
        field pool — 91 to 110 fields, internal ops fields included."""
        from config import PORTAL_TICKET_FORMS, PORTAL_TICKET_TYPES
        self.assertEqual({t["key"] for t in PORTAL_TICKET_TYPES},
                         set(PORTAL_TICKET_FORMS))

    def test_no_manifest_asks_for_an_internal_ops_field(self):
        from config import PORTAL_TICKET_FORMS
        banned = ("qa status", "task progress", "final deliverable",
                  "type of template", "resources", "due diligence done")
        for key, man in PORTAL_TICKET_FORMS.items():
            for sec in man["sections"]:
                for f in sec["fields"]:
                    n = f["name"].strip().lower()
                    if f.get("tier", "ask") != "ask":
                        continue
                    self.assertFalse(n.startswith("z_"), f"{key}: {f['name']}")
                    self.assertFalse(any(b in n for b in banned), f"{key}: {f['name']}")

    def test_every_manifest_declares_its_sla(self):
        from config import PORTAL_TICKET_FORMS
        for key, man in PORTAL_TICKET_FORMS.items():
            self.assertTrue(man.get("sla"), key)
            self.assertTrue(man.get("name_pattern"), key)


class MappingTableSelfHeal(unittest.TestCase):
    """The mapping row is what makes a filed ticket visible to the person who
    filed it — and what the recap matches on. A migration nobody ran is
    indistinguishable at runtime from a table that never existed, and the
    failure was silent: ticket files fine, "what's open" stays empty forever.
    """

    def setUp(self):
        portal_tickets._TABLE_ENSURED = False
        portal_tickets._TRACKING_DEGRADED = False
        self.addCleanup(setattr, portal_tickets, "_TABLE_ENSURED", False)
        self.addCleanup(setattr, portal_tickets, "_TRACKING_DEGRADED", False)

    def _bq(self, insert_side_effect=None, query_ok=True):
        bq = mock.MagicMock()
        bq.is_bigquery_configured.return_value = True
        bq._dataset.return_value = "rpm_portal"
        bq.insert_rows.side_effect = insert_side_effect
        if not query_ok:
            bq.query.side_effect = RuntimeError("no DDL permission")
        return mock.patch.dict("sys.modules", {"bigquery_client": bq}), bq

    def test_a_missing_table_is_created_and_the_row_retried(self):
        calls = []

        def insert(table, rows):
            calls.append(rows)
            if len(calls) == 1:
                raise RuntimeError("404 Not found: Table portal_tickets")

        patcher, bq = self._bq(insert_side_effect=insert)
        with patcher:
            portal_tickets._record_mapping("t1", "c1", "u1", "general", "cm@rpmliving.com")
        self.assertEqual(len(calls), 2)                 # failed, then retried
        self.assertIn("CREATE TABLE IF NOT EXISTS", bq.query.call_args[0][0])
        self.assertFalse(portal_tickets.tracking_degraded())

    def test_the_table_is_only_ensured_once_per_process(self):
        patcher, bq = self._bq(insert_side_effect=RuntimeError("still broken"))
        with patcher:
            portal_tickets._record_mapping("t1", "c1", "u1", "general", "a@rpmliving.com")
            portal_tickets._record_mapping("t2", "c1", "u1", "general", "a@rpmliving.com")
        self.assertEqual(bq.query.call_count, 1)

    def test_an_unfixable_write_flips_tracking_to_degraded(self):
        patcher, _ = self._bq(insert_side_effect=RuntimeError("boom"), query_ok=False)
        with patcher:
            portal_tickets._record_mapping("t1", "c1", "u1", "general", "a@rpmliving.com")
        self.assertTrue(portal_tickets.tracking_degraded())

    def test_unconfigured_bigquery_is_degraded_not_silent(self):
        bq = mock.MagicMock()
        bq.is_bigquery_configured.return_value = False
        with mock.patch.dict("sys.modules", {"bigquery_client": bq}):
            portal_tickets._record_mapping("t1", "c1", "u1", "general", "a@rpmliving.com")
        self.assertTrue(portal_tickets.tracking_degraded())
        bq.insert_rows.assert_not_called()

    def test_the_runtime_ddl_matches_migration_0012(self):
        """Two definitions of one table drift. Pin them together."""
        import os as _os
        import re as _re
        path = _os.path.join(_os.path.dirname(__file__), "..", "migrations",
                             "0012_portal_tickets_table.py")
        ns = {}
        exec(compile(open(path).read(), path, "exec"), ns)   # noqa: S102 — a constant
        cols = lambda ddl: sorted(_re.findall(r"^\s+(\w+)\s+(?:STRING|TIMESTAMP)",
                                              ddl, _re.M))
        self.assertEqual(cols(ns["DDL"]), cols(portal_tickets._MAPPING_DDL))
        self.assertTrue(cols(ns["DDL"]), "column regex matched nothing")


class DomainGate(unittest.TestCase):
    """Ticketing writes into live ClickUp queues, so its floor must not move
    when a HubDB row does. The domain check is deliberately independent of
    feature_access's internal/client inference."""

    def setUp(self):
        from flask import Flask
        from routes.portal_tickets import portal_tickets_bp
        app = Flask(__name__)
        app.register_blueprint(portal_tickets_bp)
        self.c = app.test_client()
        p = mock.patch("routes.portal_tickets.require_access", return_value=None)
        p.start()
        self.addCleanup(p.stop)

    def _get(self, email):
        with mock.patch.object(portal_tickets, "types_with_schema", return_value=[]):
            return self.c.get("/api/portal-tickets/types",
                              headers={"X-Portal-Email": email}).status_code

    def test_an_rpm_address_gets_in(self):
        self.assertEqual(self._get("cm@rpmliving.com"), 200)

    def test_a_client_address_is_refused(self):
        self.assertEqual(self._get("manager@someclient.com"), 403)

    def test_the_domain_is_matched_exactly_not_by_suffix(self):
        """`evil-rpmliving.com` and `rpmliving.com.attacker.net` must not pass."""
        self.assertEqual(self._get("x@evil-rpmliving.com"), 403)
        self.assertEqual(self._get("x@rpmliving.com.attacker.net"), 403)

    def test_case_and_subdomain_handling(self):
        self.assertEqual(self._get("CM@RPMLIVING.COM"), 200)
        self.assertEqual(self._get("x@mail.rpmliving.com"), 403)

    def test_the_allowed_domains_are_configurable(self):
        with mock.patch.dict(os.environ,
                             {"PORTAL_TICKETS_ALLOWED_DOMAINS": "rpmliving.com,partner.io"}):
            self.assertEqual(self._get("x@partner.io"), 200)

    def test_star_opens_it_to_any_domain(self):
        with mock.patch.dict(os.environ, {"PORTAL_TICKETS_ALLOWED_DOMAINS": "*"}):
            self.assertEqual(self._get("anyone@anywhere.net"), 200)

    def test_the_domain_gate_runs_before_the_roster(self):
        """So a client address is refused even if someone pastes it into the
        pilot roster by mistake."""
        with mock.patch.dict(os.environ,
                             {"PORTAL_TICKETS_PILOT_EMAILS": "manager@someclient.com"}):
            self.assertEqual(self._get("manager@someclient.com"), 403)


class KnownTierResolution(unittest.TestCase):
    """Phase 2 — identity comes from the property record, not the requester.

    The previous mapping was a single global {ClickUp field name: HubSpot
    property} table keyed "Market" and "uuid". No live field has either name,
    so those values silently never landed on any ticket — the same class of
    bug as Account Manager pointing at a HubSpot property that does not exist.
    Sources now live on the manifest entry, next to the field they fill.
    """

    COMPANY = {"name": "10x Riverwalk", "property_code": "42831",
               "website": "10xriverwalkapts.com", "market": "Miami",
               "hubspot_owner_id": "1284471", "uuid": "u-1"}

    def _prefill(self, type_key="creative_ad_copy", company=None, owner="Dane",
                 submitted_by="cm@rpmliving.com"):
        with mock.patch("hubspot_client.get_company",
                        return_value=self.COMPANY if company is None else company), \
             mock.patch.object(portal_tickets, "_owner_name", return_value=owner):
            return portal_tickets._prefill_values(type_key, "c1", "u-1", submitted_by)

    def test_it_fills_the_exact_live_field_names(self):
        p = self._prefill()
        self.assertEqual(p["Property Code"], "42831")
        self.assertEqual(p["Property URL"], "10xriverwalkapts.com")
        # "Market*", with the asterisk — the name the field actually has. The
        # old table said "Market", which matches nothing on this list.
        self.assertEqual(p["Market*"], "Miami")

    def test_the_account_manager_is_a_name_not_an_owner_id(self):
        self.assertEqual(self._prefill()["Account Manager"], "Dane")

    def test_the_region_table_is_only_a_fallback_for_an_ownerless_property(self):
        """The owners lookup is the real answer — it stays right when someone
        changes desks. The screenshot-of-a-spreadsheet table does not."""
        company = dict(self.COMPANY, hubspot_owner_id="", market="Dallas")
        p = self._prefill(company=company, owner="")
        self.assertEqual(p["Account Manager"], "Dane")      # from the table
        company = dict(self.COMPANY, hubspot_owner_id="", market="Nowhere")
        self.assertNotIn("Account Manager", self._prefill(company=company, owner=""))

    def test_the_requester_email_fills_the_requested_by_field(self):
        self.assertEqual(self._prefill()["z_Requested By"], "cm@rpmliving.com")

    def test_a_users_field_is_never_written_to_clickup(self):
        """`users` takes ClickUp member ids. A portal requester may have no
        ClickUp account, so an email is rejected or silently dropped —
        attribution lives in the description, where it always resolves."""
        self.assertIsNone(portal_tickets._coerce(
            {"name": "z_Requested By", "type": "users"}, "cm@rpmliving.com"))

    def test_a_hubspot_outage_yields_a_partial_map_not_an_error(self):
        with mock.patch("hubspot_client.get_company", side_effect=RuntimeError("502")):
            p = portal_tickets._prefill_values("creative_ad_copy", "c1", "u-1",
                                               "cm@rpmliving.com")
        self.assertEqual(p, {"z_Requested By": "cm@rpmliving.com"})

    def test_unresolved_known_fields_names_what_we_could_not_fill(self):
        p = {"Property Code": "42831", "Property URL": ""}
        missing = portal_tickets.unresolved_known_fields("creative_ad_copy", p)
        self.assertIn("property url", missing)     # present but empty
        self.assertIn("market*", missing)          # absent entirely
        self.assertNotIn("property code", missing)


class KnownSourcesExistInHubSpot(unittest.TestCase):
    """Every manifest `source` must be a real company property.

    This is the check nobody ran on `account_manager`, `rm_email`, `rvp_email`,
    `portfolio_name` and `email` — five sources that named nothing. A mapping
    that points at a property which does not exist fails silently, forever, on
    every ticket.
    """

    def test_every_source_names_a_real_company_property(self):
        import hubspot_client
        from config import PORTAL_TICKET_FORMS
        # Skip on ANY credential failure rather than guessing which placeholder
        # value the environment uses: CI sets HUBSPOT_API_KEY to a dummy, so a
        # name-based guard let this run and 401 there. A live-only check must
        # never fail a build for lack of credentials — only for real drift.
        try:
            r = hubspot_client._request(
                "GET", f"{hubspot_client.API_BASE}/crm/v3/properties/companies")
            live = {p["name"] for p in r.json().get("results", [])}
        except Exception as e:  # noqa: BLE001
            self.skipTest(f"no live HubSpot access: {type(e).__name__}")
        if not live:
            self.skipTest("HubSpot returned no properties")
        # Not HubSpot properties: resolved from the session / the URL instead.
        synthetic = {"submitted_by", "uuid"}
        bad = sorted({
            (key, e["source"])
            for key, man in PORTAL_TICKET_FORMS.items()
            for sec in man["sections"] for e in sec["fields"]
            if e.get("tier") == "known" and e.get("source")
            and e["source"] not in synthetic and e["source"] not in live
        })
        self.assertEqual(bad, [], f"sources that name no HubSpot property: {bad}")


class PrefillMustFitTheField(unittest.TestCase):
    """Resolving a value is not the same as it fitting the field.

    Both of these are live. Account Manager's options are first names while
    HubSpot's owner is a full name, and Market* holds DIGITAL REGIONS while
    HubSpot's `market` is a physical market. A resolved-but-unfittable value
    used to hide the field (because prefill "worked") and then get dropped at
    write time (because the option didn't match) — invisible on both ends.
    """

    AM = {"id": "am", "name": "Account Manager", "type": "drop_down",
          "type_config": {"options": [{"id": "o1", "name": "Katie"},
                                      {"id": "o2", "name": "Dane"}]}}
    MARKET = {"id": "mk", "name": "Market*", "type": "drop_down",
              "type_config": {"options": [{"id": "m1", "name": "Florida"},
                                          {"id": "m2", "name": "Atlanta"}]}}

    def test_a_full_name_fits_a_first_name_option(self):
        self.assertEqual(portal_tickets._fit_to_options(self.AM, "Katie Cannon"), "Katie")
        self.assertEqual(portal_tickets._coerce(self.AM, "Katie Cannon"), "o1")

    def test_an_exact_match_still_wins(self):
        self.assertEqual(portal_tickets._fit_to_options(self.MARKET, "Atlanta"), "Atlanta")

    def test_a_physical_market_does_NOT_get_guessed_into_a_region(self):
        """Miami is in Florida, and a human knows that. Encoding the guess here
        would route tickets to the wrong region and look correct doing it."""
        self.assertIsNone(portal_tickets._fit_to_options(self.MARKET, "Miami"))
        self.assertIsNone(portal_tickets._coerce(self.MARKET, "Miami"))

    def test_an_ambiguous_first_name_is_refused(self):
        field = dict(self.AM, type_config={"options": [{"id": "a", "name": "Katie"},
                                                       {"id": "b", "name": "Katie"}]})
        self.assertIsNone(portal_tickets._fit_to_options(field, "Katie Cannon"))

    def test_an_unfittable_value_counts_as_unresolved_so_the_field_renders(self):
        defs = [self.AM, self.MARKET]
        missing = portal_tickets.unresolved_known_fields(
            "creative_ad_copy",
            {"Account Manager": "Katie Cannon", "Market*": "Miami"}, defs)
        self.assertIn("market*", missing)            # will not fit → render it
        self.assertNotIn("account manager", missing)  # fits → stays hidden

    def test_free_text_fields_are_never_option_checked(self):
        self.assertEqual(
            portal_tickets._fit_to_options({"name": "Property Code", "type": "short_text"},
                                           "42831"), "42831")
