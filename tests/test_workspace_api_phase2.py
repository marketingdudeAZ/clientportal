"""Workspace Phase 2 — signals, new requests, search, caches, money guard.

Offline: every reader, the model call and ClickUp are mocked; `requests` is
disabled except where a test installs a recorder to prove where traffic goes.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import date
from pathlib import Path
from unittest import mock
from urllib.parse import urlparse

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
from skills import workspace_decisions as wd  # noqa: E402
from skills import workspace_inbox as wi  # noqa: E402
from skills import workspace_portfolio as wp  # noqa: E402
from skills import workspace_requests as wr  # noqa: E402
from skills import workspace_search as wsearch  # noqa: E402
from skills import workspace_signals as ws  # noqa: E402

TODAY = date(2026, 9, 14)
INTERNAL = "dana@rpmliving.com"
CLIENT = "owner@acme.com"
CID = "123"
VERIFIED = {"portal.identity_verified": True}


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
    wd._recent.clear()
    yield
    feature_access.clear_cache()


@pytest.fixture
def events(monkeypatch):
    rec = mock.Mock(return_value="event-1")
    monkeypatch.setattr(loop_writer, "record", rec)
    return rec


@pytest.fixture
def client():
    app = Flask(__name__)
    app.register_blueprint(workspace_bp)
    return app.test_client()


def _ctx(**props):
    base = {"uuid": "u-123", "name": "Skye Reserve", "aptiq_property_id": "ap-1", "address": "1 Main St",
            "rpmmarket": "Tampa", "marketing_manager_email": INTERNAL}
    base.update(props)
    return wi.PropertyContext(CID, base["uuid"], base["name"], base)


def _allowlist_client(monkeypatch, companies):
    monkeypatch.setattr(feature_access, "_load_access_table", lambda: {
        CLIENT: {"role": "client", "beta_features": {"workspace"}, "companies": set(companies)}})
    feature_access.clear_cache()


# ── signals: rules ───────────────────────────────────────────────────────────

class TestSignalRules:
    def test_occupancy_drop_thresholds(self):
        ctx = _ctx()
        snaps = [{"snapshot_month": "2026-09-01", "occupancy": 76.0},
                 {"snapshot_month": "2026-08-01", "occupancy": 79.1}]
        sig = ws.occupancy_drop(ctx, snaps, TODAY)
        assert (sig["severity"], sig["title"]) == ("high", "Occupancy down 3.1 points in 31 days")
        assert sig["metric"] == {"value": 0.76, "source": "aptiq_snapshots", "as_of": "2026-09-01T00:00:00Z"}
        assert sig["change"] == {"from": 0.791, "to": 0.76, "window_days": 31, "source": "aptiq_snapshots"}
        assert sig["id"] == f"occupancy_drop:{CID}:2026-09-14"
        medium = ws.occupancy_drop(ctx, [{"snapshot_month": "2026-09-01", "occupancy": 0.90},
                                         {"snapshot_month": "2026-08-01", "occupancy": 0.92}], TODAY)
        assert medium["severity"] == "medium"
        assert ws.occupancy_drop(ctx, [{"snapshot_month": "2026-09-01", "occupancy": 0.91},
                                       {"snapshot_month": "2026-08-01", "occupancy": 0.92}], TODAY) is None
        assert ws.occupancy_drop(ctx, snaps[:1], TODAY) is None

    def test_stale_inventory(self):
        rows = [{"Floor Plan Name": "S1", "Available Units": "8", "Days on Market": "96",
                 "Report Generation Date": "09/13/2026"},
                {"Floor Plan Name": "A1", "Available Units": "3", "Days on Market": "120"},
                {"Floor Plan Name": "B1", "Available Units": "9", "Days on Market": "40"},
                {"Floor Plan Name": "B2", "Available Units": "0", "Days on Market": "200"}]
        sig = ws.stale_inventory(_ctx(), rows, TODAY)
        assert sig["severity"] == "high" and sig["metric"]["value"] == 11
        assert sig["title"] == "11 available units sit in floor plans on the market 90+ days"
        assert sig["detail"].startswith("S1 (8 available, 96 days), A1 (3 available, 120 days)")
        assert ws.stale_inventory(_ctx(), rows[2:], TODAY) is None
        assert ws.stale_inventory(_ctx(), rows[1:2], TODAY)["severity"] == "low"

    def test_lease_wave(self):
        row = {"Exposure % (Next 30d)": "5.0", "Exposure % (Next 90d)": "12.5", "Report Generation Date": "09/13/2026"}
        sig = ws.lease_wave(_ctx(), row, TODAY)
        assert sig["severity"] == "high" and sig["change"]["from"] == 0.05 and sig["change"]["to"] == 0.125
        assert ws.lease_wave(_ctx(), dict(row, **{"Exposure % (Next 90d)": "9.5"}), TODAY)["severity"] == "medium"
        assert ws.lease_wave(_ctx(), dict(row, **{"Exposure % (Next 90d)": "4.0"}), TODAY) is None
        assert ws.lease_wave(_ctx(), None, TODAY) is None

    def test_lead_drop(self):
        months = [{"month": "2026-08", "leads": 40}, {"month": "2026-07", "leads": 100}]
        sig = ws.lead_drop(_ctx(), months, TODAY)
        assert sig["severity"] == "high" and sig["title"] == "Leads down 60% month over month"
        assert ws.lead_drop(_ctx(), [{"month": "2026-08", "leads": 65}, {"month": "2026-07", "leads": 100}],
                            TODAY)["severity"] == "medium"
        assert ws.lead_drop(_ctx(), [{"month": "2026-08", "leads": 2}, {"month": "2026-07", "leads": 10}],
                            TODAY) is None

    def test_spend_pacing(self):
        months = [{"month": "2026-08", "spend": 1200.0}]
        sig = ws.spend_pacing(_ctx(), months, 3000.0, "2026-09-14T00:00:00Z", TODAY)
        assert sig["severity"] == "high" and "40% of plan" in sig["title"]
        assert ws.spend_pacing(_ctx(), [{"month": "2026-08", "spend": 2100.0}], 3000.0, None, TODAY)["severity"] == "medium"
        assert ws.spend_pacing(_ctx(), [{"month": "2026-08", "spend": 3000.0}], 3000.0, None, TODAY) is None
        assert ws.spend_pacing(_ctx(), months, None, None, TODAY) is None

    def test_data_stale(self):
        out = ws.data_stale(_ctx(red_light_run_date="2026-06-01"), {"Report Generation Date": "09/01/2026"}, TODAY)
        assert [(s["severity"], s["title"]) for s in out] == [
            ("high", "AptIQ data is 13 days old"), ("low", "Red Light last ran 105 days ago")]
        assert len({s["id"] for s in out}) == 2
        assert ws.data_stale(_ctx(), {"Report Generation Date": "09/13/2026"}, TODAY) == []


@pytest.fixture
def signal_data(monkeypatch):
    monkeypatch.setattr(wcache, "aptiq_daily", lambda: ({"ap-1": {
        "Exposure % (Next 30d)": "5", "Exposure % (Next 90d)": "13", "Report Generation Date": "09/13/2026"}},
        "2026-09-14T00:00:00Z"))
    monkeypatch.setattr(wcache, "aptiq_floor_plans", lambda: ({"ap-1": [
        {"Floor Plan Name": "S1", "Available Units": "12", "Days on Market": "95"}]}, "2026-09-14T00:00:00Z"))
    import bigquery_client
    monkeypatch.setattr(bigquery_client, "is_bigquery_configured", lambda: False)


class TestSignalsEndpoint:
    def test_property_signals_and_gaps(self, client, monkeypatch, signal_data):
        monkeypatch.setattr(wi, "load_context", lambda cid: _ctx())
        body = client.get(f"/api/workspace/signals?company_id={CID}", headers={"X-Portal-Email": INTERNAL}).get_json()
        contract.assert_shape(body, "signals")
        assert contract.numbers_without_source(body) == []
        assert {s["kind"] for s in body["signals"]} == {"stale_inventory", "lease_wave"}
        assert body["counts"] == {"high": 2, "medium": 0, "low": 0}
        sources = {g.get("source") for g in body["gaps"]}
        assert {"reputation", "ga4", "bigquery"} <= sources

    def test_portfolio_signals_use_assigned_properties(self, client, monkeypatch, signal_data):
        monkeypatch.setattr(wp, "assigned_properties", lambda email: [{"hubspot_company_id": CID}])
        monkeypatch.setattr(wi, "load_context", lambda cid: _ctx())
        with mock.patch("skills.data_quality.check_dimension_freshness", return_value=None):
            body = client.get("/api/workspace/signals", headers={"X-Portal-Email": INTERNAL}).get_json()
        assert len(body["signals"]) == 2 and body["signals"][0]["property_name"] == "Skye Reserve"

    def test_start_work_needs_verified_identity(self, client, monkeypatch, signal_data, events):
        monkeypatch.setattr(wi, "load_context", lambda cid: _ctx())
        r = client.post("/api/workspace/signals/stale_inventory:123:2026-09-14/start-work",
                        headers={"X-Portal-Email": INTERNAL}, json={"company_id": CID})
        assert r.status_code == 401
        events.assert_not_called()

    def test_start_work_files_through_the_portal_ticket_path(self, client, monkeypatch, signal_data, events):
        import portal_tickets
        monkeypatch.setattr(wi, "load_context", lambda cid: _ctx())
        sig_id = f"stale_inventory:{CID}:{date.today().isoformat()}"
        with mock.patch.object(portal_tickets, "create_ticket",
                               return_value=({"ok": True, "ticket": {"id": "cu77"}}, 201)) as create:
            r = client.post(f"/api/workspace/signals/{sig_id}/start-work", headers={"X-Portal-Email": INTERNAL},
                            json={"company_id": CID}, environ_overrides=VERIFIED)
        assert r.status_code == 201
        contract.assert_shape(r.get_json(), "start_work")
        assert r.get_json()["work_item_id"] == "portal_ticket:cu77"
        args, kw = create.call_args
        assert args == (CID, "campaign_review") and kw["submitted_by"] == INTERNAL
        assert "95 days" in kw["fields"]["Details"]
        assert events.call_args.args == ("ops", "workspace_request_filed")

    def test_unknown_signal_is_404(self, client, monkeypatch, signal_data, events):
        monkeypatch.setattr(wi, "load_context", lambda cid: _ctx())
        r = client.post(f"/api/workspace/signals/lead_drop:{CID}:2026-09-14/start-work",
                        headers={"X-Portal-Email": INTERNAL}, json={"company_id": CID}, environ_overrides=VERIFIED)
        assert r.status_code == 404


# ── new request ──────────────────────────────────────────────────────────────

REQUEST_TEXT = "Reshoot the A1 photos after the renovation. 7 units open Oct 5, need it by Sept 28."


def _model(tickets):
    return json.dumps({"tickets": tickets})


class TestDraftValidator:
    def test_valid_structured_output(self):
        raw = _model([{"title": "Reshoot A1 photography after renovation", "category": "creative",
                       "needed_by": "2026-09-28", "needed_by_reason": "7 units open Oct 5", "warnings": []}])
        tickets, gaps = wr.parse_draft(raw, REQUEST_TEXT, _ctx(), TODAY)
        t = tickets[0]
        contract.assert_shape({"tickets": tickets, "gaps": []}, "draft")
        assert (t["draft_id"], t["category"], t["needed_by"], t["needed_by_reason"]) == \
            ("d1", "creative", "2026-09-28", "7 units open Oct 5")
        assert t["attached_context"] == ["Property address", "Market", "PM contact"]
        assert gaps == []

    def test_code_fences_are_tolerated(self):
        raw = "```json\n" + _model([{"title": "Fix the amenity list", "category": "listing"}]) + "\n```"
        tickets, _ = wr.parse_draft(raw, "The pool is listed wrong", _ctx(), TODAY)
        assert tickets[0]["warnings"] == [wr.CATEGORY_WARNINGS["listing"]]

    def test_invented_numbers_are_removed(self):
        raw = _model([{"title": "Reshoot 12 floor plans", "category": "creative",
                       "needed_by": "2026-10-01", "needed_by_reason": "Leasing push of 30 units",
                       "warnings": ["Budget of $900 may be needed.", "Coordinate with the PM."]}])
        tickets, gaps = wr.parse_draft(raw, "Reshoot the photos", _ctx(), TODAY)
        t = tickets[0]
        assert t["title"] == "Creative request"
        assert t["needed_by"] is None and t["needed_by_reason"] is None
        assert t["warnings"] == ["Coordinate with the PM."]
        assert {g["field"] for g in gaps} == {"title", "needed_by"}

    def test_unknown_category_becomes_other(self):
        tickets, gaps = wr.parse_draft(_model([{"title": "Something", "category": "billing"}]), "x", _ctx(), TODAY)
        assert tickets[0]["category"] == "other" and gaps[0]["field"] == "category"

    @pytest.mark.parametrize("raw", ["not json at all", "{\"tickets\": []}", "[1, 2]",
                                     "{\"tickets\": [{\"category\": \"web\"}]}"])
    def test_bad_output_is_draft_failed(self, raw):
        with pytest.raises(wc.WorkspaceError) as err:
            wr.parse_draft(raw, "x", _ctx(), TODAY)
        assert err.value.status == 502 and err.value.message == "draft_failed"

    def test_high_severity_output_carries_a_warning(self):
        tickets, _ = wr.parse_draft(_model([{"title": "Ads for adults only", "category": "paid"}]), "x", _ctx(), TODAY)
        assert any("Fair Housing" in w for w in tickets[0]["warnings"])


class TestDraftEndpoint:
    def _post(self, client, text, **headers):
        return client.post("/api/workspace/requests/draft", headers={"X-Portal-Email": INTERNAL, **headers},
                           json={"company_id": CID, "text": text})

    def test_draft_goes_through_the_gateway(self, client, monkeypatch):
        from skills import llm_gateway
        monkeypatch.setattr(wi, "load_context", lambda cid: _ctx())
        resp = llm_gateway.Response(text=_model([{"title": "Reshoot A1 photos", "category": "creative"}]), model="m")
        with mock.patch.object(llm_gateway, "complete", return_value=resp) as complete:
            r = self._post(client, REQUEST_TEXT)
        assert r.status_code == 200
        contract.assert_shape(r.get_json(), "draft")
        assert complete.call_args.kwargs["purpose"] == "workspace_request_draft"
        assert "Never invent numbers" in complete.call_args.kwargs["system"]

    def test_fair_housing_refuses_before_the_model_is_called(self, client, monkeypatch):
        from skills import llm_gateway
        monkeypatch.setattr(wi, "load_context", lambda cid: _ctx())
        with mock.patch.object(llm_gateway, "complete") as complete:
            r = self._post(client, "Make the ads say no kids allowed")
        assert r.status_code == 400 and r.get_json()["error"] == "fair_housing"
        complete.assert_not_called()

    def test_bad_json_from_the_model_is_502(self, client, monkeypatch):
        from skills import llm_gateway
        monkeypatch.setattr(wi, "load_context", lambda cid: _ctx())
        with mock.patch.object(llm_gateway, "complete", return_value=llm_gateway.Response(text="Sure! Here", model="m")):
            r = self._post(client, REQUEST_TEXT)
        assert r.status_code == 502 and r.get_json()["error"] == "draft_failed"

    def test_model_not_configured_is_503(self, client, monkeypatch):
        from skills import llm_gateway
        monkeypatch.setattr(wi, "load_context", lambda cid: _ctx())
        with mock.patch.object(llm_gateway, "complete", side_effect=llm_gateway.LLMNotConfigured("no key")):
            assert self._post(client, REQUEST_TEXT).status_code == 503

    def test_empty_text_is_400(self, client, monkeypatch):
        monkeypatch.setattr(wi, "load_context", lambda cid: _ctx())
        assert self._post(client, "  ").status_code == 400


class TestFileAndRecent:
    def test_filing_needs_verified_identity(self, client, monkeypatch, events):
        monkeypatch.setattr(wi, "load_context", lambda cid: _ctx())
        r = client.post("/api/workspace/requests", headers={"X-Portal-Email": INTERNAL},
                        json={"company_id": CID, "tickets": [{"draft_id": "d1", "title": "x", "category": "web"}]})
        assert r.status_code == 401

    def test_files_each_ticket_and_reports_failures(self, client, monkeypatch, events):
        import portal_tickets
        monkeypatch.setattr(wi, "load_context", lambda cid: _ctx())
        tickets = [
            {"draft_id": "d1", "title": "Reshoot A1 photos", "category": "creative",
             "needed_by": "2026-09-28", "needed_by_reason": "7 units open Oct 5"},
            {"draft_id": "d2", "title": "Ads for adults only", "category": "paid"},
            {"draft_id": "d3", "title": "Fix the hours", "category": "nope"},
        ]
        with mock.patch.object(portal_tickets, "create_ticket",
                               return_value=({"ok": True, "ticket": {"id": "cu9"}}, 201)) as create:
            r = client.post("/api/workspace/requests", headers={"X-Portal-Email": INTERNAL},
                            json={"company_id": CID, "tickets": tickets}, environ_overrides=VERIFIED)
        assert r.status_code == 201
        body = r.get_json()
        contract.assert_shape(body, "filed")
        assert body["created"] == [{"draft_id": "d1", "work_item_id": "portal_ticket:cu9", "clickup_task_id": "cu9"}]
        assert body["failed"] == [{"draft_id": "d2", "reason": "fair_housing"},
                                  {"draft_id": "d3", "reason": "Unknown category"}]
        args, kw = create.call_args
        assert args == (CID, "creative_ad_copy") and "Needed by: 2026-09-28" in kw["fields"]["Details"]
        assert events.call_args.args == ("ops", "workspace_request_filed")

    def test_client_filing_goes_through_the_existing_ticket_gate(self, client, monkeypatch, events):
        _allowlist_client(monkeypatch, companies=(CID,))
        monkeypatch.setattr(wi, "load_context", lambda cid: _ctx())
        r = client.post("/api/workspace/requests", headers={"X-Portal-Email": CLIENT},
                        json={"company_id": CID, "tickets": [{"title": "x", "category": "web"}]},
                        environ_overrides=VERIFIED)
        assert r.status_code == 403          # portal ticketing is RPM staff only by default
        events.assert_not_called()

    def test_recent(self, client, monkeypatch):
        import portal_tickets
        monkeypatch.setattr(wi, "load_context", lambda cid: _ctx())
        monkeypatch.setattr(portal_tickets, "tracking_degraded", lambda: False)
        monkeypatch.setattr(portal_tickets, "list_tickets", lambda *a, **k: [
            {"id": "a", "subject": "A", "status": "Done", "created_ts": 1757500000000},
            {"id": "b", "subject": "B", "status": "Open"},
            {"id": "c", "subject": "C", "status": "Needs your approval"}])
        body = client.get(f"/api/workspace/requests?company_id={CID}", headers={"X-Portal-Email": INTERNAL}).get_json()
        contract.assert_shape(body, "recent")
        assert [r["status"] for r in body["recent"]] == ["done", "new", "in_progress"]


# ── search ───────────────────────────────────────────────────────────────────

MANAGED = [{"hubspot_company_id": "123", "name": "Skye Reserve", "city": "Tampa", "state": "FL"},
           {"hubspot_company_id": "456", "name": "Skyline Lofts", "city": "Dallas", "state": "TX"},
           {"hubspot_company_id": "789", "name": "The Reserve at Elm", "city": "Austin", "state": "TX"}]


class TestSearch:
    def test_internal_searches_every_property_and_ranks(self, client, monkeypatch):
        monkeypatch.setattr(wp, "managed_properties", lambda: MANAGED)
        body = client.get("/api/workspace/search?q=sky", headers={"X-Portal-Email": INTERNAL}).get_json()
        contract.assert_shape(body, "search")
        assert [r["title"] for r in body["results"] if r["type"] == "property"] == ["Skye Reserve", "Skyline Lofts"]
        assert any("company_id" in g["message"] for g in body["gaps"])

    def test_client_sees_only_their_properties(self, client, monkeypatch):
        import hubspot_client
        _allowlist_client(monkeypatch, companies=("456",))
        monkeypatch.setattr(wp, "managed_properties", lambda: MANAGED)
        names = {"456": "Skyline Lofts", "123": "Skye Reserve"}
        monkeypatch.setattr(hubspot_client, "get_company", lambda cid, props=None: {"name": names[cid]})
        monkeypatch.setattr(wi, "load_context", lambda cid: wi.PropertyContext(cid, "", names[cid], {}))
        monkeypatch.setattr(wi, "collect", lambda ctx, **kw: ([], []))
        body = client.get("/api/workspace/search?q=sky", headers={"X-Portal-Email": CLIENT}).get_json()
        assert [r["company_id"] for r in body["results"] if r["type"] == "property"] == ["456"]
        assert client.get("/api/workspace/search?q=sky&company_id=123",
                          headers={"X-Portal-Email": CLIENT}).status_code == 403

    def test_items_reports_and_questions_within_a_property(self, client, monkeypatch):
        monkeypatch.setattr(wp, "managed_properties", lambda: [])
        ctx = wi.PropertyContext(CID, "u", "Skye Reserve", {"red_light_run_date": "2026-09-02"})
        monkeypatch.setattr(wi, "load_context", lambda cid: ctx)
        monkeypatch.setattr(wi, "collect", lambda c, **kw: (
            [wi.finalize(wi._new_item("hubdb_rec", "991", "Refresh the report photos", needs_approval=True))], []))
        body = client.get(f"/api/workspace/search?q=report&company_id={CID}",
                          headers={"X-Portal-Email": INTERNAL}).get_json()
        by_type = {r["type"]: r for r in body["results"]}
        assert by_type["work_item"]["href"] == f"#/item/hubdb_rec:991?company_id={CID}"
        assert by_type["report"]["title"] == "Red Light report · September 2026"
        assert len(body["results"]) <= wsearch.MAX_RESULTS

    def test_questions_match(self, monkeypatch):
        from skills import question_registry
        monkeypatch.setattr(wp, "managed_properties", lambda: [])
        first = question_registry.ordered()[0]
        out = wsearch.search(INTERNAL, first.label[:6], internal=True)
        assert any(r["type"] == "question" and r["id"] == first.key for r in out["results"])

    def test_short_query_is_400(self, client):
        assert client.get("/api/workspace/search?q=a", headers={"X-Portal-Email": INTERNAL}).status_code == 400

    def test_ranking(self):
        assert wsearch.score("skye", "Skye") > wsearch.score("skye", "Skye Reserve") > \
            wsearch.score("res", "Skye Reserve") > wsearch.score("erv", "Skye Reserve") > 0


# ── caches (amendment 3) ─────────────────────────────────────────────────────

class TestServeStale:
    def test_a_warmed_read_is_fast_even_when_sources_are_slow(self, client, monkeypatch):
        import hubspot_client
        delay = 1.5

        def slow(value):
            def build():
                time.sleep(delay)
                return value
            return build

        monkeypatch.setattr(wcache.SPEND, "build", slow([{"company_id": CID, "deal_id": "d", "search": 900.0}]))
        monkeypatch.setattr(wcache.APTIQ_DAILY, "build", slow({"ap-1": {"Advertised Occupancy %": "91",
                                                                         "Available Units": "10"}}))
        monkeypatch.setattr(wcache.APTIQ_FLOOR_PLANS, "build", slow({}))
        monkeypatch.setattr(wcache.PORTFOLIO, "build", slow([]))
        monkeypatch.setattr(wi, "load_context", lambda cid: _ctx(totalunits="100"))
        monkeypatch.setattr(hubspot_client, "get_open_deals_for_company", lambda cid, channel=None: [])

        warmed = wcache.warm()
        assert all(r["ok"] for r in warmed["warmed"])
        # Everything is now past its TTL: a request must serve the last good
        # copy immediately and refresh in the background, not rebuild inline.
        for entry in wcache.ENTRIES:
            entry.built_at -= entry.ttl + 1
        start = time.monotonic()
        r = client.get(f"/api/workspace/performance?company_id={CID}", headers={"X-Portal-Email": INTERNAL})
        elapsed = time.monotonic() - start
        assert r.status_code == 200 and r.get_json()["occupied"]["value"] == 0.91
        assert elapsed < 5.0
        assert elapsed < delay           # proof it did not wait on a rebuild

    def test_cold_read_builds_once(self, monkeypatch):
        calls = []
        monkeypatch.setattr(wcache.SPEND, "build", lambda: calls.append(1) or [])
        wcache.spend_rows()
        wcache.spend_rows()
        assert calls == [1]

    def test_refresh_failure_keeps_the_last_good_copy(self, monkeypatch):
        monkeypatch.setattr(wcache.SPEND, "build", lambda: [{"company_id": "1"}])
        wcache.spend_rows()
        monkeypatch.setattr(wcache.SPEND, "build", mock.Mock(side_effect=RuntimeError("HubSpot down")))
        wcache.SPEND.built_at -= wcache.SPEND.ttl + 1
        rows, _ = wcache.spend_rows()
        for _ in range(50):
            if not wcache.SPEND.refreshing:
                break
            time.sleep(0.01)
        assert rows == [{"company_id": "1"}] and wcache.spend_rows()[0] == [{"company_id": "1"}]

    def test_unconfigured_aptiq_is_reported_not_raised_by_warm(self, monkeypatch):
        monkeypatch.delenv("APT_IQ_DAILY_SHEET_URL", raising=False)
        monkeypatch.delenv("APT_IQ_FLOOR_PLAN_SHEET_URL", raising=False)
        monkeypatch.setattr(wcache.SPEND, "build", lambda: [])
        monkeypatch.setattr(wcache.PORTFOLIO, "build", lambda: [])
        out = wcache.warm()
        assert out["ok"] is True
        with pytest.raises(wcache.SourceUnavailable):
            wcache.aptiq_daily()


# ── money guard (amendment 8) ────────────────────────────────────────────────

class TestNoSpendWrites:
    """Run the real approval handlers with a transport recorder: every request
    must go to HubSpot CRM/HubDB or ClickUp, never to an ad platform, Fluency's
    feed sheet or the spend sheet."""

    ALLOWED_HOSTS = {"api.hubapi.com", "api.clickup.com"}
    FORBIDDEN = ("googleads", "google-ads", "fluency", "sheets.googleapis", "graph.facebook")

    @pytest.fixture
    def recorder(self, monkeypatch):
        seen = []

        class _Resp:
            status_code = 201
            ok = True
            text = "{}"

            def json(self):
                return {"id": "42", "results": [{"id": "9", "properties": {
                    "callprep_data_json": json.dumps({"recommendations": [{"rec_id": "r1", "title": "T"}]}),
                    "video_variants_json": json.dumps([{"variant_id": "v1", "title": "V"}])}}],
                    "properties": {
                        "callprep_data_json": json.dumps({"recommendations": [{"rec_id": "r1", "title": "T"}]}),
                        "video_variants_json": json.dumps([{"variant_id": "v1", "title": "V"}])}}

            def raise_for_status(self):
                return None

        def record(self, method, url, *a, **k):
            seen.append((str(method).upper(), str(url)))
            return _Resp()

        monkeypatch.setattr("requests.sessions.Session.request", record)
        return seen

    def _check(self, seen):
        assert seen, "the handler made no requests at all"
        for method, url in seen:
            host = urlparse(url).hostname
            assert host in self.ALLOWED_HOSTS, (method, url)
            assert not any(f in url.lower() for f in self.FORBIDDEN), (method, url)

    def test_budget_recommendation_approval(self, recorder, monkeypatch):
        import approval_agent
        approval_agent.route_approval(rec_id="r", rec_type="budget_change", property_uuid="u",
                                      company_id=CID, property_name="P", rec_title="Shift budget",
                                      rec_body="Move paid search")
        self._check(recorder)

    def test_call_prep_and_video_approval(self, recorder):
        import approval_actions
        approval_actions.callprep_approve(CID, "r1", INTERNAL)
        approval_actions.approve_video_variants(CID, ["v1"])
        self._check(recorder)

    def test_requires_signature_is_written_for_budget_decisions(self, monkeypatch):
        rec = mock.Mock(return_value="e")
        monkeypatch.setattr(loop_writer, "record", rec)
        ctx = _ctx()
        item = wi._new_item("hubdb_rec", "1", "t", _requires_signature=True)
        wd.record_event(ctx, item, "approve", None, INTERNAL, outcome="ok")
        assert rec.call_args.kwargs["payload"]["requires_signature"] is True
