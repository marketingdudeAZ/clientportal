"""Tests for the portal ticket page — curated per-type forms + closed loop.

Forms are pinned in portal_ticket_specs.py (fields/order/sections/required) with
options resolved live from ClickUp; on submit, descriptive fields refresh the
property profile with conflict detection. External I/O (ClickUp/HubSpot/BigQuery)
is mocked; BigQuery is left unconfigured so the mapping store no-ops.
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
os.environ["CLICKUP_LIST_NEW_ACCOUNT_BUILD"] = "901-nab"
os.environ["CLICKUP_LIST_GENERAL"] = "901-gen"
os.environ["CLICKUP_LIST_CAMPAIGN_REVIEW"] = "901-rev"

import clickup_client  # noqa: E402
import portal_tickets  # noqa: E402
import portal_ticket_profile  # noqa: E402


def _fld(fid, name, ftype, options=None, required=False):
    d = {"id": fid, "name": name, "type": ftype, "required": required}
    if options is not None:
        d["type_config"] = {"options": [{"id": f"opt-{o.lower().replace(' ', '-')}", "name": o}
                                        for o in options]}
    return d


# Field defs per list — only what the tests exercise (the real lists carry more).
_NAB_FIELDS = [
    _fld("f-name", "Property Name", "short_text"),
    _fld("f-status", "Property Status", "drop_down", ["Stabilized", "Lease Up"]),
    _fld("f-comp", "Top Competitors", "text"),
    _fld("f-voice", "Voice & Tone", "short_text"),
    _fld("f-cms", "Property Website Platform", "short_text"),
]
_GEN_FIELDS = [
    _fld("f-name", "Property Name", "short_text"),
    _fld("f-cat", "Category", "drop_down",
         ["Billing Issues", "CMS Switch / Website Change", "Other"]),
    _fld("f-cms", "CMS / Website Change - What information is changing or needs to be updated?", "text"),
    _fld("f-bill", "Billing - Can you explain the issue/request in more detail?", "text"),
]


def _patch_fields(mapping):
    def _side(list_id):
        return mapping.get(list_id, [])
    return mock.patch.object(clickup_client, "get_list_fields", side_effect=_side)


class BuildForm(unittest.TestCase):
    def test_prefill_hidden_client_fields_shown(self):
        with _patch_fields({"901-nab": _NAB_FIELDS}):
            form = portal_tickets.build_form("new_account_build")
        labels = [f["label"] for f in form["fields"]]
        self.assertNotIn("Property Name", labels)          # prefill → hidden
        self.assertIn("Top Competitors", labels)
        self.assertTrue(form["updates_profile"])
        self.assertTrue(form["intro"])

    def test_options_resolved_live_and_sectioned(self):
        with _patch_fields({"901-nab": _NAB_FIELDS}):
            form = portal_tickets.build_form("new_account_build")
        status = next(f for f in form["fields"] if f["label"] == "Property Status")
        self.assertEqual(status["input"], "select")
        self.assertEqual([o["label"] for o in status["options"]], ["Stabilized", "Lease Up"])
        self.assertEqual(status["section"], "Property Information")

    def test_general_category_conditional(self):
        with _patch_fields({"901-gen": _GEN_FIELDS}):
            form = portal_tickets.build_form("general")
        cat = next(f for f in form["fields"] if f["label"] == "Category")
        cms = next(f for f in form["fields"] if f["label"].startswith("CMS / Website Change"))
        self.assertIsNotNone(cms["show_if"])
        self.assertEqual(cms["show_if"]["field"], cat["key"])
        self.assertIn("CMS Switch / Website Change", cms["show_if"]["values"])


class CreateAndCloseLoop(unittest.TestCase):
    def setUp(self):
        self.created = {}

        def _create(list_id, name, **kw):
            self.created = {"list_id": list_id, "name": name, **kw}
            return {"id": "task-9", "name": name, "url": "u", "status": {"status": "to do"},
                    "date_created": "1720000000000"}

        self.patchers = [
            mock.patch.object(clickup_client, "CLICKUP_API_KEY", "test-key"),
            _patch_fields({"901-nab": _NAB_FIELDS}),
            mock.patch.object(clickup_client, "create_task", side_effect=_create),
            mock.patch("hubspot_client.get_company",
                       return_value={"name": "Maple", "market": "Austin", "uuid": "u-1"}),
        ]
        for p in self.patchers:
            p.start()

    def tearDown(self):
        for p in self.patchers:
            p.stop()

    def test_create_maps_inputs_and_calls_closed_loop(self):
        with mock.patch.object(portal_tickets, "_record_mapping"), \
             mock.patch.object(portal_ticket_profile, "apply_updates",
                               return_value={"applied": [], "conflicts": []}) as prof:
            body, status = portal_tickets.create_ticket(
                "cid-1", "new_account_build",
                subject="Launch Maple",
                fields={"f-comp": "Acme, Beta", "f-voice": "Upscale, modern",
                        "f-status": "Stabilized"},
                submitted_by="user@rpmliving.com", property_uuid="u-1",
            )
        self.assertEqual(status, 201)
        cf = {c["id"]: c["value"] for c in self.created["custom_fields"]}
        self.assertEqual(cf["f-comp"], "Acme, Beta")
        self.assertEqual(cf["f-status"], "opt-stabilized")   # dropdown label → option id
        # Closed loop got the descriptive fields mapped to profile keys.
        prof.assert_called_once()
        pv = prof.call_args[0][1]
        self.assertEqual(pv["competitors"], "Acme, Beta")
        self.assertEqual(pv["brand_adjectives"], "Upscale, modern")

    def test_non_profile_type_skips_closed_loop(self):
        with _patch_fields({"901-rev": [_fld("f-occ", "Occupancy", "number")]}), \
             mock.patch.object(portal_tickets, "_record_mapping"), \
             mock.patch.object(portal_ticket_profile, "apply_updates") as prof:
            body, status = portal_tickets.create_ticket(
                "cid-1", "campaign_review", subject="Review", fields={"f-occ": "88"})
        self.assertEqual(status, 201)
        prof.assert_not_called()          # campaign_review.updates_profile is False


class ClosedLoopApply(unittest.TestCase):
    def _run(self, current):
        import community_brief as cb
        with mock.patch.object(cb, "resolve_value", return_value=current), \
             mock.patch.object(cb, "write_field", return_value=(True, "ok")) as wf, \
             mock.patch("hubspot_client.get_company", return_value={}):
            res = portal_ticket_profile.apply_updates("cid", {"competitors": "Acme, Beta"})
        return res, wf

    def test_empty_profile_field_is_written(self):
        res, wf = self._run("")
        self.assertEqual([a["key"] for a in res["applied"]], ["competitors"])
        self.assertEqual(res["conflicts"], [])
        wf.assert_called_once()

    def test_same_value_is_noop(self):
        res, wf = self._run("Acme, Beta")
        self.assertEqual(res["applied"], [])
        self.assertEqual(res["conflicts"], [])
        wf.assert_not_called()

    def test_conflict_is_flagged_not_written(self):
        res, wf = self._run("Existing Comp Set")
        self.assertEqual(res["applied"], [])
        self.assertEqual(len(res["conflicts"]), 1)
        self.assertEqual(res["conflicts"][0]["current_value"], "Existing Comp Set")
        self.assertEqual(res["conflicts"][0]["ticket_value"], "Acme, Beta")
        wf.assert_not_called()

    def test_lifecycle_value_is_mapped(self):
        import community_brief as cb
        with mock.patch.object(cb, "resolve_value", return_value=""), \
             mock.patch.object(cb, "write_field", return_value=(True, "ok")) as wf, \
             mock.patch("hubspot_client.get_company", return_value={}):
            portal_ticket_profile.apply_updates("cid", {"lifecycle_state": "Lease Up"})
        self.assertEqual(wf.call_args[0][2], "lease_up")   # "Lease Up" → enum


class Guards(unittest.TestCase):
    def test_unknown_type_400(self):
        body, status = portal_tickets.create_ticket("cid", "nope", subject="x", fields={})
        self.assertEqual(status, 400)

    def test_unconfigured_type_503(self):
        body, status = portal_tickets.create_ticket("cid", "rebrand", subject="x", fields={})
        self.assertEqual(status, 503)   # no CLICKUP_LIST_REBRAND in this test env


if __name__ == "__main__":
    unittest.main()
