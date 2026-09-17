"""Tests for skills.rpmi_roster and the paging fix underneath it.

The roster is one query away from being wrong in three ways that all look like
a smaller portfolio rather than a bug: filtering one `client` spelling drops a
third of it, HubSpot's unsent `limit` truncates it to ten, and counting HubSpot
records as properties double-counts eleven of them. Each has a test here.

Offline: `hubspot_client` is stubbed, except the paging test, which drives the
real `search_companies` against a fake transport so that the fix is what is
under test rather than a mock of it.
"""

from __future__ import annotations

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

from skills import rpmi_roster as rr  # noqa: E402


# ── fixture data ─────────────────────────────────────────────────────────────
# Shaped like the live records: both spellings, the Hampton Lakes / Lakeside
# pair that share one site under different owners, a Volume-style pair, a
# dispositioning record, and a record with no totalunits.

def _co(cid, name, client, site, *, plestatus="RPM Managed", uuid=None,
        units="300", market="Dallas", region="RPMI", city="Dallas", state="TX",
        hyly=None, aptiq=None, ga4=None, ads=None, ninjacat=None, domain=None):
    return {
        "id": cid,
        "properties": {
            "name": name, "client": client, "plestatus": plestatus,
            "uuid": uuid if uuid is not None else f"u-{cid}",
            "website": site, "domain": domain if domain is not None else site,
            "rpmmarket": market, "rpmregion": region, "city": city, "state": state,
            "totalunits": units,
            "hyly_property_id": hyly, "aptiq_property_id": aptiq,
            "ga4_property_id": ga4, "google_ads_customer_id": ads,
            "ninjacat_system_id": ninjacat,
        },
    }


RECORDS = {
    # 3 records under the "RPMI" spelling.
    "RPMI": [
        _co("1", "Hampton Lakes", "RPMI", "https://www.livehamptonlakesapts.com/",
            units="668", market="Miami", city="Miami", state="FL",
            hyly="h-1", aptiq="a-1", ga4="g-1", ads="ads-1", ninjacat="nc-1"),
        _co("2", "Volume 1", "RPMI", "volumeapartments.com", units="120",
            aptiq="a-2", ga4="g-2"),
        _co("3", "The Brighton Garden Oaks", "RPMI", "brightongardenoaks.com",
            plestatus="", units="210"),
    ],
    # 3 under "RPM Investments" — including the same site as Hampton Lakes.
    "RPM Investments": [
        _co("4", "Lakeside", "RPM Investments", "livehamptonlakesapts.com",
            units="668", market="Miami", city="Miami", state="FL", uuid=""),
        _co("5", "Volume 2", "RPM Investments", "www.volumeapartments.com",
            units="140", ninjacat="nc-5"),
        _co("6", "Palm Vista", "RPM Investments", "palmvista.com",
            plestatus="Dispositioning", units="180"),
        # No totalunits: units must be None with a gap, never 0.
        _co("7", "The Louis Las Colinas", "RPM Investments", "thelouislascolinas.com",
            units=None, state=None, aptiq="a-7"),
    ],
}


@pytest.fixture
def hubspot(monkeypatch):
    """Stub `hubspot_client.search_companies`, keyed on the `client` filter."""
    seen = []

    def search_companies(filters, properties=None, limit=None):
        value = filters[0]["value"]
        seen.append(value)
        return list(RECORDS.get(value, []))

    monkeypatch.setitem(sys.modules, "hubspot_client", types.SimpleNamespace(
        search_companies=search_companies))
    rr.clear_cache()
    yield seen
    rr.clear_cache()


# ── both spellings ───────────────────────────────────────────────────────────


class TestBothSpellings:
    """`client` holds two values for one owner. Either one alone is a wrong
    answer that looks like a complete one."""

    def test_queries_both_and_merges(self, hubspot):
        rr.get_roster(include_unmanaged=True)
        assert hubspot == ["RPMI", "RPM Investments"]

    def test_summary_keeps_the_per_spelling_split_visible(self, hubspot):
        summary = rr.coverage_summary(include_unmanaged=True)
        assert summary["records_by_client"] == {"RPMI": 3, "RPM Investments": 4}

    def test_dropping_one_spelling_would_lose_records(self, hubspot):
        """The guard this module exists for: RPM Investments alone is 4 of 7."""
        summary = rr.coverage_summary(include_unmanaged=True)
        assert summary["records"] == 7
        assert summary["records_by_client"]["RPM Investments"] < summary["records"]


# ── managed filter ───────────────────────────────────────────────────────────


class TestUnmanagedExcluded:
    def test_excluded_by_default(self, hubspot):
        names = {r["name"] for r in rr.get_roster()}
        assert "Palm Vista" not in names               # Dispositioning
        assert "The Brighton Garden Oaks" not in names  # blank plestatus
        assert "Hampton Lakes" in names

    def test_included_on_request(self, hubspot):
        names = {r["name"] for r in rr.get_roster(include_unmanaged=True)}
        assert {"Palm Vista", "The Brighton Garden Oaks"} <= names

    def test_managed_count(self, hubspot):
        assert rr.coverage_summary()["records"] == 5
        assert rr.coverage_summary(include_unmanaged=True)["records"] == 7


# ── de-duplication by website ────────────────────────────────────────────────


class TestWebsiteDedup:
    def test_one_row_per_site(self, hubspot):
        roster = rr.get_roster()
        # 5 managed records on 3 sites: hamptonlakes(2), volume(2), louis(1).
        assert len(roster) == 3
        assert len({r["domain"] for r in roster}) == 3

    def test_url_forms_of_one_site_collapse(self, hubspot):
        """"https://www.x.com/" and "x.com" are one site, not two."""
        roster = {r["domain"]: r for r in rr.get_roster()}
        assert "livehamptonlakesapts.com" in roster
        assert roster["livehamptonlakesapts.com"]["records_covered"] == 2

    def test_the_collapsed_record_survives_in_also_covers(self, hubspot):
        row = next(r for r in rr.get_roster() if r["domain"] == "livehamptonlakesapts.com")
        # Hampton Lakes carries every platform id; Lakeside carries none, so
        # Hampton Lakes is the row and Lakeside is named, not dropped.
        assert row["name"] == "Hampton Lakes"
        assert [o["name"] for o in row["also_covers"]] == ["Lakeside"]
        assert row["also_covers"][0]["company_id"] == "4"

    def test_the_collapse_is_reported_as_a_gap(self, hubspot):
        row = next(r for r in rr.get_roster() if r["domain"] == "volumeapartments.com")
        assert row["records_covered"] == 2
        assert any(g["field"] == "records_covered" for g in row["gaps"])

    def test_sites_and_records_are_reported_separately(self, hubspot):
        summary = rr.coverage_summary()
        assert summary["records"] == 5      # HubSpot records
        assert summary["sites"] == 3        # distinct sites
        assert summary["sites_with_multiple_records"] == 2


# ── coverage ─────────────────────────────────────────────────────────────────


class TestCoverage:
    def test_row_coverage_is_booleans_for_every_platform(self, hubspot):
        row = next(r for r in rr.get_roster() if r["name"] == "Hampton Lakes")
        assert row["coverage"] == {"hyly": True, "aptiq": True, "ga4": True,
                                   "google_ads": True, "ninjacat": True}

    def test_counts_are_over_records_not_sites(self, hubspot):
        """Volume 1 and Volume 2 each carry their own ids. Counting sites would
        report aptiq as 2 of 3 rather than 3 of 5."""
        coverage = rr.coverage_summary()["coverage"]
        assert coverage["aptiq"]["present"] == 3      # ids on 1, 2 and 7
        assert coverage["aptiq"]["of"] == 5
        assert coverage["aptiq"]["pct"] == 60.0
        assert coverage["hyly"]["present"] == 1
        assert coverage["ninjacat"]["present"] == 2

    def test_every_tally_carries_its_denominator_and_source(self, hubspot):
        for key, tally in rr.coverage_summary()["coverage"].items():
            assert tally["of"] == 5, key
            assert tally["source"].startswith("hubspot:"), key
            assert tally["present"] + tally["missing"] == tally["of"], key

    def test_a_missing_platform_is_a_gap_not_a_zero(self, hubspot):
        row = next(r for r in rr.get_roster() if r["name"] == "Volume 1")
        assert row["ids"]["hyly"] is None
        assert any(g["field"] == "hyly" and g["source"] == "hubspot:hyly_property_id"
                   for g in row["gaps"])

    def test_summary_carries_the_query_it_came_from(self, hubspot):
        summary = rr.coverage_summary()
        assert "RPMI" in summary["source"] and "RPM Investments" in summary["source"]
        assert summary["as_of"]


# ── measured field names: rpmmarket / rpmregion / totalunits ─────────────────


class TestMeasuredFieldNames:
    """`market` and `unit_count` (what property_resolver reads) are empty on
    these records. The roster reads the populated ones."""

    def test_market_and_region_come_from_rpmmarket_and_rpmregion(self, hubspot):
        row = next(r for r in rr.get_roster() if r["name"] == "Hampton Lakes")
        assert row["market"] == "Miami"
        assert row["region"] == "RPMI"

    def test_units_come_from_totalunits_as_an_int(self, hubspot):
        row = next(r for r in rr.get_roster() if r["name"] == "Hampton Lakes")
        assert row["units"] == 668

    def test_missing_units_are_none_with_a_gap_never_zero(self, hubspot):
        row = next(r for r in rr.get_roster() if r["name"] == "The Louis Las Colinas")
        assert row["units"] is None
        assert any(g["field"] == "units" and "not zero" in g["message"]
                   for g in row["gaps"])

    def test_unit_total_is_declared_as_a_floor_when_a_record_is_missing(self, hubspot):
        summary = rr.coverage_summary()
        assert summary["units_counted_over"] == 4        # not 5
        assert summary["units_total"] == 668 + 668 + 120 + 140
        assert any(g["field"] == "units_total" for g in summary["gaps"])

    def test_blank_state_is_none_not_empty_string(self, hubspot):
        row = next(r for r in rr.get_roster() if r["name"] == "The Louis Las Colinas")
        assert row["state"] is None


# ── cache ────────────────────────────────────────────────────────────────────


class TestCache:
    def test_hubspot_is_read_once_per_ttl(self, hubspot):
        rr.get_roster()
        rr.coverage_summary()
        rr.get_roster(include_unmanaged=True)
        assert hubspot == ["RPMI", "RPM Investments"]   # one build, three reads

    def test_clear_cache_forces_a_rebuild(self, hubspot):
        rr.get_roster()
        rr.clear_cache()
        rr.get_roster()
        assert hubspot == ["RPMI", "RPM Investments"] * 2



# ── paging ───────────────────────────────────────────────────────────────────


class TestPaging:
    """The roster is the caller that found the ten-row bug: 116 records came
    back as 20. `hubspot_client.search_companies` owns the fix and
    tests/test_hubspot_client.py pins it at that level; this pins it at the
    level that matters here — a roster built from a multi-page HubSpot response
    contains every page, not the first one.
    """

    def _paged_transport(self, monkeypatch, pages_by_client):
        """Drive the REAL search_companies against a fake transport, so the
        roster's completeness depends on the paging walk rather than a mock
        of it."""
        import hubspot_client as hc

        sent = []
        queues = {k: list(v) for k, v in pages_by_client.items()}

        class _Resp:
            def __init__(self, body):
                self._body = body

            def json(self):
                return self._body

        def _request(method, url, **kwargs):
            payload = kwargs.get("json") or {}
            value = payload["filterGroups"][0]["filters"][0]["value"]
            sent.append((value, payload.get("after"), payload.get("limit")))
            queue = queues.get(value) or []
            return _Resp(queue.pop(0) if queue else {"results": []})

        monkeypatch.setattr(hc, "_request", _request)
        monkeypatch.delitem(sys.modules, "hubspot_client", raising=False)
        monkeypatch.setitem(sys.modules, "hubspot_client", hc)
        rr.clear_cache()
        return sent

    def test_the_roster_spans_every_page(self, monkeypatch):
        # 12 RPMI records arriving as two pages, plus one RPM Investments page.
        page_a = {"results": [_co(str(i), f"Prop {i:02d}", "RPMI", f"prop{i}.com")
                              for i in range(10)],
                  "paging": {"next": {"after": "10"}}}
        page_b = {"results": [_co(str(i), f"Prop {i:02d}", "RPMI", f"prop{i}.com")
                              for i in range(10, 12)]}
        page_c = {"results": [_co("99", "Solo", "RPM Investments", "solo.com")]}
        sent = self._paged_transport(monkeypatch, {
            "RPMI": [page_a, page_b], "RPM Investments": [page_c]})

        roster = rr.get_roster()

        # 13, not the 10 a single unpaged page would have handed back.
        assert len(roster) == 13
        assert rr.coverage_summary()["records"] == 13
        assert {"Prop 10", "Prop 11", "Solo"} <= {r["name"] for r in roster}
        rr.clear_cache()

    def test_the_second_page_is_requested_with_the_cursor(self, monkeypatch):
        page_a = {"results": [_co("1", "A", "RPMI", "a.com")],
                  "paging": {"next": {"after": "CURSOR"}}}
        page_b = {"results": [_co("2", "B", "RPMI", "b.com")]}
        sent = self._paged_transport(monkeypatch, {"RPMI": [page_a, page_b]})

        rr.get_roster()

        assert sent[0] == ("RPMI", None, 100)        # first page, no cursor
        assert sent[1] == ("RPMI", "CURSOR", 100)    # second page carries it
        assert sent[2][0] == "RPM Investments"       # then the other spelling
        rr.clear_cache()
