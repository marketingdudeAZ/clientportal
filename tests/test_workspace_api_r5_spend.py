"""Round 5 spend sheet: scoping, internal columns stripped server-side, filters,
sort, paging, and nulls that stay null.

Offline: the spend sheet cache is mocked, so HubSpot is never called.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent / "webhook-server"))
sys.path.insert(0, str(TESTS))

from flask import Flask  # noqa: E402

import feature_access  # noqa: E402
import workspace_contract as contract  # noqa: E402
from routes.workspace import workspace_bp  # noqa: E402
from skills import workspace_cache  # noqa: E402
from skills import workspace_spend as wsp  # noqa: E402

INTERNAL = "dana@rpmliving.com"
CLIENT = "owner@acme.com"
URL = "/api/workspace/spend-sheet"
AS_OF = "2026-09-15T06:00:00Z"


def _row(cid, name, market, manager, status, **skus):
    base = {"company_id": cid, "property_name": name, "market": market, "marketing_manager": manager,
            "ple_status": status, "zillow_per_month": None, "zillow_per_lease": None, "costar_package": "Gold",
            "cx_bundle": False, "deal_id": f"d{cid}", "deal_name": f"{name} 2026", "deal_stage": "closedwon",
            "close_date": "2026-01-01", "deal_amount": 60000.0, "quote_status": "SIGNED", "quote_title": "IO"}
    for key in wsp.STAFF_TOTAL_KEYS:
        base[key] = None
    base.update(skus)
    return base


ROWS = [
    _row("111", "LYV Broadway", "Dallas", "Marcus Lee", "RPM Managed", search=1762.0, seo=900.0, mgmt_fee=450.0),
    _row("222", "Skye Reserve", "Houston", "Ana Ruiz", "Onboarding", search=1200.0, paid_social=0.0, mgmt_fee=300.0),
    _row("333", "Remi West Dallas", "Dallas", "Marcus Lee", "RPM Managed", pmax=2000.0),
    _row("444", "Bromley", "Denver", "Ana Ruiz", "Dispositioning"),
]


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    def _no_network(*a, **k):
        raise RuntimeError("network disabled in workspace tests")
    monkeypatch.setattr("requests.sessions.Session.request", _no_network)
    monkeypatch.setenv("WORKSPACE_ENABLED", "true")
    monkeypatch.setattr(workspace_cache, "spend_rows", lambda: ([dict(r) for r in ROWS], AS_OF))
    monkeypatch.setattr(feature_access, "_load_access_table", lambda: {
        CLIENT: {"role": "client", "beta_features": {"workspace"}, "companies": {"111", "333"}}})
    monkeypatch.setattr(feature_access, "_load_stage_table", lambda: {})
    feature_access.clear_cache()
    yield
    feature_access.clear_cache()


@pytest.fixture
def client():
    app = Flask(__name__)
    app.register_blueprint(workspace_bp)
    return app.test_client()


def _get(client, email=INTERNAL, preview=False, **params):
    headers = {"X-Portal-Email": email}
    if preview:
        headers["X-Workspace-Preview-Role"] = "client"
    r = client.get(URL, query_string=params, headers=headers)
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    contract.assert_shape(body, "spend_sheet")
    return body


def _no_internal_keys(body):
    keys = {c["key"] for c in body["columns"]}
    assert not keys & wsp.INTERNAL_KEYS
    for row in body["rows"]:
        assert not set(row["values"]) & wsp.INTERNAL_KEYS
        assert row["status"] is None
    assert not set(body["totals"]["values"]) & wsp.INTERNAL_KEYS
    assert body["filters"]["statuses"] == []


class TestScopeAndColumns:
    def test_staff_see_everything(self, client):
        body = _get(client)
        assert body["scope"] == "portfolio" and body["count"] == 4 and body["as_of"] == AS_OF
        keys = {c["key"] for c in body["columns"]}
        assert {"mgmt_fee", "deal_name", "quote_status"} <= keys
        lyv = next(r for r in body["rows"] if r["company_id"] == "111")
        assert lyv["status"] == "RPM Managed" and lyv["values"]["mgmt_fee"] == 450.0
        assert lyv["total"] == 1762.0 + 900.0 + 450.0
        assert body["filters"]["statuses"] == ["Dispositioning", "Onboarding", "RPM Managed"]
        assert contract.numbers_without_source(body) == []

    def test_clients_see_their_properties_without_internal_columns(self, client):
        body = _get(client, email=CLIENT)
        assert body["scope"] == "client"
        assert sorted(r["company_id"] for r in body["rows"]) == ["111", "333"]
        _no_internal_keys(body)
        lyv = next(r for r in body["rows"] if r["company_id"] == "111")
        assert lyv["total"] == 1762.0 + 900.0                 # the management fee is not in a client total
        assert body["totals"]["total"] == 1762.0 + 900.0 + 2000.0
        assert body["totals"]["values"]["search"] == 1762.0

    def test_preview_as_client_strips_the_same_columns(self, client):
        body = _get(client, preview=True)
        assert body["scope"] == "client"
        _no_internal_keys(body)
        assert "mgmt_fee" not in str(body)

    def test_client_cannot_filter_or_sort_by_status(self, client):
        assert _get(client, email=CLIENT, status="Onboarding")["count"] == 2
        r = client.get(URL, query_string={"sort": "status"}, headers={"X-Portal-Email": CLIENT})
        assert r.status_code == 400
        r = client.get(URL, query_string={"sort": "mgmt_fee"}, headers={"X-Portal-Email": CLIENT})
        assert r.status_code == 400


class TestFiltersSortPaging:
    def test_filters(self, client):
        assert [r["company_id"] for r in _get(client, q="broad")["rows"]] == ["111"]
        assert sorted(r["company_id"] for r in _get(client, market="dallas")["rows"]) == ["111", "333"]
        assert sorted(r["company_id"] for r in _get(client, manager="Ana Ruiz")["rows"]) == ["222", "444"]
        assert [r["company_id"] for r in _get(client, status="Onboarding")["rows"]] == ["222"]
        body = _get(client, market="Dallas")
        assert body["totals"]["values"]["search"] == 1762.0 and body["totals"]["values"]["pmax"] == 2000.0

    def test_sort_puts_nulls_last_both_ways(self, client):
        asc = [r["company_id"] for r in _get(client, sort="search")["rows"]]
        desc = [r["company_id"] for r in _get(client, sort="search", dir="desc")["rows"]]
        assert asc[:2] == ["222", "111"] and desc[:2] == ["111", "222"]
        assert set(asc[2:]) == {"333", "444"} and set(desc[2:]) == {"333", "444"}
        assert [r["company_id"] for r in _get(client, sort="total", dir="desc")["rows"]][0] == "111"

    def test_paging(self, client):
        body = _get(client, page_size=3, page=2)
        assert body["count"] == 4 and body["page"] == 2 and len(body["rows"]) == 1
        assert body["totals"]["total"] == _get(client)["totals"]["total"]     # totals cover every page

    @pytest.mark.parametrize("params", [{"page": "0"}, {"page_size": "500"}, {"dir": "up"}, {"sort": "nope"}])
    def test_bad_params_are_400(self, client, params):
        assert client.get(URL, query_string=params, headers={"X-Portal-Email": INTERNAL}).status_code == 400


class TestNulls:
    def test_empty_values_stay_null(self, client):
        rows = {r["company_id"]: r for r in _get(client)["rows"]}
        assert rows["111"]["values"]["pmax"] is None
        assert rows["222"]["values"]["paid_social"] == 0.0          # a $0 line item is 0, not null
        assert rows["444"]["total"] is None and rows["444"]["values"]["search"] is None
        body = _get(client, market="Denver")
        assert body["totals"]["values"]["search"] is None and body["totals"]["total"] is None

    def test_cache_failure_is_a_gap_not_a_500(self, client, monkeypatch):
        def boom():
            raise RuntimeError("HubSpot down")
        monkeypatch.setattr(workspace_cache, "spend_rows", boom)
        body = _get(client)
        assert body["rows"] == [] and any(g.get("field") == "rows" for g in body["gaps"])

    def test_requires_workspace_access(self, client, monkeypatch):
        monkeypatch.setattr(feature_access, "_load_access_table", lambda: {
            CLIENT: {"role": "client", "beta_features": set(), "companies": {"111"}}})
        feature_access.clear_cache()
        assert client.get(URL, headers={"X-Portal-Email": CLIENT}).status_code == 403
