"""Round 4 reports: last full month by default, and a report for every property.

Offline: the property resolver, HubSpot and the listing query are mocked.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent / "webhook-server"))
sys.path.insert(0, str(TESTS))

from skills import workspace_report as wr  # noqa: E402
from skills.property_resolver import PropertyIdentity  # noqa: E402

TODAY = date(2026, 9, 14)
NO_HYLY = PropertyIdentity(company_id="21598594106", uuid="u-lyv", name="LYV Broadway", unit_count="390")


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    def _no_network(*a, **k):
        raise RuntimeError("network disabled in workspace tests")
    monkeypatch.setattr("requests.sessions.Session.request", _no_network)
    monkeypatch.delenv(wr.POLISH_FLAG, raising=False)
    from skills import property_resolver
    monkeypatch.setattr(property_resolver, "resolve", lambda cid: NO_HYLY)
    monkeypatch.setattr(wr, "_city_state", lambda cid: ("Carrollton", "TX"))
    monkeypatch.setenv("BIGQUERY_PROJECT_ID", "proj")
    monkeypatch.setenv("BIGQUERY_DATASET_PROD", "ds")


def _ils(sql, params):
    assert "apartmentscom_ils_resolved_v1" in sql
    assert ("start", "DATE", "2026-08-01") in params and ("end", "DATE", "2026-08-31") in params
    return [{"impressions": 12000, "leads": 34, "media_views": 900}]


class TestWithoutHyly:
    def test_defaults_to_the_last_full_month_and_fills_what_it_can(self):
        report = wr.build_report("21598594106", None, today=TODAY, ils_query=_ils)
        assert report["month"] == "2026-08" and report["month_label"] == "August 2026"
        assert report["property"]["name"] == "LYV Broadway" and report["property"]["city"] == "Carrollton"
        assert wr.val(report["property"]["units"]) == 390
        listing = report["listings"]
        assert listing, "the apartments.com listing section should be filled"
        reasons = " ".join(g.get("reason", "") for g in report["gaps"])
        for label in wr.HYLY_SECTIONS.values():
            assert f"{label} needs the Hyly reporting beta" in reasons
        assert "Leasing results for" in report["summary"]["text"]
        assert not wr.banned_phrases_in(report["summary"]["text"])

    def test_listings_missing_is_a_named_gap_too(self):
        report = wr.build_report("21598594106", "2026-08", today=TODAY, ils_query=lambda sql, p: [])
        assert any("No apartments.com rows" in g.get("reason", "") for g in report["gaps"])

    def test_future_and_invalid_months(self):
        with pytest.raises(wr.MonthUnavailable):
            wr.build_report("21598594106", "2026-09", today=TODAY, ils_query=_ils)
        with pytest.raises(wr.InvalidMonth):
            wr.build_report("21598594106", "2026-13", today=TODAY, ils_query=_ils)

    def test_resolve_still_refuses_for_callers_that_need_hyly(self):
        with pytest.raises(wr.PropertyUnavailable) as err:
            wr._resolve("21598594106")
        assert isinstance(err.value, wr.NotInHylyBeta) and err.value.identity is NO_HYLY

    def test_unknown_property_is_still_unavailable(self, monkeypatch):
        from skills import property_resolver

        def missing(cid):
            raise property_resolver.PropertyNotFound(cid)
        monkeypatch.setattr(property_resolver, "resolve", missing)
        with pytest.raises(wr.PropertyUnavailable) as err:
            wr.build_report("999", None, today=TODAY)
        assert not isinstance(err.value, wr.NotInHylyBeta)
