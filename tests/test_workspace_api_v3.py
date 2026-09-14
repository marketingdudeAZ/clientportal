"""Workspace v3 screens — dashboard, approvals, property overview, visibility,
content, creative, media plan, value.

Offline: HubSpot, HubDB, BigQuery, AptIQ, the spend tracker sheet and ClickUp are
all mocked, and `requests` is disabled so a missed mock fails loudly. Each class
covers one endpoint: its contract shape, that every number carries a source, the
real-versus-gap split, role scoping, and the rules specific to that screen
(vendor verdicts stay unknown, pacing never pauses a campaign, auto-approve
needs 20 decisions at 90%, create-brief and regenerate need a verified identity).
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent / "webhook-server"))
sys.path.insert(0, str(TESTS))

from flask import Flask  # noqa: E402

import feature_access  # noqa: E402
import loop_writer  # noqa: E402
import workspace_contract as contract  # noqa: E402
from routes.workspace import workspace_bp  # noqa: E402
from skills import workspace_cache as wcache  # noqa: E402
from skills import workspace_common as wc  # noqa: E402
from skills import workspace_history as whist  # noqa: E402
from skills import workspace_inbox as wi  # noqa: E402
from skills import workspace_portfolio as wp  # noqa: E402

INTERNAL = "dana@rpmliving.com"
OTHER = "marcus@rpmliving.com"
CLIENT = "owner@acme.com"
CID = "123"
CID2 = "456"
TS = "2026-09-14T00:00:00Z"
VERIFIED = {"portal.identity_verified": True}
TODAY = date(2026, 9, 14)

PROPS = [
    {"hubspot_company_id": CID, "name": "Parkline", "city": "Tampa", "state": "FL", "totalunits": "138",
     "uuid": "u-123", "aptiq_property_id": "ap-1", "red_light_report_score": "82",
     "marketing_manager_email": INTERNAL},
    {"hubspot_company_id": CID2, "name": "Arcadia West", "city": "Austin", "state": "TX", "totalunits": "200",
     "uuid": "u-456", "aptiq_property_id": "ap-2", "red_light_report_score": "48",
     "marketing_manager_email": INTERNAL},
]
DAILY = {
    "ap-1": {"Advertised Occupancy %": "91.0", "Available Units": "12", "Exposure % (Next 30d)": "4",
             "Exposure % (Next 60d)": "7", "Exposure % (Next 90d)": "10", "Report Generation Date": "09/13/2026"},
    "ap-2": {"Advertised Occupancy %": "80.0", "Available Units": "30", "Exposure % (Next 30d)": "6",
             "Exposure % (Next 60d)": "9", "Exposure % (Next 90d)": "12", "Report Generation Date": "09/13/2026"},
}


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    def _no_network(*a, **k):
        raise RuntimeError("network disabled in workspace tests")
    monkeypatch.setattr("requests.sessions.Session.request", _no_network)
    for var in ("PORTAL_STRICT_IDENTITY", "PORTAL_COMPANY_ACCESS", "PORTAL_TICKETS_PILOT_EMAILS",
                "WORKSPACE_SIGNED_LINKS_ENABLED"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("WORKSPACE_ENABLED", "true")
    monkeypatch.setattr(feature_access, "_load_access_table", lambda: {})
    monkeypatch.setattr(feature_access, "_load_stage_table", lambda: {})
    monkeypatch.setattr(loop_writer, "_bq", lambda: None)
    feature_access.clear_cache()
    wcache.clear()
    import ai_mentions
    ai_mentions._cache.clear()
    yield
    feature_access.clear_cache()


@pytest.fixture
def client():
    app = Flask(__name__)
    app.register_blueprint(workspace_bp)
    return app.test_client()


def _h(email=INTERNAL, **extra):
    return {"X-Portal-Email": email, **extra}


def _ok(body, name):
    contract.assert_shape(body, name)
    assert contract.numbers_without_source(body) == [], contract.numbers_without_source(body)


def _ctx(cid=CID, **over):
    base = dict(next(p for p in PROPS if p["hubspot_company_id"] == cid))
    base.update({"seo_tier": "premium", "red_light_run_date": "2026-09-02", "target_occupancy": "95",
                 "occupancy": "Stable"})
    base.update(over)
    return wi.PropertyContext(cid, base["uuid"], base["name"], base)


def _item(source, sid, title, **kw):
    return wi.finalize(wi._new_item(source, sid, title, **kw))


def _allowlist_client(monkeypatch, companies):
    monkeypatch.setattr(feature_access, "_load_access_table", lambda: {
        CLIENT: {"role": "client", "beta_features": {"workspace"}, "companies": set(companies)}})
    feature_access.clear_cache()


@pytest.fixture
def scope(monkeypatch):
    import hubspot_client
    monkeypatch.setattr(wp, "managed_properties", lambda: [dict(p) for p in PROPS])
    monkeypatch.setattr(wcache, "aptiq_daily", lambda: (DAILY, TS))
    contexts = {CID: _ctx(CID), CID2: _ctx(CID2)}
    monkeypatch.setattr(wi, "load_context", lambda cid: contexts[str(cid)])

    class _Owners:
        def json(self):
            return {"results": [{"firstName": "Dana"}]}
    monkeypatch.setattr(hubspot_client, "_request", lambda *a, **k: _Owners())
    monkeypatch.setattr(hubspot_client, "get_company",
                        lambda cid, props=None: dict(next(p for p in PROPS if p["hubspot_company_id"] == cid)))
    return contexts


@pytest.fixture
def items(monkeypatch):
    fh = _item("hubdb_rec", "fh1", "Market it as adults only", needs_approval=True,
               _raw={"rec_id": "fh1", "rec_type": "strategy_change"})
    fh["fair_housing_review"], fh["_fh_high"] = {"severity": "high", "terms": ["adults only"]}, True
    by_company = {
        CID: [_item("hubdb_rec", "991", "Step down Parkline ILS", needs_approval=True,
                    _raw={"rec_id": "991", "rec_type": "budget_change"}, found="Seven units open soon.",
                    receipts=[{"label": "Red Light report finding", "source": "red_light", "as_of": TS}]),
              _item("content_brief", "b1", "Content brief: parking", needs_approval=True),
              _item("portal_ticket", "cu1", "New pool photos", status="in_motion")],
        CID2: [_item("video_variant", "v1", "Courtyard morning", needs_approval=True), fh],
    }
    monkeypatch.setattr(wi, "collect", lambda ctx, **kw: ([dict(i) for i in by_company.get(ctx.company_id, [])], []))
    return by_company


def _decision_event(item_id, action, at, actor=INTERNAL, source=None, company_id=CID, title=None, outcome="ok"):
    return {"event_type": "workspace_decision", "company_id": company_id, "property_uuid": "u-123",
            "occurred_at": at, "source": "workspace", "trigger": "client_action",
            "payload": json.dumps({"item_id": item_id, "source": source or item_id.split(":")[0],
                                   "action": action, "actor": actor, "title": title, "outcome": outcome})}


# ── 1. dashboard ─────────────────────────────────────────────────────────────

class TestDashboard:
    @pytest.fixture
    def visibility(self, monkeypatch):
        import ai_mentions
        import config
        monkeypatch.setattr(config, "HUBDB_AI_MENTIONS_TABLE_ID", "t-ai", raising=False)
        snaps = {"u-123": {"composite_index": 70, "scanned_at": "2026-09-10T00:00:00Z"},
                 "u-456": {"composite_index": 50, "scanned_at": "2026-09-12T00:00:00Z"}}
        monkeypatch.setattr(ai_mentions, "get_latest_snapshot", lambda uuid: snaps[uuid])

    def test_shape_and_real_values(self, client, scope, items, visibility):
        body = client.get("/api/workspace/dashboard", headers=_h()).get_json()
        _ok(body, "dashboard")
        assert body["greeting_name"] == "Dana"
        k = body["kpis"]
        from skills import workspace_dashboard as wdash
        # JSON does not keep key order; the fixed strip order is KPI_ORDER.
        assert wdash.KPI_ORDER == ("occupancy", "units_to_lease_90d", "leases_this_month", "cost_per_lease",
                                   "ai_visibility", "actions_taken", "waiting_on_you")
        assert set(k) == set(wdash.KPI_ORDER)
        assert k["ai_visibility"] == {"value": 60, "source": "ai_mentions", "as_of": "2026-09-12T00:00:00Z",
                                      "properties": 2}
        assert k["occupancy"]["value"] == round((0.91 * 138 + 0.80 * 200) / 338, 4)
        assert k["units_to_lease_90d"]["value"] == 14 + 24
        assert k["waiting_on_you"]["value"] == 4 and k["leases_this_month"] is None
        assert [(t["name"], t["band"]) for t in body["health_tiles"]] == [("Arcadia West", "critical"),
                                                                           ("Parkline", "healthy")]
        parkline = next(p for p in body["properties"] if p["company_id"] == CID)
        assert parkline["to_lease_90d"]["value"] == 14 and parkline["overspend_per_year"] is None
        assert body["waiting"][0]["company_id"] == CID2
        assert {w["category"] for w in body["waiting"]} >= {"cost", "content", "creative"}
        fields = {g.get("field") for g in body["gaps"]}
        assert {"kpis.leases_this_month", "kpis.actions_taken", "properties.overspend_per_year", "activity"} <= fields
        assert body["loop_status"] == {"running": None, "property_count": 2, "last_pass": None}

    def test_no_lens_toggle(self, client, scope, items, visibility):
        body = client.get("/api/workspace/dashboard?lens=asset_manager", headers=_h())
        assert body.status_code == 200
        assert "lens" not in body.get_json() and "kpi_order" not in body.get_json()

    def test_activity_from_loop_events_is_role_filtered(self, client, scope, items, visibility, monkeypatch):
        now = wc.utc_now()
        monkeypatch.setattr(whist, "recent_events", lambda uuids, **kw: [
            {"event_type": "workspace_decision", "property_uuid": "u-123", "occurred_at": now,
             "payload": {"action": "approve", "title": "Step down Parkline ILS"}},
            {"event_type": "forecast_run", "property_uuid": "u-456", "occurred_at": now - timedelta(hours=2),
             "payload": {}},
            {"event_type": "seo_refresh", "property_uuid": "u-456", "occurred_at": now - timedelta(hours=3),
             "payload": {}}])
        body = client.get("/api/workspace/dashboard", headers=_h()).get_json()
        assert [a["kind"] for a in body["activity"]] == ["decision", "forecast", "check"]
        assert body["activity"][0]["text"] == "Approved: Step down Parkline ILS — Parkline"
        assert body["loop_status"]["running"] is True

        _allowlist_client(monkeypatch, [CID, CID2])
        as_client = client.get("/api/workspace/dashboard", headers=_h(CLIENT)).get_json()
        assert [a["kind"] for a in as_client["activity"]] == ["decision", "forecast"]

    def test_client_scope_is_their_companies(self, client, scope, items, visibility, monkeypatch):
        _allowlist_client(monkeypatch, [CID])
        body = client.get("/api/workspace/dashboard", headers=_h(CLIENT)).get_json()
        assert [t["company_id"] for t in body["health_tiles"]] == [CID]
        assert body["loop_status"]["property_count"] == 1

    def test_needs_the_feature(self, client):
        assert client.get("/api/workspace/dashboard", headers=_h(CLIENT)).status_code == 403
        assert client.get("/api/workspace/dashboard").status_code == 401

    def test_bands(self):
        from skills import workspace_scope as wscope
        assert [wscope.health_band(s) for s in (90, 75, 74, 60, 55, 49, None)] == \
            ["healthy", "healthy", "attention", "attention", "warning", "critical", "new"]


# ── 2. approvals ─────────────────────────────────────────────────────────────

class TestApprovals:
    def test_batch_and_compliance_interrupt(self, client, scope, items):
        body = client.get("/api/workspace/approvals", headers=_h()).get_json()
        _ok(body, "approvals")
        assert body["waiting"] == 4
        assert {r["item_id"] for r in body["batch"]["rows"]} == \
            {"hubdb_rec:991", "content_brief:b1", "video_variant:v1", "hubdb_rec:fh1"}
        assert all(r["can_edit"] is False and r["savings_per_year"] is None for r in body["batch"]["rows"])
        compliance = [i for i in body["interrupts"] if i["kind"] == "compliance"]
        assert compliance and compliance[0]["item_id"] == "hubdb_rec:fh1"
        assert body["stats"]["edit_rate"] is None and body["stats"]["approval_rate"] is None
        assert body["approved_this_month"] is None
        assert {"stats", "stats.edit_rate"} <= {g.get("field") for g in body["gaps"]}

    def test_category_filter(self, client, scope, items):
        body = client.get("/api/workspace/approvals?category=creative", headers=_h()).get_json()
        assert [r["item_id"] for r in body["batch"]["rows"]] == ["video_variant:v1"]
        assert client.get("/api/workspace/approvals?category=vibes", headers=_h()).status_code == 400

    def test_clients_get_no_interrupts(self, client, scope, items, monkeypatch):
        _allowlist_client(monkeypatch, [CID2])
        body = client.get("/api/workspace/approvals", headers=_h(CLIENT)).get_json()
        assert body["interrupts"] == []
        row = next(r for r in body["batch"]["rows"] if r["item_id"] == "hubdb_rec:fh1")
        assert row["action"] == "Recommendation"            # high-severity copy is held from clients

    def test_pacing_interrupt_creates_work_and_never_pauses(self, client, scope, items, monkeypatch):
        import bigquery_client
        import portal_tickets
        from skills import workspace_signals
        monkeypatch.setattr(bigquery_client, "is_bigquery_configured", lambda: True)
        sig = {"id": f"spend_pacing:{CID2}:2026-09-14", "kind": "spend_pacing", "severity": "high",
               "detail": "$900 spent against $3,000 of monthly paid line items (under plan)."}
        monkeypatch.setattr(workspace_signals, "signals_for_property",
                            lambda ctx, gaps, today=None, **kw: [dict(sig)] if ctx.company_id == CID2 else [])
        with mock.patch.object(portal_tickets, "create_ticket") as create, \
                mock.patch("hubspot_client.patch_deal") as patch_deal:
            body = client.get("/api/workspace/approvals", headers=_h()).get_json()
        pacing = next(i for i in body["interrupts"] if i["kind"] == "pacing")
        assert pacing["primary_action"]["label"] == "Pause campaign"
        assert pacing["primary_action"]["creates"] == "work_item"
        assert pacing["primary_action"]["href"] == f"/api/workspace/signals/{sig['id']}/start-work"
        assert "Nothing is paused" in pacing["primary_action"]["note"]
        create.assert_not_called()
        patch_deal.assert_not_called()

    def test_stats_and_auto_approve_candidates(self, client, scope, items, monkeypatch):
        at = TODAY.isoformat() + "T10:00:00+00:00"
        events = [_decision_event(f"content_brief:c{n}", "approve", at) for n in range(19)]
        events += [_decision_event("content_brief:c99", "not_now", at)]
        events += [_decision_event(f"content_brief:c{n}", "approve", at) for n in range(100, 102)]
        events += [_decision_event(f"video_variant:v{n}", "approve", at) for n in range(19)]
        events += [_decision_event(f"call_prep:r{n}", "approve" if n < 17 else "not_now", at) for n in range(20)]
        monkeypatch.setattr(whist, "decision_events", lambda cids, uuids, since: events)
        with mock.patch("skills.workspace_approvals.date") as d:
            d.today.return_value = TODAY
            body = client.get("/api/workspace/approvals", headers=_h()).get_json()
        assert body["stats"]["auto_approve_candidates"] == ["Content briefs"]
        rate = body["stats"]["approval_rate"]
        assert rate["source"] == "workspace_decision" and rate["decisions"] == 61
        assert rate["value"] == round(57 / 61, 4)
        assert body["approved_this_month"] == 57

    def test_candidates_rule_edges(self):
        at = "2026-09-01T00:00:00+00:00"
        decs = whist.to_decisions([_decision_event(f"hubdb_rec:{n}", "approve", at) for n in range(20)])
        assert whist.auto_approve_candidates(decs) == ["Red Light recommendations"]
        decs = whist.to_decisions([_decision_event(f"hubdb_rec:{n}", "approve", at) for n in range(19)])
        assert whist.auto_approve_candidates(decs) == []
        mixed = [_decision_event(f"hubdb_rec:{n}", "approve" if n < 17 else "not_now", at) for n in range(20)]
        assert whist.auto_approve_candidates(whist.to_decisions(mixed)) == []

    def test_history_pairs_undo_and_marks_autopilot(self):
        at1, at2 = "2026-09-01T10:00:00+00:00", "2026-09-01T10:05:00+00:00"
        events = [
            _decision_event("loop_rec:a", "approve", at1),
            {"event_type": "workspace_decision_undone", "occurred_at": at2,
             "payload": json.dumps({"item_id": "loop_rec:a"})},
            {"event_type": "recommendation_approved", "occurred_at": at1, "source": "loop_autopilot",
             "trigger": "autopilot", "property_uuid": "u-123",
             "payload": json.dumps({"recommendation": {"action": "shift_budget", "amount": 200,
                                                       "from_channel": "paid_social", "to_channel": "seo"}})},
            {"event_type": "recommendation_approved", "occurred_at": at1, "source": "client_action",
             "payload": json.dumps({})},
        ]
        decs = whist.to_decisions(events)
        assert [d["undone"] for d in decs] == [True, False]
        assert decs[1]["automatic"] is True and decs[1]["title"] == "Shift $200 from paid_social to seo"
        assert len(whist.effective(decs)) == 1


# ── 3. property overview ─────────────────────────────────────────────────────

class TestPropertyOverview:
    @pytest.fixture
    def sources(self, monkeypatch, scope, items):
        import ai_mentions
        import config
        import sheets_reader
        monkeypatch.setattr(config, "HUBDB_AI_MENTIONS_TABLE_ID", "t-ai", raising=False)
        monkeypatch.setattr(ai_mentions, "get_latest_snapshot", lambda uuid: {
            "composite_index": 62, "scanned_at": "2026-09-10T00:00:00Z",
            "by_engine": {"chatgpt": {"cited_rate": 0.8}, "perplexity": {"cited_rate": 0.0}}, "history": []})
        monkeypatch.setattr(sheets_reader, "get_spend_row", lambda cid: {"zillow_per_month": 450.0,
                                                                         "costar_package": "Premium"})

    def test_shape_and_real_values(self, client, sources):
        body = client.get(f"/api/workspace/property-overview?company_id={CID}", headers=_h()).get_json()
        _ok(body, "property_overview")
        assert body["health"] == {"score": 82.0, "band": "healthy", "source": "redlight",
                                  "as_of": "2026-09-02T00:00:00Z"}
        assert body["kpis"]["ai_visibility"]["value"] == 62
        assert body["kpis"]["units_to_lease"] == {"value": 12, "source": "aptiq", "as_of": "2026-09-13T00:00:00Z"}
        assert body["kpis"]["renewal_rate"] is None and body["kpis"]["lead_to_lease"] is None
        assert [m["units_to_lease"] for m in body["exposure_forecast"]["months"]] == [6, 4, 4]
        assert body["objective"] == "Grow mode"
        assert [v["verdict"] for v in body["vendor_audit"]] == ["unknown", "unknown"]
        assert body["vendor_audit"][0]["monthly"]["value"] == 450.0
        assert [e["score"]["value"] for e in body["visibility_by_engine"]] == [80, 0]
        red_light = next(f for f in body["findings"] if f["item_id"] == "hubdb_rec:991")
        assert red_light["receipts"][0]["source"] == "red_light"
        assert body["recommended_action"]["item_id"] in {"hubdb_rec:991", "content_brief:b1"}
        assert body["draft_email"] is None
        assert {l["lens"]: l["status"] for l in body["loop"]} == \
            {"express": "upcoming", "tailor": "waiting", "amplify": "done", "evolve": "waiting"}
        assert body["links"]["media_plan"] == f"#/property/{CID}/media-plan"
        fields = {g.get("field") for g in body["gaps"]}
        assert {"vendor_audit.verdict", "kpis.renewal_rate", "kpis.lead_to_lease", "draft_email"} <= fields

    def test_no_verdict_without_a_market_rate_source(self, client, sources, monkeypatch):
        import sheets_reader
        monkeypatch.setattr(sheets_reader, "get_spend_row", lambda cid: {})
        body = client.get(f"/api/workspace/property-overview?company_id={CID}", headers=_h()).get_json()
        assert body["vendor_audit"] == []
        assert any(g.get("field") == "vendor_audit" for g in body["gaps"])

    def test_property_gate(self, client, sources, monkeypatch):
        _allowlist_client(monkeypatch, [CID2])
        assert client.get(f"/api/workspace/property-overview?company_id={CID}",
                          headers=_h(CLIENT)).status_code == 403


# ── 4. visibility ────────────────────────────────────────────────────────────

class TestVisibility:
    @pytest.fixture
    def snapshot(self, monkeypatch, scope):
        import ai_mentions
        import bigquery_client
        import config
        monkeypatch.setattr(config, "HUBDB_AI_MENTIONS_TABLE_ID", "t-ai", raising=False)
        monkeypatch.setattr(bigquery_client, "is_bigquery_configured", lambda: False)
        monkeypatch.setattr(ai_mentions, "get_latest_snapshot", lambda uuid: {
            "composite_index": 62, "scanned_at": "2026-09-10T00:00:00Z",
            "by_engine": {"chatgpt": {"cited_rate": 0.8}, "perplexity": {"cited_rate": 0.0},
                          "gemini": {"cited_rate": 0.4}, "google_aio": {"cited_rate": None}},
            "history": [{"composite": 62}, {"composite": 58}]})

    def test_ai_mentions_fallback(self, client, snapshot):
        body = client.get(f"/api/workspace/visibility?company_id={CID}", headers=_h()).get_json()
        _ok(body, "visibility")
        assert body["score"]["value"] == 62 and body["score"]["source"] == "ai_mentions"
        assert body["change"] == {"value": 4, "window": "previous audit", "source": "ai_mentions"}
        assert [e["engine"] for e in body["engines"]] == ["ChatGPT", "Perplexity", "Gemini", "Google AI Overviews"]
        assert body["engines"][3]["score"] is None and body["engines"][0]["queries_hit"] is None
        assert [r["action"]["engine"] for r in body["recommendations"]] == ["Perplexity"]
        assert body["comp_stack"] is None and body["citation_sources"] == []
        assert {"comp_stack", "citation_sources", "next_audit"} <= {g.get("field") for g in body["gaps"]}

    def test_geo_tables_win_when_they_have_rows(self, client, snapshot, monkeypatch):
        import bigquery_client
        monkeypatch.setattr(bigquery_client, "is_bigquery_configured", lambda: True)
        monkeypatch.setattr(bigquery_client, "_dataset", lambda: "ds")
        results = [
            [{"engine": "chatgpt", "total": 20, "hit": 12, "last_at": "2026-09-13T00:00:00Z"},
             {"engine": "perplexity", "total": 20, "hit": 0, "last_at": "2026-09-13T00:00:00Z"}],
            [{"domain": "apartments.com", "n": 34}, {"domain": "parkline.com", "n": 66}],
            [{"brand_name": "Parkline", "is_self": True, "n": 12}, {"brand_name": "The Reserve", "is_self": False, "n": 16}],
        ]
        monkeypatch.setattr(bigquery_client, "query", mock.Mock(side_effect=results))
        body = client.get(f"/api/workspace/visibility?company_id={CID}", headers=_h()).get_json()
        _ok(body, "visibility")
        assert body["score"] == {"value": 30, "source": "geo_brand_mentions", "as_of": "2026-09-13T00:00:00Z"}
        assert (body["engines"][0]["queries_hit"], body["engines"][0]["queries_total"]) == (12, 20)
        assert body["citation_sources"][0] == {"source": "apartments.com", "share": 0.34,
                                               "share_source": "geo_sources"}
        assert body["comp_stack"]["competitors"] == ["The Reserve"]
        assert body["comp_stack"]["rows"][0]["values"] == {"self": 30, "The Reserve": 40}

    def test_missing_geo_tables_fall_back(self, client, snapshot, monkeypatch):
        import bigquery_client
        monkeypatch.setattr(bigquery_client, "is_bigquery_configured", lambda: True)
        monkeypatch.setattr(bigquery_client, "_dataset", lambda: "ds")
        monkeypatch.setattr(bigquery_client, "query", mock.Mock(side_effect=RuntimeError("Not found: geo_responses")))
        body = client.get(f"/api/workspace/visibility?company_id={CID}", headers=_h()).get_json()
        assert body["score"]["source"] == "ai_mentions"

    def test_create_brief_needs_verified_identity(self, client, snapshot):
        with mock.patch("routes.seo.start_content_brief") as start:
            r = client.post("/api/workspace/visibility/create-brief", headers=_h(),
                            json={"company_id": CID, "hub_keyword": "parking faq"})
        assert r.status_code == 401
        start.assert_not_called()

    def test_create_brief_uses_the_existing_path(self, client, snapshot, monkeypatch):
        import seo_entitlement
        monkeypatch.setattr(seo_entitlement, "has_feature", lambda tier, f: True)
        with mock.patch("routes.seo.start_content_brief") as start:
            r = client.post("/api/workspace/visibility/create-brief", headers=_h(), environ_overrides=VERIFIED,
                            json={"company_id": CID, "hub_keyword": "parking faq"})
        assert r.status_code == 202
        _ok(r.get_json(), "create_brief")
        start.assert_called_once_with(CID, "u-123", "parking faq", {})

    def test_create_brief_refusals(self, client, snapshot, monkeypatch):
        import seo_entitlement
        monkeypatch.setattr(seo_entitlement, "has_feature", lambda tier, f: False)
        post = lambda kw, **h: client.post("/api/workspace/visibility/create-brief", headers=_h(**h),
                                           environ_overrides=VERIFIED, json={"company_id": CID, "hub_keyword": kw})
        with mock.patch("routes.seo.start_content_brief") as start:
            assert post("parking faq").status_code == 403
            monkeypatch.setattr(seo_entitlement, "has_feature", lambda tier, f: True)
            assert post("adults only community").status_code == 400
            assert post("x").status_code == 400
            assert post("parking faq", **{"X-Workspace-Preview-Role": "client"}).status_code == 403
        start.assert_not_called()

    def test_seo_route_still_uses_the_same_function(self):
        import inspect
        from routes import seo
        assert "start_content_brief(company_id, property_uuid, hub_keyword, payload)" in inspect.getsource(seo.content_briefs)


# ── 5. content ───────────────────────────────────────────────────────────────

class TestContent:
    @pytest.fixture
    def briefs(self, monkeypatch, scope):
        import config
        import hubdb_helpers
        import seo_entitlement
        monkeypatch.setattr(config, "HUBDB_CONTENT_BRIEFS_TABLE_ID", "t-b", raising=False)
        monkeypatch.setattr(seo_entitlement, "has_feature", lambda tier, f: True)
        monkeypatch.setattr(hubdb_helpers, "read_rows", lambda t, filters=None, limit=500: [
            {"brief_id": "b1", "hub_keyword": "parking in tampa", "status": "generated", "h1": "Parking at Parkline",
             "schema_types": "FAQPage", "generated_at": "2026-09-01T00:00:00Z"},
            {"brief_id": "b2", "hub_keyword": "tampa lofts", "status": "published", "schema_types": "Article"},
            {"brief_id": "b3", "hub_keyword": "pet policy", "status": "approved", "h1": "Top 10 pet perks",
             "target_word_count": 1500}])

    def test_rows_counts_and_gaps(self, client, briefs):
        body = client.get(f"/api/workspace/content?company_id={CID}", headers=_h()).get_json()
        _ok(body, "content")
        assert body["counts"] == {"recommendations": 1, "published": 1, "in_review": 1}
        rows = {r["id"]: r for r in body["rows"]}
        assert (rows["b1"]["type"], rows["b1"]["status"], rows["b1"]["item_id"]) == \
            ("FAQ page", "draft_ready", "content_brief:b1")
        assert rows["b2"]["priority"] == "done" and rows["b2"]["item_id"] is None
        assert rows["b3"]["title"] == "pet policy"          # an unverifiable number in the h1 falls back
        assert body["impact"] == []
        assert {"impact", "rows.gap_source", "rows.priority"} <= {g.get("field") for g in body["gaps"]}

    def test_tier_without_briefs_is_a_gap(self, client, briefs, monkeypatch):
        import seo_entitlement
        monkeypatch.setattr(seo_entitlement, "has_feature", lambda tier, f: False)
        body = client.get(f"/api/workspace/content?company_id={CID}", headers=_h()).get_json()
        assert body["rows"] == [] and any("tier" in g["message"] for g in body["gaps"])


# ── 6. creative ──────────────────────────────────────────────────────────────

class TestCreative:
    @pytest.fixture
    def assets(self, monkeypatch, scope):
        import config
        import hubdb_helpers
        monkeypatch.setattr(config, "HUBDB_ASSET_TABLE_ID", "t-assets", raising=False)
        rows = {
            "u-123": [{"id": 1, "asset_name": "parkline-pool-03", "category": "Photography", "subcategory": "Amenity",
                       "file_type": "jpg", "file_url": "https://cdn/pool.jpg", "source": "photography", "status": "live"},
                      {"id": 2, "asset_name": "Parkline brochure", "category": "Marketing Collateral",
                       "file_type": "pdf", "file_url": "https://cdn/b.pdf", "source": "client_upload", "status": "live"},
                      {"id": 3, "asset_name": "old", "category": "Photography", "file_type": "jpg", "status": "archived"}],
            CID: [{"id": 4, "asset_name": "2026-09 - Courtyard", "category": "Video", "subcategory": "Ad Creative",
                   "file_type": "mp4", "file_url": "https://cdn/v.mp4", "source": "video_pipeline", "status": "live"},
                  {"id": 1, "asset_name": "parkline-pool-03", "category": "Photography", "file_type": "jpg",
                   "status": "live"}],
        }
        monkeypatch.setattr(hubdb_helpers, "read_rows",
                            lambda t, filters=None, limit=500: [dict(r) for r in rows.get(filters["property_uuid"], [])])
        scope[CID].props["video_variants_json"] = json.dumps([
            {"variant_id": "v9", "title": "Courtyard evening", "status": "pending_review", "poster_url": "https://p"},
            {"variant_id": "v8", "title": "Failed", "status": "failed"}])

    def test_assets_origins_and_performance_gap(self, client, assets):
        body = client.get(f"/api/workspace/creative?company_id={CID}", headers=_h()).get_json()
        _ok(body, "creative")
        by_id = {a["id"]: a for a in body["assets"]}
        assert set(by_id) == {"asset:1", "asset:2", "asset:4", "video_variant:v9"}
        assert (by_id["asset:1"]["type"], by_id["asset:1"]["origin"], by_id["asset:1"]["thumbnail_url"]) == \
            ("photos", "inherited", "https://cdn/pool.jpg")
        assert (by_id["asset:2"]["type"], by_id["asset:2"]["origin"]) == ("documents", "uploaded")
        assert (by_id["asset:4"]["type"], by_id["asset:4"]["origin"]) == ("ad_creative", "generated")
        assert all(a["ctr"] is None and a["impressions"] is None for a in body["assets"])
        assert body["counts"] == {"assets": 4, "tracked_in_ads": None} and body["top"] is None
        assert any(g.get("source") == "google_ads" for g in body["gaps"])

    def test_type_filter(self, client, assets):
        body = client.get(f"/api/workspace/creative?company_id={CID}&type=ad_creative", headers=_h()).get_json()
        assert {a["id"] for a in body["assets"]} == {"asset:4", "video_variant:v9"}
        assert body["counts"]["assets"] == 4
        assert client.get(f"/api/workspace/creative?company_id={CID}&type=gifs", headers=_h()).status_code == 400


# ── 7. media plan ────────────────────────────────────────────────────────────

class TestMediaPlan:
    @pytest.fixture
    def plan(self, monkeypatch, scope):
        monkeypatch.setattr(wcache, "monthly_spend", lambda cid: ({
            "company_id": cid, "total": 5000.0, "deal_id": "d1", "zero_skus": [],
            "by_sku": {"search": 2000.0, "pmax": 1000.0, "seo": 1500.0, "mgmt_fee": 500.0}}, TS))

    def test_fiscal_year_channels_and_exposure(self, plan, scope):
        from skills import workspace_media_plan as wmp
        body = wmp.build_media_plan(scope[CID], today=TODAY)
        _ok(body, "media_plan")
        assert body["fiscal_year"] == "FY 2026-27"
        assert [m["month"] for m in body["months"]][:3] == ["2026-07", "2026-08", "2026-09"]
        assert body["months"][-1]["month"] == "2027-06"
        assert {m["month"]: m["units_to_lease"] for m in body["months"] if m["units_to_lease"] is not None} == \
            {"2026-09": 6, "2026-10": 4, "2026-11": 4}
        search = next(c for c in body["channels"] if c["channel"] == "Paid search")
        assert search["monthly"] == [3000.0] * 12 and search["annual"] == 36000.0 and search["share"] == 0.6
        assert search["cpl_target"] is None
        assert body["envelope"] == {"value": 60000.0, "source": "hubspot_line_items", "as_of": TS, "period": "annual"}
        assert body["objective"] == "Grow mode" and body["notes"]
        assert {"channels.monthly", "channels.cpl_target", "months.units_to_lease"} <= \
            {g.get("field") for g in body["gaps"]}

    def test_route_shape(self, client, plan):
        _ok(client.get(f"/api/workspace/media-plan?company_id={CID}", headers=_h()).get_json(), "media_plan")

    def test_regenerate_needs_verified_identity(self, client, plan):
        import portal_tickets
        with mock.patch.object(portal_tickets, "create_ticket") as create:
            r = client.post("/api/workspace/media-plan/regenerate", headers=_h(), json={"company_id": CID})
        assert r.status_code == 401
        create.assert_not_called()

    def test_regenerate_files_a_work_item_and_changes_no_budget(self, client, plan, monkeypatch):
        import hubspot_client
        import portal_tickets
        rec = mock.Mock(return_value="e")
        monkeypatch.setattr(loop_writer, "record", rec)
        with mock.patch.object(portal_tickets, "create_ticket",
                               return_value=({"ok": True, "ticket": {"id": "cu5"}}, 201)) as create, \
                mock.patch.object(hubspot_client, "patch_deal") as patch_deal, \
                mock.patch.object(hubspot_client, "patch_company") as patch_company:
            r = client.post("/api/workspace/media-plan/regenerate", headers=_h(), environ_overrides=VERIFIED,
                            json={"company_id": CID})
        assert r.status_code == 201
        assert r.get_json() == {"work_item_id": "portal_ticket:cu5", "clickup_task_id": "cu5",
                                "status": "draft_requested"}
        assert create.call_args.args == (CID, "campaign_review")
        assert "Nothing changes in live budgets" in create.call_args.kwargs["fields"]["Details"]
        patch_deal.assert_not_called()
        patch_company.assert_not_called()
        assert rec.call_args.args == ("ops", "workspace_request_filed")


# ── 8. value ─────────────────────────────────────────────────────────────────

class TestValue:
    def test_rows_from_decision_history(self, client, scope, monkeypatch):
        at = wc.to_iso_ts(wc.utc_now() - timedelta(days=3))
        events = [
            _decision_event("hubdb_rec:991", "approve", at, title="Step down Parkline ILS"),
            _decision_event("video_variant:v1", "approve", at, actor=OTHER, company_id=CID2),
            _decision_event("content_brief:b1", "approve", at),
            {"event_type": "workspace_decision_undone", "occurred_at": wc.to_iso_ts(wc.utc_now()),
             "payload": json.dumps({"item_id": "content_brief:b1"})},
            _decision_event("call_prep:r1", "not_now", at),
            {"event_type": "recommendation_approved", "occurred_at": at, "source": "loop_autopilot",
             "trigger": "autopilot", "property_uuid": "u-456", "company_id": None,
             "payload": json.dumps({"recommendation": {"action": "shift_budget", "amount": 150,
                                                       "from_channel": "paid_social", "to_channel": "seo"}})},
        ]
        monkeypatch.setattr(whist, "decision_events", lambda cids, uuids, since: events)
        body = client.get("/api/workspace/value?range=6m", headers=_h()).get_json()
        _ok(body, "value")
        assert body["totals"]["changes"] == 3
        assert body["totals"]["automatic_share"]["value"] == round(1 / 3, 4)
        by_change = {r["change"]: r for r in body["rows"]}
        assert by_change["Step down Parkline ILS"]["decided_by"] == "you"
        assert by_change["Approved one of the video variants"]["decided_by"] == "team"
        auto = by_change["Shift $150 from paid_social to seo"]
        assert (auto["decided_by"], auto["property"], auto["company_id"]) == ("automatic", "Arcadia West", CID2)
        assert all(r["annual_value"] is None for r in body["rows"])
        assert body["headline"]["savings_captured"] is None
        assert body["headline"]["changes_shipped"]["value"] == 3
        assert "noi" not in json.dumps(body).lower()

    def test_without_bigquery_everything_is_a_gap(self, client, scope):
        body = client.get("/api/workspace/value", headers=_h()).get_json()
        _ok(body, "value")
        assert body["rows"] == [] and body["totals"]["changes"] is None
        assert body["headline"]["changes_shipped"] is None
        assert any(g.get("source") == "loop_events" for g in body["gaps"])

    def test_bad_range_is_400(self, client):
        assert client.get("/api/workspace/value?range=forever", headers=_h()).status_code == 400
