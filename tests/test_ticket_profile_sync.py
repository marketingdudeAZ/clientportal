"""Tests for the ticket → property-profile loop (ticket_profile_sync.py).

The load-bearing guarantees, locked:

  * a completed ticket PROPOSES; it never writes the profile
  * a human override is never silently overwritten — the proposal is flagged
  * the current value is read through community_brief.resolve_value(), the one
    precedence rule (override > resolved > empty)
  * dispo_cancel proposes nothing (it is excluded from client-facing writes)
  * only fields allow-listed for the ticket type, and only editable ones
  * no-op proposals (value already live on the profile) are dropped
  * accepting applies via community_brief.write_field — the same write path the
    portal editor uses — and only records the acceptance if the write succeeded

All external I/O (ClickUp, HubSpot, BigQuery, Anthropic) is mocked.
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

import community_brief  # noqa: E402
import ticket_profile_sync as tps  # noqa: E402


def _task(fields, task_id="task-9", list_name="Rebrands"):
    return {
        "id": task_id,
        "name": "Rebrand rollout",
        "list": {"name": list_name},
        "custom_fields": fields,
    }


def _cf(name, value, ftype="short_text"):
    return {"id": "f_" + name.replace(" ", "_"), "name": name,
            "type": ftype, "value": value}


class BuildProposals(unittest.TestCase):
    def test_maps_a_clickup_field_to_a_profile_field(self):
        out = tps.build_proposals(
            _task([_cf("New Property Name", "The Maple at Foxwood")]),
            [], "cid-1", "u-1", "rebrand", props={},
        )
        self.assertEqual(len(out), 1)
        p = out[0]
        self.assertEqual(p["field_key"], "advertised_name")
        self.assertEqual(p["proposed_value"], "The Maple at Foxwood")
        self.assertEqual(p["status"], tps.STATUS_PENDING)
        self.assertEqual(p["extractor"], tps.EXTRACTOR_FIELD_MAP)
        self.assertEqual(p["current_source"], "empty")
        self.assertFalse(p["conflicts_with_override"])

    def test_dispo_cancel_proposes_nothing(self):
        out = tps.build_proposals(
            _task([_cf("New Property Name", "Anything")], list_name="Dispo / Cancel"),
            [], "cid-1", "u-1", "dispo_cancel", props={},
        )
        self.assertEqual(out, [])

    def test_field_not_allowed_for_this_ticket_type_is_dropped(self):
        # "New Budget" → marketing_budget, which a REBRAND may not propose.
        out = tps.build_proposals(
            _task([_cf("New Budget", 4000, "currency")]),
            [], "cid-1", "u-1", "rebrand", props={},
        )
        self.assertEqual(out, [])

    def test_same_field_is_allowed_for_a_budget_ticket(self):
        out = tps.build_proposals(
            _task([_cf("New Budget", 4000, "currency")], list_name="Budget Updates"),
            [], "cid-1", "u-1", "budget_update", props={},
        )
        self.assertEqual([p["field_key"] for p in out], ["marketing_budget"])
        self.assertEqual(out[0]["proposed_value"], "$4,000")

    def test_identity_fields_are_never_proposed(self):
        # Property URL / uuid / Property Code are matching data, not facts.
        out = tps.build_proposals(
            _task([_cf("Property URL", "https://maple.example.com"),
                   _cf("uuid", "u-1"),
                   _cf("Property Code", "MAPL01")]),
            [], "cid-1", "u-1", "rebrand", props={},
        )
        self.assertEqual(out, [])

    def test_unmapped_clickup_field_is_ignored(self):
        out = tps.build_proposals(
            _task([_cf("QA Status", "Passed")]),
            [], "cid-1", "u-1", "rebrand", props={},
        )
        self.assertEqual(out, [])

    def test_unknown_ticket_type_proposes_nothing(self):
        out = tps.build_proposals(
            _task([_cf("New Property Name", "Whatever")], list_name="General"),
            [], "cid-1", "u-1", "general", props={},
        )
        self.assertEqual(out, [])


class OverrideWins(unittest.TestCase):
    """CLAUDE.md rule 4 — a human edit is never silently overwritten."""

    def test_existing_override_is_flagged_not_applied(self):
        props = {"fluency_advertised_name_override": "Maple Court (human-set)"}
        out = tps.build_proposals(
            _task([_cf("New Property Name", "The Maple at Foxwood")]),
            [], "cid-1", "u-1", "rebrand", props=props,
        )
        self.assertEqual(len(out), 1)
        p = out[0]
        self.assertTrue(p["conflicts_with_override"])
        self.assertEqual(p["current_source"], "override")
        self.assertEqual(p["current_value"], "Maple Court (human-set)")
        # Still only a proposal — the profile is untouched.
        self.assertEqual(p["status"], tps.STATUS_PENDING)

    def test_pipeline_value_is_not_an_override_conflict(self):
        props = {"fluency_landmarks": "Zilker Park"}
        out = tps.build_proposals(
            _task([_cf("Nearby Employers", "Acme Corp")],
                  list_name="New Account Build"),
            [], "cid-1", "u-1", "new_account_build", props=props,
        )
        self.assertEqual(len(out), 1)
        self.assertFalse(out[0]["conflicts_with_override"])
        self.assertEqual(out[0]["current_source"], "empty")

    def test_current_value_comes_from_resolve_value(self):
        """The precedence rule is delegated, never reimplemented here."""
        props = {"fluency_neighborhood": "pipeline value",
                 "fluency_neighborhood_override": "human value"}
        with mock.patch.object(community_brief, "resolve_value",
                               wraps=community_brief.resolve_value) as rv:
            out = tps.build_proposals(
                _task([_cf("Neighborhood", "South Congress")],
                      list_name="New Account Build"),
                [], "cid-1", "u-1", "new_account_build", props=props,
            )
        rv.assert_called()
        self.assertEqual(out[0]["current_value"], "human value")  # override wins

    def test_whitespace_only_override_reads_as_empty(self):
        """Matches the resolver's rule — "  " is not a human edit."""
        props = {"fluency_advertised_name_override": "   "}
        out = tps.build_proposals(
            _task([_cf("New Property Name", "The Maple at Foxwood")]),
            [], "cid-1", "u-1", "rebrand", props=props,
        )
        self.assertEqual(out[0]["current_source"], "empty")
        self.assertFalse(out[0]["conflicts_with_override"])


class NoiseSuppression(unittest.TestCase):
    def test_no_op_proposal_is_dropped(self):
        props = {"fluency_advertised_name_override": "The Maple at Foxwood"}
        out = tps.build_proposals(
            _task([_cf("New Property Name", "The Maple at Foxwood")]),
            [], "cid-1", "u-1", "rebrand", props=props,
        )
        self.assertEqual(out, [])

    def test_blank_value_is_dropped(self):
        out = tps.build_proposals(
            _task([_cf("New Property Name", "   ")]),
            [], "cid-1", "u-1", "rebrand", props={},
        )
        self.assertEqual(out, [])

    def test_proposal_id_is_deterministic_per_task_and_field(self):
        a = tps.proposal_id("task-9", "advertised_name")
        b = tps.proposal_id("task-9", "advertised_name")
        c = tps.proposal_id("task-9", "short_name")
        self.assertEqual(a, b)      # a re-fired webhook re-proposes, not duplicates
        self.assertNotEqual(a, c)


class ValueNormalization(unittest.TestCase):
    def test_list_values_become_one_item_per_line(self):
        out = tps.build_proposals(
            _task([_cf("Property Amenities", ["Pool", "Gym"], "labels")],
                  list_name="New Account Build"),
            [], "cid-1", "u-1", "new_account_build", props={},
        )
        self.assertEqual(out[0]["proposed_value"], "Pool\nGym")

    def test_option_field_value_outside_its_options_is_dropped(self):
        """A proposal that write_field would reject is never surfaced."""
        field = community_brief.FIELDS["voice_tier"]
        self.assertEqual(tps._normalize(field, "not-a-tier"), "")
        self.assertEqual(tps._normalize(field, "luxury; value"), "luxury;value")

    def test_structured_table_fields_are_not_proposable(self):
        self.assertIsNone(tps._proposable("floor_plans", "new_account_build"))
        self.assertIsNone(tps._proposable("tracking", "new_account_build"))

    def test_readonly_field_is_not_proposable(self):
        # `name` has no override property — nowhere to write.
        self.assertIsNone(tps._proposable("name", "new_account_build"))


class Persistence(unittest.TestCase):
    def test_propose_from_task_is_inert_while_the_flag_is_off(self):
        os.environ.pop("TICKET_PROFILE_LOOP_ENABLED", None)
        with mock.patch.object(tps, "_append") as append:
            out = tps.propose_from_task(
                _task([_cf("New Property Name", "The Maple")]),
                "cid-1", "u-1", "rebrand", props={},
            )
        self.assertEqual(out, [])
        append.assert_not_called()

    def test_propose_from_task_persists_when_enabled(self):
        os.environ["TICKET_PROFILE_LOOP_ENABLED"] = "true"
        try:
            with mock.patch.object(tps, "_append") as append:
                out = tps.propose_from_task(
                    _task([_cf("New Property Name", "The Maple")]),
                    "cid-1", "u-1", "rebrand", props={},
                )
            self.assertEqual(len(out), 1)
            append.assert_called_once()
        finally:
            os.environ.pop("TICKET_PROFILE_LOOP_ENABLED", None)

    def test_persist_false_builds_without_storing(self):
        os.environ["TICKET_PROFILE_LOOP_ENABLED"] = "true"
        try:
            with mock.patch.object(tps, "_append") as append:
                out = tps.propose_from_task(
                    _task([_cf("New Property Name", "The Maple")]),
                    "cid-1", "u-1", "rebrand", props={}, persist=False,
                )
            self.assertEqual(len(out), 1)
            append.assert_not_called()
        finally:
            os.environ.pop("TICKET_PROFILE_LOOP_ENABLED", None)

    def test_no_bigquery_degrades_to_empty_not_error(self):
        with mock.patch.object(tps, "_bq", return_value=None):
            self.assertEqual(tps.list_proposals("cid-1"), [])
            self.assertIsNone(tps.get_proposal("pid-1"))


_ROW = {
    "proposal_id": "pid-1", "task_id": "task-9", "company_id": "cid-1",
    "property_uuid": "u-1", "ticket_type": "rebrand",
    "field_key": "advertised_name", "field_label": "Advertised Name",
    "proposed_value": "The Maple at Foxwood", "current_value": "",
    "current_source": "empty", "conflicts_with_override": False,
    "extractor": "field_map", "status": tps.STATUS_PENDING, "actor": "",
    "created_at": "2026-08-06T00:00:00+00:00",
}


class AcceptReject(unittest.TestCase):
    def setUp(self):
        self.appended = []
        self.patchers = [
            mock.patch.object(tps, "_bq", return_value=mock.Mock()),
            mock.patch.object(tps, "_append", side_effect=self.appended.append),
            mock.patch.object(tps, "_emit_loop_event"),
        ]
        for p in self.patchers:
            p.start()

    def tearDown(self):
        for p in self.patchers:
            p.stop()

    def test_accept_writes_through_community_brief_write_field(self):
        with mock.patch.object(tps, "get_proposal", return_value=dict(_ROW)), \
             mock.patch.object(community_brief, "write_field",
                               return_value=(True, "The Maple at Foxwood")) as wf:
            out = tps.accept("pid-1", "amy@rpmliving.com")
        wf.assert_called_once_with("cid-1", "advertised_name",
                                   "The Maple at Foxwood",
                                   edited_by="amy@rpmliving.com")
        self.assertEqual(out["status"], tps.STATUS_ACCEPTED)
        self.assertEqual(self.appended[0][0]["status"], tps.STATUS_ACCEPTED)
        self.assertEqual(self.appended[0][0]["actor"], "amy@rpmliving.com")

    def test_accept_records_nothing_when_the_write_fails(self):
        with mock.patch.object(tps, "get_proposal", return_value=dict(_ROW)), \
             mock.patch.object(community_brief, "write_field",
                               return_value=(False, "HubSpot 500")):
            with self.assertRaises(tps.ProposalError) as ctx:
                tps.accept("pid-1", "amy@rpmliving.com")
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(self.appended, [])

    def test_accepting_twice_is_a_conflict(self):
        done = dict(_ROW, status=tps.STATUS_ACCEPTED)
        with mock.patch.object(tps, "get_proposal", return_value=done), \
             mock.patch.object(community_brief, "write_field") as wf:
            with self.assertRaises(tps.ProposalError) as ctx:
                tps.accept("pid-1", "amy@rpmliving.com")
        self.assertEqual(ctx.exception.status, 409)
        wf.assert_not_called()

    def test_missing_proposal_is_404(self):
        with mock.patch.object(tps, "get_proposal", return_value=None):
            with self.assertRaises(tps.ProposalError) as ctx:
                tps.accept("nope", "amy@rpmliving.com")
        self.assertEqual(ctx.exception.status, 404)

    def test_reject_never_touches_the_profile(self):
        with mock.patch.object(tps, "get_proposal", return_value=dict(_ROW)), \
             mock.patch.object(community_brief, "write_field") as wf:
            out = tps.reject("pid-1", "amy@rpmliving.com", note="wrong name")
        wf.assert_not_called()
        self.assertEqual(out["status"], tps.STATUS_REJECTED)
        self.assertEqual(self.appended[0][0]["note"], "wrong name")


class TicketHistory(unittest.TestCase):
    def test_joins_accepted_changes_onto_the_mapped_tickets(self):
        import portal_tickets
        tickets = [{"id": "task-9", "subject": "Rebrand rollout",
                    "status": "Done", "type_label": "Rebrands"},
                   {"id": "task-7", "subject": "Budget bump", "status": "Open",
                    "type_label": "Budget Changes"}]
        proposals = [
            dict(_ROW, status=tps.STATUS_ACCEPTED, actor="amy@rpmliving.com"),
            dict(_ROW, proposal_id="pid-2", field_key="short_name",
                 field_label="Short Name", status=tps.STATUS_PENDING),
            dict(_ROW, proposal_id="pid-3", task_id="task-7",
                 field_key="marketing_budget", status=tps.STATUS_REJECTED),
        ]
        with mock.patch.object(portal_tickets, "list_tickets", return_value=tickets), \
             mock.patch.object(tps, "list_proposals", return_value=proposals):
            out = tps.ticket_history("cid-1", "u-1")

        self.assertEqual(len(out), 2)
        rebrand = out[0]
        self.assertEqual([c["field_key"] for c in rebrand["profile_changes"]],
                         ["advertised_name"])
        self.assertEqual(rebrand["pending_proposals"], 1)
        # A rejected proposal changed nothing.
        self.assertEqual(out[1]["profile_changes"], [])

    def test_a_ticket_read_failure_degrades_to_empty(self):
        import portal_tickets
        with mock.patch.object(portal_tickets, "list_tickets",
                               side_effect=RuntimeError("clickup down")), \
             mock.patch.object(tps, "list_proposals", return_value=[]):
            self.assertEqual(tps.ticket_history("cid-1"), [])


class AIExtractor(unittest.TestCase):
    def test_off_by_default(self):
        os.environ.pop("TICKET_PROFILE_AI_EXTRACT", None)
        with mock.patch.object(tps, "_extract_with_ai") as ai:
            tps.build_proposals(_task([]), [], "cid-1", "u-1", "rebrand", props={})
        ai.assert_not_called()

    def test_only_allow_listed_fields_survive(self):
        os.environ["TICKET_PROFILE_AI_EXTRACT"] = "true"
        try:
            fake = mock.Mock()
            fake.messages.create.return_value = mock.Mock(content=[
                mock.Mock(type="text", text='{"proposals":['
                          '{"field_key":"advertised_name","value":"The Maple"},'
                          '{"field_key":"marketing_budget","value":"$9,000"}]}')
            ])
            with mock.patch.dict(sys.modules, {"anthropic": mock.Mock(
                    Anthropic=mock.Mock(return_value=fake))}):
                out = tps.build_proposals(
                    _task([]), [{"text": "renamed the property"}],
                    "cid-1", "u-1", "rebrand", props={},
                )
            # marketing_budget is not allow-listed for a rebrand → dropped.
            self.assertEqual([p["field_key"] for p in out], ["advertised_name"])
            self.assertEqual(out[0]["extractor"], tps.EXTRACTOR_AI)
        finally:
            os.environ.pop("TICKET_PROFILE_AI_EXTRACT", None)

    def test_deterministic_map_beats_the_model_on_the_same_field(self):
        os.environ["TICKET_PROFILE_AI_EXTRACT"] = "true"
        try:
            with mock.patch.object(tps, "_extract_with_ai", return_value=[
                    ("advertised_name", "Model Guess", tps.EXTRACTOR_AI)]):
                out = tps.build_proposals(
                    _task([_cf("New Property Name", "Mapped Value")]),
                    [], "cid-1", "u-1", "rebrand", props={},
                )
            self.assertEqual(len(out), 1)
            self.assertEqual(out[0]["proposed_value"], "Mapped Value")
            self.assertEqual(out[0]["extractor"], tps.EXTRACTOR_FIELD_MAP)
        finally:
            os.environ.pop("TICKET_PROFILE_AI_EXTRACT", None)

    def test_llm_failure_degrades_to_no_proposals(self):
        os.environ["TICKET_PROFILE_AI_EXTRACT"] = "true"
        try:
            with mock.patch.dict(sys.modules, {"anthropic": mock.Mock(
                    Anthropic=mock.Mock(side_effect=RuntimeError("no key")))}):
                out = tps._extract_with_ai(_task([]), [{"text": "x"}], "rebrand")
            self.assertEqual(out, [])
        finally:
            os.environ.pop("TICKET_PROFILE_AI_EXTRACT", None)


if __name__ == "__main__":
    unittest.main()
