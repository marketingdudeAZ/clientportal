"""Workspace report routes — flag, feature gate, per-property access, errors, page.

build_report is mocked; no connector is reached.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from flask import Flask

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "webhook-server"))

import feature_access  # noqa: E402
from routes import workspace_report as route  # noqa: E402
from skills import workspace_report as wr  # noqa: E402

FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "workspace" / "report_bromley_2026_06.json").read_text())
INTERNAL = {"X-Portal-Email": "dana.reyes@rpmliving.com"}
CLIENT = {"X-Portal-Email": "owner@example-capital.com"}
URL = "/api/workspace/report?company_id=26136316506&month=2026-06"
VERIFIED = {"portal.identity_verified": True}


@pytest.fixture
def client(monkeypatch):
    for var in ("WORKSPACE_ENABLED", "INTERNAL_API_KEY", "PORTAL_STRICT_IDENTITY", "PORTAL_COMPANY_ACCESS",
                "CLERK_PUBLISHABLE_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(feature_access, "_load_access_table", lambda: {})
    monkeypatch.setattr(feature_access, "_load_stage_table", lambda: {})
    feature_access.clear_cache()
    calls = []

    def fake_build(company_id, month=None):
        calls.append((company_id, month))
        return FIXTURE

    monkeypatch.setattr(wr, "build_report", fake_build)
    app = Flask(__name__)
    # The gate that protects this route lives at app level, not on the
    # blueprint — that split is exactly how the hole below went unnoticed.
    from _route_utils import workspace_proof_gate
    app.before_request(workspace_proof_gate)
    app.register_blueprint(route.workspace_report_bp)
    c = app.test_client()
    c.calls = calls
    yield c
    feature_access.clear_cache()


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("WORKSPACE_ENABLED", "true")


class TestFlag:
    def test_api_404_when_flag_off(self, client):
        r = client.get(URL, headers=INTERNAL, environ_overrides=VERIFIED)
        assert r.status_code == 404 and client.calls == []

    def test_page_404_when_flag_off(self, client):
        assert client.get("/workspace/report").status_code == 404

    @pytest.mark.parametrize("value", ["false", "0", "", "no"])
    def test_falsy_values_keep_it_off(self, client, monkeypatch, value):
        monkeypatch.setenv("WORKSPACE_ENABLED", value)
        assert client.get(URL, headers=INTERNAL, environ_overrides=VERIFIED).status_code == 404


class TestAccess:
    def test_anonymous_is_401(self, client, enabled):
        assert client.get(URL, environ_overrides=VERIFIED).status_code == 401  # KEEP-UNVERIFIED
        assert client.calls == []

    def test_an_asserted_rpm_email_alone_is_refused(self, client, enabled):
        """REGRESSION. This route lives on its own blueprint, so the workspace
        blueprint's before_request never ran for it: anyone who knew an RPM
        address could read any property's report. The old version of this test
        asserted the 200."""
        r = client.get(URL, headers=INTERNAL)
        assert r.status_code == 401
        assert r.get_json()["error"] == "Verified sign-in required"
        assert client.calls == []

    def test_a_verified_internal_session_gets_the_report(self, client, enabled):
        r = client.get(URL, headers=INTERNAL, environ_overrides=VERIFIED)
        assert r.status_code == 200
        assert r.get_json()["month"] == "2026-06"
        assert client.calls == [("26136316506", "2026-06")]
        assert r.headers["Cache-Control"] == "no-store"

    def test_the_gate_can_be_turned_off_for_a_rollback(self, client, enabled, monkeypatch):
        monkeypatch.setenv("WORKSPACE_REQUIRE_PROOF", "false")
        assert client.get(URL, headers=INTERNAL, environ_overrides=VERIFIED).status_code == 200  # KEEP-UNVERIFIED

    def test_client_without_feature_is_403(self, client, enabled):
        r = client.get(URL, headers=CLIENT, environ_overrides=VERIFIED)
        assert r.status_code == 403 and client.calls == []

    def test_client_with_feature_but_other_property_is_403(self, client, enabled, monkeypatch):
        monkeypatch.setattr(feature_access, "can_access", lambda email, key: key == "workspace")
        monkeypatch.setattr(feature_access, "companies_for", lambda email: {"999"})
        r = client.get(URL, headers=CLIENT, environ_overrides=VERIFIED)
        assert r.status_code == 403 and client.calls == []

    def test_client_with_feature_and_property_gets_it(self, client, enabled, monkeypatch):
        monkeypatch.setattr(feature_access, "can_access", lambda email, key: key == "workspace")
        monkeypatch.setattr(feature_access, "companies_for", lambda email: {"26136316506"})
        assert client.get(URL, headers=CLIENT, environ_overrides=VERIFIED).status_code == 200

    def test_feature_gate_asks_for_workspace(self, client, enabled, monkeypatch):
        seen = []
        monkeypatch.setattr(feature_access, "can_access", lambda email, key: seen.append(key) or True)
        client.get(URL, headers=INTERNAL, environ_overrides=VERIFIED)
        assert seen == ["workspace"]

    def test_missing_company_id_is_400(self, client, enabled):
        assert client.get("/api/workspace/report", headers=INTERNAL,
                          environ_overrides=VERIFIED).status_code == 400

    def test_strict_identity_requires_verified_session(self, client, enabled, monkeypatch):
        monkeypatch.setenv("PORTAL_STRICT_IDENTITY", "true")
        assert client.get(URL, headers=INTERNAL).status_code == 401

    def test_email_query_param_is_not_identity(self, client, enabled):
        r = client.get(URL + "&email=dana.reyes@rpmliving.com", environ_overrides=VERIFIED)
        assert r.status_code == 401


class TestErrors:
    @pytest.mark.parametrize("exc,status,error", [
        (wr.InvalidMonth("bad"), 400, "invalid_month"),
        (wr.MonthUnavailable("none"), 404, "month_unavailable"),
        (wr.PropertyUnavailable("no hyly"), 404, "report_unavailable"),
        (wr.SourceError("lake query failed"), 502, "source_failed"),
    ])
    def test_errors_map_to_statuses(self, client, enabled, monkeypatch, exc, status, error):
        def raiser(company_id, month=None):
            raise exc

        monkeypatch.setattr(wr, "build_report", raiser)
        r = client.get(URL, headers=INTERNAL, environ_overrides=VERIFIED)
        assert r.status_code == status
        assert r.get_json()["error"] == error


class TestPage:
    def test_page_served_without_email_identity(self, client, enabled):
        r = client.get("/workspace/report?company_id=26136316506&email=dana.reyes@rpmliving.com")
        assert r.status_code == 200
        body = r.get_data(as_text=True)
        assert "dana.reyes@rpmliving.com" not in body
        assert "searchParams.get('email')" not in body and 'get("email")' not in body
        assert "X-Workspace-Link" in body
        assert r.headers["Referrer-Policy"] == "no-referrer"

    def test_page_is_self_contained(self, client, enabled):
        body = client.get("/workspace/report").get_data(as_text=True)
        import re
        external_scripts = re.findall(r"<script[^>]+src=", body)
        assert external_scripts == []
        for href in re.findall(r'<link[^>]+href="([^"]+)"', body):
            assert href.startswith("https://fonts.googleapis.com") or href.startswith("https://fonts.gstatic.com")

    def test_clerk_key_injected_from_env(self, client, enabled, monkeypatch):
        monkeypatch.setenv("CLERK_PUBLISHABLE_KEY", "pk_test_abc123")
        body = client.get("/workspace/report").get_data(as_text=True)
        assert 'window.__CLERK_PK__ = "pk_test_abc123";' in body

    def test_non_publishable_key_is_ignored(self, client, enabled, monkeypatch):
        monkeypatch.setenv("CLERK_PUBLISHABLE_KEY", "sk_live_secret")
        body = client.get("/workspace/report").get_data(as_text=True)
        assert "sk_live_secret" not in body


class TestRegistration:
    def test_blueprint_is_registered_in_routes_package(self):
        text = (ROOT / "webhook-server" / "routes" / "__init__.py").read_text()
        assert "from .workspace_report import workspace_report_bp" in text
        assert "app.register_blueprint(workspace_report_bp)" in text
