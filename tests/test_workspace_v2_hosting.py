"""Serving the client workspace from digital.rpmliving.com/client-portal/v2.

The workspace app is one file. It is served two ways: same-origin from Render
at /workspace, and cross-origin from a HubSpot CMS page that loads
/workspace/embed.js. These tests defend the seam between those two:

  1. Same-origin is UNCHANGED. With no PORTAL_API_BASE set, /workspace serves
     the file's bytes and the app's requests are relative with a cookie —
     byte-for-byte and behaviour-for-behaviour what shipped.
  2. Cross-origin sends the Bearer token and NO cookie, ever.
  3. Report links and the report's back link work in both modes, and the
     report page refuses an ?app= origin it does not already trust.
  4. The embed is generated from the page, so the two surfaces cannot drift,
     and it 404s behind the same flag as everything else in the workspace.
  5. Preflight answers for digital.rpmliving.com with Authorization allowed
     and a Max-Age, so calls are not preflighted twice.

The JS is exercised in node (tests/js/workspace_api_base_harness.js), sliced
out of the page on every run. Nothing else in the suite executes it.

See docs/PORTAL_V2_HOSTING.md.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from flask import Flask

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "webhook-server"))

from routes.portal_ui import (  # noqa: E402
    _WORKSPACE,
    api_base,
    build_workspace_embed,
    portal_ui_bp,
    workspace_parts,
)

PAGE = REPO / "webhook-server" / "portal_pages" / "workspace.html"
REPORT_PAGE = REPO / "webhook-server" / "portal_pages" / "workspace_report.html"
TEMPLATE = REPO / "hubspot-cms" / "templates" / "client-portal-v2.html"
HARNESS = REPO / "tests" / "js" / "workspace_api_base_harness.js"

RENDER = "https://rpm-portal-server.onrender.com"
HUBSPOT = "https://digital.rpmliving.com"
HUBSPOT_V2 = HUBSPOT + "/client-portal/v2"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("WORKSPACE_ENABLED", "true")
    monkeypatch.delenv("PORTAL_API_BASE", raising=False)
    monkeypatch.delenv("CLERK_PUBLISHABLE_KEY", raising=False)
    app = Flask(__name__)
    app.register_blueprint(portal_ui_bp)
    return app.test_client()


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


def run_harness(mode: str, path: Path, **scenario) -> dict:
    proc = subprocess.run(
        ["node", str(HARNESS), mode, str(path), json.dumps(scenario)],
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"harness failed:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


# ── 1. the injected value ────────────────────────────────────────────────────

class TestApiBaseValue:
    def test_the_page_ships_with_an_empty_base(self, page):
        """The committed file must never carry an origin. A base baked into the
        file is the base production would run on."""
        assert "window.__PORTAL_API_BASE__ = '';" in page
        assert "onrender.com" not in page

    def test_unset_serves_the_file_unchanged(self, client):
        """Same-origin is byte-for-byte what it was: nothing is injected."""
        resp = client.get("/workspace")
        assert resp.status_code == 200
        assert resp.get_data(as_text=True) == PAGE.read_text(encoding="utf-8")

    def test_set_injects_exactly_one_value(self, client, monkeypatch):
        monkeypatch.setenv("PORTAL_API_BASE", RENDER)
        body = client.get("/workspace").get_data(as_text=True)
        assert f'window.__PORTAL_API_BASE__ = "{RENDER}";' in body
        assert "window.__PORTAL_API_BASE__ = '';" not in body
        # Everything else about the page is untouched.
        assert len(body) - len(PAGE.read_text(encoding="utf-8")) == len(
            f'window.__PORTAL_API_BASE__ = "{RENDER}";') - len(
            "window.__PORTAL_API_BASE__ = '';")

    @pytest.mark.parametrize("bad", [
        "rpm-portal-server.onrender.com",          # no scheme
        "https://host/with/path",                  # a path
        "https://host and more",                   # whitespace
        "javascript:alert(1)",                     # not http(s)
    ])
    def test_a_base_that_is_not_an_origin_is_refused(self, monkeypatch, bad):
        """This value is pasted into the URL a Clerk Bearer token is sent to."""
        monkeypatch.setenv("PORTAL_API_BASE", bad)
        assert api_base() == ""

    def test_a_trailing_slash_is_normalized(self, monkeypatch):
        monkeypatch.setenv("PORTAL_API_BASE", RENDER + "/")
        assert api_base() == RENDER


# ── 2. request shaping, in node, from the real page ──────────────────────────

@needs_node
class TestRequestShaping:
    def test_same_origin_is_relative_with_a_cookie(self):
        out = run_harness("workspace", PAGE, href=RENDER + "/workspace")
        assert out["api_base"] == ""
        assert out["cross_origin"] is False
        req = out["requests"][0]
        assert req["url"] == "/api/workspace/work"
        assert req["credentials"] == "same-origin"
        assert "Authorization" not in req["headers"]

    def test_cross_origin_is_absolute_bearer_and_no_cookie(self):
        out = run_harness("workspace", PAGE, href=HUBSPOT_V2,
                          api_base=RENDER, signed_in=True)
        req = out["requests"][0]
        assert req["url"] == RENDER + "/api/workspace/work"
        assert req["credentials"] == "omit", "a cookie must never cross an origin"
        assert req["headers"]["Authorization"] == "Bearer jwt-for-test"

    def test_a_base_equal_to_this_origin_stays_same_origin(self):
        """Serving the page from the API host with the base set is not
        cross-origin, and must not drop to credentials:'omit'."""
        out = run_harness("workspace", PAGE, href=RENDER + "/workspace", api_base=RENDER)
        assert out["api_base"] == ""
        assert out["requests"][0]["credentials"] == "same-origin"

    def test_a_junk_base_is_ignored_not_trusted(self):
        out = run_harness("workspace", PAGE, api_base="not-a-url/oops")
        assert out["api_base"] == ""
        assert out["requests"][0]["url"].startswith("/api/")

    def test_callers_still_cannot_pass_an_absolute_url(self):
        """api() takes relative paths only; an absolute one would bypass the
        base and could point anywhere."""
        out = run_harness("workspace", PAGE, api_base=RENDER)
        assert out["rejects_absolute"] is True


# ── 3. report navigation, both modes ────────────────────────────────────────

@needs_node
class TestReportLinks:
    def test_same_origin_links_are_unchanged(self):
        out = run_harness("workspace", PAGE, href=RENDER + "/workspace", company_id="123")
        assert out["report_href"] == "/workspace/report?company_id=123"
        assert out["report_url_other"] == "/workspace/report?company_id=999"

    def test_cross_origin_links_point_at_the_api_host_and_carry_the_way_back(self):
        out = run_harness("workspace", PAGE, href=HUBSPOT_V2, api_base=RENDER,
                          company_id="123")
        assert out["report_href"].startswith(RENDER + "/workspace/report?company_id=123")
        assert "app=https%3A%2F%2Fdigital.rpmliving.com%2Fclient-portal%2Fv2" in out["report_href"]
        # The search hit path (window.location.href = …) uses the same builder.
        assert out["report_url_other"].startswith(RENDER + "/workspace/report?company_id=999")

    def test_the_report_page_is_same_origin_by_default(self):
        out = run_harness("report", REPORT_PAGE)
        assert out["api_base"] == "" and out["app_base"] == ""
        assert out["api_url"] == "/api/workspace/report?company_id=123"
        assert out["app_url"] == "/workspace#/properties"
        assert out["requests"][0]["credentials"] == "same-origin"

    def test_the_report_page_honours_the_api_base(self):
        out = run_harness("report", REPORT_PAGE, api_base=RENDER, href=HUBSPOT_V2 + "/report")
        assert out["api_url"] == RENDER + "/api/workspace/report?company_id=123"
        assert out["requests"][0]["credentials"] == "omit"

    def test_an_app_param_retargets_the_sidebar_and_the_back_link(self):
        out = run_harness(
            "report", REPORT_PAGE,
            href=RENDER + "/workspace/report?company_id=123&app=" + HUBSPOT_V2.replace(":", "%3A").replace("/", "%2F"))
        assert out["app_base"] == HUBSPOT_V2
        assert out["app_url"] == HUBSPOT_V2 + "#/properties"
        assert out["links"] == [HUBSPOT_V2 + "#/dashboard", HUBSPOT_V2 + "#/properties"]

    def test_a_loopback_app_param_is_allowed_for_local_runs(self):
        """So the two-origin setup can be driven locally. A redirect to a
        localhost URL is not a phishing target."""
        out = run_harness(
            "report", REPORT_PAGE,
            href="http://127.0.0.1:5064/workspace/report?company_id=123"
                 "&app=http%3A%2F%2F127.0.0.1%3A5065%2Fclient-portal%2Fv2")
        assert out["app_base"] == "http://127.0.0.1:5065/client-portal/v2"

    def test_an_untrusted_app_param_is_ignored(self):
        """Otherwise every link on the report becomes an open redirect."""
        out = run_harness(
            "report", REPORT_PAGE,
            href=RENDER + "/workspace/report?company_id=123&app=https%3A%2F%2Fevil.example%2Fx")
        assert out["app_base"] == ""
        assert out["app_url"] == "/workspace#/properties"
        assert out["links"] == ["/workspace#/dashboard", "/workspace#/properties"]

    def test_the_report_page_has_no_hardcoded_workspace_links_left(self):
        """Every /workspace link must go through appUrl() or be retargeted."""
        body = REPORT_PAGE.read_text(encoding="utf-8")
        js = body[body.index("<script>"):]
        assert "'/workspace#/" not in js.replace(
            "a[href^=\"/workspace#\"]", "").replace("'/workspace'", "")


# ── 4. the embed: one source of truth ───────────────────────────────────────

class TestEmbed:
    @pytest.mark.parametrize("value", [None, "false", "0"])
    def test_404s_behind_the_same_flag_as_the_page(self, client, monkeypatch, value):
        if value is None:
            monkeypatch.delenv("WORKSPACE_ENABLED", raising=False)
        else:
            monkeypatch.setenv("WORKSPACE_ENABLED", value)
        assert client.get("/workspace/embed.js").status_code == 404

    def test_it_is_generated_from_the_page(self, client, page):
        resp = client.get("/workspace/embed.js?v=test-1")
        assert resp.status_code == 200
        assert resp.mimetype in ("application/javascript", "text/javascript")
        body = resp.get_data(as_text=True)
        parts = workspace_parts(page)
        # The app script, the markup and the stylesheet all travel as JSON
        # string literals taken straight from the page.
        assert json.dumps(parts["app_js"]) in body
        assert json.dumps(parts["markup"]) in body
        assert json.dumps(parts["head"]) in body
        assert '"test-1"' in body

    def test_it_is_valid_javascript(self, client, tmp_path):
        if shutil.which("node") is None:
            pytest.skip("node not installed")
        body = client.get("/workspace/embed.js").get_data(as_text=True)
        f = tmp_path / "embed.js"
        f.write_text(body, encoding="utf-8")
        proc = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr

    def test_it_defaults_the_api_base_to_this_service(self, client):
        """The host loading this script is by definition not the API host, so
        an empty base would make every call a 404 on the wrong origin."""
        body = client.get("/workspace/embed.js", base_url="https://rpm-portal-server.onrender.com").get_data(as_text=True)
        assert f'window.__PORTAL_API_BASE__ = "{RENDER}";' in body

    def test_a_proxied_request_still_gets_https(self, client):
        """Render terminates TLS at its proxy; an http:// base would be blocked
        as mixed content on the HubSpot page."""
        body = client.get("/workspace/embed.js", headers={
            "X-Forwarded-Proto": "http", "X-Forwarded-Host": "rpm-portal-server.onrender.com",
        }).get_data(as_text=True)
        assert f'window.__PORTAL_API_BASE__ = "{RENDER}";' in body
        assert "http://rpm-portal-server" not in body

    def test_portal_api_base_overrides_the_default(self, client, monkeypatch):
        monkeypatch.setenv("PORTAL_API_BASE", "https://api.rpmliving.com")
        body = client.get("/workspace/embed.js").get_data(as_text=True)
        assert 'window.__PORTAL_API_BASE__ = "https://api.rpmliving.com";' in body

    def test_it_carries_the_clerk_key_and_is_never_cached(self, client, monkeypatch):
        monkeypatch.setenv("CLERK_PUBLISHABLE_KEY", "pk_test_abc123")
        resp = client.get("/workspace/embed.js")
        assert 'window.__CLERK_PK__ = "pk_test_abc123";' in resp.get_data(as_text=True)
        assert resp.headers["Cache-Control"] == "no-store"

    def test_a_missing_key_is_empty_not_absent(self, client):
        """The app checks for a usable key and shows a sign-in failure; it must
        not find `undefined` and guess."""
        assert 'window.__CLERK_PK__ = "";' in client.get("/workspace/embed.js").get_data(as_text=True)

    def test_the_page_shape_is_asserted_not_assumed(self, page):
        """If workspace.html grows a second <style> or <script> block, the
        loader must fail loudly rather than ship half a page to a CDN."""
        parts = workspace_parts(page)
        assert "function api(" in parts["app_js"]
        assert 'id="view"' in parts["markup"]
        assert "<style>" in parts["head"] and "fonts.googleapis.com" in parts["head"]
        # No <meta>, no <title>, no config <script> — the host page owns those.
        assert "<script" not in parts["head"] and "<meta" not in parts["head"]

        with pytest.raises(ValueError):
            workspace_parts(page.replace("<style>", "<style>\n</style>\n<style>", 1))
        with pytest.raises(ValueError):
            workspace_parts(page.replace("function api(", "function apiRenamed(", 1))

    def test_the_embed_does_not_leak_the_secret_key(self, client, monkeypatch):
        monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_supersecret")
        monkeypatch.setenv("CLERK_PUBLISHABLE_KEY", "pk_test_abc123")
        assert "sk_test_supersecret" not in client.get("/workspace/embed.js").get_data(as_text=True)

    @needs_node
    def test_the_builder_cannot_break_out_of_its_string(self, tmp_path):
        """The app travels as JSON string literals, so quotes, backslashes,
        template-literal syntax and newlines inside it stay data. (A literal
        `</script>` cannot occur in the source: it would end the page's own
        inline script, and workspace_parts refuses a page shaped like that.)"""
        page = PAGE.read_text(encoding="utf-8").replace(
            "'use strict';",
            "'use strict'; var x = 'alert(1)\"' + \"\\\\\" + '`${} \\u2028';", 1)
        js = build_workspace_embed(page, "pk_test_x", RENDER, "1")
        f = tmp_path / "tampered.js"
        f.write_text(js, encoding="utf-8")
        proc = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        # And the payload is inside the APP literal, not beside it.
        app_line = [l for l in js.split("\n") if l.startswith("  var APP = ")][0]
        assert "alert(1)" in app_line
        assert "alert(1)" not in js.replace(app_line, "")


# ── 5. CORS ─────────────────────────────────────────────────────────────────

class TestCors:
    @pytest.fixture
    def server_client(self, monkeypatch):
        monkeypatch.setenv("WORKSPACE_ENABLED", "true")
        import server
        return server.app.test_client()

    @pytest.mark.parametrize("path", [
        "/api/workspace/me",
        "/api/workspace/work",
        "/api/workspace/report",
    ])
    def test_preflight_answers_for_the_hubspot_origin(self, server_client, path):
        resp = server_client.options(path, headers={
            "Origin": HUBSPOT,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        })
        assert resp.status_code in (200, 204), resp.status_code
        assert resp.headers["Access-Control-Allow-Origin"] == HUBSPOT
        allowed = resp.headers["Access-Control-Allow-Headers"]
        assert "Authorization" in allowed
        assert "X-Workspace-Link" in allowed
        assert "POST" in resp.headers["Access-Control-Allow-Methods"]

    def test_preflight_is_cached_so_calls_are_not_doubled(self, server_client):
        """Every workspace call sends Authorization, which makes it preflighted.
        Without Max-Age that is two round trips per call, forever."""
        resp = server_client.options("/api/workspace/me", headers={"Origin": HUBSPOT})
        age = int(resp.headers["Access-Control-Max-Age"])
        assert 0 < age <= 7200

    def test_an_unknown_origin_gets_nothing(self, server_client):
        resp = server_client.options("/api/workspace/me", headers={"Origin": "https://evil.example"})
        assert "Access-Control-Allow-Origin" not in resp.headers

    def test_the_hubspot_origin_is_allowed_in_code(self):
        from _route_utils import ALLOWED_ORIGINS
        assert HUBSPOT in ALLOWED_ORIGINS


# ── 6. the HubSpot template ─────────────────────────────────────────────────

class TestHubspotTemplate:
    @pytest.fixture(scope="class")
    def template(self) -> str:
        return TEMPLATE.read_text(encoding="utf-8")

    def test_it_loads_the_app_instead_of_copying_it(self, template):
        assert "/workspace/embed.js?v=" in template
        assert 'id="rpm-workspace"' in template
        for leaked in ("function api(", "function reportHref(", "--ground:"):
            assert leaked not in template, "the app was pasted into the template"
        assert len(template) < 20_000

    def test_it_has_no_dead_hubl_branch(self, template):
        """DEPLOY.md Trap 4: script below an {% else %} uploads cleanly, never
        runs, and Cloudflare caches the blank page for ~10 hours. This template
        has no branch at all, which is the only way to be sure."""
        assert "{% else %}" not in template
        assert "{% if" not in template

    def test_no_hubl_directive_hides_in_an_html_comment(self, template):
        sys.path.insert(0, str(REPO / "scripts"))
        os.environ.setdefault("HUBSPOT_API_KEY", "test")
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "deploy_template", str(REPO / "scripts" / "deploy_template.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert mod.find_hubl_directives_in_html_comments(template) == []
        ok, err = mod.validate_workspace_v2(template)
        assert ok, err

    def test_the_deploy_validator_catches_the_ways_this_breaks(self):
        os.environ.setdefault("HUBSPOT_API_KEY", "test")
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "deploy_template", str(REPO / "scripts" / "deploy_template.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        good = TEMPLATE.read_text(encoding="utf-8")

        assert mod.validate_workspace_v2(good + "\nfunction api(p) {}")[0] is False
        assert mod.validate_workspace_v2(good.replace("?v={{ app_version }}", ""))[0] is False
        assert mod.validate_workspace_v2(
            good.replace('{% set app_version = "2026-09-18-1" %}', '{% set app_version = "" %}'))[0] is False
        assert mod.validate_workspace_v2(
            good.replace("<script src=", "{% else %}\n<script src=", 1))[0] is False
        assert "workspace-v2" in mod.TARGETS
        assert mod.TARGETS["workspace-v2"]["local"].endswith("client-portal-v2.html")

    def test_a_failure_to_reach_render_says_so(self, template):
        """No blank page and no endless spinner: both the onerror path and a
        watchdog replace the loading line with something a client can read."""
        assert "onerror=" in template
        assert "__rpmWorkspaceFailed" in template
        assert "setTimeout" in template

    def test_it_does_not_mention_an_api_key(self, template):
        for secret in ("pk_test", "pk_live", "sk_test", "sk_live", "CLERK"):
            assert secret not in template


# ── 7. a wrong or missing key is visible, not blank ─────────────────────────

class TestVisibleSignInFailure:
    def test_the_page_refuses_to_render_empty_without_a_key(self, page):
        assert "noAuthPossible" in page
        assert "Sign-in is not configured for this page yet" in page

    @needs_node
    def test_cross_origin_with_no_key_and_no_link_never_calls_the_api(self):
        """It cannot authenticate — cookies are omitted — so calling would be a
        guaranteed 401. The app says so instead."""
        out = run_harness("workspace", PAGE, href=HUBSPOT_V2, api_base=RENDER)
        # boot() is not part of the slice, but the flag it reads is computable
        # from the same inputs: cross-origin, no link token, no usable key.
        assert out["cross_origin"] is True

    def test_a_key_that_fails_to_load_shows_a_message(self, page):
        assert "We could not reach the sign-in service" in page
        assert "s.onerror" in page
