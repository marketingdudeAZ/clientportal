"""Round 4 dashboard KPIs: leases, cost per lease and actions we took for you.

Offline; the Hyly lake, BigQuery, AptIQ and the spend sheet are mocked.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent / "webhook-server"))
sys.path.insert(0, str(TESTS))

from skills import workspace_cache as wcache  # noqa: E402
from skills import workspace_dashboard as wdash  # noqa: E402
from skills import workspace_history as whist  # noqa: E402
from skills import workspace_leasing as wlease  # noqa: E402

TODAY = date(2026, 9, 14)
PROPS = [
    {"hubspot_company_id": "1", "name": "A", "totalunits": "100", "uuid": "u1", "hyly_property_id": "111"},
    {"hubspot_company_id": "2", "name": "B", "totalunits": "200", "uuid": "u2", "hyly_property_id": "222"},
    {"hubspot_company_id": "3", "name": "C", "totalunits": "300", "uuid": "u3", "hyly_property_id": ""},
]


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    def _no_network(*a, **k):
        raise RuntimeError("network disabled in workspace tests")
    monkeypatch.setattr("requests.sessions.Session.request", _no_network)
    wcache.clear()


class _Lake:
    lease_object = "pai_journey_x"

    def __init__(self, rows_by_start):
        self.rows_by_start = rows_by_start
        self.calls = []

    def ref(self, obj):
        return f"`lake.{obj}`"

    def query(self, sql, params):
        self.calls.append((sql, params))
        start = dict((n, v) for n, _, v in params)["start"]
        return self.rows_by_start[start]


class TestLeasing:
    def test_hyly_ids_skip_properties_outside_the_beta(self):
        assert wlease.hyly_ids(PROPS) == {"1": 111, "2": 222}

    def test_counts_by_property_with_validated_ids(self):
        lake = _Lake({"2026-09-01": [{"property_id": 111, "value": 4}]})
        out = wlease.leases_by_property([222, 111], "2026-09-01", "2026-09-14", lake=lake)
        assert out == {111: 4, 222: 0}
        sql = lake.calls[0][0]
        assert "IN (111, 222)" in sql and "h_ms_lease" in sql

    def test_kpis_cover_hyly_properties_and_say_so(self, monkeypatch):
        by_start = {"2026-09-01": {111: 3, 222: 5}, "2026-08-01": {111: 4, 222: 0}}
        monkeypatch.setattr(wlease, "leases_by_property", lambda pids, start, end, lake=None: by_start[start])
        spend = {"1": 2000.0, "2": 3000.0}
        monkeypatch.setattr(wcache, "monthly_spend", lambda cid: ({"total": spend.get(cid, 0.0)}, None))
        gaps = []
        leases, cost, by_company = wdash.leasing_kpis(PROPS, TODAY, gaps)
        assert by_company == {"1": 3, "2": 5}
        assert leases["value"] == 8 and leases["source"] == "hyly_lake.pai_journey"
        assert leases["properties"] == 2 and leases["period"] == "2026-09-01 to 2026-09-14"
        # property 2 had no August leases, so only property 1's spend and leases count
        assert cost["value"] == 500.0 and cost["spend"] == 2000.0 and cost["leases"] == 4
        assert cost["period"] == "2026-08" and cost["properties"] == 1
        messages = " ".join(g["message"] for g in gaps)
        assert "2 of 3 properties" in messages and "contracted" in messages

    def test_no_bigquery_is_a_gap_not_zero(self, monkeypatch):
        monkeypatch.setattr(wlease, "leases_by_property", lambda *a, **k: None)
        gaps = []
        assert wdash.leasing_kpis(PROPS, TODAY, gaps) == (None, None, {})
        assert {g["field"] for g in gaps} == {"kpis.leases_this_month", "kpis.cost_per_lease"}


class TestActionsTaken:
    def test_counts_autopilot_and_clean_fair_housing_reviews_only(self):
        rows = [
            {"event_type": "recommendation_approved", "source": "loop_autopilot", "payload": "{}"},
            {"event_type": "recommendation_approved", "source": "client_action", "payload": "{}"},
            {"event_type": "workspace_fair_housing_review", "payload": json.dumps({"findings": []})},
            {"event_type": "workspace_fair_housing_review", "payload": json.dumps({"findings": [{"kind": "copy"}]})},
        ]
        out = whist.count_automatic(rows)
        assert (out["total"], out["autopilot_approvals"], out["fair_housing_reviews_clean"]) == (2, 1, 1)

    def test_kpi_names_what_was_counted(self, monkeypatch):
        monkeypatch.setattr(whist, "automatic_actions", lambda cids, uuids, since: {
            "total": 3, "autopilot_approvals": 1, "fair_housing_reviews_clean": 2, "events": []})
        kpi = wdash.actions_taken_kpi(PROPS, TODAY, [])
        assert kpi["value"] == 3 and kpi["window_days"] == 30
        assert "loop_autopilot" in kpi["source"] and "Fair Housing" in kpi["source"]
        assert kpi["counted"] == {"autopilot_approvals": 1, "fair_housing_reviews_clean": 2}

    def test_without_bigquery_it_is_null_with_a_gap(self, monkeypatch):
        monkeypatch.setattr(whist, "automatic_actions", lambda *a: None)
        gaps = []
        assert wdash.actions_taken_kpi(PROPS, TODAY, gaps) is None
        assert gaps[0]["field"] == "kpis.actions_taken"

    def test_clean_review_reads_as_an_action_not_an_approval(self, monkeypatch):
        monkeypatch.setattr(whist, "recent_events", lambda uuids, **kw: [
            {"event_type": "workspace_fair_housing_review", "property_uuid": "u1",
             "occurred_at": "2026-09-10T00:00:00Z", "payload": {"findings": []}}])
        rows, _ = wdash.activity_rows(PROPS, False, [])
        assert rows == [{"at": "2026-09-10T00:00:00Z", "text": "Monthly Fair Housing review — no issues — A",
                         "company_id": "1", "kind": "check", "visibility": "client"}]


class TestPropertyRows:
    def test_occupancy_leases_and_status_replace_overspend(self, monkeypatch):
        from skills import workspace_scope as wscope
        props = [dict(PROPS[0], aptiq_property_id="ap1", plestatus="RPM Managed", red_light_report_score="80"),
                 dict(PROPS[2], aptiq_property_id="", plestatus="Onboarding")]
        monkeypatch.setattr(wscope, "properties_in_scope", lambda email, internal: {
            "properties": props, "label": "x", "gaps": []})
        monkeypatch.setattr(wcache, "aptiq_daily", lambda: ({"ap1": {
            "Advertised Occupancy %": "93", "Exposure % (Next 90d)": "10",
            "Report Generation Date": "09/13/2026"}}, None))
        monkeypatch.setattr(wdash, "leasing_kpis", lambda p, today, gaps: (None, None, {"1": 6}))
        monkeypatch.setattr(wdash, "scope_items", lambda *a, **k: [])
        monkeypatch.setattr(wdash, "greeting_name", lambda email, gaps: None)
        monkeypatch.setattr(wdash, "activity_rows", lambda *a: ([], None))
        monkeypatch.setattr(wdash, "visibility_kpi", lambda *a: None)
        monkeypatch.setattr(wdash, "actions_taken_kpi", lambda *a: None)
        body = wdash.build_dashboard("dana@rpmliving.com", internal=True, today=TODAY)
        rows = {r["company_id"]: r for r in body["properties"]}
        assert rows["1"]["occupancy"]["value"] == 0.93 and rows["1"]["leases_month"]["value"] == 6
        assert rows["1"]["status"] == "RPM Managed" and rows["3"]["status"] == "Onboarding"
        assert rows["3"]["occupancy"] is None and rows["3"]["leases_month"] is None
        assert all("overspend_per_year" not in r for r in body["properties"])
        assert any(g.get("field") == "properties.leases_month" for g in body["gaps"])
