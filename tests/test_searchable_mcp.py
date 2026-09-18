"""The Searchable MCP client, and the health route that proves it works.

Context worth keeping: the previous client (`searchable_client.py`) pointed at
`https://api.searchable.ai/v1`, a host that does not resolve. Its normalizers
were written from captured payloads and are fine; its transport aimed at a
server that has never existed, and nothing caught that because nothing ever
made a live call.

So these tests care most about the two things that mistake teaches:

  * A credential that is SET is not a credential that is ACCEPTED. `probe()`
    reports those separately, because only the second one means anything.
  * A failure degrades to a reason, never to a zero or an empty reading. An
    unmeasured property must read as "not measured yet".
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
from io import BytesIO

import pytest
from flask import Flask, jsonify

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "webhook-server"))
sys.path.insert(0, ROOT)

import searchable_mcp as sm  # noqa: E402

VARS = ("SEARCHABLE_API_TOKEN", "SEARCHABLE_MCP_REFRESH_TOKEN",
        "SEARCHABLE_MCP_CLIENT_ID", "SEARCHABLE_MCP_URL",
        "SEARCHABLE_OAUTH_TOKEN_URL")


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for name in VARS:
        monkeypatch.delenv(name, raising=False)
    sm.clear_cache()
    yield
    sm.clear_cache()


class FakeHTTP:
    """Stands in for urllib.request.urlopen. Records what was sent."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.sent = []

    def __call__(self, req, timeout=None):
        self.sent.append({
            "url": req.full_url,
            "method": req.get_method(),
            "headers": {k.lower(): v for k, v in req.header_items()},
            "body": (req.data or b"").decode("utf-8") or None,
        })
        reply = self.replies.pop(0) if self.replies else ""
        if isinstance(reply, Exception):
            raise reply
        return _Resp(reply)


class _Resp:
    def __init__(self, text):
        self._b = BytesIO(text.encode("utf-8") if isinstance(text, str) else text)

    def read(self):
        return self._b.read()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def rpc(result):
    return json.dumps({"jsonrpc": "2.0", "id": 1, "result": result})


TOOLS = rpc({"tools": [
    {"name": "get_visibility", "description": "AI visibility for a project",
     "inputSchema": {"properties": {"project_id": {}, "days": {}}}},
    {"name": "list_projects", "description": "Projects in the account",
     "inputSchema": {"properties": {}}},
]})


# --- configuration ---------------------------------------------------------

class TestConfiguration:
    def test_nothing_set_is_not_configured(self):
        assert sm.is_configured() is False

    def test_a_static_token_is_enough(self, monkeypatch):
        monkeypatch.setenv("SEARCHABLE_API_TOKEN", "tok")
        assert sm.is_configured() is True

    def test_a_refresh_token_needs_its_client_id(self, monkeypatch):
        monkeypatch.setenv("SEARCHABLE_MCP_REFRESH_TOKEN", "r")
        assert sm.is_configured() is False
        monkeypatch.setenv("SEARCHABLE_MCP_CLIENT_ID", "c")
        assert sm.is_configured() is True

    def test_credential_names_never_leak_values(self, monkeypatch):
        monkeypatch.setenv("SEARCHABLE_API_TOKEN", "super-secret-value")
        names = sm.credential_names()
        assert names["SEARCHABLE_API_TOKEN"] is True
        assert "super-secret-value" not in json.dumps(names)

    def test_the_endpoint_defaults_to_the_real_one(self):
        """api.searchable.ai does not resolve; this is the host that answers."""
        assert sm.endpoint() == "https://app.searchable.com/api/mcp-server/mcp"

    def test_the_endpoint_can_be_overridden(self, monkeypatch):
        monkeypatch.setenv("SEARCHABLE_MCP_URL", "https://example.test/mcp")
        assert sm.endpoint() == "https://example.test/mcp"


# --- transport -------------------------------------------------------------

class TestTransport:
    def test_a_static_token_is_sent_as_a_bearer(self, monkeypatch):
        monkeypatch.setenv("SEARCHABLE_API_TOKEN", "tok-123")
        http = FakeHTTP(TOOLS)
        monkeypatch.setattr(sm.urllib.request, "urlopen", http)
        tools, reason = sm.list_tools()
        assert reason is None and [t["name"] for t in tools] == ["get_visibility", "list_projects"]
        assert http.sent[0]["headers"]["authorization"] == "Bearer tok-123"
        assert http.sent[0]["url"] == sm.DEFAULT_URL

    def test_it_speaks_json_rpc(self, monkeypatch):
        monkeypatch.setenv("SEARCHABLE_API_TOKEN", "t")
        http = FakeHTTP(TOOLS)
        monkeypatch.setattr(sm.urllib.request, "urlopen", http)
        sm.list_tools()
        body = json.loads(http.sent[0]["body"])
        assert body["jsonrpc"] == "2.0" and body["method"] == "tools/list"

    def test_an_sse_framed_reply_is_unwrapped(self, monkeypatch):
        """Their server may answer either way; ours does the same thing."""
        monkeypatch.setenv("SEARCHABLE_API_TOKEN", "t")
        monkeypatch.setattr(sm.urllib.request, "urlopen",
                            FakeHTTP("event: message\ndata: %s\n\n" % TOOLS))
        tools, reason = sm.list_tools()
        assert reason is None and len(tools) == 2

    def test_a_refused_credential_says_so_plainly(self, monkeypatch):
        monkeypatch.setenv("SEARCHABLE_API_TOKEN", "bad")
        monkeypatch.setattr(sm.urllib.request, "urlopen", FakeHTTP(
            urllib.error.HTTPError(sm.DEFAULT_URL, 401, "Unauthorized", {}, None)))
        tools, reason = sm.list_tools()
        assert tools is None and "refused our credentials" in reason

    def test_rate_limiting_is_its_own_message(self, monkeypatch):
        monkeypatch.setenv("SEARCHABLE_API_TOKEN", "t")
        monkeypatch.setattr(sm.urllib.request, "urlopen", FakeHTTP(
            urllib.error.HTTPError(sm.DEFAULT_URL, 429, "Too Many", {}, None)))
        _, reason = sm.list_tools()
        assert "rate-limiting" in reason

    def test_an_unreachable_host_is_a_reason_not_a_crash(self, monkeypatch):
        """The whole point of the old client's failure: this must not raise."""
        monkeypatch.setenv("SEARCHABLE_API_TOKEN", "t")
        monkeypatch.setattr(sm.urllib.request, "urlopen",
                            FakeHTTP(OSError("Name or service not known")))
        tools, reason = sm.list_tools()
        assert tools is None and "temporarily unreachable" in reason

    def test_a_jsonrpc_error_is_reported_not_treated_as_data(self, monkeypatch):
        monkeypatch.setenv("SEARCHABLE_API_TOKEN", "t")
        monkeypatch.setattr(sm.urllib.request, "urlopen", FakeHTTP(json.dumps(
            {"jsonrpc": "2.0", "id": 1, "error": {"code": -32602, "message": "bad project"}})))
        result, reason = sm.call("tools/list")
        assert result is None and "bad project" in reason

    def test_non_json_is_refused(self, monkeypatch):
        monkeypatch.setenv("SEARCHABLE_API_TOKEN", "t")
        monkeypatch.setattr(sm.urllib.request, "urlopen", FakeHTTP("<html>nope</html>"))
        result, reason = sm.call("tools/list")
        assert result is None and "not JSON" in reason


class TestToolResults:
    def test_structured_content_is_preferred(self, monkeypatch):
        monkeypatch.setenv("SEARCHABLE_API_TOKEN", "t")
        monkeypatch.setattr(sm.urllib.request, "urlopen", FakeHTTP(
            rpc({"structuredContent": {"score": 41}, "content": [{"type": "text", "text": "41"}]})))
        value, reason = sm.call_tool("get_visibility", {"project_id": "p1"})
        assert reason is None and value == {"score": 41}

    def test_a_json_string_in_a_text_block_is_parsed(self, monkeypatch):
        """Most servers answer this way, ours included."""
        monkeypatch.setenv("SEARCHABLE_API_TOKEN", "t")
        monkeypatch.setattr(sm.urllib.request, "urlopen", FakeHTTP(
            rpc({"content": [{"type": "text", "text": '{"score": 41}'}]})))
        value, _ = sm.call_tool("get_visibility")
        assert value == {"score": 41}

    def test_plain_text_survives_as_text(self, monkeypatch):
        monkeypatch.setenv("SEARCHABLE_API_TOKEN", "t")
        monkeypatch.setattr(sm.urllib.request, "urlopen", FakeHTTP(
            rpc({"content": [{"type": "text", "text": "not measured yet"}]})))
        value, _ = sm.call_tool("get_visibility")
        assert value == "not measured yet"

    def test_an_error_result_is_a_reason_not_a_value(self, monkeypatch):
        monkeypatch.setenv("SEARCHABLE_API_TOKEN", "t")
        monkeypatch.setattr(sm.urllib.request, "urlopen", FakeHTTP(
            rpc({"isError": True, "content": [{"type": "text", "text": "no such project"}]})))
        value, reason = sm.call_tool("get_visibility")
        assert value is None and reason

    def test_repeat_calls_are_cached(self, monkeypatch):
        monkeypatch.setenv("SEARCHABLE_API_TOKEN", "t")
        http = FakeHTTP(rpc({"structuredContent": {"score": 41}}))
        monkeypatch.setattr(sm.urllib.request, "urlopen", http)
        sm.call_tool("get_visibility", {"project_id": "p1"})
        sm.call_tool("get_visibility", {"project_id": "p1"})
        assert len(http.sent) == 1

    def test_different_arguments_are_different_cache_entries(self, monkeypatch):
        monkeypatch.setenv("SEARCHABLE_API_TOKEN", "t")
        http = FakeHTTP(rpc({"structuredContent": {"score": 1}}),
                        rpc({"structuredContent": {"score": 2}}))
        monkeypatch.setattr(sm.urllib.request, "urlopen", http)
        a, _ = sm.call_tool("get_visibility", {"project_id": "p1"})
        b, _ = sm.call_tool("get_visibility", {"project_id": "p2"})
        assert a != b and len(http.sent) == 2


# --- OAuth, only when there is no static token ------------------------------

class TestOAuth:
    def test_a_static_token_skips_the_oauth_dance_entirely(self, monkeypatch):
        monkeypatch.setenv("SEARCHABLE_API_TOKEN", "static")
        monkeypatch.setenv("SEARCHABLE_MCP_REFRESH_TOKEN", "r")
        monkeypatch.setenv("SEARCHABLE_MCP_CLIENT_ID", "c")
        http = FakeHTTP(TOOLS)
        monkeypatch.setattr(sm.urllib.request, "urlopen", http)
        sm.list_tools()
        assert len(http.sent) == 1                      # no token request
        assert http.sent[0]["url"] == sm.DEFAULT_URL

    def test_the_refresh_token_is_exchanged_then_used(self, monkeypatch):
        monkeypatch.setenv("SEARCHABLE_MCP_REFRESH_TOKEN", "refresh-1")
        monkeypatch.setenv("SEARCHABLE_MCP_CLIENT_ID", "client-1")
        http = FakeHTTP(json.dumps({"access_token": "fresh", "expires_in": 3600}), TOOLS)
        monkeypatch.setattr(sm.urllib.request, "urlopen", http)
        tools, reason = sm.list_tools()
        assert reason is None and len(tools) == 2
        assert http.sent[0]["url"] == sm.TOKEN_URL
        assert "grant_type=refresh_token" in http.sent[0]["body"]
        assert http.sent[1]["headers"]["authorization"] == "Bearer fresh"

    def test_the_access_token_is_reused_until_it_expires(self, monkeypatch):
        monkeypatch.setenv("SEARCHABLE_MCP_REFRESH_TOKEN", "r")
        monkeypatch.setenv("SEARCHABLE_MCP_CLIENT_ID", "c")
        http = FakeHTTP(json.dumps({"access_token": "fresh", "expires_in": 3600}),
                        TOOLS, TOOLS)
        monkeypatch.setattr(sm.urllib.request, "urlopen", http)
        sm.list_tools()
        sm.list_tools()
        assert sum(1 for s in http.sent if s["url"] == sm.TOKEN_URL) == 1

    def test_a_dead_grant_says_to_re_authorize(self, monkeypatch):
        monkeypatch.setenv("SEARCHABLE_MCP_REFRESH_TOKEN", "stale")
        monkeypatch.setenv("SEARCHABLE_MCP_CLIENT_ID", "c")
        monkeypatch.setattr(sm.urllib.request, "urlopen", FakeHTTP(
            urllib.error.HTTPError(sm.TOKEN_URL, 400, "Bad Request", {}, None)))
        tools, reason = sm.list_tools()
        assert tools is None and "searchable_auth.py" in reason


# --- the read-only rule ----------------------------------------------------

class TestWriteGuard:
    """Searchable exposes four tools that change ITS account. The arrangement is
    that the vendor measures and the portal decides, so the portal never writes
    there — and a rule or an agent must not be able to reach one by accident."""

    @pytest.mark.parametrize("name", sorted(sm.WRITE_TOOLS))
    def test_every_write_tool_is_refused(self, name, monkeypatch):
        monkeypatch.setenv("SEARCHABLE_API_TOKEN", "t")
        http = FakeHTTP(TOOLS)
        monkeypatch.setattr(sm.urllib.request, "urlopen", http)
        with pytest.raises(sm.WriteRefused):
            sm.call_tool(name, {"confirm": True})
        assert http.sent == [], "a refused write must never reach the network"

    def test_the_refusal_names_the_tool_and_the_reason(self, monkeypatch):
        monkeypatch.setenv("SEARCHABLE_API_TOKEN", "t")
        with pytest.raises(sm.WriteRefused) as exc:
            sm.call_tool("trigger_audit")
        assert "trigger_audit" in str(exc.value)
        assert "does not write" in str(exc.value)

    def test_an_unknown_tool_is_declined_but_not_an_exception(self, monkeypatch):
        """A tool we have not vetted is a config gap, not a safety incident."""
        monkeypatch.setenv("SEARCHABLE_API_TOKEN", "t")
        value, reason = sm.call_tool("some_new_tool")
        assert value is None and "READ_TOOLS" in reason

    def test_the_two_sets_never_overlap(self):
        assert not (sm.READ_TOOLS & sm.WRITE_TOOLS)

    def test_the_read_list_covers_what_the_service_needs(self):
        """Named explicitly: if one of these disappears from the allowlist, the
        GEO/AEO service loses a panel and it should be a deliberate choice."""
        for needed in ("list_projects", "get_visibility", "get_visibility_history",
                       "get_share_of_voice", "get_sentiment", "get_opportunities",
                       "get_site_health", "get_ai_traffic", "get_prompt_answers",
                       "get_competitors"):
            assert needed in sm.READ_TOOLS


# --- the probe -------------------------------------------------------------

class TestProbe:
    def test_set_and_accepted_are_reported_separately(self, monkeypatch):
        """The lesson from the dead REST base: 'configured' proved nothing."""
        monkeypatch.setenv("SEARCHABLE_API_TOKEN", "bad")
        monkeypatch.setattr(sm.urllib.request, "urlopen", FakeHTTP(
            urllib.error.HTTPError(sm.DEFAULT_URL, 401, "Unauthorized", {}, None)))
        out = sm.probe()
        assert out["credentials_present"]["SEARCHABLE_API_TOKEN"] is True
        assert out["static_token"]["accepted"] is False
        assert out["works"] is False

    def test_a_working_token_reports_the_tools(self, monkeypatch):
        monkeypatch.setenv("SEARCHABLE_API_TOKEN", "good")
        monkeypatch.setattr(sm.urllib.request, "urlopen", FakeHTTP(TOOLS))
        out = sm.probe()
        assert out["works"] is True
        assert [t["name"] for t in out["tools"]] == ["get_visibility", "list_projects"]
        assert out["tools"][0]["args"] == ["days", "project_id"]

    def test_it_falls_back_to_oauth_when_the_static_token_is_refused(self, monkeypatch):
        monkeypatch.setenv("SEARCHABLE_API_TOKEN", "bad")
        monkeypatch.setenv("SEARCHABLE_MCP_REFRESH_TOKEN", "r")
        monkeypatch.setenv("SEARCHABLE_MCP_CLIENT_ID", "c")
        monkeypatch.setattr(sm.urllib.request, "urlopen", FakeHTTP(
            urllib.error.HTTPError(sm.DEFAULT_URL, 401, "Unauthorized", {}, None),
            json.dumps({"access_token": "fresh", "expires_in": 3600}),
            TOOLS))
        out = sm.probe()
        assert out["static_token"]["accepted"] is False
        assert out["oauth"]["accepted"] is True
        assert out["works"] is True

    def test_the_probe_never_echoes_a_credential(self, monkeypatch):
        monkeypatch.setenv("SEARCHABLE_API_TOKEN", "secret-value-do-not-print")
        monkeypatch.setattr(sm.urllib.request, "urlopen", FakeHTTP(TOOLS))
        assert "secret-value-do-not-print" not in json.dumps(sm.probe())


# --- the route -------------------------------------------------------------

class TestHealthRoute:
    @pytest.fixture
    def client(self, monkeypatch):
        monkeypatch.setenv("INTERNAL_API_KEY", "internal-secret")
        import server
        server.app.config["TESTING"] = True
        return server.app.test_client()

    def test_it_needs_the_internal_key(self, client):
        assert client.get("/api/internal/searchable-health").status_code == 401

    def test_unconfigured_says_what_to_do(self, client):
        resp = client.get("/api/internal/searchable-health",
                          headers={"X-Internal-Key": "internal-secret"})
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["configured"] is False
        assert "searchable_auth.py" in body["next"]

    def test_it_reports_the_probe(self, client, monkeypatch):
        monkeypatch.setenv("SEARCHABLE_API_TOKEN", "good")
        monkeypatch.setattr(sm.urllib.request, "urlopen", FakeHTTP(TOOLS))
        resp = client.get("/api/internal/searchable-health?tools=1",
                          headers={"X-Internal-Key": "internal-secret"})
        body = resp.get_json()
        assert body["configured"] is True and body["works"] is True
        assert [t["name"] for t in body["tools"]] == ["get_visibility", "list_projects"]

    def test_it_can_run_one_read_tool_for_diagnosis(self, client, monkeypatch):
        """So "does this connection see our properties" is answerable from the
        machine that holds the token, without anyone pasting it around."""
        monkeypatch.setenv("SEARCHABLE_API_TOKEN", "good")
        monkeypatch.setattr(sm.urllib.request, "urlopen", FakeHTTP(
            rpc({"structuredContent": {"projects": [{"name": "Atwood", "id": "p1"}]}})))
        body = client.get("/api/internal/searchable-health?tool=list_projects",
                          headers={"X-Internal-Key": "internal-secret"}).get_json()
        assert body["result"]["projects"][0]["name"] == "Atwood"
        assert body["reason"] is None

    def test_the_diagnostic_refuses_a_write_tool(self, client, monkeypatch):
        monkeypatch.setenv("SEARCHABLE_API_TOKEN", "good")
        resp = client.get("/api/internal/searchable-health?tool=trigger_audit&args=%7B%22confirm%22%3Atrue%7D",
                          headers={"X-Internal-Key": "internal-secret"})
        assert resp.status_code == 403
        assert "does not write" in resp.get_json()["error"]

    def test_bad_args_are_refused_before_the_call(self, client, monkeypatch):
        monkeypatch.setenv("SEARCHABLE_API_TOKEN", "good")
        resp = client.get("/api/internal/searchable-health?tool=list_projects&args=notjson",
                          headers={"X-Internal-Key": "internal-secret"})
        assert resp.status_code == 400

    def test_tools_are_omitted_unless_asked_for(self, client, monkeypatch):
        monkeypatch.setenv("SEARCHABLE_API_TOKEN", "good")
        monkeypatch.setattr(sm.urllib.request, "urlopen", FakeHTTP(TOOLS))
        body = client.get("/api/internal/searchable-health",
                          headers={"X-Internal-Key": "internal-secret"}).get_json()
        assert "tools" not in body and body["works"] is True
