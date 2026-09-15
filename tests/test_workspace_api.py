"""Workspace API — routes, gates, decisions, undo, signed links, preview, shapes.

Offline: every HubSpot, HubDB, BigQuery, ClickUp and AptIQ read is mocked, and
`requests` is disabled outright so a missed mock fails loudly instead of
reaching a live system. Nothing here writes anywhere.

What these tests defend, in priority order:

1. AN ACTION NEEDS A PROVEN IDENTITY. An asserted X-Portal-Email, a read-only
   signed link, or "Preview as client" can read but never act.
2. EVERY ROUTE IS DARK UNTIL THE FLAG FLIPS, and every property-scoped route
   checks the property, not just the feature.
3. A DECISION REACHES THE SOURCE'S EXISTING HANDLER and is written to the loop
   with lens, reason, source, actor and requires_signature. UNDO is the
   decider's alone, for ten minutes, and only where a safe reverse exists.
4. INTERNAL NOTES AND TRAIL ENTRIES NEVER REACH A CLIENT-ROLE RESPONSE.
5. EVERY RESPONSE MATCHES THE CONTRACT and no number leaves without a source.
"""

from __future__ import annotations

import copy
import hmac
import importlib.util
import json
import sys
import time
from datetime import date, timedelta
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
from skills import workspace_cache as wcache  # noqa: E402
from skills import workspace_decisions as wd  # noqa: E402
from skills import workspace_inbox as wi  # noqa: E402
from skills import workspace_links as wl  # noqa: E402
from skills import workspace_portfolio as wp  # noqa: E402

INTERNAL = "dana@rpmliving.com"
OTHER_INTERNAL = "marcus@rpmliving.com"
CLIENT = "owner@acme.com"
CID = "123"
TS = "2026-09-14T00:00:00Z"


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
                  "updated_at": "2026-09-02T00:00:00Z", "description": "The contact form returns an error"}


def _props(**over) -> dict:
    p = {
        "uuid": "u-123", "name": "LYV Broadway", "plestatus": "RPM Managed", "seo_tier": "premium",
        "address": "2800 Broadway Blvd", "city": "Carrollton", "state": "TX", "zip": "75007",
        "domain": "lyvbroadway.com", "managementstart": "2024-07-01", "totalunits": "390",
        "target_occupancy": "95", "aptiq_property_id": "ap-1", "ga4_property_id": "g-1",
        "marketing_manager": "Dana Reyes", "marketing_manager_email": INTERNAL, "hubspot_owner_id": "77",
        "fluency_romance": "A courtyard community near the lake.", "occupancy__": "91.8",
        "callprep_cycle_month": _month(),
        "callprep_data_json": json.dumps({
            "generated_at": "2026-09-02T00:00:00Z",
            "summary": {"changed": "Occupancy is 91.8 percent.", "working": "Search is steady."},
            "questions": ["Any events this month?"],
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
    """No network, no HubDB access table, a clean flag environment and caches."""
    def _no_network(*a, **k):
        raise RuntimeError("network disabled in workspace tests")
    monkeypatch.setattr("requests.sessions.Session.request", _no_network)
    for var in ("PORTAL_STRICT_IDENTITY", "PORTAL_COMPANY_ACCESS", "INTERNAL_API_KEY",
                "WORKSPACE_SIGNED_LINKS_ENABLED", "WORKSPACE_LINK_SECRET",
                "WORKSPACE_SIGNED_LINKS_CAN_DECIDE", "PORTAL_TICKETS_PILOT_EMAILS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("WORKSPACE_ENABLED", "true")
    monkeypatch.setattr(feature_access, "_load_access_table", lambda: {})
    monkeypatch.setattr(feature_access, "_load_stage_table", lambda: {})
    monkeypatch.setattr(loop_writer, "_bq", lambda: None)
    feature_access.clear_cache()
    wi._onboarding_cache.clear()
    wcache.clear()
    wd._recent.clear()
    yield
    feature_access.clear_cache()
    wd._recent.clear()


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
    monkeypatch.setattr(portal_tickets, "tracking_degraded", lambda: False)
    monkeypatch.setattr(ticket_manager, "list_tickets",
                        lambda cid, include_closed=False: [dict(SERVICE_TICKET)])
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
PREVIEW = {"X-Workspace-Preview-Role": "client"}

GET_ROUTES = [
    "/api/workspace/me",
    "/api/workspace/portfolio",
    "/api/workspace/signals",
    f"/api/workspace/work?company_id={CID}",
    f"/api/workspace/work/hubdb_rec:991?company_id={CID}",
    f"/api/workspace/property?company_id={CID}",
    f"/api/workspace/performance?company_id={CID}",
    f"/api/workspace/plan?company_id={CID}",
    f"/api/workspace/client-view?company_id={CID}",
    f"/api/workspace/requests?company_id={CID}",
    "/api/workspace/search?q=broadway",
]
PROPERTY_ROUTES = [r for r in GET_ROUTES if "company_id" in r]
POST_ROUTES = [
    "/api/workspace/work/hubdb_rec:991/decision",
    "/api/workspace/work/hubdb_rec:991/undo",
    "/api/workspace/signals/stale_inventory:123:2026-09-14/start-work",
    "/api/workspace/requests",
]


def _allowlist_client(monkeypatch, companies=("555",)):
    monkeypatch.setattr(feature_access, "_load_access_table", lambda: {
        CLIENT: {"role": "client", "beta_features": {"workspace", "portal_tickets"},
                 "companies": set(companies)}})
    feature_access.clear_cache()


# ── 1. flag ──────────────────────────────────────────────────────────────────

class TestFlag:
    @pytest.mark.parametrize("url", GET_ROUTES)
    def test_every_read_404s_when_flag_off(self, client, monkeypatch, url):
        monkeypatch.setenv("WORKSPACE_ENABLED", "false")
        assert client.get(url, headers=_h()).status_code == 404

    @pytest.mark.parametrize("url", POST_ROUTES + ["/api/workspace/requests/draft",
                                                   "/api/internal/workspace/warm"])
    def test_every_write_404s_when_flag_off(self, client, monkeypatch, url):
        monkeypatch.delenv("WORKSPACE_ENABLED")
        r = client.post(url, headers=_h(), json={"company_id": CID}, environ_overrides=VERIFIED)
        assert r.status_code == 404

    def test_preflight_allows_the_workspace_headers(self, client):
        r = client.options("/api/workspace/me", headers={"Origin": "https://go.rpmliving.com"})
        assert r.status_code == 204
        allowed = r.headers["Access-Control-Allow-Headers"]
        assert "X-Workspace-Link" in allowed and "X-Workspace-Preview-Role" in allowed

    def test_server_cors_allows_the_workspace_headers(self):
        import server
        r = server.app.test_client().get("/health", headers={"Origin": "https://go.rpmliving.com"})
        allowed = r.headers.get("Access-Control-Allow-Headers") or ""
        assert "X-Workspace-Link" in allowed and "X-Workspace-Preview-Role" in allowed

    def test_feature_key_is_registered_beta(self):
        assert feature_access.FEATURES["workspace"].default_stage == feature_access.STAGE_BETA

    def test_workspace_event_types_are_registered(self):
        for t in ("workspace_decision", "workspace_decision_undone", "workspace_request_filed",
                  "recommendation_undone"):
            assert loop_writer.is_known_event_type(t), t


# ── 2. access gates ──────────────────────────────────────────────────────────

class TestAccess:
    @pytest.mark.parametrize("url", GET_ROUTES)
    def test_no_identity_is_401(self, client, url):
        assert client.get(url).status_code == 401

    @pytest.mark.parametrize("url", GET_ROUTES)
    def test_client_without_the_feature_is_403(self, client, url):
        r = client.get(url, headers=_h(CLIENT))
        assert r.status_code == 403

    @pytest.mark.parametrize("url", PROPERTY_ROUTES)
    def test_client_with_feature_but_not_this_property_is_403(self, client, monkeypatch, url):
        _allowlist_client(monkeypatch, companies=("555",))
        r = client.get(url, headers=_h(CLIENT))
        assert r.status_code == 403
        assert r.get_json()["error"] == "Not authorized for this property"

    @pytest.mark.parametrize("url", ["/api/workspace/work", "/api/workspace/property",
                                     "/api/workspace/plan", "/api/workspace/client-view",
                                     "/api/workspace/requests"])
    def test_missing_company_id_is_400(self, client, url):
        assert client.get(url, headers=_h()).status_code == 400

    @pytest.mark.parametrize("url", ["/api/workspace/portfolio", "/api/workspace/signals"])
    def test_portfolio_and_signals_are_internal_only(self, client, monkeypatch, url):
        _allowlist_client(monkeypatch)
        r = client.get(url, headers=_h(CLIENT))
        assert r.status_code == 403
        assert r.get_json()["error"] == "Internal role required"

    def test_unknown_property_is_404(self, client, monkeypatch):
        def missing(company_id):
            raise wi.PropertyNotFound(company_id)
        monkeypatch.setattr(wi, "load_context", missing)
        assert client.get(f"/api/workspace/plan?company_id={CID}", headers=_h()).status_code == 404

    def test_bad_params_are_400(self, client, ctx):
        assert client.get(f"/api/workspace/work?company_id={CID}&status=later", headers=_h()).status_code == 400
        assert client.get(f"/api/workspace/performance?company_id={CID}&range=7", headers=_h()).status_code == 400
        assert client.get("/api/workspace/portfolio?view=mine", headers=_h()).status_code == 400
        assert client.get("/api/workspace/portfolio?page=0", headers=_h()).status_code == 400

    def test_unknown_item_id_is_404(self, client, ctx, readers):
        assert client.get(f"/api/workspace/work/nope:1?company_id={CID}", headers=_h()).status_code == 404
        assert client.get(f"/api/workspace/work/hubdb_rec:000?company_id={CID}", headers=_h()).status_code == 404

    def test_warm_needs_the_internal_key(self, client, monkeypatch):
        monkeypatch.setenv("INTERNAL_API_KEY", "k")
        assert client.post("/api/internal/workspace/warm").status_code == 401
        assert client.post("/api/internal/workspace/warm", headers={"X-Internal-Key": "bad"}).status_code == 401
        with mock.patch.object(wcache, "warm", return_value={"ok": True, "warmed": []}) as warm:
            r = client.post("/api/internal/workspace/warm", headers={"X-Internal-Key": "k"})
        assert r.status_code == 200 and r.get_json()["ok"] is True
        warm.assert_called_once()


# ── 3. transparency ──────────────────────────────────────────────────────────

class TestTransparency:
    def test_clients_see_every_item(self, client, monkeypatch, readers):
        _allowlist_client(monkeypatch, companies=(CID,))
        c = _ctx()
        monkeypatch.setattr(wi, "load_context", lambda company_id: c)
        body = client.get(f"/api/workspace/work?company_id={CID}&status=all", headers=_h(CLIENT)).get_json()
        items = body["groups"]["late"] + body["groups"]["this_week"]
        sources = {i["source"] for i in items}
        assert {"loop_rec", "hubdb_rec", "portal_ticket"} <= sources
        # call prep is due at month end, so it groups under "later" (titles only)
        assert client.get(f"/api/workspace/work/call_prep:cp1?company_id={CID}",
                          headers=_h(CLIENT)).status_code == 200
        assert all(i["client_visible"] is True and "internal_only" not in i for i in items)

    def test_internal_notes_and_trail_never_reach_a_client(self, client, monkeypatch, readers):
        _allowlist_client(monkeypatch, companies=(CID,))
        c = _ctx()
        monkeypatch.setattr(wi, "load_context", lambda company_id: c)
        internal = client.get(f"/api/workspace/work/call_prep:cp1?company_id={CID}", headers=_h()).get_json()
        assert internal["notes"] and all(n["visibility"] == "internal" for n in internal["notes"])
        assert internal["comments_count"] == len(internal["notes"])
        assert any(t["visibility"] == "internal" for t in internal["trail"])

        as_client = client.get(f"/api/workspace/work/call_prep:cp1?company_id={CID}", headers=_h(CLIENT)).get_json()
        assert as_client["notes"] == [] and as_client["comments_count"] == 0
        assert all(t["visibility"] == "client" for t in as_client["trail"])
        assert "fair_housing_review" not in as_client
        assert json.dumps(as_client).find("Ask on the call") == -1

    def test_client_visible_notes_are_kept_and_counted(self, client, monkeypatch, readers):
        _allowlist_client(monkeypatch, companies=(CID,))
        c = _ctx()
        monkeypatch.setattr(wi, "load_context", lambda company_id: c)
        body = client.get(f"/api/workspace/work/service_ticket:hs1?company_id={CID}", headers=_h(CLIENT)).get_json()
        assert body["comments_count"] == 1 and body["notes"][0]["visibility"] == "client"

    def test_internal_gaps_never_reach_a_client(self, client, monkeypatch, readers):
        _allowlist_client(monkeypatch, companies=(CID,))
        c = _ctx()
        monkeypatch.setattr(wi, "load_context", lambda company_id: c)
        # BigQuery is off in these tests, which yields an internal-only trail gap.
        internal = client.get(f"/api/workspace/work?company_id={CID}&status=all", headers=_h()).get_json()
        as_client = client.get(f"/api/workspace/work?company_id={CID}&status=all", headers=_h(CLIENT)).get_json()
        assert any(g.get("field") == "trail" for g in internal["gaps"])
        assert not any(g.get("field") == "trail" for g in as_client["gaps"])
        for g in internal["gaps"] + as_client["gaps"]:
            assert set(g) <= {"message", "field", "source"}


class TestPreviewAsClient:
    def test_reads_render_with_client_filtering(self, client, ctx, readers):
        body = client.get(f"/api/workspace/work/call_prep:cp1?company_id={CID}",
                          headers=_h(**PREVIEW)).get_json()
        assert body["notes"] == [] and all(t["visibility"] == "client" for t in body["trail"])

    def test_me_reports_client_role_and_no_decisions(self, client, monkeypatch):
        monkeypatch.setattr(wp, "assigned_properties", lambda email: [])
        body = client.get("/api/workspace/me", headers=_h(**PREVIEW), environ_overrides=VERIFIED).get_json()
        assert body["role"] == "client" and body["can_decide"] is False and body["preview_as"] == "client"

    @pytest.mark.parametrize("url", ["/api/workspace/portfolio", "/api/workspace/signals"])
    def test_internal_screens_are_closed(self, client, url):
        assert client.get(url, headers=_h(**PREVIEW)).status_code == 403

    @pytest.mark.parametrize("url", POST_ROUTES + ["/api/workspace/requests/draft"])
    def test_every_write_is_forbidden(self, client, ctx, readers, events, url):
        r = client.post(url, headers=_h(**PREVIEW), environ_overrides=VERIFIED,
                        json={"company_id": CID, "action": "approve", "text": "x", "tickets": []})
        assert r.status_code == 403 and r.get_json()["error"] == "preview_read_only"
        events.assert_not_called()

    def test_header_from_a_client_is_ignored(self, client, monkeypatch, readers):
        _allowlist_client(monkeypatch, companies=(CID,))
        c = _ctx()
        monkeypatch.setattr(wi, "load_context", lambda company_id: c)
        r = client.post("/api/workspace/work/hubdb_rec:991/decision", headers=_h(CLIENT, **PREVIEW),
                        json={"company_id": CID, "action": "approve"})
        assert r.status_code == 401          # not "preview_read_only": the header did nothing


# ── 4. decisions ─────────────────────────────────────────────────────────────

def _decide(client, item_id, action="approve", reason=None, *, email=INTERNAL, verified=True,
            company_id=CID):
    return client.post(
        f"/api/workspace/work/{item_id}/decision", headers=_h(email),
        json={"company_id": company_id, "action": action, "reason": reason},
        environ_overrides=VERIFIED if verified else {},
    )


def _undo(client, item_id, *, email=INTERNAL, verified=True):
    return client.post(f"/api/workspace/work/{item_id}/undo", headers=_h(email),
                       json={"company_id": CID}, environ_overrides=VERIFIED if verified else {})


LOOP_ID = "loop_rec:" + wi.loop_rec_hash("f1", LOOP_REC)


class TestDecisionGates:
    def test_refused_without_verified_identity_even_for_internal(self, client, ctx, readers, events):
        with mock.patch("approval_agent.route_approval") as handler:
            r = _decide(client, "hubdb_rec:991", verified=False)
        assert r.status_code == 401
        handler.assert_not_called()
        events.assert_not_called()

    def test_refused_without_verified_identity_with_strict_mode_off(self, client, ctx, readers, monkeypatch):
        monkeypatch.setenv("PORTAL_STRICT_IDENTITY", "false")
        assert _decide(client, "hubdb_rec:991", verified=False).status_code == 401

    def test_not_now_without_reason_is_400(self, client, ctx, readers, events):
        r = _decide(client, "hubdb_rec:991", action="not_now")
        assert r.status_code == 400
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

    def test_source_without_an_approval_step_is_400(self, client, ctx, readers, events):
        assert _decide(client, "portal_ticket:cu1").status_code == 400

    def test_item_not_waiting_on_a_decision_is_400(self, client, monkeypatch, readers, events):
        c = _ctx(callprep_data_json=json.dumps({"recommendations": [
            {"rec_id": "cp1", "title": "Refresh", "status": "approved"}]}))
        monkeypatch.setattr(wi, "load_context", lambda company_id: c)
        assert _decide(client, "call_prep:cp1").status_code == 400

    def test_unknown_item_is_404(self, client, ctx, readers):
        assert _decide(client, "hubdb_rec:nope").status_code == 404


class TestDispatch:
    """Each source reaches its existing handler as a module call."""

    def _event(self, events, event_type="workspace_decision"):
        calls = [c for c in events.call_args_list if _etype(c) == event_type]
        assert len(calls) == 1, [c.args for c in events.call_args_list]
        return calls[0]

    def _assert_event(self, events, item_id, action, reason=None, outcome="ok"):
        call = self._event(events)
        source = item_id.split(":")[0]
        assert call.args == (wi.STAGE[source], "workspace_decision")
        p = call.kwargs["payload"]
        assert (p["lens"], p["source"], p["reason"], p["actor"], p["action"], p["outcome"], p["item_id"]) == \
            (wi.LENS[source], source, reason, INTERNAL, action, outcome, item_id)
        assert wi.STAGE[source] in loop_writer.LOOP_STAGES
        return p

    def test_hubdb_rec_approve(self, client, ctx, readers, events, monkeypatch):
        import config
        monkeypatch.setattr(config, "HUBSPOT_PORTAL_ID", "19843861", raising=False)
        with mock.patch("approval_agent.route_approval", return_value={
                "status": "ok", "actions_taken": ["HubSpot Deal created: 42", "ClickUp paid_media task created: abc"],
                "errors": []}) as h:
            r = _decide(client, "hubdb_rec:991")
        assert r.status_code == 200
        kw = h.call_args.kwargs
        assert (kw["rec_id"], kw["rec_type"], kw["company_id"], kw["property_uuid"]) == \
            ("991", "budget_change", CID, "u-123")
        payload = self._assert_event(events, "hubdb_rec:991", "approve")
        assert payload["requires_signature"] is True
        body = r.get_json()
        contract.assert_shape(body, "decision")
        assert body["item"]["status"] == "in_motion"
        links = [m["action"] for m in body["in_motion"] if m["action"]]
        assert {"label": "Open the draft deal",
                "href": "https://app.hubspot.com/contacts/19843861/record/0-3/42"} in links
        assert {"label": "Open the ClickUp task", "href": "https://app.clickup.com/t/abc"} in links
        assert any(m["kind"] == "person" and m["note"] for m in body["in_motion"])
        assert body["undo"]["available"] is False and body["undo"]["reason"]

    def test_hubdb_rec_not_now(self, client, ctx, readers, events):
        with mock.patch("approval_agent._update_rec_status") as upd, \
                mock.patch("approval_agent._log_hubspot_activity") as log:
            r = _decide(client, "hubdb_rec:991", action="not_now", reason="already_handled")
        assert r.status_code == 200
        upd.assert_called_once_with("991", "dismissed")
        log.assert_called_once()
        self._assert_event(events, "hubdb_rec:991", "not_now", "already_handled")
        body = r.get_json()
        assert body["item"]["status"] == "done"
        assert body["undo"]["available"] is True and body["undo"]["until"]

    def test_loop_rec_approve_requires_signature(self, client, ctx, readers, events):
        with mock.patch("routes.loop.record_recommendation_approved", return_value="ev-9") as h:
            r = _decide(client, LOOP_ID)
        assert r.status_code == 200
        args, kw = h.call_args
        assert args == ("u-123", LOOP_REC)
        assert kw["forecast_id"] == "f1" and kw["approver_email"] == INTERNAL
        assert self._assert_event(events, LOOP_ID, "approve")["requires_signature"] is True

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
        assert self._assert_event(events, "call_prep:cp1", "approve")["requires_signature"] is False

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
        assert h.call_args.args == ("b1",) and h.call_args.kwargs["company_id"] == CID
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

    def test_ticket_profile_approve_and_not_now(self, client, ctx, readers, events):
        import ticket_profile_sync
        with mock.patch.object(ticket_profile_sync, "accept", return_value={}) as h:
            assert _decide(client, "ticket_profile:p1").status_code == 200
        h.assert_called_once_with("p1", INTERNAL)
        events.reset_mock()
        with mock.patch.object(ticket_profile_sync, "reject", return_value={}) as h:
            assert _decide(client, "ticket_profile:p1", action="not_now", reason="not_priority").status_code == 200
        h.assert_called_once_with("p1", INTERNAL, wi.REASONS["not_priority"])

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

    def test_record_from_decision_history(self, client, ctx, readers, events, monkeypatch):
        history = {"call_prep:old": [{"at": "2026-09-01T00:00:00Z", "action": "approve", "actor": "x",
                                      "outcome": "ok", "undone": False}] * 19
                   + [{"at": "2026-09-02T00:00:00Z", "action": "not_now", "actor": "x",
                       "outcome": "ok", "undone": False}]}
        monkeypatch.setattr(wi, "decision_history", lambda ctx, gaps: history)
        with mock.patch("approval_actions.callprep_approve", return_value=({"recommendations": []}, "created")):
            body = _decide(client, "call_prep:cp1").get_json()
        assert body["record"] == {"label": "Decisions on call prep recommendations at this property",
                                  "approved_unedited": 20, "total": 21, "threshold": None,
                                  "pct": round(20 / 21, 4)}

    def test_record_is_null_without_history(self, client, ctx, readers, events):
        with mock.patch("approval_actions.callprep_approve", return_value=({"recommendations": []}, "created")):
            assert _decide(client, "call_prep:cp1").get_json()["record"] is None

    def test_every_decidable_source_has_handlers_and_an_undo_rule(self):
        for source in wi.DECIDABLE:
            for action in ("approve", "not_now"):
                assert (source, action) in wd.HANDLERS and (source, action) in wd.UNDO


class TestUndo:
    def test_undo_reverses_a_loop_decision_and_logs_it(self, client, ctx, readers, events):
        assert _decide(client, LOOP_ID).status_code == 200
        events.reset_mock()
        r = _undo(client, LOOP_ID)
        assert r.status_code == 200
        body = r.get_json()
        contract.assert_shape(body, "undo_result")
        assert body["undone"] is True and body["item"]["status"] == "to_do"
        types = [_etype(c) for c in events.call_args_list]
        assert types == ["recommendation_undone", "workspace_decision_undone"]
        undone = events.call_args_list[1]
        assert undone.args[0] == "optimize"
        assert undone.kwargs["payload"]["undone_action"] == "approve"
        assert undone.kwargs["payload"]["actor"] == INTERNAL

    def test_undo_not_now_puts_the_card_back(self, client, ctx, readers, events):
        with mock.patch("approval_agent._update_rec_status") as upd, \
                mock.patch("approval_agent._log_hubspot_activity"):
            assert _decide(client, "hubdb_rec:991", action="not_now", reason="not_priority").status_code == 200
            r = _undo(client, "hubdb_rec:991")
        assert r.status_code == 200
        assert upd.call_args_list[-1] == mock.call("991", "pending")

    def test_only_the_decider_can_undo(self, client, ctx, readers, events):
        assert _decide(client, LOOP_ID).status_code == 200
        r = _undo(client, LOOP_ID, email=OTHER_INTERNAL)
        assert r.status_code == 403
        assert not [c for c in events.call_args_list if _etype(c) == "workspace_decision_undone"]

    def test_window_closes_after_ten_minutes(self, client, ctx, readers, events):
        assert _decide(client, LOOP_ID).status_code == 200
        key = (CID, LOOP_ID)
        wd._recent[key]["at"] = wd._recent[key]["at"] - timedelta(minutes=11)
        r = _undo(client, LOOP_ID)
        assert r.status_code == 409
        contract.assert_shape(r.get_json(), "not_undoable")
        assert "10-minute" in r.get_json()["reason"]

    def test_non_undoable_source_is_409(self, client, ctx, readers, events):
        with mock.patch("approval_actions.approve_video_variants", return_value=(
                {"approved": 1, "cycle_status": "Approved", "asset_rows": 1}, 200)):
            body = _decide(client, "video_variant:v1").get_json()
        assert body["undo"] == {"available": False, "until": None,
                                "reason": "Approval wrote the variant to the asset library."}
        r = _undo(client, "video_variant:v1")
        assert r.status_code == 409 and r.get_json()["error"] == "not_undoable"

    def test_nothing_to_undo_is_409(self, client, ctx, readers, events):
        r = _undo(client, LOOP_ID)
        assert r.status_code == 409 and r.get_json()["error"] == "not_undoable"

    def test_undo_needs_verified_identity(self, client, ctx, readers, events):
        assert _decide(client, LOOP_ID).status_code == 200
        assert _undo(client, LOOP_ID, verified=False).status_code == 401

    def test_undo_falls_back_to_decision_history(self, client, ctx, readers, events, monkeypatch):
        recent = wc_now_iso(minutes_ago=3)
        monkeypatch.setattr(wi, "decision_history", lambda ctx, gaps: {
            LOOP_ID: [{"at": recent, "action": "not_now", "reason": "not_priority", "actor": INTERNAL,
                       "outcome": "ok", "undone": False, "undone_at": None}]})
        r = _undo(client, LOOP_ID)
        assert r.status_code == 200
        assert events.call_args_list[-1].kwargs["payload"]["undone_action"] == "not_now"


def _etype(call) -> str:
    return call.args[1] if len(call.args) > 1 else call.kwargs.get("event_type")


def wc_now_iso(minutes_ago: int) -> str:
    from skills import workspace_common as wc
    return wc.to_iso_ts(wc.utc_now() - timedelta(minutes=minutes_ago))


class TestMoneyRuleStatic:
    """Nothing the workspace adds may reference a spend writer."""

    FILES = sorted((REPO / "webhook-server" / "skills").glob("workspace_*.py")) + [
        REPO / "webhook-server" / "routes" / "workspace.py",
        REPO / "webhook-server" / "approval_actions.py",
    ]
    FORBIDDEN = ("import fluency", "from fluency", "fluency_exporter", "budget_sync",
                 "google_ads_islost", "deal_creator", "patch_deal", "create_deal",
                 "rent_roll", "customer_match", "write_monthly_spend", "sheets.googleapis")

    @pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
    def test_no_spend_writers_referenced(self, path):
        text = path.read_text()
        hits = [w for w in self.FORBIDDEN if w in text]
        assert not hits, f"{path.name} references {hits}"

    def test_workspace_never_writes_uuid(self):
        for path in self.FILES:
            text = path.read_text()
            assert "patch_company" not in text and "batch_patch_companies" not in text, path.name


# ── 5. signed links ──────────────────────────────────────────────────────────

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
        with mock.patch.object(wp, "assigned_properties", lambda email: []):
            return client.get("/api/workspace/me", headers={wl.HEADER: token, **headers})

    def test_valid_link_reads_but_is_not_a_verified_identity(self, client, links):
        r = self._me(client, wl.mint(INTERNAL, 3), **{"X-Portal-Email": "someone-else@rpmliving.com"})
        assert r.status_code == 200
        body = r.get_json()
        assert body["email"] == INTERNAL and body["verified"] is False
        assert body["can_decide"] is False and body["signed_link"] is True

    def test_link_cannot_decide_by_default(self, client, links, ctx, readers, events):
        r = client.post("/api/workspace/work/call_prep:cp1/decision",
                        headers={wl.HEADER: wl.mint(INTERNAL, 1)},
                        json={"company_id": CID, "action": "not_now", "reason": "not_priority"})
        assert r.status_code == 401
        events.assert_not_called()

    def test_can_decide_flag_makes_the_link_a_verified_identity(self, client, links, ctx, readers,
                                                                events, monkeypatch):
        monkeypatch.setenv("WORKSPACE_SIGNED_LINKS_CAN_DECIDE", "true")
        assert self._me(client, wl.mint(INTERNAL, 1)).get_json()["can_decide"] is True
        with mock.patch("approval_actions.callprep_dismiss", return_value={"recommendations": []}):
            r = client.post("/api/workspace/work/call_prep:cp1/decision",
                            headers={wl.HEADER: wl.mint(INTERNAL, 1)},
                            json={"company_id": CID, "action": "not_now", "reason": "not_priority"})
        assert r.status_code == 200 and r.get_json()["decided_by"] == INTERNAL

    def test_internal_role_outside_rpmliving_is_refused(self, client, links, monkeypatch):
        monkeypatch.setattr(feature_access, "INTERNAL_EMAILS", {"contractor@agency.com"})
        assert feature_access.role_for("contractor@agency.com") == feature_access.ROLE_INTERNAL
        with pytest.raises(ValueError):
            wl.mint("contractor@agency.com", 1)
        r = self._me(client, _forge("contractor@agency.com", int(time.time()) + 3600))
        assert r.status_code == 401 and "rpmliving" in r.get_json()["detail"]

    def test_expired_link_is_401(self, client, links):
        r = self._me(client, wl.mint(INTERNAL, 1, now=time.time() - 2 * 86400))
        assert r.status_code == 401 and r.get_json()["detail"] == "Link expired"

    def test_tampered_payload_is_401(self, client, links):
        good = wl.mint(INTERNAL, 1)
        other = _forge("kyle@rpmliving.com", int(time.time()) + 3600).split(".")[1]
        head, _, sig = good.split(".")
        r = self._me(client, f"{head}.{other}.{sig}")
        assert r.status_code == 401 and "signature" in r.get_json()["detail"]

    def test_tampered_signature_and_wrong_secret_are_401(self, client, links):
        good = wl.mint(INTERNAL, 1)
        assert self._me(client, good[:-4] + ("AAAA" if not good.endswith("AAAA") else "BBBB")).status_code == 401
        assert self._me(client, _forge(INTERNAL, int(time.time()) + 3600, secret="other")).status_code == 401

    def test_client_email_and_long_lifetime_are_401(self, client, links):
        assert self._me(client, _forge(CLIENT, int(time.time()) + 3600)).status_code == 401
        assert self._me(client, _forge(INTERNAL, int(time.time()) + 10 * 86400)).status_code == 401
        assert self._me(client, "not-a-token").status_code == 401

    def test_flag_off_ignores_the_link(self, client, links, monkeypatch):
        monkeypatch.setenv("WORKSPACE_SIGNED_LINKS_ENABLED", "false")
        token = wl.mint(INTERNAL, 1)
        assert self._me(client, token).status_code == 401
        r = self._me(client, token, **{"X-Portal-Email": INTERNAL})
        assert r.get_json()["verified"] is False and r.get_json()["signed_link"] is False

    def test_no_secret_is_401(self, client, links, monkeypatch):
        token = wl.mint(INTERNAL, 1)
        monkeypatch.delenv("WORKSPACE_LINK_SECRET")
        assert self._me(client, token).status_code == 401

    def test_verified_session_wins_over_a_link(self, client, links):
        token = _forge(CLIENT, int(time.time()) + 3600)     # would 401 on its own
        with mock.patch.object(wp, "assigned_properties", lambda email: []):
            r = client.get("/api/workspace/me", headers={wl.HEADER: token, "X-Portal-Email": INTERNAL},
                           environ_overrides=VERIFIED)
        assert r.get_json()["email"] == INTERNAL and r.get_json()["verified"] is True

    def test_signature_compare_is_constant_time(self, links):
        token = wl.mint(INTERNAL, 1)
        with mock.patch("skills.workspace_links.hmac.compare_digest", wraps=hmac.compare_digest) as cmp:
            wl.verify(token)
        cmp.assert_called_once()

    def test_script_mints_a_verifiable_token(self, links, capsys):
        spec = importlib.util.spec_from_file_location("workspace_link_script", REPO / "scripts" / "workspace_link.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert mod.main([INTERNAL, "--days", "2"]) == 0
        assert wl.verify(capsys.readouterr().out.strip()) == INTERNAL
        assert mod.main([INTERNAL, "--days", "9"]) == 2
        assert mod.main([CLIENT]) == 2


# ── 6. contract shapes ───────────────────────────────────────────────────────

def _shape_ok(body, name):
    contract.assert_shape(body, name)
    assert contract.numbers_without_source(body) == [], contract.numbers_without_source(body)


DAILY = {"ap-1": {"Property ID": "ap-1", "Advertised Occupancy %": "91.8", "Available Units": "44",
                  "Exposure % (Next 30d)": "4.0", "Exposure % (Next 90d)": "9.1",
                  "Report Generation Date": "09/13/2026"}}
PLANS = {"ap-1": [{"Floor Plan Name": "A1", "Beds": "1", "Baths": "1", "Avg Sq Ft": "712",
                   "Available Units": "7", "Days on Market": "96", "Report Generation Date": "09/13/2026"}]}


@pytest.fixture
def aptiq(monkeypatch):
    monkeypatch.setattr(wcache, "aptiq_daily", lambda: (DAILY, TS))
    monkeypatch.setattr(wcache, "aptiq_floor_plans", lambda: (PLANS, TS))


@pytest.fixture
def spend(monkeypatch):
    import hubspot_client
    monkeypatch.setattr(wcache, "monthly_spend", lambda cid: ({
        "company_id": cid, "total": 4638.0, "deal_id": "d1", "deal_name": "IO", "zero_skus": ["tiktok"],
        "by_sku": {"search": 1762.0, "pmax": 1077.0, "seo": 800.0, "social_posting": 499.0, "mgmt_fee": 500.0}},
        TS))
    monkeypatch.setattr(hubspot_client, "get_open_deals_for_company", lambda cid, channel=None: [{"id": "d9"}])


class TestContractShapes:
    def test_me_internal(self, client, monkeypatch):
        monkeypatch.setattr(wp, "assigned_properties", lambda email: [
            {"hubspot_company_id": CID, "uuid": "u-123", "name": "LYV Broadway", "city": "Carrollton",
             "state": "TX", "totalunits": "390"}])
        body = client.get("/api/workspace/me", headers=_h(), environ_overrides=VERIFIED).get_json()
        _shape_ok(body, "me")
        assert body["role"] == "internal" and body["verified"] is True and body["can_decide"] is True

    def test_me_client(self, client, monkeypatch):
        import hubspot_client
        _allowlist_client(monkeypatch, companies=(CID,))
        monkeypatch.setattr(hubspot_client, "get_company", lambda cid, props=None: {
            "uuid": "u-123", "name": "LYV Broadway", "city": "Carrollton", "state": "TX", "totalunits": "390"})
        body = client.get("/api/workspace/me", headers=_h(CLIENT)).get_json()
        _shape_ok(body, "me")
        assert body["role"] == "client" and body["can_decide"] is False
        assert [c["company_id"] for c in body["companies"]] == [CID]

    def test_portfolio_needs_me(self, client, monkeypatch, readers, aptiq):
        props = [{"hubspot_company_id": CID, "name": "LYV Broadway", "city": "Carrollton", "state": "TX",
                  "totalunits": "390", "aptiq_property_id": "ap-1", "marketing_manager_email": INTERNAL},
                 {"hubspot_company_id": "456", "name": "Quiet Place", "totalunits": "100",
                  "marketing_manager_email": INTERNAL}]
        monkeypatch.setattr(wp, "managed_properties", lambda: props)
        contexts = {CID: _ctx(), "456": wi.PropertyContext("456", "u-456", "Quiet Place", {"uuid": "u-456"})}
        monkeypatch.setattr(wi, "load_context", lambda cid: contexts[cid])
        body = client.get("/api/workspace/portfolio", headers=_h()).get_json()
        _shape_ok(body, "portfolio")
        assert body["view"] == "needs_me" and body["scope_label"] == "Assigned to you · 2 properties"
        assert body["property_count"] == 2 and body["quiet_count"] == 1
        row = body["properties"][0]
        assert row["units_at_risk"] == {"value": 44, "source": "aptiq", "as_of": "2026-09-13T00:00:00Z"}
        assert row["top_item"]["needs_approval"] is True

    def test_portfolio_defaults_to_all_without_assignments(self, client, monkeypatch, readers, aptiq):
        props = [{"hubspot_company_id": str(i), "name": f"P{i:03d}"} for i in range(120)]
        monkeypatch.setattr(wp, "managed_properties", lambda: props)
        monkeypatch.setattr(wi, "load_context", lambda cid: wi.PropertyContext(cid, "", "", {}))
        body = client.get("/api/workspace/portfolio", headers=_h()).get_json()
        _shape_ok(body, "portfolio")
        assert body["view"] == "all" and body["property_count"] == 120
        assert len(body["properties"]) == 50 and body["next_page"] == 2
        page3 = client.get("/api/workspace/portfolio?view=all&page=3", headers=_h()).get_json()
        assert len(page3["properties"]) == 20 and page3["next_page"] is None

    def test_work(self, client, ctx, readers):
        body = client.get(f"/api/workspace/work?company_id={CID}&status=all", headers=_h()).get_json()
        _shape_ok(body, "work")
        items = body["groups"]["late"] + body["groups"]["this_week"]
        # no onboarding status and no stored Fair Housing review in this fixture
        assert {i["source"] for i in items} | {"call_prep"} >= set(wi.SOURCES) - {"onboarding_gap", "fair_housing_review"} - wi.INTERNAL_ONLY_SOURCES
        # the badge also counts open items grouped under "later" (call prep, due month end)
        assert body["summary"]["needs_approval"] == sum(1 for i in items if i["needs_approval"]) + 1

    def test_item(self, client, ctx, readers):
        _shape_ok(client.get(f"/api/workspace/work/hubdb_rec:991?company_id={CID}", headers=_h()).get_json(), "item")

    def test_decision(self, client, ctx, readers, events):
        with mock.patch("approval_actions.callprep_approve", return_value=({"recommendations": []}, "created")):
            body = _decide(client, "call_prep:cp1").get_json()
        _shape_ok(body, "decision")
        contract.assert_shape({"company_id": CID, "action": "approve", "reason": None}, "decision_request")

    def test_property(self, client, ctx, aptiq, monkeypatch):
        import config
        import portal_tickets
        import property_brief_audit
        monkeypatch.setattr(config, "HUBSPOT_PORTAL_ID", "19843861", raising=False)
        monkeypatch.setattr(property_brief_audit, "recent_edits", lambda cid, limit=50: [
            {"field_key": "romance", "edited_by": "AM", "edited_at": "2026-08-12T10:00:00.000Z"}])
        monkeypatch.setattr(portal_tickets, "_owner_name", lambda oid: "Marcus Jennings")
        body = client.get(f"/api/workspace/property?company_id={CID}", headers=_h()).get_json()
        _shape_ok(body, "property")
        assert body["hubspot_url"] == f"https://app.hubspot.com/contacts/19843861/record/0-2/{CID}"
        assert body["brief_edit_url"] is None
        assert body["brief"]["text"] == "A courtyard community near the lake." and body["brief"]["curated"]
        assert body["floorplans"][0]["days_on_market"] == 96
        assert body["floorplans"][0]["as_of"] == "2026-09-13T00:00:00Z"

    def test_property_hides_hubspot_url_from_clients(self, client, monkeypatch, aptiq):
        import config
        monkeypatch.setattr(config, "HUBSPOT_PORTAL_ID", "19843861", raising=False)
        _allowlist_client(monkeypatch, companies=(CID,))
        c = _ctx()
        monkeypatch.setattr(wi, "load_context", lambda company_id: c)
        body = client.get(f"/api/workspace/property?company_id={CID}", headers=_h(CLIENT)).get_json()
        assert body["hubspot_url"] is None

    def test_performance(self, client, ctx, aptiq, spend):
        body = client.get(f"/api/workspace/performance?company_id={CID}&range=90", headers=_h()).get_json()
        _shape_ok(body, "performance")
        assert body["occupied"]["value"] == 0.918 and body["occupied"]["units"] == 358
        assert body["occupied"]["as_of"] == "2026-09-13T00:00:00Z"
        assert body["available_now"]["value"] == 44 and body["available_now"]["stale_90_plus"] is None
        fields = {g.get("field") for g in body["gaps"]}
        assert {"available_now.stale_90_plus", "coming_open_90d", "coming_by_week"} <= fields

    def test_plan(self, client, ctx, spend):
        body = client.get(f"/api/workspace/plan?company_id={CID}", headers=_h()).get_json()
        _shape_ok(body, "plan")
        by = {c["channel"]: c for c in body["channels"]}
        assert by["Paid search"]["monthly"] == 2839.0 and by["Paid search"]["status"] == "running"
        assert by["Paid social"]["status"] == "ended" and by["Paid social"]["monthly"] == 0.0
        assert body["pending_changes"] == 1 and body["channel_count"] == 4
        assert round(sum(c["share"] for c in body["channels"]), 3) == 1.0

    def test_client_view(self, client, ctx, readers):
        body = client.get(f"/api/workspace/client-view?company_id={CID}", headers=_h()).get_json()
        _shape_ok(body, "client_view")
        titles = [c["title"] for c in body["changing"]]
        assert "New pool photos" in titles
        assert all(c["date"] is None for c in body["changing"])
        assert any(g.get("field") == "changing.date" for g in body["gaps"])

    @pytest.mark.parametrize("url", [u for u in GET_ROUTES if "portfolio" not in u])
    def test_errors_use_the_error_shape(self, client, url):
        contract.assert_shape(client.get(url).get_json(), "error")


class TestContractHelper:
    def test_catches_missing_wrong_types_and_removed_fields(self):
        errs = contract.check({"summary": {"open": "3"}}, contract.WORK)
        assert any("summary.open" in e for e in errs)
        assert any("counts: missing" in e for e in errs)
        assert contract.check({"message": "x", "reason": "y"}, contract.GAP)
        assert contract.check({"done_count": 1, "hidden_open_count": 0, "changing": [],
                               "done_this_quarter": [], "gaps": []}, contract.CLIENT_VIEW)

    def test_flags_a_bare_number(self):
        assert contract.numbers_without_source({"occupied": {"value": 0.9}}) == ["$.occupied.value"]
        assert contract.numbers_without_source({"occupied": {"value": 0.9, "source": "aptiq"}}) == []
        assert contract.numbers_without_source({"counts": {"to_do": 3}}) == []
