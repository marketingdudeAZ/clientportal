"""The workspace identity gate, proven across every route rather than per file.

Why this file exists: `/api/workspace/report` sits on a second blueprint, so the
workspace blueprint's own `before_request` never ran for it, and an asserted
`X-Portal-Email` could read any property's report. Per-route tests did not catch
that, because each one tested the route its author was thinking about.

So this walks `app.url_map` and asserts the rule for EVERY workspace API rule
that exists — including ones added after today.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from flask import Flask, jsonify

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "webhook-server"))
sys.path.insert(0, str(ROOT))

import _route_utils  # noqa: E402
from _route_utils import workspace_proof_gate, workspace_proof_required  # noqa: E402

ASSERTED = {"X-Portal-Email": "dana.reyes@rpmliving.com"}
VERIFIED = {"portal.identity_verified": True}


@pytest.fixture
def app(monkeypatch):
    monkeypatch.delenv("WORKSPACE_REQUIRE_PROOF", raising=False)
    monkeypatch.delenv("INTERNAL_API_KEY", raising=False)
    app = Flask(__name__)
    app.before_request(workspace_proof_gate)

    @app.route("/api/workspace/anything")
    def _workspace_api():
        return jsonify({"ok": True})

    @app.route("/api/workspace/report")
    def _workspace_report():
        return jsonify({"ok": "report"})

    @app.route("/workspace")
    def _workspace_page():
        return "<html>sign in here</html>"

    @app.route("/workspace/report")
    def _workspace_report_page():
        return "<html>sign in here</html>"

    @app.route("/api/portal-tickets")
    def _legacy_api():
        return jsonify({"ok": "legacy"})

    return app


class TestTheRule:
    def test_asserted_email_is_refused_on_every_workspace_api_route(self, app):
        """The matrix. Any new /api/workspace/* rule is covered the day it lands."""
        client = app.test_client()
        rules = [r for r in app.url_map.iter_rules()
                 if workspace_proof_required(str(r.rule)) and "GET" in (r.methods or set())]
        assert rules, "no workspace API rules found — the matrix would pass vacuously"
        for rule in rules:
            resp = client.get(str(rule.rule), headers=ASSERTED)
            assert resp.status_code == 401, "%s accepted an asserted email" % rule.rule
            assert resp.get_json()["error"] == "Verified sign-in required"

    def test_a_verified_session_passes(self, app):
        client = app.test_client()
        for path in ("/api/workspace/anything", "/api/workspace/report"):
            resp = client.get(path, headers=ASSERTED, environ_overrides=VERIFIED)
            assert resp.status_code == 200

    def test_page_routes_stay_open_so_sign_in_can_render(self, app):
        """A signed-out visitor must still get the HTML, or Clerk has nowhere to
        mount and the user sees a bare 401 instead of a sign-in box."""
        client = app.test_client()
        for path in ("/workspace", "/workspace/report"):
            assert client.get(path).status_code == 200

    def test_legacy_portal_routes_are_untouched(self, app):
        """Scoped on purpose: Clerk does not cover the legacy portal's users, so
        turning this on there would lock them out."""
        assert app.test_client().get("/api/portal-tickets", headers=ASSERTED).status_code == 200

    def test_preflight_is_not_blocked(self, app):
        resp = app.test_client().open("/api/workspace/anything", method="OPTIONS")
        assert resp.status_code != 401

    def test_internal_key_callers_pass(self, app, monkeypatch):
        monkeypatch.setenv("INTERNAL_API_KEY", "internal-secret")
        resp = app.test_client().get("/api/workspace/anything",
                                     headers={"X-Internal-Key": "internal-secret"})
        assert resp.status_code == 200

    def test_the_flag_restores_the_old_behavior(self, app, monkeypatch):
        monkeypatch.setenv("WORKSPACE_REQUIRE_PROOF", "false")
        resp = app.test_client().get("/api/workspace/anything", headers=ASSERTED)
        assert resp.status_code == 200

    @pytest.mark.parametrize("value", ["0", "no", "FALSE", " false "])
    def test_falsy_spellings_all_disable(self, app, monkeypatch, value):
        monkeypatch.setenv("WORKSPACE_REQUIRE_PROOF", value)
        assert app.test_client().get("/api/workspace/anything",
                                     headers=ASSERTED).status_code == 200

    def test_prefix_matching_does_not_leak_to_lookalike_paths(self):
        assert workspace_proof_required("/api/workspace/report") is True
        assert workspace_proof_required("/api/workspace/") is True
        assert workspace_proof_required("/api/workspaces/report") is False
        assert workspace_proof_required("/workspace") is False
        assert workspace_proof_required("/api/portal-tickets") is False


class TestClerkHardening:
    """A Clerk session token carries `azp`, not `aud`. Without checking it, any
    validly-signed token from the same instance is accepted — including one
    minted for a different site on that instance."""

    def test_unset_authorized_parties_accepts_any_origin(self, monkeypatch):
        import clerk_auth
        monkeypatch.delenv("CLERK_AUTHORIZED_PARTIES", raising=False)
        assert clerk_auth._authorized_party_ok({"azp": "https://anything.example"}) is True
        assert clerk_auth._authorized_party_ok({}) is True

    def test_configured_parties_accept_only_themselves(self, monkeypatch):
        import clerk_auth
        monkeypatch.setenv("CLERK_AUTHORIZED_PARTIES",
                           "https://go.rpmliving.com, https://digital.rpmliving.com/")
        ok = clerk_auth._authorized_party_ok
        assert ok({"azp": "https://go.rpmliving.com"}) is True
        assert ok({"azp": "https://digital.rpmliving.com"}) is True      # trailing slash
        assert ok({"azp": "https://evil.example"}) is False
        assert ok({}) is False                                           # no azp at all

    def test_a_failed_token_strips_the_asserted_header_without_a_secret_key(self, monkeypatch):
        """Regression: this used to run only when CLERK_SECRET_KEY was set, so a
        missing or wrong key left the spoofable header in place beside a token
        that had just failed verification."""
        import server

        monkeypatch.delenv("CLERK_SECRET_KEY", raising=False)
        monkeypatch.setattr(server, "clerk_auth", None, raising=False)

        flask_app = server.app
        with flask_app.test_request_context(
                "/api/portal-tickets",
                headers={"Authorization": "Bearer not-a-real-token",
                         "X-Portal-Email": "dana.reyes@rpmliving.com"}):
            import clerk_auth
            monkeypatch.setattr(clerk_auth, "verify_bearer", lambda auth: None)
            server._clerk_identity()
            from flask import request
            assert request.headers.get("X-Portal-Email") is None
            assert request.environ.get("portal.identity_verified") is not True


def test_the_gate_is_registered_on_the_real_app():
    """Belt and braces: the rule above is worthless if nothing wires it up."""
    import server

    names = [f.__name__ for f in server.app.before_request_funcs.get(None, [])]
    assert "_require_workspace_proof" in names
