"""Contract tests for the MCP endpoint and the context tools.

No credentials needed: every connector is stubbed. What these pin is the part
that would embarrass us — the token gate, the internal-field exclusions, the
promise that a missing source yields a gap rather than a zero, and the JSON-RPC
shapes a real client depends on.
"""
from __future__ import annotations

import json
import os
import sys
import types

import pytest
from flask import Flask

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from routes.mcp import mcp_bp  # noqa: E402
from skills import mcp_context  # noqa: E402

TOKEN = "test-token-abc"
AUTH = {"Authorization": "Bearer " + TOKEN}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("MCP_BEARER_TOKEN", TOKEN)
    monkeypatch.delenv("MCP_TOKENS", raising=False)
    monkeypatch.delenv("MCP_ENABLED", raising=False)
    app = Flask(__name__)
    app.register_blueprint(mcp_bp)
    app.config["TESTING"] = True
    return app.test_client()


def _rpc(client, method, params=None, req_id=1, headers=None):
    body = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        body["params"] = params
    resp = client.post("/mcp", json=body, headers=headers if headers is not None else AUTH)
    payload = None
    if resp.data:
        text = resp.get_data(as_text=True)
        if text.startswith("event:"):                    # SSE framing
            text = text.split("data: ", 1)[1].strip()
        try:
            payload = json.loads(text)
        except ValueError:
            payload = None
    return resp, payload


# --- the door --------------------------------------------------------------

class TestAccess:
    def test_unconfigured_endpoint_is_invisible(self, client, monkeypatch):
        monkeypatch.delenv("MCP_BEARER_TOKEN", raising=False)
        assert client.post("/mcp", json={}).status_code == 404
        assert client.get("/mcp/health").status_code == 404

    def test_no_token_is_401_with_a_challenge(self, client):
        resp = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1,
                                         "method": "tools/list"}, headers={})
        assert resp.status_code == 401
        assert "Bearer" in resp.headers.get("WWW-Authenticate", "")

    @pytest.mark.parametrize("header", [
        {"Authorization": "Bearer wrong"},
        {"Authorization": "Bearer "},
        {"Authorization": TOKEN},                 # missing the scheme
        {"Authorization": "Basic " + TOKEN},
    ])
    def test_bad_credentials_are_refused(self, client, header):
        resp, _ = _rpc(client, "tools/list", headers=header)
        assert resp.status_code == 401

    def test_labeled_tokens_each_work(self, client, monkeypatch):
        monkeypatch.delenv("MCP_BEARER_TOKEN", raising=False)
        monkeypatch.setenv("MCP_TOKENS", "vendor:aaa, internal:bbb")
        for token in ("aaa", "bbb"):
            resp, _ = _rpc(client, "ping", headers={"Authorization": "Bearer " + token})
            assert resp.status_code == 200
        resp, _ = _rpc(client, "ping", headers={"Authorization": "Bearer ccc"})
        assert resp.status_code == 401

    def test_explicit_disable_wins_over_a_configured_token(self, client, monkeypatch):
        monkeypatch.setenv("MCP_ENABLED", "false")
        assert client.post("/mcp", json={}, headers=AUTH).status_code == 404

    def test_health_needs_no_token(self, client):
        resp = client.get("/mcp/health")
        assert resp.status_code == 200
        assert resp.get_json()["tools"] == len(mcp_context.TOOLS)

    def test_get_on_the_endpoint_is_405(self, client):
        assert client.get("/mcp", headers=AUTH).status_code == 405


# --- protocol --------------------------------------------------------------

class TestProtocol:
    def test_initialize_returns_a_session_and_instructions(self, client):
        resp, payload = _rpc(client, "initialize",
                             {"protocolVersion": "2025-06-18", "capabilities": {},
                              "clientInfo": {"name": "probe", "version": "1"}})
        assert resp.status_code == 200
        assert resp.headers.get("Mcp-Session-Id")
        result = payload["result"]
        assert result["protocolVersion"] == "2025-06-18"
        assert result["serverInfo"]["name"] == "rpm-portal-context"
        assert result["capabilities"]["tools"] == {"listChanged": False}
        assert "Special Ad Category" in result["instructions"]

    def test_an_unknown_protocol_version_gets_our_newest(self, client):
        _, payload = _rpc(client, "initialize", {"protocolVersion": "1999-01-01"})
        assert payload["result"]["protocolVersion"] == "2025-06-18"

    def test_sse_framing_when_the_client_asks_for_it(self, client):
        resp = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                           headers=dict(AUTH, Accept="application/json, text/event-stream"))
        assert resp.status_code == 200
        assert resp.mimetype == "text/event-stream"
        text = resp.get_data(as_text=True)
        assert text.startswith("event: message\ndata: ")
        assert json.loads(text.split("data: ", 1)[1].strip())["result"] == {}

    def test_plain_json_when_it_does_not(self, client):
        resp, payload = _rpc(client, "ping")
        assert resp.mimetype == "application/json"
        assert payload == {"jsonrpc": "2.0", "id": 1, "result": {}}

    def test_a_notification_gets_no_body(self, client):
        resp = client.post("/mcp", json={"jsonrpc": "2.0",
                                         "method": "notifications/initialized"},
                           headers=AUTH)
        assert resp.status_code == 202
        assert resp.data == b""

    def test_unknown_method_is_a_jsonrpc_error(self, client):
        _, payload = _rpc(client, "does/not/exist")
        assert payload["error"]["code"] == -32601

    def test_malformed_json_is_a_parse_error(self, client):
        resp = client.post("/mcp", data="{not json", headers=AUTH,
                           content_type="application/json")
        assert json.loads(resp.get_data(as_text=True))["error"]["code"] == -32700

    def test_batches_are_refused_clearly(self, client):
        resp = client.post("/mcp", json=[{"jsonrpc": "2.0", "id": 1, "method": "ping"}],
                           headers=AUTH)
        err = json.loads(resp.get_data(as_text=True))["error"]
        assert err["code"] == -32600 and "one at a time" in err["message"]

    def test_probes_for_unsupported_features_answer_empty(self, client):
        for method, key in (("resources/list", "resources"), ("prompts/list", "prompts")):
            _, payload = _rpc(client, method)
            assert payload["result"][key] == []


class TestToolsList:
    def test_every_tool_is_described_for_a_stranger(self, client):
        _, payload = _rpc(client, "tools/list")
        tools = payload["result"]["tools"]
        assert {t["name"] for t in tools} == {
            "find_property", "get_property_brief", "get_availability",
            "get_leasing_funnel", "get_spend_authorization", "get_open_work",
            "get_metric_rules", "get_compliance_rules"}
        for t in tools:
            assert "handler" not in t                      # never serialize the callable
            assert len(t["description"]) > 80              # the description IS the prompt
            assert t["inputSchema"]["type"] == "object"

    def test_no_tool_can_write(self):
        for name in mcp_context.BY_NAME:
            assert not name.startswith(("set_", "update_", "write_", "create_",
                                        "delete_", "patch_", "post_"))


class TestToolsCall:
    def test_unknown_tool_is_an_error_result_not_a_transport_error(self, client):
        _, payload = _rpc(client, "tools/call", {"name": "nope", "arguments": {}})
        assert payload["result"]["isError"] is True
        assert "tools/list" in payload["result"]["content"][0]["text"]

    def test_missing_required_argument_is_named(self, client):
        _, payload = _rpc(client, "tools/call",
                          {"name": "find_property", "arguments": {}})
        assert payload["result"]["isError"] is True
        assert "identifier" in payload["result"]["content"][0]["text"]

    def test_unexpected_argument_is_named_with_what_is_accepted(self, client):
        _, payload = _rpc(client, "tools/call",
                          {"name": "get_metric_rules", "arguments": {"month": "2026-08"}})
        text = payload["result"]["content"][0]["text"]
        assert payload["result"]["isError"] is True and "month" in text

    def test_a_rules_tool_returns_both_text_and_structure(self, client):
        _, payload = _rpc(client, "tools/call",
                          {"name": "get_compliance_rules", "arguments": {}})
        result = payload["result"]
        assert result["isError"] is False
        assert json.loads(result["content"][0]["text"])["category"].startswith("Housing")
        assert "never_propose" in result["structuredContent"]

    def test_a_failing_connector_becomes_a_readable_error(self, client, monkeypatch):
        def _boom(**_kw):
            raise RuntimeError("hubspot exploded")

        monkeypatch.setitem(mcp_context.BY_NAME["find_property"], "handler", _boom)
        _, payload = _rpc(client, "tools/call",
                          {"name": "find_property", "arguments": {"identifier": "x"}})
        assert payload["result"]["isError"] is True
        assert "temporarily unavailable" in payload["result"]["content"][0]["text"]

    def test_a_tool_error_payload_sets_isError(self, client, monkeypatch):
        monkeypatch.setitem(mcp_context.BY_NAME["find_property"], "handler",
                            lambda **_kw: {"error": "property_not_found"})
        _, payload = _rpc(client, "tools/call",
                          {"name": "find_property", "arguments": {"identifier": "x"}})
        assert payload["result"]["isError"] is True


# --- the tools themselves --------------------------------------------------

class _Identity:
    def __init__(self, **kw):
        self._d = {"company_id": "555", "uuid": "u-555", "name": "Test Property",
                   "market": "Phoenix", "unit_count": "300", "property_code": "TP01",
                   "occupancy": "Stable", "aptiq_property_id": None,
                   "hyly_property_id": None, "ga4_property_id": "ga4-1",
                   "google_ads_customer_id": None}
        self._d.update(kw)
        self.is_managed = True
        self.is_lease_up = False

    def to_dict(self):
        return dict(self._d)

    def missing(self):
        return [k for k in ("uuid", "aptiq_property_id") if not self._d.get(k)]


@pytest.fixture
def identity(monkeypatch):
    ident = _Identity()
    monkeypatch.setattr(mcp_context, "_resolve", lambda _id: (ident, None))
    return ident


class TestTools:
    def test_find_property_says_which_sources_exist(self, identity):
        out = mcp_context.find_property("Test Property")
        assert out["company_id"] == "555"
        assert out["units"] == "300"                       # unit_count, not "units"
        assert out["available_data"] == {"leasing_funnel": False, "availability": False,
                                         "google_ads": False, "ga4": True}

    def test_availability_without_a_source_gaps_instead_of_zeroing(self, identity):
        out = mcp_context.get_availability("555")
        assert out["occupancy"] is None and out["available_units"] is None
        assert out["gaps"][0]["source"] == "aptiq"

    def test_funnel_without_the_program_gaps_and_warns_against_inference(self, identity):
        out = mcp_context.get_leasing_funnel("555", month="2026-08")
        assert out["channels"] == {}
        assert "never infer leases from leads" in out["gaps"][0]["message"].lower() or \
               "do not infer" in out["gaps"][0]["message"].lower()

    def test_june_2026_is_refused_by_rule(self, monkeypatch):
        monkeypatch.setattr(mcp_context, "_resolve",
                            lambda _id: (_Identity(hyly_property_id="h-1"), None))
        out = mcp_context.get_leasing_funnel("555", month="2026-06")
        assert out["channels"] == {}
        assert "backfill artifact" in out["gaps"][0]["message"]

    def test_funnel_defaults_to_the_last_full_month(self, monkeypatch):
        captured = {}

        fake = types.SimpleNamespace(
            is_configured=lambda: True,
            get_channel_summary=lambda pid, start_date, end_date: captured.update(
                start=start_date, end=end_date) or {"paid_search": {"leases": 3}},
            get_data_freshness=lambda: {"max_date": "2026-09-16"},
        )
        monkeypatch.setitem(sys.modules, "hyly_client", fake)
        monkeypatch.setattr(mcp_context, "_resolve",
                            lambda _id: (_Identity(hyly_property_id="h-1"), None))
        monkeypatch.setattr(mcp_context, "month_bounds",
                            lambda m: ("2026-08", "2026-08-01", "2026-08-31"))
        out = mcp_context.get_leasing_funnel("555")
        assert out["month"] == "2026-08"
        assert captured == {"start": "2026-08-01", "end": "2026-08-31"}
        assert out["channels"]["paid_search"]["leases"] == 3
        assert "do not add it" in out["note"]

    def test_spend_authorization_never_returns_the_management_fee(self, monkeypatch):
        fake = types.SimpleNamespace(get_company_monthly_spend=lambda cid: {
            "company_id": cid, "total": 10700.0,
            "by_sku": {"search": 4000.0, "seo": 1200.0, "mgmt_fee": 900.0,
                       "management_fee": 900.0},
            "deal_id": "d-1", "deal_name": "Internal deal label"})
        monkeypatch.setitem(sys.modules, "spend_sheet", fake)
        out = mcp_context.get_spend_authorization("555")
        assert out["authorized_monthly_total"]["value"] == 10700.0
        assert set(out["by_service"]) == {"search", "seo"}
        assert "deal_name" not in out
        assert "signed deal" in out["rule"]

    def test_brief_strips_internal_fields_and_marks_human_edits(self, monkeypatch):
        class _F:
            def __init__(self, key, internal=False, override=None):
                self.key, self.label, self.type = key, key.title(), "text"
                self.section = "Voice & Positioning"
                self.hs_resolved, self.hs_override = key + "_resolved", override
                self.internal = internal

        fake = types.SimpleNamespace(
            SECTIONS=[("Voice & Positioning",
                       [_F("tone", override="tone_override"),
                        _F("internal_notes", internal=True)])],
            load_company_state=lambda cid: {"tone_resolved": "warm",
                                            "tone_override": "confident",
                                            "internal_notes_resolved": "private"},
            resolve_value=lambda props, resolved, override: (
                (props.get(override) or "").strip() or (props.get(resolved) or "").strip()))
        monkeypatch.setitem(sys.modules, "community_brief", fake)

        out = mcp_context.get_property_brief("555")
        assert [f["key"] for s in out["sections"] for f in s["fields"]] == ["tone"]
        field = out["sections"][0]["fields"][0]
        assert field["value"] == "confident" and field["human_edited"] is True
        assert out["completeness"] == {"filled": 1, "of": 1, "percent": 100}
        assert "Override-wins" in out["note"]

    def test_unknown_brief_section_lists_the_real_ones(self, monkeypatch):
        class _F:
            key, label, type = "tone", "Tone", "text"
            section, hs_resolved, hs_override, internal = "x", "tone_resolved", None, False

        fake = types.SimpleNamespace(
            SECTIONS=[("Voice & Positioning", [_F()]), ("Amenities", [_F()])],
            load_company_state=lambda cid: {}, resolve_value=lambda *a: "")
        monkeypatch.setitem(sys.modules, "community_brief", fake)
        out = mcp_context.get_property_brief("555", section="nope")
        assert out["error"] == "unknown_section"
        assert out["available_sections"] == ["voice_and_positioning", "amenities"]

    def test_open_work_without_configured_lists_gaps(self, identity, monkeypatch):
        monkeypatch.setitem(sys.modules, "config", types.SimpleNamespace())
        monkeypatch.setitem(sys.modules, "clickup_client",
                            types.SimpleNamespace(get_tasks=lambda *a, **k: []))
        out = mcp_context.get_open_work("555")
        assert out["items"] == [] and out["gaps"][0]["source"] == "clickup"

    def test_open_work_filters_to_the_property_and_honors_the_cap(self, identity, monkeypatch):
        tasks = [{"name": "Test Property — new creative",
                  "status": {"status": "open"}, "url": "u1"},
                 {"name": "Other Place — budget", "status": {"status": "open"}, "url": "u2"},
                 {"name": "test property lower case", "status": {"status": "open"}, "url": "u3"}]
        monkeypatch.setitem(sys.modules, "config",
                            types.SimpleNamespace(CLICKUP_LIST_ONE="123"))
        monkeypatch.setitem(sys.modules, "clickup_client",
                            types.SimpleNamespace(get_tasks=lambda *a, **k: tasks))
        out = mcp_context.get_open_work("555", limit=1)
        assert len(out["items"]) == 1
        assert out["items"][0]["title"].startswith("Test Property")

    def test_rule_tools_carry_the_rules_that_bite(self):
        rules = " ".join(r["rule"] for r in mcp_context.get_metric_rules()["rules"]).lower()
        assert "total, not a peer channel" in rules and "backfill" in rules
        comp = mcp_context.get_compliance_rules()
        never = " ".join(comp["never_propose"]).lower()
        assert "radius" in never and "zip" in never and "audience" in never
        assert "budget level" in " ".join(comp["allowed_optimizations"]).lower()


class TestMonthBounds:
    def test_defaults_to_last_full_month(self, monkeypatch):
        import datetime as _dt

        class _Date(_dt.date):
            @classmethod
            def today(cls):
                return cls(2026, 3, 9)

        monkeypatch.setattr(mcp_context, "date", _Date)
        assert mcp_context.month_bounds(None) == ("2026-02", "2026-02-01", "2026-02-28")

    def test_handles_a_year_boundary(self):
        assert mcp_context.month_bounds("2026-12") == ("2026-12", "2026-12-01",
                                                       "2026-12-31")
