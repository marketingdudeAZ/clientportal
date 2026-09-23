"""The Properties screen's Market filter.

Market is `rpmmarket` on the HubSpot company record — set across the portfolio,
where the bare `market` field is set on a handful of properties. The server
filters, so the KPI strip and the ranking describe the chosen market instead of
the whole book with some rows hidden. The options always come from the
unfiltered scope: choosing Phoenix must not make Dallas disappear from the list.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent / "webhook-server"))
sys.path.insert(0, str(TESTS))

from skills import workspace_cache as wcache  # noqa: E402
from skills import workspace_dashboard as wdash  # noqa: E402
from skills import workspace_inbox as wi  # noqa: E402
from skills import workspace_portfolio as wp  # noqa: E402
from skills import workspace_scope as wscope  # noqa: E402

TODAY = date(2026, 9, 22)
PROPS = [
    {"hubspot_company_id": "1", "name": "Alder", "totalunits": "100", "rpmmarket": "Phoenix"},
    {"hubspot_company_id": "2", "name": "Birch", "totalunits": "200", "rpmmarket": "Dallas"},
    {"hubspot_company_id": "3", "name": "Cedar", "totalunits": "300", "rpmmarket": " phoenix "},
    {"hubspot_company_id": "4", "name": "Dogwood", "totalunits": "400", "rpmmarket": ""},
]


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    def _no_network(*a, **k):
        raise RuntimeError("network disabled in workspace tests")
    monkeypatch.setattr("requests.sessions.Session.request", _no_network)
    wcache.clear()


class TestHelpers:
    def test_markets_are_distinct_sorted_and_skip_blanks(self):
        """" phoenix " on Cedar is the same market as Alder's "Phoenix"."""
        assert wscope.markets_in(PROPS) == ["Dallas", "Phoenix"]

    def test_matching_ignores_case_and_whitespace(self):
        assert [p["name"] for p in wscope.in_market(PROPS, "PHOENIX")] == ["Alder", "Cedar"]

    def test_blank_means_every_property(self):
        assert wscope.in_market(PROPS, None) == PROPS and wscope.in_market(PROPS, " ") == PROPS

    def test_the_client_scope_reads_the_field(self):
        assert "rpmmarket" in wscope.SCOPE_FIELDS


class TestAllProperties:
    @pytest.fixture(autouse=True)
    def _portfolio(self, monkeypatch):
        monkeypatch.setattr(wp, "managed_properties", lambda: PROPS)
        monkeypatch.setattr(wcache, "aptiq_daily", lambda: ({}, None))
        monkeypatch.setattr(wi, "load_context", lambda cid: wi.PropertyContext(cid, "", "", {}))

    def test_filters_to_the_market_and_counts_only_it(self):
        body = wp.build_portfolio("dana@rpmliving.com", view="all", market="Phoenix", today=TODAY)
        assert {r["name"] for r in body["properties"]} == {"Alder", "Cedar"}
        assert body["property_count"] == 2 and body["market"] == "Phoenix"

    def test_options_come_from_the_whole_portfolio(self):
        body = wp.build_portfolio("dana@rpmliving.com", view="all", market="Dallas", today=TODAY)
        assert "Phoenix" in body["markets"] and "Dallas" in body["markets"]

    def test_rows_carry_their_market(self):
        body = wp.build_portfolio("dana@rpmliving.com", view="all", today=TODAY)
        by_name = {r["name"]: r["market"] for r in body["properties"]}
        assert by_name["Birch"] == "Dallas" and by_name["Dogwood"] is None
        assert body["property_count"] == 4 and body["market"] is None


class TestAssignedToMe:
    def test_kpis_describe_the_chosen_market(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(wscope, "properties_in_scope", lambda email, internal: {
            "properties": PROPS, "label": "x", "gaps": []})
        monkeypatch.setattr(wcache, "aptiq_daily", lambda: ({}, None))

        def _leasing(props, today, gaps):
            seen["names"] = [p["name"] for p in props]
            return None, None, {}
        monkeypatch.setattr(wdash, "leasing_kpis", _leasing)
        monkeypatch.setattr(wdash, "scope_items", lambda *a, **k: [])
        monkeypatch.setattr(wdash, "greeting_name", lambda email, gaps: None)
        monkeypatch.setattr(wdash, "activity_rows", lambda *a: ([], None))
        monkeypatch.setattr(wdash, "visibility_kpi", lambda *a: None)
        monkeypatch.setattr(wdash, "actions_taken_kpi", lambda *a: None)
        body = wdash.build_dashboard("dana@rpmliving.com", internal=True, today=TODAY, market="dallas")
        assert seen["names"] == ["Birch"]
        assert [r["name"] for r in body["properties"]] == ["Birch"]
        assert body["loop_status"]["property_count"] == 1
        assert body["markets"] == ["Dallas", "Phoenix"]
