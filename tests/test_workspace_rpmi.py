"""The RPMI portfolio roll-up — `GET /api/workspace/rpmi`.

Offline: HubSpot, the AptIQ export, the spend sheet, the Hyly lake and the
recommendation engine are all mocked, and `requests` is disabled so a missed
mock fails loudly rather than reaching the network.

What is pinned here:

* the roster rule — `client` in ('RPMI','RPM Investments'), then the managed
  filter (`plestatus`, a uuid, management end date, the retain toggle);
* the endpoint's shape and the internal-only gate (a client role, and an
  asserted-but-unproven identity, are both refused);
* null-with-a-gap: a property with no AptIQ has no occupancy and no units at
  risk and says why, and a property outside the Hyly beta has no leases and no
  cost per lease and says why — neither prints a zero;
* coverage counts, which the screen's strip reads straight off;
* that nothing invented: every number carries a receipt.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent / "webhook-server"))
sys.path.insert(0, str(TESTS))

from flask import Flask  # noqa: E402

import feature_access  # noqa: E402
import workspace_contract as contract  # noqa: E402
from routes.workspace import workspace_bp  # noqa: E402
from skills import workspace_cache as wcache  # noqa: E402
from skills import workspace_rpmi as wrpmi  # noqa: E402

INTERNAL = "dana@rpmliving.com"
CLIENT = "owner@acme.com"
TODAY = date(2026, 9, 17)
TS = "2026-09-16T00:00:00Z"

# Four managed RPMI properties and three that must not appear: a different
# client, a dispositioned one, and one with no uuid.
FULL = "40001"        # every source connected
NO_APTIQ = "40002"    # analytics and paid only — no availability, no funnel
BARE = "40003"        # nothing connected but the property record itself
RETAINED = "40004"    # disposition date in the past, retain toggle on


def _company(cid, name, **over):
    props = {"name": name, "client": "RPMI", "plestatus": "RPM Managed", "uuid": "u-" + cid,
             "city": "Austin", "state": "TX", "totalunits": "200", "rpmmarket": "",
             "aptiq_property_id": "", "hyly_property_id": "", "ga4_property_id": "",
             "google_ads_customer_id": "", "managementend": "", "disposition_retained": ""}
    props.update(over)
    return {"id": cid, "properties": props}


ROSTER = [
    _company(FULL, "Parkline", totalunits="300", aptiq_property_id="ap-1", hyly_property_id="111",
             ga4_property_id="ga-1", google_ads_customer_id="123|456", rpmmarket="Central Texas"),
    _company(NO_APTIQ, "Arcadia West", totalunits="150", ga4_property_id="ga-2",
             google_ads_customer_id="789|456", city="Dallas"),
    _company(BARE, "Sable Ridge", totalunits="90", city="Houston"),
    _company(RETAINED, "Verano", totalunits="120", plestatus="Dispositioning",
             managementend="2026-01-31", disposition_retained="true"),
    # Excluded: not RPMI-managed.
    _company("40005", "Not Managed", plestatus="Prospect"),
    _company("40006", "Gone", plestatus="RPM Managed", managementend="2026-02-01"),
    _company("40007", "No uuid", uuid=""),
]
MANAGED = (FULL, NO_APTIQ, BARE, RETAINED)

DAILY = {
    "ap-1": {"Advertised Occupancy %": "88.0", "Available Units": "36",
             "Report Generation Date": "09/16/2026"},
}
SPEND_ROWS = [
    {"company_id": FULL, "seo": 1500, "search": 4000, "mgmt_fee": 900},
    {"company_id": NO_APTIQ, "seo": 800, "mgmt_fee": 500},
    # BARE and RETAINED have no deal row at all.
]
LEASES = {111: 10}


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    def _no_network(*a, **k):
        raise RuntimeError("network disabled in workspace tests")
    monkeypatch.setattr("requests.sessions.Session.request", _no_network)
    for var in ("PORTAL_STRICT_IDENTITY", "PORTAL_COMPANY_ACCESS", "WORKSPACE_SIGNED_LINKS_ENABLED"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("WORKSPACE_ENABLED", "true")
    monkeypatch.setattr(feature_access, "_load_access_table", lambda: {})
    monkeypatch.setattr(feature_access, "_load_stage_table", lambda: {})
    feature_access.clear_cache()
    wcache.clear()
    wrpmi.clear_cache()
    yield
    feature_access.clear_cache()
    wrpmi.clear_cache()


@pytest.fixture
def sources(monkeypatch):
    """Every portfolio-wide read the screen makes, answered from the fixtures."""
    import hubspot_client
    from skills import reco_engine
    from skills import workspace_leasing as wlease

    calls = {"search": 0, "reco": 0}

    def _search(filters, properties=None, limit=None):
        calls["search"] += 1
        calls["filters"] = filters
        calls["properties"] = properties
        return [dict(c) for c in ROSTER]

    monkeypatch.setattr(hubspot_client, "search_companies", _search)
    monkeypatch.setattr(wcache, "aptiq_daily", lambda: (DAILY, TS))
    monkeypatch.setattr(wcache, "spend_rows", lambda: ([dict(r) for r in SPEND_ROWS], TS))
    monkeypatch.setattr(wlease, "leases_by_property", lambda pids, start, end, lake=None: dict(LEASES))

    def _portfolio(props, **kw):
        calls["reco"] += 1
        calls["reco_props"] = props
        surfaced = {FULL: 3, NO_APTIQ: 1}
        return {"properties": {p["company_id"]: {"counts": {"surfaced": surfaced.get(p["company_id"], 0)}}
                               for p in props},
                "gaps": [], "as_of": TS}

    monkeypatch.setattr(reco_engine, "for_portfolio", _portfolio)
    return calls


@pytest.fixture
def client():
    app = Flask(__name__)
    app.register_blueprint(workspace_bp)
    c = app.test_client()
    # Stands for a Clerk session: internal reads need a PROVEN identity.
    c.environ_base["portal.identity_verified"] = True
    return c


def _h(email=INTERNAL, **extra):
    headers = {"X-Portal-Email": email}
    headers.update(extra)
    return headers


def _rows(body):
    return dict((r["company_id"], r) for r in body["properties"])


def _messages(gaps):
    return " ".join(g["message"] for g in gaps)


def _coverage(body):
    return dict((s["key"], s["count"]) for s in body["coverage"]["sources"])


# ── the roster ───────────────────────────────────────────────────────────────

class TestRoster:
    def test_searches_hubspot_for_both_client_spellings(self, sources):
        wrpmi.roster()
        assert sources["filters"] == [
            {"propertyName": "client", "operator": "IN", "values": ["RPMI", "RPM Investments"]}]

    def test_roster_asks_for_every_join_key_it_reports_on(self, sources):
        wrpmi.roster()
        asked = set(sources["properties"])
        assert {"aptiq_property_id", "ga4_property_id", "google_ads_customer_id",
                "hyly_property_id", "uuid", "totalunits", "client"} <= asked

    def test_roster_is_cached_between_calls(self, sources):
        wrpmi.roster()
        wrpmi.roster()
        assert sources["search"] == 1

    def test_managed_filter_keeps_the_retained_property_and_drops_the_rest(self, sources):
        props, records, _ = wrpmi.managed_roster()
        assert records == len(ROSTER)
        assert [p["hubspot_company_id"] for p in props] == list(MANAGED)

    def test_a_property_without_a_uuid_is_never_managed(self):
        assert wrpmi.is_managed({"uuid": "", "plestatus": "RPM Managed"}) is False
        assert wrpmi.is_managed({"uuid": "u", "plestatus": "RPM Managed"}) is True
        assert wrpmi.is_managed({"uuid": "u", "plestatus": "Onboarding"}) is True


# ── access ───────────────────────────────────────────────────────────────────

class TestAccess:
    def test_internal_role_gets_the_payload(self, client, sources):
        assert client.get("/api/workspace/rpmi", headers=_h()).status_code == 200

    def test_a_client_role_is_refused(self, client, sources, monkeypatch):
        monkeypatch.setattr(feature_access, "_load_access_table", lambda: {
            CLIENT: {"role": "client", "beta_features": {"workspace"}, "companies": {FULL}}})
        feature_access.clear_cache()
        r = client.get("/api/workspace/rpmi", headers=_h(CLIENT))
        assert r.status_code == 403 and r.get_json()["error"] == "Internal role required"

    def test_an_asserted_email_without_a_session_is_refused(self, sources):
        app = Flask(__name__)
        app.register_blueprint(workspace_bp)
        unproven = app.test_client()          # no portal.identity_verified
        assert unproven.get("/api/workspace/rpmi", headers=_h()).status_code == 401

    def test_preview_as_client_is_refused(self, client, sources):
        r = client.get("/api/workspace/rpmi",
                       headers=_h(**{"X-Workspace-Preview-Role": "client"}))
        assert r.status_code == 403

    def test_the_route_404s_when_the_workspace_is_off(self, client, sources, monkeypatch):
        monkeypatch.setenv("WORKSPACE_ENABLED", "false")
        assert client.get("/api/workspace/rpmi", headers=_h()).status_code == 404


# ── the payload ──────────────────────────────────────────────────────────────

class TestPayload:
    @pytest.fixture
    def body(self, client, sources):
        return client.get("/api/workspace/rpmi", headers=_h()).get_json()

    def test_matches_the_contract_and_every_number_has_a_receipt(self, body):
        contract.assert_shape(body, "rpmi")
        assert contract.numbers_without_source(body) == [], contract.numbers_without_source(body)

    def test_counts_records_and_managed_properties_separately(self, body):
        assert body["record_count"] == len(ROSTER)
        assert body["property_count"] == len(MANAGED)
        assert body["scope_label"] == "RPM Investments · 4 managed properties"

    def test_coverage_counts_the_join_keys_each_property_carries(self, body):
        assert _coverage(body) == {"aptiq": 1, "ga4": 2, "google_ads": 2, "hyly": 1}
        assert body["coverage"]["property_count"] == 4
        missing = dict((s["key"], s["missing"]) for s in body["coverage"]["sources"])
        assert missing == {"aptiq": 3, "ga4": 2, "google_ads": 2, "hyly": 3}

    def test_coverage_labels_are_client_safe(self, body):
        labels = [s["label"] for s in body["coverage"]["sources"]]
        assert labels == ["Availability", "Analytics", "Paid", "Funnel"]

    def test_rows_rank_by_units_at_risk_with_the_unknown_ones_last(self, body):
        order = [r["company_id"] for r in body["properties"]]
        assert order[0] == FULL
        assert set(order[1:]) == {NO_APTIQ, BARE, RETAINED}

    def test_totals_are_summed_from_the_rows_that_have_the_number(self, body):
        totals = body["totals"]
        assert totals["units"]["value"] == 300 + 150 + 90 + 120
        assert totals["units_at_risk"]["value"] == 36
        assert totals["available_units"]["properties"] == 1
        # Management fee is not marketing money, so it is out of spend.
        assert totals["spend_monthly"]["value"] == 1500 + 4000 + 800
        assert totals["leases_last_month"]["value"] == 10
        assert totals["occupancy"]["value"] == pytest.approx(0.88)

    def test_portfolio_cost_per_lease_divides_spend_by_leases(self, body):
        assert body["totals"]["cost_per_lease"]["value"] == pytest.approx(6300 / 10)
        assert body["totals"]["cost_per_lease"]["source"] == "hubspot_line_items+hyly"

    def test_the_lease_period_is_the_last_full_month(self, body):
        assert body["period"]["leases_month"] == "2026-08"
        assert body["totals"]["leases_last_month"]["as_of"] == "2026-08-31"

    def test_the_open_count_is_the_engines_number_not_a_second_definition(self, body):
        assert body["totals"]["open_recommendations"] == {
            "value": 4, "source": "workspace_inbox", "as_of": TS}

    def test_it_falls_back_to_state_when_most_rows_have_no_market(self, body):
        # This fixture has market on 1 of 4, so grouping by it would be mostly
        # empty headings. The gap says that, rather than the screen going quiet.
        assert body["grouping"]["field"] == "state"
        assert "Market is not set on 3 of 4" in _messages(body["gaps"])
        assert _rows(body)[FULL]["market"] == "Central Texas"
        assert _rows(body)[BARE]["market"] is None

    def test_it_groups_by_market_once_the_field_is_populated(self, client, sources, monkeypatch):
        # rpmmarket is the field that is actually filled in (109 of 109 live);
        # `market`, which the property resolver reads, is not. Grouping is
        # decided from the rows, so the populated case must group by market.
        full = [{"id": c["id"], "properties": dict(c["properties"], rpmmarket="Central Texas")}
                for c in ROSTER]
        monkeypatch.setattr("hubspot_client.search_companies",
                            lambda f, properties=None, limit=None: [dict(c) for c in full])
        wrpmi.clear_cache()
        body = client.get("/api/workspace/rpmi", headers=_h()).get_json()
        assert body["grouping"]["field"] == "market"
        assert body["grouping"]["note"] == "Grouped by market."
        assert "market" not in [g.get("field") for g in body["gaps"]]
        assert all(r["market"] == "Central Texas" for r in body["properties"])

    def test_market_comes_from_rpmmarket_not_the_resolvers_market_field(self, sources):
        wrpmi.roster()
        assert "rpmmarket" in sources["properties"]


# ── null with a gap ──────────────────────────────────────────────────────────

class TestPartialProperties:
    @pytest.fixture
    def rows(self, client, sources):
        return _rows(client.get("/api/workspace/rpmi", headers=_h()).get_json())

    def test_the_fully_connected_property_carries_every_number(self, rows):
        row = rows[FULL]
        assert row["occupancy"]["value"] == pytest.approx(0.88)
        assert row["occupancy"]["source"] == "aptiq"
        assert row["units_at_risk"]["value"] == 36
        assert row["available_units"]["value"] == 36
        assert row["leases_last_month"]["value"] == 10
        assert row["cost_per_lease"]["value"] == pytest.approx(550.0)
        assert row["spend_monthly"]["value"] == 5500
        assert row["open_recommendations"]["value"] == 3
        assert row["gaps"] == []

    def test_no_aptiq_means_null_occupancy_and_a_named_gap(self, rows):
        row = rows[NO_APTIQ]
        assert row["occupancy"] is None
        assert row["available_units"] is None
        assert row["units_at_risk"] is None
        fields = [g["field"] for g in row["gaps"]]
        assert "units_at_risk" in fields
        assert "Availability" in _messages(row["gaps"])

    def test_no_hyly_means_null_leases_and_null_cost_per_lease(self, rows):
        row = rows[NO_APTIQ]
        assert row["leases_last_month"] is None
        assert row["cost_per_lease"] is None
        assert "leases_last_month" in [g["field"] for g in row["gaps"]]
        assert "leasing funnel isn’t connected" in _messages(row["gaps"])

    def test_a_partial_property_still_shows_what_it_does_have(self, rows):
        row = rows[NO_APTIQ]
        assert row["units"]["value"] == 150
        assert row["spend_monthly"]["value"] == 800
        assert row["open_recommendations"]["value"] == 1
        assert row["sources"] == {"aptiq": False, "ga4": True, "google_ads": True, "hyly": False}

    def test_a_property_with_nothing_connected_names_all_four(self, rows):
        row = rows[BARE]
        assert all(row[k] is None for k in
                   ("occupancy", "available_units", "units_at_risk", "leases_last_month",
                    "cost_per_lease", "spend_monthly"))
        assert row["units"]["value"] == 90
        assert set(row["sources"].values()) == {False}
        fields = {g["field"] for g in row["gaps"]}
        assert {"units_at_risk", "leases_last_month", "ga4", "google_ads", "spend_monthly"} <= fields

    def test_gap_messages_never_name_an_internal_system_or_an_env_var(self, client, sources):
        body = client.get("/api/workspace/rpmi", headers=_h()).get_json()
        text = _messages(body["gaps"]) + " " + " ".join(
            _messages(r["gaps"]) for r in body["properties"])
        assert text.strip(), "this fixture must produce gaps for the check to mean anything"
        for banned in ("AptIQ", "ApartmentIQ", "Hyly", "HubSpot", "HubDB", "BigQuery", "ClickUp",
                       "NinjaCat", "Fluency", "APT_IQ", "_SHEET_URL", "_TABLE_ID",
                       "Traceback", "Error", "Exception"):
            assert banned not in text, banned

    def test_zero_recommendations_is_a_number_not_a_gap(self, rows):
        assert rows[BARE]["open_recommendations"]["value"] == 0
        assert "open_recommendations" not in [g["field"] for g in rows[BARE]["gaps"]]


# ── the sources it leans on ──────────────────────────────────────────────────

class TestSourceFailures:
    def test_no_availability_export_leaves_every_occupancy_null(self, client, sources, monkeypatch):
        def _boom():
            raise RuntimeError("APT_IQ_DAILY_SHEET_URL is not set")
        monkeypatch.setattr(wcache, "aptiq_daily", _boom)
        body = client.get("/api/workspace/rpmi", headers=_h()).get_json()
        assert body["totals"]["occupancy"] is None
        assert all(r["occupancy"] is None for r in body["properties"])
        assert "Availability data could not be read" in _messages(body["gaps"])
        assert "APT_IQ_DAILY_SHEET_URL" not in _messages(body["gaps"])

    def test_no_lease_feed_leaves_leases_unknown_rather_than_zero(self, client, sources, monkeypatch):
        from skills import workspace_leasing as wlease
        monkeypatch.setattr(wlease, "leases_by_property", lambda pids, start, end, lake=None: None)
        body = client.get("/api/workspace/rpmi", headers=_h()).get_json()
        assert body["totals"]["leases_last_month"] is None
        assert body["totals"]["cost_per_lease"] is None
        assert all(r["leases_last_month"] is None for r in body["properties"])

    def test_a_broken_recommendation_engine_is_a_gap_not_a_500(self, client, sources, monkeypatch):
        from skills import reco_engine

        def _boom(props, **kw):
            raise RuntimeError("queue offline")
        monkeypatch.setattr(reco_engine, "for_portfolio", _boom)
        r = client.get("/api/workspace/rpmi", headers=_h())
        assert r.status_code == 200
        body = r.get_json()
        assert all(row["open_recommendations"] is None for row in body["properties"])
        assert "recommendation queue could not be read" in _messages(body["gaps"])

    def test_the_recommendation_engine_is_called_once_for_the_whole_roster(self, client, sources):
        client.get("/api/workspace/rpmi", headers=_h())
        assert sources["reco"] == 1
        assert [p["company_id"] for p in sources["reco_props"]] == list(MANAGED)

    def test_exposure_is_only_passed_for_properties_we_can_measure(self, client, sources):
        client.get("/api/workspace/rpmi", headers=_h())
        by_id = dict((p["company_id"], p) for p in sources["reco_props"])
        assert by_id[FULL]["exposure"] == pytest.approx(0.12)
        assert "exposure" not in by_id[BARE]


class TestLastFullMonth:
    @pytest.mark.parametrize("today,expected", [
        (date(2026, 9, 17), ("2026-08-01", "2026-08-31", "2026-08")),
        (date(2026, 1, 4), ("2025-12-01", "2025-12-31", "2025-12")),
        (date(2026, 3, 1), ("2026-02-01", "2026-02-28", "2026-02")),
    ])
    def test_it_is_the_month_before_this_one(self, today, expected):
        assert wrpmi.last_full_month(today) == expected
