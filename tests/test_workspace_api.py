"""Workspace API — routes, gates, decisions, signed links, contract shapes.

Offline: every HubSpot, HubDB, BigQuery, ClickUp and AptIQ read is mocked, and
`requests` is disabled outright so a missed mock fails loudly instead of
reaching a live system. Nothing here writes anywhere.

What these tests defend, in priority order:

1. A DECISION NEEDS A PROVEN IDENTITY. An asserted X-Portal-Email, even an
   internal one, can read but never act.
2. EVERY ROUTE IS DARK UNTIL THE FLAG FLIPS, and every property-scoped route
   checks the property, not just the feature.
3. A DECISION REACHES THE SOURCE'S EXISTING HANDLER and is written to the loop
   with lens, reason, source and actor.
4. A SIGNED LINK IS HONORED ONLY WHEN IT SHOULD BE: enabled, internal, unexpired,
   untampered, at most 7 days.
5. EVERY RESPONSE MATCHES THE CONTRACT and no number leaves without a source.
"""

from __future__ import annotations

import base64
import copy
import hmac
import importlib.util
import json
import os
import sys
import time
from datetime import date
from pathlib import Path
from unittest import mock

import pytest

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parent
sys.path.insert(0, str(REPO / "webhook-server"))
sys.path.insert(0, str(TESTS))

from flask import Flask  # noqa: E402

import feature_access  # noqa: E402
import loop_writer  # noqa: E402
import workspace_contract as contract  # noqa: E402
from routes.workspace import workspace_bp  # noqa: E402
from skills import workspace_decisions as wd  # noqa: E402
from skills import workspace_inbox as wi  # noqa: E402
from skills import workspace_links as wl  # noqa: E402
from skills import workspace_portfolio as wp  # noqa: E402

INTERNAL = "dana@rpmliving.com"
CLIENT = "owner@acme.com"
CID = "123"


def _month() -> str:
    return date.today().strftime("%Y-%m")


# ── sample source data ───────────────────────────────────────────────────────

REC_ROW = {"rec_id": "991", "property_uuid": "u-123", "source": "red_light", "rec_type": "budget_change",
           "title": "Put paid search onto one-bedrooms", "body": "Seven one-bedrooms open soon",
           "status": "pending", "created_date": 1757808000000}
BRIEF_ROW = {"brief_id": "b1", "property_uuid": "u-123", "hub_keyword": "apartments in carrollton",
             "status": "generated", "h1": "Apartments in Carrollton", "generated_at": "2026-09-01T00:00:00Z"}
LOOP_REC = {"action": "shift_budget", "from_channel": "paid_social", "to_channel": "paid_search",
            "amount": 300.0, "reason": "paid_search converts at $391/lease, paid_social at $702/lease",
            "forecast_impact": 1.2}
FORECAST = {"forecast_id": "f1", "run_at": "2026-09-10T00:00:00+00:00", "horizon_days": 30,
            "recommendations": [LOOP_REC, {"action": "hold", "reason": "balanced"}]}
PROPOSAL = {"proposal_id": "p1", "task_id": "t9", "company_id": CID, "property_uuid": "u-123",
            "field_key": "pet_policy", "field_label": "Pet policy", "proposed_value": "Cats and dogs welcome",
            "current_value": "Cats only", "extractor": "field_map", "status": "proposed",
            "created_at": "2026-09-12T00:00:00Z"}
PORTAL_TICKET = {"id": "cu1", "subject": "New pool photos", "status": "In progress",
                 "created_ts": 1757500000000, "submitted_by": "pm@acme.com", "unresolved": False}
SERVICE_TICKET = {"id": "hs1", "subject": "Website form broken", "stage_id": "4", "stage_label": "Closed",
                  "owner_name": "Your AM", "created_at": "2026-07-20T00:00:00Z",
                  "updated_at": "2026-09-02T00:00:00Z"}


def _props(**over) -> dict:
    p = {
        "uuid": "u-123", "name": "LYV Broadway", "plestatus": "RPM Managed", "seo_tier": "premium",
        "address": "2800 Broadway Blvd", "city": "Carrollton", "state": "TX", "zip": "75007",
        "domain": "lyvbroadway.com", "managementstart": "2024-07-01", "totalunits": "390",
        "target_occupancy": "95", "aptiq_property_id": "ap-1", "ga4_property_id": "g-1",
        "marketing_manager": "Dana Reyes", "marketing_manager_email": INTERNAL, "hubspot_owner_id": "77",
        "fluency_romance": "A courtyard community near the lake.",
        "callprep_cycle_month": _month(),
        "callprep_data_json": json.dumps({
            "generated_at": "2026-09-02T00:00:00Z",
            "recommendations": [{"rec_id": "cp1", "title": "Refresh the creative set",
                                 "body": "The current set is tired", "channel": "paid_social",
                                 "status": "pending"}],
        }),
        "video_cycle_month": _month(),
        "video_variants_json": json.dumps([{"variant_id": "v1", "title": "Courtyard morning",
                                            "rationale": "Shows the courtyard", "status": "pending_review",
                                            "created_at": "2026-09-08T00:00:00Z"}]),
    }
    p.update(over)
    return p


def _ctx(**over) -> wi.PropertyContext:
    props = _props(**over)
    return wi.PropertyContext(CID, props["uuid"], props["name"], props)


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """No network, no HubDB access table, a clean flag environment."""
    def _no_network(*a, **k):
        raise RuntimeError("network disabled in workspace tests")
    monkeypatch.setattr("requests.sessions.Session.request", _no_network)
    for var in ("PORTAL_STRICT_IDENTITY", "PORTAL_COMPANY_ACCESS", "INTERNAL_API_KEY",
                "WORKSPACE_SIGNED_LINKS_ENABLED", "WORKSPACE_LINK_SECRET"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("WORKSPACE_ENABLED", "true")
    monkeypatch.setattr(feature_access, "_load_access_table", lambda: {})
    monkeypatch.setattr(feature_access, "_load_stage_table", lambda: {})
    feature_access.clear_cache()
    wi._onboarding_cache.clear()
    yield
    feature_access.clear_cache()


@pytest.fixture
def events(monkeypatch):
    rec = mock.Mock(return_value="event-1")
    monkeypatch.setattr(loop_writer, "record", rec)
    return rec


@pytest.fixture
def readers(monkeypatch):
    """Every inbox source returns one known row."""
    import config
    import forecasting
    import hubdb_helpers
    import portal_tickets
    import seo_entitlement
    import ticket_manager
    import ticket_profile_sync

    monkeypatch.setattr(config, "HUBDB_RECOMMENDATIONS_TABLE_ID", "t-recs", raising=False)
    monkeypatch.setattr(config, "HUBDB_CONTENT_BRIEFS_TABLE_ID", "t-briefs", raising=False)
    tables = {"t-recs": [REC_ROW], "t-briefs": [BRIEF_ROW]}

    def read_rows(table_id, filters=None, limit=500):
        rows = [dict(r) for r in tables.get(table_id, [])]
        for k, v in (filters or {}).items():
            rows = [r for r in rows if r.get(k) == v]
        return rows

    monkeypatch.setattr(hubdb_helpers, "read_rows", read_rows)
    monkeypatch.setattr(forecasting, "get_latest_forecast", lambda uuid: copy.deepcopy(FORECAST))
    monkeypatch.setattr(seo_entitlement, "has_feature", lambda tier, feature: True)
    monkeypatch.setattr(ticket_profile_sync, "enabled", lambda: True)
    monkeypatch.setattr(ticket_profile_sync, "list_proposals",
                        lambda cid, uuid="", status=None, limit=100: [dict(PROPOSAL)])
    monkeypatch.setattr(portal_tickets, "list_tickets",
                        lambda cid, property_uuid="", limit=50: [dict(PORTAL_TICKET)])
    monkeypatch.setattr(ticket_manager, "list_tickets",
                        lambda cid, include_closed=False: [dict(SERVICE_TICKET)])
    monkeypatch.setattr(loop_writer, "_bq", lambda: None)
    return tables


@pytest.fixture
def ctx(monkeypatch):
    c = _ctx()
    monkeypatch.setattr(wi, "load_context", lambda company_id: c)
    return c


@pytest.fixture
def client():
    app = Flask(__name__)
    app.register_blueprint(workspace_bp)
    return app.test_client()


def _h(email=INTERNAL, **extra):
    h = {"X-Portal-Email": email}
    h.update(extra)
    return h


VERIFIED = {"portal.identity_verified": True}

GET_ROUTES = [
    "/api/workspace/me",
    "/api/workspace/portfolio",
    f"/api/workspace/work?company_id={CID}",
    f"/api/workspace/work/hubdb_rec:991?company_id={CID}",
    f"/api/workspace/property?company_id={CID}",
    f"/api/workspace/performance?company_id={CID}",
    f"/api/workspace/plan?company_id={CID}",
    f"/api/workspace/client-view?company_id={CID}",
]
PROPERTY_ROUTES = [r for r in GET_ROUTES if "company_id" in r]


def _allowlist_client(monkeypatch, companies=("555",)):
    monkeypatch.setattr(feature_access, "_load_access_table", lambda: {
        CLIENT: {"role": "client", "beta_features": {"workspace"}, "companies": set(companies)}})
    feature_access.clear_cache()


# ── 1. flag ──────────────────────────────────────────────────────────────────

class TestFlag:
    @pytest.mark.parametrize("url", GET_ROUTES)
    def test_every_read_404s_when_flag_off(self, client, monkeypatch, url):
        monkeypatch.setenv("WORKSPACE_ENABLED", "false")
        assert client.get(url, headers=_h()).status_code == 404

    def test_decision_404s_when_flag_off(self, client, monkeypatch):
        monkeypatch.delenv("WORKSPACE_ENABLED")
        r = client.post("/api/workspace/work/hubdb_rec:991/decision", headers=_h(),
                        json={"company_id": CID, "action": "approve"}, environ_overrides=VERIFIED)
        assert r.status_code == 404

    def test_preflight_404s_when_flag_off(self, client, monkeypatch):
        monkeypatch.setenv("WORKSPACE_ENABLED", "")
        assert client.options("/api/workspace/me").status_code == 404

    def test_preflight_allows_the_link_header(self, client):
        r = client.options("/api/workspace/me", headers={"Origin": "https://go.rpmliving.com"})
        assert r.status_code == 204
        assert "X-Workspace-Link" in r.headers["Access-Control-Allow-Headers"]

    def test_server_cors_allows_the_link_header(self):
        import server
        r = server.app.test_client().get("/health", headers={"Origin": "https://go.rpmliving.com"})
        assert "X-Workspace-Link" in (r.headers.get("Access-Control-Allow-Headers") or "")

    def test_feature_key_is_registered_beta(self):
        assert feature_access.FEATURES["workspace"].default_stage == feature_access.STAGE_BETA


# ── 2. access gates ──────────────────────────────────────────────────────────

class TestAccess:
    @pytest.mark.parametrize("url", GET_ROUTES)
    def test_no_identity_is_401(self, client, url):
        assert client.get(url).status_code == 401

    @pytest.mark.parametrize("url", GET_ROUTES)
    def test_client_without_the_feature_is_403(self, client, url):
        r = client.get(url, headers=_h(CLIENT))
        assert r.status_code == 403
        assert r.get_json()["feature"] == "workspace"

    @pytest.mark.parametrize("url", PROPERTY_ROUTES)
    def test_client_with_feature_but_not_this_property_is_403(self, client, monkeypatch, url):
        _allowlist_client(monkeypatch, companies=("555",))
        r = client.get(url, headers=_h(CLIENT))
        assert r.status_code == 403
        assert r.get_json()["error"] == "Not authorized for this property"

    @pytest.mark.parametrize("url", ["/api/workspace/work", "/api/workspace/property",
                                     "/api/workspace/plan", "/api/workspace/client-view"])
    def test_missing_company_id_is_400(self, client, url):
        assert client.get(url, headers=_h()).status_code == 400

    def test_client_scoped_to_the_property_passes_the_gate(self, client, monkeypatch, readers):
        _allowlist_client(monkeypatch, companies=(CID,))
        c = _ctx()
        monkeypatch.setattr(wi, "load_context", lambda company_id: c)
        r = client.get(f"/api/workspace/work?company_id={CID}&status=all", headers=_h(CLIENT))
        assert r.status_code == 200

    def test_portfolio_is_internal_only(self, client, monkeypatch):
        _allowlist_client(monkeypatch)
        r = client.get("/api/workspace/portfolio", headers=_h(CLIENT))
        assert r.status_code == 403
        assert r.get_json()["error"] == "Internal role required"

    def test_client_never_sees_internal_only_items(self, client, monkeypatch, readers):
        _allowlist_client(monkeypatch, companies=(CID,))
        c = _ctx()
        monkeypatch.setattr(wi, "load_context", lambda company_id: c)
        body = client.get(f"/api/workspace/work?company_id={CID}&status=all", headers=_h(CLIENT)).get_json()
        items = body["groups"]["late"] + body["groups"]["this_week"]
        sources = {i["source"] for i in items}
        assert not sources & set(wi.INTERNAL_ONLY)
        assert all(not g["field"].startswith("source:call_prep") for g in body["gaps"])
        r = client.get(f"/api/workspace/work/call_prep:cp1?company_id={CID}", headers=_h(CLIENT))
        assert r.status_code == 404

    def test_unknown_property_is_404(self, client, monkeypatch):
        def missing(company_id):
            raise wi.PropertyNotFound(company_id)
        monkeypatch.setattr(wi, "load_context", missing)
        assert client.get(f"/api/workspace/plan?company_id={CID}", headers=_h()).status_code == 404

    def test_bad_status_and_range_are_400(self, client, ctx):
        assert client.get(f"/api/workspace/work?company_id={CID}&status=later", headers=_h()).status_code == 400
        assert client.get(f"/api/workspace/performance?company_id={CID}&range=7", headers=_h()).status_code == 400

    def test_unknown_item_id_is_404(self, client, ctx, readers):
        assert client.get(f"/api/workspace/work/nope:1?company_id={CID}", headers=_h()).status_code == 404
        assert client.get(f"/api/workspace/work/hubdb_rec:000?company_id={CID}", headers=_h()).status_code == 404


# ── 3. decisions ─────────────────────────────────────────────────────────────

def _decide(client, item_id, action="approve", reason=None, *, email=INTERNAL, verified=True,
            company_id=CID):
    return client.post(
        f"/api/workspace/work/{item_id}/decision", headers=_h(email),
        json={"company_id": company_id, "action": action, "reason": reason},
        environ_overrides=VERIFIED if verified else {},
    )


LOOP_ID = "loop_rec:" + wi.loop_rec_hash("f1", LOOP_REC)


class TestDecisionGates:
    def test_refused_without_verified_identity_even_for_internal(self, client, ctx, readers, events):
        with mock.patch("approval_agent.route_approval") as handler:
            r = _decide(client, "hubdb_rec:991", verified=False)
        assert r.status_code == 401
        assert r.get_json()["error"] == "Verified sign-in required"
        handler.assert_not_called()
        events.assert_not_called()

    def test_refused_without_verified_identity_with_strict_mode_off(self, client, ctx, readers, monkeypatch):
        monkeypatch.setenv("PORTAL_STRICT_IDENTITY", "false")
        assert _decide(client, "hubdb_rec:991", verified=False).status_code == 401

    def test_not_now_without_reason_is_400(self, client, ctx, readers, events):
        r = _decide(client, "hubdb_rec:991", action="not_now")
        assert r.status_code == 400
        assert "reason" in r.get_json()["error"].lower()
        events.assert_not_called()

    @pytest.mark.parametrize("body", [
        {"company_id": CID, "action": "delete"},
        {"company_id": CID, "action": "not_now", "reason": "because"},
    ])
    def test_bad_action_or_reason_is_400(self, client, ctx, readers, body):
        r = client.post("/api/workspace/work/hubdb_rec:991/decision", headers=_h(), json=body,
                        environ_overrides=VERIFIED)
        assert r.status_code == 400

    def test_verified_client_without_property_access_is_403(self, client, ctx, readers, monkeypatch):
        _allowlist_client(monkeypatch, companies=("555",))
        assert _decide(client, "hubdb_rec:991", email=CLIENT).status_code == 403

    def test_missing_company_id_is_400(self, client, ctx, readers):
        assert _decide(client, "hubdb_rec:991", company_id="").status_code == 400

    def test_source_without_an_approval_step_is_400(self, client, ctx, readers, events):
        r = _decide(client, "portal_ticket:cu1")
        assert r.status_code == 400

    def test_item_not_waiting_on_a_decision_is_400(self, client, monkeypatch, readers, events):
        c = _ctx(callprep_data_json=json.dumps({"recommendations": [
            {"rec_id": "cp1", "title": "Refresh", "status": "approved"}]}))
        monkeypatch.setattr(wi, "load_context", lambda company_id: c)
        assert _decide(client, "call_prep:cp1").status_code == 400

    def test_unknown_item_is_404(self, client, ctx, readers):
        assert _decide(client, "hubdb_rec:nope").status_code == 404


class TestDispatch:
    """Each source reaches its existing handler as a module call."""

    def _assert_event(self, events, item_id, action, reason=None, outcome="ok"):
        events.assert_called_once()
        args, kwargs = events.call_args
        source = item_id.split(":")[0]
        assert args == (wi.STAGE[source], "workspace_decision")
        assert kwargs["payload"]["lens"] == wi.LENS[source]
        assert kwargs["payload"]["source"] == source
        assert kwargs["payload"]["reason"] == reason
        assert kwargs["payload"]["actor"] == INTERNAL
        assert kwargs["payload"]["action"] == action
        assert kwargs["payload"]["outcome"] == outcome
        assert kwargs["payload"]["item_id"] == item_id
        assert wi.STAGE[source] in loop_writer.LOOP_STAGES

    def test_hubdb_rec_approve(self, client, ctx, readers, events):
        with mock.patch("approval_agent.route_approval", return_value={
                "status": "ok", "actions_taken": ["HubSpot Deal created: 42"], "errors": []}) as h:
            r = _decide(client, "hubdb_rec:991")
        assert r.status_code == 200
        kw = h.call_args.kwargs
        assert (kw["rec_id"], kw["rec_type"], kw["company_id"], kw["property_uuid"]) == \
            ("991", "budget_change", CID, "u-123")
        self._assert_event(events, "hubdb_rec:991", "approve")
        body = r.get_json()
        contract.assert_shape(body, "decision")
        assert body["item"]["status"] == "in_motion"
        assert any(m["kind"] == "person" for m in body["in_motion"])

    def test_hubdb_rec_not_now(self, client, ctx, readers, events):
        with mock.patch("approval_agent._update_rec_status") as upd, \
                mock.patch("approval_agent._log_hubspot_activity") as log:
            r = _decide(client, "hubdb_rec:991", action="not_now", reason="already_handled")
        assert r.status_code == 200
        upd.assert_called_once_with("991", "dismissed")
        log.assert_called_once()
        self._assert_event(events, "hubdb_rec:991", "not_now", "already_handled")
        assert r.get_json()["item"]["status"] == "done"

    def test_loop_rec_approve(self, client, ctx, readers, events):
        with mock.patch("routes.loop.record_recommendation_approved", return_value="ev-9") as h:
            r = _decide(client, LOOP_ID)
        assert r.status_code == 200
        args, kw = h.call_args
        assert args == ("u-123", LOOP_REC)
        assert kw["forecast_id"] == "f1" and kw["approver_email"] == INTERNAL
        self._assert_event(events, LOOP_ID, "approve")

    def test_loop_rec_not_now(self, client, ctx, readers, events):
        with mock.patch("routes.loop.record_recommendation_rejected", return_value="ev-9") as h:
            r = _decide(client, LOOP_ID, action="not_now", reason="discuss_on_call")
        assert r.status_code == 200
        assert h.call_args.kwargs["reason"] == "discuss_on_call"
        self._assert_event(events, LOOP_ID, "not_now", "discuss_on_call")

    def test_call_prep_approve(self, client, ctx, readers, events):
        with mock.patch("approval_actions.callprep_approve", return_value=({"recommendations": []}, "created")) as h:
            r = _decide(client, "call_prep:cp1")
        assert r.status_code == 200
        h.assert_called_once_with(CID, "cp1", INTERNAL)
        self._assert_event(events, "call_prep:cp1", "approve")

    def test_call_prep_not_now(self, client, ctx, readers, events):
        with mock.patch("approval_actions.callprep_dismiss", return_value={"recommendations": []}) as h:
            r = _decide(client, "call_prep:cp1", action="not_now", reason="wrong_data")
        assert r.status_code == 200
        h.assert_called_once_with(CID, "cp1", INTERNAL)
        self._assert_event(events, "call_prep:cp1", "not_now", "wrong_data")

    def test_content_brief_approve(self, client, ctx, readers, events):
        with mock.patch("routes.seo.approve_content_brief", return_value=(
                {"status": "ok", "actions_taken": ["ClickUp SEO content task created: 7"], "errors": []}, 200)) as h:
            r = _decide(client, "content_brief:b1")
        assert r.status_code == 200
        assert h.call_args.args == ("b1",)
        assert h.call_args.kwargs["company_id"] == CID
        self._assert_event(events, "content_brief:b1", "approve")

    def test_content_brief_not_now_records_only(self, client, ctx, readers, events):
        with mock.patch("routes.seo.approve_content_brief") as h:
            r = _decide(client, "content_brief:b1", action="not_now", reason="not_priority")
        assert r.status_code == 200
        h.assert_not_called()
        self._assert_event(events, "content_brief:b1", "not_now", "not_priority")

    def test_video_variant_approve(self, client, ctx, readers, events):
        with mock.patch("approval_actions.approve_video_variants", return_value=(
                {"approved": 1, "cycle_status": "Approved", "asset_rows": 1}, 200)) as h:
            r = _decide(client, "video_variant:v1")
        assert r.status_code == 200
        h.assert_called_once_with(CID, ["v1"])
        self._assert_event(events, "video_variant:v1", "approve")
        assert r.get_json()["item"]["status"] == "done"

    def test_ticket_profile_approve(self, client, ctx, readers, events):
        import ticket_profile_sync
        with mock.patch.object(ticket_profile_sync, "accept", return_value={}) as h:
            r = _decide(client, "ticket_profile:p1")
        assert r.status_code == 200
        h.assert_called_once_with("p1", INTERNAL)
        self._assert_event(events, "ticket_profile:p1", "approve")

    def test_ticket_profile_not_now(self, client, ctx, readers, events):
        import ticket_profile_sync
        with mock.patch.object(ticket_profile_sync, "reject", return_value={}) as h:
            r = _decide(client, "ticket_profile:p1", action="not_now", reason="not_priority")
        assert r.status_code == 200
        h.assert_called_once_with("p1", INTERNAL, wi.REASONS["not_priority"])
        self._assert_event(events, "ticket_profile:p1", "not_now", "not_priority")

    def test_handler_failure_is_502_and_still_logged(self, client, ctx, readers, events):
        with mock.patch("approval_actions.callprep_approve", side_effect=RuntimeError("boom")):
            r = _decide(client, "call_prep:cp1")
        assert r.status_code == 502
        self._assert_event(events, "call_prep:cp1", "approve", outcome="failed")

    def test_handler_refusal_keeps_its_status(self, client, ctx, readers, events):
        import ticket_profile_sync
        with mock.patch.object(ticket_profile_sync, "accept",
                               side_effect=ticket_profile_sync.ProposalError(409, "proposal already accepted")):
            r = _decide(client, "ticket_profile:p1")
        assert r.status_code == 409
        self._assert_event(events, "ticket_profile:p1", "approve", outcome="failed")

    def test_every_decidable_source_has_both_handlers(self):
        for source in wi.DECIDABLE:
            assert (source, "approve") in wd.HANDLERS and (source, "not_now") in wd.HANDLERS


class TestMoneyRule:
    """Nothing the workspace adds may move spend or write to an ad platform."""

    FILES = sorted((REPO / "webhook-server" / "skills").glob("workspace_*.py")) + [
        REPO / "webhook-server" / "routes" / "workspace.py",
        REPO / "webhook-server" / "approval_actions.py",
    ]
    FORBIDDEN = ("import fluency", "from fluency", "fluency_exporter", "budget_sync",
                 "google_ads_islost", "deal_creator", "patch_deal", "create_deal",
                 "rent_roll", "customer_match")

    @pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
    def test_no_spend_writers_imported(self, path):
        text = path.read_text()
        hits = [w for w in self.FORBIDDEN if w in text]
        assert not hits, f"{path.name} references {hits}"

    def test_workspace_never_writes_uuid(self):
        for path in self.FILES:
            text = path.read_text()
            assert "patch_company" not in text and "batch_patch_companies" not in text, path.name


# ── 4. signed links ──────────────────────────────────────────────────────────

SECRET = "test-secret"


@pytest.fixture
def links(monkeypatch):
    monkeypatch.setenv("WORKSPACE_SIGNED_LINKS_ENABLED", "true")
    monkeypatch.setenv("WORKSPACE_LINK_SECRET", SECRET)


def _forge(email, exp, secret=SECRET):
    payload = wl._b64(json.dumps({"email": email, "exp": exp}, sort_keys=True,
                                 separators=(",", ":")).encode())
    return f"v1.{payload}.{wl._sign(payload, secret.encode())}"


class TestSignedLinks:
    def _me(self, client, token, **headers):
        with mock.patch("skills.workspace_views.build_me",
                        side_effect=lambda email, verified: {"email": email, "verified": verified}):
            return client.get("/api/workspace/me", headers={wl.HEADER: token, **headers})

    def test_valid_link_sets_a_verified_identity(self, client, links):
        r = self._me(client, wl.mint(INTERNAL, 3), **{"X-Portal-Email": "someone-else@rpmliving.com"})
        assert r.status_code == 200
        assert r.get_json() == {"email": INTERNAL, "verified": True}

    def test_valid_link_can_decide(self, client, links, ctx, readers, events):
        with mock.patch("approval_actions.callprep_dismiss", return_value={"recommendations": []}):
            r = client.post("/api/workspace/work/call_prep:cp1/decision",
                            headers={wl.HEADER: wl.mint(INTERNAL, 1)},
                            json={"company_id": CID, "action": "not_now", "reason": "not_priority"})
        assert r.status_code == 200
        assert r.get_json()["decided_by"] == INTERNAL

    def test_expired_link_is_401(self, client, links):
        token = wl.mint(INTERNAL, 1, now=time.time() - 2 * 86400)
        r = self._me(client, token)
        assert r.status_code == 401
        assert r.get_json()["detail"] == "Link expired"

    def test_tampered_payload_is_401(self, client, links):
        good = wl.mint(INTERNAL, 1)
        other = _forge("kyle@rpmliving.com", int(time.time()) + 3600).split(".")[1]
        head, _, sig = good.split(".")
        r = self._me(client, f"{head}.{other}.{sig}")
        assert r.status_code == 401
        assert "signature" in r.get_json()["detail"]

    def test_tampered_signature_is_401(self, client, links):
        good = wl.mint(INTERNAL, 1)
        bad = good[:-4] + ("AAAA" if not good.endswith("AAAA") else "BBBB")
        assert self._me(client, bad).status_code == 401

    def test_wrong_secret_is_401(self, client, links):
        assert self._me(client, _forge(INTERNAL, int(time.time()) + 3600, secret="other")).status_code == 401

    def test_non_internal_email_is_401(self, client, links):
        r = self._me(client, _forge(CLIENT, int(time.time()) + 3600))
        assert r.status_code == 401
        assert "internal" in r.get_json()["detail"]

    def test_lifetime_over_seven_days_is_401(self, client, links):
        r = self._me(client, _forge(INTERNAL, int(time.time()) + 10 * 86400))
        assert r.status_code == 401

    def test_malformed_is_401(self, client, links):
        assert self._me(client, "not-a-token").status_code == 401

    def test_flag_off_ignores_the_link(self, client, links, monkeypatch):
        monkeypatch.setenv("WORKSPACE_SIGNED_LINKS_ENABLED", "false")
        token = wl.mint(INTERNAL, 1)
        # No other identity: the link does not sign anyone in.
        assert self._me(client, token).status_code == 401
        # With an asserted header the request reads, but is not verified.
        r = self._me(client, token, **{"X-Portal-Email": INTERNAL})
        assert r.get_json()["verified"] is False

    def test_flag_off_link_cannot_decide(self, client, links, monkeypatch, ctx, readers):
        monkeypatch.setenv("WORKSPACE_SIGNED_LINKS_ENABLED", "false")
        r = client.post("/api/workspace/work/call_prep:cp1/decision",
                        headers={wl.HEADER: wl.mint(INTERNAL, 1), "X-Portal-Email": INTERNAL},
                        json={"company_id": CID, "action": "approve"})
        assert r.status_code == 401

    def test_no_secret_is_401(self, client, links, monkeypatch):
        token = wl.mint(INTERNAL, 1)
        monkeypatch.delenv("WORKSPACE_LINK_SECRET")
        assert self._me(client, token).status_code == 401

    def test_verified_session_wins_over_a_link(self, client, links):
        token = _forge(CLIENT, int(time.time()) + 3600)     # would 401 on its own
        with mock.patch("skills.workspace_views.build_me",
                        side_effect=lambda email, verified: {"email": email, "verified": verified}):
            r = client.get("/api/workspace/me", headers={wl.HEADER: token, "X-Portal-Email": INTERNAL},
                           environ_overrides=VERIFIED)
        assert r.get_json() == {"email": INTERNAL, "verified": True}

    def test_signature_compare_is_constant_time(self, links):
        token = wl.mint(INTERNAL, 1)
        with mock.patch("skills.workspace_links.hmac.compare_digest", wraps=hmac.compare_digest) as cmp:
            wl.verify(token)
        cmp.assert_called_once()

    def test_mint_refuses_long_lifetimes_and_clients(self, links):
        with pytest.raises(ValueError):
            wl.mint(INTERNAL, 8)
        with pytest.raises(ValueError):
            wl.mint(CLIENT, 1)

    def test_script_mints_a_verifiable_token(self, links, capsys):
        spec = importlib.util.spec_from_file_location("workspace_link_script", REPO / "scripts" / "workspace_link.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert mod.main([INTERNAL, "--days", "2"]) == 0
        token = capsys.readouterr().out.strip()
        assert wl.verify(token) == INTERNAL
        assert mod.main([INTERNAL, "--days", "9"]) == 2
        assert mod.main([CLIENT]) == 2


# ── 5. contract shapes ───────────────────────────────────────────────────────

def _shape_ok(body, name):
    contract.assert_shape(body, name)
    assert contract.numbers_without_source(body) == [], contract.numbers_without_source(body)


@pytest.fixture
def aptiq(monkeypatch):
    from services.fluency_ingestion import apt_iq_csv_client, apt_iq_reader
    monkeypatch.setenv("APT_IQ_DAILY_SHEET_URL", "https://example.invalid/daily.csv")
    monkeypatch.setenv("APT_IQ_FLOOR_PLAN_SHEET_URL", "https://example.invalid/fp.csv")
    monkeypatch.setattr(apt_iq_reader, "read_property", lambda company: {
        "matched": True, "occupancy_pct": 91.8, "available_units": 44, "exposure_90d_pct": 9.1})
    monkeypatch.setattr(apt_iq_reader, "read_floor_plans", lambda pid: [
        {"name": "A1", "beds": 1, "baths": 1.0, "sqft": 712, "total_units": 40, "available": 7}])
    monkeypatch.setattr(apt_iq_csv_client, "_cache_loaded_at", 1757830000.0)
    monkeypatch.setattr(apt_iq_csv_client, "_fp_loaded_at", 1757830000.0)
    monkeypatch.setattr(apt_iq_csv_client, "get_all_rows", lambda: {
        "ap-1": {"Property ID": "ap-1", "Occupancy %": "76", "Available Units": "108"}})


@pytest.fixture
def spend(monkeypatch):
    import hubspot_client
    import spend_sheet
    monkeypatch.setattr(spend_sheet, "get_company_monthly_spend", lambda cid: {
        "company_id": cid, "total": 4638.0, "deal_id": "d1", "deal_name": "IO",
        "by_sku": {"search": 1762.0, "pmax": 1077.0, "seo": 800.0, "social_posting": 499.0, "mgmt_fee": 500.0}})
    monkeypatch.setattr(spend_sheet, "_cache", {"data": (1757830000.0, [])})
    monkeypatch.setattr(hubspot_client, "get_open_deals_for_company", lambda cid, channel=None: [{"id": "d9"}])


class TestContractShapes:
    def test_me_internal(self, client, monkeypatch):
        monkeypatch.setattr(wp, "assigned_properties", lambda email: [
            {"hubspot_company_id": CID, "uuid": "u-123", "name": "LYV Broadway", "city": "Carrollton",
             "state": "TX", "totalunits": "390"}])
        body = client.get("/api/workspace/me", headers=_h(), environ_overrides=VERIFIED).get_json()
        _shape_ok(body, "me")
        assert body["role"] == "internal" and body["verified"] is True
        assert body["companies"][0]["units"] == 390

    def test_me_client(self, client, monkeypatch):
        import hubspot_client
        _allowlist_client(monkeypatch, companies=(CID,))
        monkeypatch.setattr(hubspot_client, "get_company", lambda cid, props=None: {
            "uuid": "u-123", "name": "LYV Broadway", "city": "Carrollton", "state": "TX", "totalunits": "390"})
        body = client.get("/api/workspace/me", headers=_h(CLIENT)).get_json()
        _shape_ok(body, "me")
        assert body["role"] == "client" and body["verified"] is False
        assert [c["company_id"] for c in body["companies"]] == [CID]

    def test_portfolio(self, client, monkeypatch, readers, aptiq):
        monkeypatch.setattr(wp, "assigned_properties", lambda email: [
            {"hubspot_company_id": CID, "name": "LYV Broadway", "city": "Carrollton", "state": "TX",
             "totalunits": "390", "aptiq_property_id": "ap-1"},
            {"hubspot_company_id": "456", "name": "Quiet Place", "totalunits": "100", "aptiq_property_id": ""},
        ])
        contexts = {CID: _ctx(), "456": wi.PropertyContext("456", "u-456", "Quiet Place", {"uuid": "u-456"})}
        monkeypatch.setattr(wi, "load_context", lambda cid: contexts[cid])
        body = client.get("/api/workspace/portfolio", headers=_h()).get_json()
        _shape_ok(body, "portfolio")
        assert body["property_count"] == 2 and body["quiet_count"] == 1
        row = body["properties"][0]
        assert row["units_at_risk"] == {"value": 108, "source": "aptiq", "as_of": "2025-09-14T06:06:40Z"}
        assert row["occupancy"]["value"] == 0.76

    def test_work(self, client, ctx, readers):
        body = client.get(f"/api/workspace/work?company_id={CID}&status=all", headers=_h()).get_json()
        _shape_ok(body, "work")
        items = body["groups"]["late"] + body["groups"]["this_week"]
        assert {i["source"] for i in items} | {"call_prep"} >= set(wi.SOURCES) - {"onboarding_gap"}
        assert body["hidden_count"] == 0
        assert sum(body["counts"].values()) == len(items) + body["groups"]["later"]["count"]

    def test_work_default_status_hides_the_rest(self, client, ctx, readers):
        body = client.get(f"/api/workspace/work?company_id={CID}", headers=_h()).get_json()
        shown = body["groups"]["late"] + body["groups"]["this_week"]
        assert all(i["status"] == "to_do" for i in shown)
        assert body["hidden_count"] == body["counts"]["in_motion"] + body["counts"]["done"]

    def test_item(self, client, ctx, readers):
        r = client.get(f"/api/workspace/work/hubdb_rec:991?company_id={CID}", headers=_h())
        _shape_ok(r.get_json(), "item")

    def test_decision(self, client, ctx, readers, events):
        with mock.patch("approval_actions.callprep_approve", return_value=({"recommendations": []}, "created")):
            body = _decide(client, "call_prep:cp1").get_json()
        _shape_ok(body, "decision")
        contract.assert_shape({"company_id": CID, "action": "approve", "reason": None}, "decision_request")

    def test_property(self, client, ctx, aptiq, monkeypatch):
        import portal_tickets
        import property_brief_audit
        monkeypatch.setattr(property_brief_audit, "recent_edits", lambda cid, limit=50: [
            {"field_key": "romance", "edited_by": "AM", "edited_at": "2026-08-12T10:00:00.000Z"}])
        monkeypatch.setattr(portal_tickets, "_owner_name", lambda oid: "Marcus Jennings")
        body = client.get(f"/api/workspace/property?company_id={CID}", headers=_h()).get_json()
        _shape_ok(body, "property")
        assert body["address"] == "2800 Broadway Blvd, Carrollton TX 75007"
        assert body["managed_since"] == "2024-07"
        assert body["brief"] == {"text": "A courtyard community near the lake.", "curated": True,
                                 "edited_by": "AM", "edited_at": "2026-08-12"}
        assert body["people"][0] == {"name": "Marcus Jennings", "role": "Account manager", "email": None}
        assert body["floorplans"][0]["code"] == "A1"
        assert {c["name"]: c["status"] for c in body["connections"]}["ApartmentIQ"] == "connected"

    def test_performance(self, client, ctx, aptiq, spend):
        body = client.get(f"/api/workspace/performance?company_id={CID}&range=90", headers=_h()).get_json()
        _shape_ok(body, "performance")
        assert body["occupied"]["value"] == 0.918 and body["occupied"]["units"] == 358
        assert body["occupied"]["target"] == 0.95
        assert body["available_now"]["value"] == 44 and body["available_now"]["stale_90_plus"] is None
        assert body["coming_open_90d"] is None and body["coming_by_week"] == []
        assert body["monthly_plan"]["value"] == 4638.0
        fields = {g["field"] for g in body["gaps"]}
        assert {"available_now.stale_90_plus", "coming_open_90d", "coming_by_week"} <= fields

    def test_plan(self, client, ctx, spend):
        body = client.get(f"/api/workspace/plan?company_id={CID}", headers=_h()).get_json()
        _shape_ok(body, "plan")
        by = {c["channel"]: c for c in body["channels"]}
        assert by["Paid search"]["monthly"] == 2839.0
        assert body["pending_changes"] == 1 and body["monthly_total"] == 4638.0
        assert round(sum(c["share"] for c in body["channels"]), 3) == 1.0
        assert all(c["cost_per_lease"] is None for c in body["channels"])

    def test_client_view(self, client, ctx, readers):
        body = client.get(f"/api/workspace/client-view?company_id={CID}", headers=_h()).get_json()
        _shape_ok(body, "client_view")
        assert [c["title"] for c in body["changing"]] == ["New pool photos"]
        # call prep and loop recs are internal-only; they are open but hidden
        assert body["hidden_open_count"] >= 2

    @pytest.mark.parametrize("url", [u for u in GET_ROUTES if "portfolio" not in u])
    def test_errors_use_the_error_shape(self, client, url):
        body = client.get(url).get_json()
        contract.assert_shape(body, "error")


class TestContractHelper:
    def test_catches_missing_and_wrong_types(self):
        errs = contract.check({"summary": {"open": "3"}}, contract.WORK)
        assert any("summary.open" in e for e in errs)
        assert any("counts: missing" in e for e in errs)

    def test_flags_a_bare_number(self):
        assert contract.numbers_without_source({"occupied": {"value": 0.9}}) == ["$.occupied.value"]
        assert contract.numbers_without_source({"occupied": {"value": 0.9, "source": "aptiq"}}) == []
        assert contract.numbers_without_source({"counts": {"to_do": 3}}) == []
