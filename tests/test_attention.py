"""Tests for the attention queue (Workstream C).

Offline. ClickUp, HubSpot and BigQuery are all mocked; nothing here touches a
live API. What is under test:

  * the aggregation composes every source and survives any one of them dying
  * open/closed classification agrees with the client-facing status map
  * a ClickUp task's property comes from the mapping table first and the task's
    OWN custom fields second — never from a space-wide field lookup
  * HubSpot Service Hub and ClickUp rows land in ONE list with ONE status
    vocabulary, and the same task arriving from two sources merges rather than
    duplicating
  * scoping cannot leak another property's work, and an unresolvable scope
    narrows to a 404 instead of widening to the portfolio
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

# webhook-server/ must win for `import config` — it holds the app config.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("HUBSPOT_API_KEY", "test-key")
os.environ.setdefault("CLICKUP_API_KEY", "test-key")
os.environ["CLICKUP_LIST_CREATIVE_AD_COPY"] = "901-creative"
os.environ["CLICKUP_LIST_REBRAND"] = "901-brand"
os.environ["CLICKUP_LIST_CAMPAIGN_REVIEW"] = "901-digital"
# Left unset so the "omit sources with no list id" path is exercised too.
for _unset in ("CLICKUP_LIST_SEO", "CLICKUP_LIST_PAID_MEDIA",
               "CLICKUP_LIST_SOCIAL", "CLICKUP_LIST_REPUTATION"):
    os.environ.pop(_unset, None)

from flask import Flask  # noqa: E402

import attention  # noqa: E402
from routes.attention import attention_bp  # noqa: E402

CID = "555000111"
UUID = "9900011122"
OTHER_CID = "777000222"
OTHER_UUID = "8800011122"


# ── fixtures ────────────────────────────────────────────────────────────────

def _task(task_id, name, status="open", status_type="open", list_key="creative",
          uuid_value=None, created="1700000000000"):
    t = {
        "id": task_id,
        "name": name,
        "status": {"status": status, "type": status_type},
        "date_created": created,
        "url": f"https://app.clickup.com/t/{task_id}",
        "assignees": [{"username": "Dana R."}],
        "priority": {"priority": "high"},
        "custom_fields": [],
    }
    if uuid_value:
        t["custom_fields"].append(
            {"id": "f_uuid", "name": "uuid", "type": "short_text", "value": uuid_value})
    return t


_LISTS = {
    "901-creative": [
        _task("cu_1", "New specials banner", uuid_value=UUID),
        _task("cu_2", "Photo refresh"),                       # unattributed
        _task("cu_3", "Old ask", status="complete", status_type="custom"),
    ],
    "901-brand": [_task("cu_4", "Rebrand signage", uuid_value=OTHER_UUID)],
    "901-digital": [_task("cu_5", "Q3 campaign review", uuid_value=UUID)],
}


def _fake_get_tasks(list_id, *, params=None):
    """Page 0 returns the list; every later page is empty (short-page stop)."""
    if (params or {}).get("page", 0) != 0:
        return []
    return list(_LISTS.get(list_id, []))


def _identity(company_id=CID, uuid=UUID, name="Atwood", market="Phoenix"):
    return mock.Mock(company_id=company_id, uuid=uuid, name=name, market=market)


class _Base(unittest.TestCase):
    def setUp(self):
        attention.invalidate_cache()
        self.addCleanup(attention.invalidate_cache)


# ── source registry ─────────────────────────────────────────────────────────

class Sources(_Base):
    def test_the_registry_is_identical_in_both_config_files(self):
        """There are two config.py files and which one wins depends on sys.path
        order. A registry present in only one is an ImportError in half the
        processes that import it — which is exactly how this test was earned."""
        import re
        here = os.path.dirname(__file__)
        blocks = []
        for path in (os.path.join(here, "..", "config.py"),
                     os.path.join(here, "..", "webhook-server", "config.py")):
            with open(path) as fh:
                body = fh.read()
            m = re.search(r"^ATTENTION_TICKET_LISTS = \[.*?^\]$", body, re.M | re.S)
            self.assertIsNotNone(m, f"ATTENTION_TICKET_LISTS missing from {path}")
            blocks.append(m.group(0))
        self.assertEqual(blocks[0], blocks[1])

    def test_only_lists_with_an_id_are_watched(self):
        keys = [s["key"] for s in attention.sources()]
        self.assertEqual(keys, ["creative_ad_copy", "rebrand", "campaign_review"])

    def test_every_named_bucket_is_covered(self):
        cats = {s["category"] for s in attention.sources()}
        self.assertEqual(cats, {"creative", "branding", "digital"})

    def test_env_overrides_the_verified_default_list_id(self):
        with mock.patch.dict(os.environ, {"CLICKUP_LIST_CREATIVE_AD_COPY": "from-env"}):
            by_key = {s["key"]: s["list_id"] for s in attention.sources()}
        self.assertEqual(by_key["creative_ad_copy"], "from-env")


# ── open/closed classification ──────────────────────────────────────────────

class OpenClassification(_Base):
    def test_a_clickup_closed_type_is_not_open(self):
        self.assertFalse(attention._is_open(
            _task("x", "done", status="whatever", status_type="closed")))

    def test_a_custom_status_the_client_map_calls_done_is_not_open(self):
        """'complete' has type `custom` on these lists — only the status map knows."""
        self.assertFalse(attention._is_open(
            _task("x", "done", status="complete", status_type="custom")))

    def test_a_normal_open_task_is_open(self):
        self.assertTrue(attention._is_open(_task("x", "todo", status="to do")))


# ── property attribution ────────────────────────────────────────────────────

class Attribution(_Base):
    def test_the_mapping_table_wins_over_the_task_field(self):
        task = _task("cu_1", "n", uuid_value="task-field-uuid")
        row = attention._row_from_task(
            task, {"key": "k", "label": "L", "category": "creative"},
            {"company_id": CID, "property_uuid": UUID, "submitted_by": "a@rpmliving.com"})
        self.assertEqual(row["uuid"], UUID)
        self.assertEqual(row["company_id"], CID)
        self.assertEqual(row["submitted_by"], "a@rpmliving.com")

    def test_the_tasks_own_field_is_the_fallback(self):
        row = attention._row_from_task(
            _task("cu_1", "n", uuid_value=UUID),
            {"key": "k", "label": "L", "category": "creative"}, None)
        self.assertEqual(row["uuid"], UUID)

    def test_attribution_never_consults_the_space_wide_field_endpoint(self):
        """ClickUp custom fields are space-level: GET /list/{id}/field returns the
        union for the whole space, so it cannot prove this list carries `uuid`."""
        with mock.patch("clickup_client.get_list_fields") as fields, \
             mock.patch("clickup_client.get_tasks", side_effect=_fake_get_tasks), \
             mock.patch("portal_tickets.mappings_for_tasks", return_value={}):
            attention.scan_open_tickets(force=True)
        fields.assert_not_called()

    def test_an_unattributable_task_still_appears_on_the_queue(self):
        with mock.patch("clickup_client.get_tasks", side_effect=_fake_get_tasks), \
             mock.patch("portal_tickets.mappings_for_tasks", return_value={}):
            rows = attention.scan_open_tickets(force=True)["rows"]
        blank = [r for r in rows if r["id"] == "cu_2"]
        self.assertEqual(len(blank), 1)
        self.assertEqual(blank[0]["uuid"], "")


# ── the list scan ───────────────────────────────────────────────────────────

class Scan(_Base):
    def _scan(self, mappings=None):
        with mock.patch("clickup_client.get_tasks", side_effect=_fake_get_tasks), \
             mock.patch("portal_tickets.mappings_for_tasks",
                        return_value=mappings or {}):
            return attention.scan_open_tickets(force=True)

    def test_closed_tasks_are_dropped_and_open_ones_kept(self):
        ids = {r["id"] for r in self._scan()["rows"]}
        self.assertEqual(ids, {"cu_1", "cu_2", "cu_4", "cu_5"})

    def test_rows_carry_the_bucket_of_the_list_they_came_from(self):
        cats = {r["id"]: r["category"] for r in self._scan()["rows"]}
        self.assertEqual(cats["cu_1"], "creative")
        self.assertEqual(cats["cu_4"], "branding")
        self.assertEqual(cats["cu_5"], "digital")

    def test_one_dead_list_degrades_that_source_only(self):
        def flaky(list_id, *, params=None):
            if list_id == "901-brand":
                raise RuntimeError("ClickUp 500")
            return _fake_get_tasks(list_id, params=params)

        with mock.patch("clickup_client.get_tasks", side_effect=flaky), \
             mock.patch("portal_tickets.mappings_for_tasks", return_value={}):
            out = attention.scan_open_tickets(force=True)
        self.assertIn("rebrand", out["degraded"])
        self.assertTrue(any(r["id"] == "cu_1" for r in out["rows"]))

    def test_the_mapping_table_is_read_once_for_the_whole_queue(self):
        with mock.patch("clickup_client.get_tasks", side_effect=_fake_get_tasks), \
             mock.patch("portal_tickets.mappings_for_tasks",
                        return_value={}) as bulk:
            attention.scan_open_tickets(force=True)
        self.assertEqual(bulk.call_count, 1)

    def test_a_lost_mapping_read_degrades_attribution_not_the_queue(self):
        with mock.patch("clickup_client.get_tasks", side_effect=_fake_get_tasks), \
             mock.patch("portal_tickets.mappings_for_tasks",
                        side_effect=RuntimeError("BQ down")):
            out = attention.scan_open_tickets(force=True)
        self.assertIn("ticket_mapping", out["degraded"])
        self.assertEqual(len(out["rows"]), 4)

    def test_the_scan_is_cached_between_calls(self):
        with mock.patch("clickup_client.get_tasks", side_effect=_fake_get_tasks) as g, \
             mock.patch("portal_tickets.mappings_for_tasks", return_value={}):
            attention.scan_open_tickets(force=True)
            first = g.call_count
            attention.scan_open_tickets()
        self.assertEqual(g.call_count, first)


# ── unified ticket view ─────────────────────────────────────────────────────

_HS_TICKETS = [
    {"id": "hs_1", "subject": "Landing page copy", "stage_label": "New",
     "priority": "HIGH", "owner_name": "Dana R.", "submitter_email": "pm@rpmliving.com",
     "created_at": "2026-08-01T12:00:00Z"},
    {"id": "hs_2", "subject": "Budget question", "stage_label": "In Progress",
     "priority": "MEDIUM", "owner_name": "", "submitter_email": "",
     "created_at": "2026-08-02T12:00:00Z"},
]

_PORTAL_TICKETS = [
    {"id": "cu_1", "type": "creative_ad_copy", "type_label": "Ad Updates",
     "subject": "New specials banner", "submitted_by": "pm@rpmliving.com",
     "status": "In progress", "raw_status": "in progress",
     "created_ts": 1700000000000, "age_days": 3, "url": "u", "unresolved": False},
    {"id": "cu_9", "type": "general", "type_label": "General Ticket",
     "subject": "Request", "submitted_by": "pm@rpmliving.com",
     "status": "Done", "raw_status": "complete",
     "created_ts": 1690000000000, "age_days": 90, "url": "u", "unresolved": False},
]


class UnifiedView(_Base):
    def _tickets(self, **kw):
        with mock.patch("clickup_client.get_tasks", side_effect=_fake_get_tasks), \
             mock.patch("portal_tickets.mappings_for_tasks", return_value={}), \
             mock.patch("portal_tickets.list_tickets", return_value=_PORTAL_TICKETS), \
             mock.patch.dict(sys.modules, {"ticket_manager": mock.Mock(
                 list_tickets=mock.Mock(return_value=_HS_TICKETS))}):
            return attention.property_tickets(company_id=CID, property_uuid=UUID, **kw)

    def test_both_systems_land_in_one_list(self):
        systems = {r["system"] for r in self._tickets()["rows"]}
        self.assertEqual(systems, {"hubspot", "clickup"})

    def test_hubspot_stages_speak_the_clickup_status_vocabulary(self):
        by_id = {r["id"]: r for r in self._tickets()["rows"]}
        self.assertEqual(by_id["hs_1"]["status"], "Open")        # "New" → Open
        self.assertEqual(by_id["hs_2"]["status"], "In progress")

    def test_closed_portal_tickets_are_excluded_by_default(self):
        ids = {r["id"] for r in self._tickets()["rows"]}
        self.assertNotIn("cu_9", ids)
        self.assertIn("cu_9", {r["id"] for r in self._tickets(include_closed=True)["rows"]})

    def test_the_same_task_from_two_sources_appears_once_and_merges(self):
        """cu_1 arrives from both the list scan and the portal mapping."""
        rows = [r for r in self._tickets()["rows"] if r["id"] == "cu_1"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["submitted_by"], "pm@rpmliving.com")   # portal side
        self.assertEqual(rows[0]["assignees"], ["Dana R."])            # scan side

    def test_another_propertys_ticket_never_appears(self):
        ids = {r["id"] for r in self._tickets()["rows"]}
        self.assertNotIn("cu_4", ids)      # belongs to OTHER_UUID

    def test_hubspot_down_still_returns_the_clickup_work(self):
        with mock.patch("clickup_client.get_tasks", side_effect=_fake_get_tasks), \
             mock.patch("portal_tickets.mappings_for_tasks", return_value={}), \
             mock.patch("portal_tickets.list_tickets", return_value=_PORTAL_TICKETS), \
             mock.patch.dict(sys.modules, {"ticket_manager": mock.Mock(
                 list_tickets=mock.Mock(side_effect=RuntimeError("HubSpot 500")))}):
            out = attention.property_tickets(company_id=CID, property_uuid=UUID)
        self.assertIn("service_hub", out["degraded"])
        self.assertTrue(any(r["system"] == "clickup" for r in out["rows"]))

    def test_an_unresolvable_ticket_keeps_its_placeholder_flag(self):
        ghost = [{"id": "cu_ghost", "type": "general", "type_label": "General Ticket",
                  "subject": "Request · ghost", "submitted_by": "pm@rpmliving.com",
                  "status": "Status unavailable", "raw_status": "",
                  "created_ts": 1700000000000, "age_days": 3, "url": None,
                  "unresolved": True}]
        with mock.patch("clickup_client.get_tasks", side_effect=_fake_get_tasks), \
             mock.patch("portal_tickets.mappings_for_tasks", return_value={}), \
             mock.patch("portal_tickets.list_tickets", return_value=ghost), \
             mock.patch.dict(sys.modules, {"ticket_manager": mock.Mock(
                 list_tickets=mock.Mock(return_value=[]))}):
            rows = attention.property_tickets(company_id=CID, property_uuid=UUID)["rows"]
        self.assertTrue([r for r in rows if r["id"] == "cu_ghost"][0]["unresolved"])


# ── the aggregate ───────────────────────────────────────────────────────────

_ONBOARDING = [{"company_id": CID, "uuid": UUID, "name": "Atwood", "market": "Phoenix",
                "checklist": {}, "done": 2, "total": 4},
               {"company_id": OTHER_CID, "uuid": OTHER_UUID, "name": "Henry",
                "market": "Austin", "checklist": {}, "done": 1, "total": 4}]
_DISPOS = [{"company_id": OTHER_CID, "uuid": OTHER_UUID, "name": "Henry",
            "market": "Austin", "retained": False}]
_TRIAGE = {"rows": [
    {"property_id": CID, "uuid": UUID, "name": "Atwood", "severity": "critical",
     "reason": "Open ticket aged 7 days", "reason_kind": "ticket_aging", "age_days": 7},
    {"property_id": OTHER_CID, "uuid": OTHER_UUID, "name": "Henry", "severity": "warning",
     "reason": "Health score 61 · watch", "reason_kind": "health", "age_days": 0},
]}


def _patched_build(**over):
    """Patch every source `attention.build` composes. Values are overridable."""
    mods = {
        "onboarding": mock.Mock(list_onboarding=mock.Mock(
            return_value=over.get("onboarding", _ONBOARDING))),
        "disposition": mock.Mock(list_dispositioning=mock.Mock(
            return_value=over.get("dispositions", _DISPOS))),
        "triage": mock.Mock(get_portfolio_triage=mock.Mock(
            return_value=over.get("triage", _TRIAGE))),
        "ticket_manager": mock.Mock(list_tickets=mock.Mock(
            return_value=over.get("hubspot", _HS_TICKETS))),
    }
    return mods


class Aggregate(_Base):
    def _build(self, scope="", **over):
        with mock.patch("clickup_client.get_tasks", side_effect=_fake_get_tasks), \
             mock.patch("portal_tickets.mappings_for_tasks", return_value={}), \
             mock.patch("portal_tickets.list_tickets", return_value=[]), \
             mock.patch("attention._enrich_names"), \
             mock.patch.dict(sys.modules, _patched_build(**over)):
            return attention.build(scope_identifier=scope)

    def test_the_legacy_response_keys_are_unchanged(self):
        out = self._build()
        for key in ("onboarding", "dispositions", "attention"):
            self.assertIn(key, out)
        self.assertEqual(len(out["onboarding"]), 2)
        self.assertEqual(len(out["dispositions"]), 1)

    def test_health_rows_are_still_excluded_from_attention(self):
        kinds = {r["reason_kind"] for r in self._build()["attention"]}
        self.assertEqual(kinds, {"ticket_aging"})

    def test_open_creative_branding_and_digital_work_is_included(self):
        work = self._build()["work"]
        self.assertEqual(work["by_category"],
                         {"creative": 2, "branding": 1, "digital": 1})
        self.assertEqual(work["total"], 4)

    def test_a_dead_source_degrades_by_name_and_the_rest_still_render(self):
        mods = _patched_build()
        mods["onboarding"].list_onboarding.side_effect = RuntimeError("HubSpot 500")
        with mock.patch("clickup_client.get_tasks", side_effect=_fake_get_tasks), \
             mock.patch("portal_tickets.mappings_for_tasks", return_value={}), \
             mock.patch("portal_tickets.list_tickets", return_value=[]), \
             mock.patch("attention._enrich_names"), \
             mock.patch.dict(sys.modules, mods):
            out = attention.build()
        self.assertIn("onboarding", out["degraded"])
        self.assertEqual(out["onboarding"], [])
        self.assertEqual(len(out["dispositions"]), 1)
        self.assertEqual(out["work"]["total"], 4)

    def test_scoping_filters_every_section_to_that_property(self):
        with mock.patch("attention.resolve_scope",
                        return_value={"company_id": CID, "uuid": UUID,
                                      "name": "Atwood", "market": "Phoenix"}):
            out = self._build(scope=CID)
        self.assertEqual([r["uuid"] for r in out["onboarding"]], [UUID])
        self.assertEqual(out["dispositions"], [])
        self.assertEqual([r["property_id"] for r in out["attention"]], [CID])

    def test_scoping_drops_work_belonging_to_another_property(self):
        with mock.patch("attention.resolve_scope",
                        return_value={"company_id": CID, "uuid": UUID,
                                      "name": "Atwood", "market": "Phoenix"}):
            out = self._build(scope=CID)
        ids = {r["id"] for r in out["work"]["rows"]}
        self.assertNotIn("cu_4", ids)      # OTHER_UUID's rebrand
        self.assertIn("cu_1", ids)

    def test_scoping_drops_unattributed_work_rather_than_assuming_ownership(self):
        with mock.patch("attention.resolve_scope",
                        return_value={"company_id": CID, "uuid": UUID,
                                      "name": "Atwood", "market": "Phoenix"}):
            out = self._build(scope=CID)
        self.assertNotIn("cu_2", {r["id"] for r in out["work"]["rows"]})

    def test_an_unresolvable_scope_raises_instead_of_widening(self):
        with mock.patch("attention.resolve_scope", return_value=None):
            with self.assertRaises(attention.ScopeUnresolved):
                self._build(scope="not-a-property")

    def test_the_cached_scan_is_not_mutated_by_a_scoped_request(self):
        with mock.patch("attention.resolve_scope",
                        return_value={"company_id": CID, "uuid": UUID,
                                      "name": "Atwood", "market": "Phoenix"}):
            self._build(scope=CID)
        with mock.patch("clickup_client.get_tasks", side_effect=_fake_get_tasks), \
             mock.patch("portal_tickets.mappings_for_tasks", return_value={}):
            cached = attention.scan_open_tickets()
        self.assertTrue(all(r["property_name"] == "" for r in cached["rows"]))


# ── HTTP surface ────────────────────────────────────────────────────────────

def _client():
    app = Flask(__name__)
    app.register_blueprint(attention_bp)
    return app.test_client()


_EMPTY = {"onboarding": [], "dispositions": [], "attention": [],
          "work": {"rows": [], "total": 0, "by_category": {}, "sources": []},
          "summary": {}, "degraded": [], "scope": None, "generated_at": "now"}


class Routes(_Base):
    def setUp(self):
        super().setUp()
        self.c = _client()
        self.h = {"X-Portal-Email": "kyle@rpmliving.com"}

    def test_every_endpoint_requires_a_signed_in_portal_user(self):
        for path in ("/api/attention", "/api/attention/tickets", "/api/needs-you"):
            self.assertEqual(self.c.get(path).status_code, 401, path)

    def test_needs_you_still_returns_its_original_keys(self):
        with mock.patch("attention.build", return_value=_EMPTY):
            body = self.c.get("/api/needs-you", headers=self.h).get_json()
        for key in ("onboarding", "dispositions", "attention"):
            self.assertIn(key, body)
        self.assertIn("work", body)

    def test_needs_you_is_never_scoped_from_the_query_string(self):
        """It is the whole-team queue; a stray ?company_id= must not narrow it."""
        with mock.patch("attention.build", return_value=_EMPTY) as build:
            self.c.get(f"/api/needs-you?company_id={OTHER_CID}", headers=self.h)
        build.assert_called_once_with()

    def test_attention_passes_either_identifier_through_as_the_scope(self):
        with mock.patch("attention.build", return_value=_EMPTY) as build:
            self.c.get(f"/api/attention?company_id={CID}", headers=self.h)
            self.assertEqual(build.call_args.kwargs["scope_identifier"], CID)
            self.c.get(f"/api/attention?uuid={UUID}", headers=self.h)
            self.assertEqual(build.call_args.kwargs["scope_identifier"], UUID)

    def test_an_unresolvable_scope_404s_rather_than_serving_the_portfolio(self):
        with mock.patch("attention.build",
                        side_effect=attention.ScopeUnresolved("nope")):
            r = self.c.get("/api/attention?company_id=nope", headers=self.h)
        self.assertEqual(r.status_code, 404)

    def test_the_ticket_view_requires_a_scope(self):
        r = self.c.get("/api/attention/tickets", headers=self.h)
        self.assertEqual(r.status_code, 400)

    def test_the_ticket_view_404s_on_an_unknown_property(self):
        with mock.patch("attention.resolve_scope", return_value=None):
            r = self.c.get("/api/attention/tickets?company_id=nope", headers=self.h)
        self.assertEqual(r.status_code, 404)

    def test_the_ticket_view_scopes_by_the_resolved_identity_not_the_raw_input(self):
        ident = {"company_id": CID, "uuid": UUID, "name": "Atwood", "market": "Phoenix"}
        with mock.patch("attention.resolve_scope", return_value=ident), \
             mock.patch("attention.property_tickets",
                        return_value={"rows": [], "degraded": [], "count": 0}) as pt:
            body = self.c.get(f"/api/attention/tickets?uuid={UUID}",
                              headers=self.h).get_json()
        self.assertEqual(pt.call_args.kwargs["company_id"], CID)
        self.assertEqual(pt.call_args.kwargs["property_uuid"], UUID)
        self.assertEqual(body["property"], ident)

    def test_the_workstream_a_hook_is_marked_on_every_scoped_handler(self):
        """The auth gap is real and known — the marker is how A finds it."""
        import routes.attention as mod
        with open(mod.__file__) as fh:
            src = fh.read()
        # One per scoped handler: /api/attention and /api/attention/tickets.
        # /api/needs-you is portfolio-wide and takes no scope, so it has none.
        self.assertEqual(
            src.count("TODO(workstream-A): wrap in require_company_access"), 2)


if __name__ == "__main__":
    unittest.main()
