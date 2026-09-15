"""Round 5 keeping the profile fresh: monthly check-in, suggestions from data
(accept runs the same edit flow, dismiss records the reason), history, and
completeness on the Properties rows and Property detail.

Offline: HubSpot, the audit log, BigQuery and loop events are mocked.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent / "webhook-server"))
sys.path.insert(0, str(TESTS))

from flask import Flask  # noqa: E402

import community_brief as cb  # noqa: E402
import feature_access  # noqa: E402
import loop_writer  # noqa: E402
import workspace_contract as contract  # noqa: E402
from routes.workspace import workspace_bp  # noqa: E402
from skills import workspace_dashboard as wdash  # noqa: E402
from skills import workspace_fair_housing_review as fhr  # noqa: E402
from skills import workspace_inbox as wi  # noqa: E402
from skills import workspace_profile as wpr  # noqa: E402

CID = "111"
INTERNAL = "dana.rogers@rpmliving.com"
CLIENT = "owner@acme.com"
VERIFIED = {"portal.identity_verified": True}
NOW = datetime.now(timezone.utc).replace(microsecond=0)

PROPS = {
    "name": "LYV Broadway",
    "fluency_taglines": "Live where Broadway meets the lake. Perfect for young professionals.",
    "fluency_goals": "Reach 95% occupancy by spring",
    "fluency_property_amenities": "Rooftop pool\nFitness center\nDog park",
    "fluency_property_amenities_override": "Pool\nGym",
    "fluency_competitors": "Parkline\nThe Mercer",
    "fluency_competitors_override": "Parkline",
    "fluency_pms": "Yardi",
}


def _ago(days):
    return (NOW - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


AUDIT = [
    {"field_key": "taglines", "edited_by": CLIENT, "edited_at": _ago(120), "new_value": PROPS["fluency_taglines"]},
    {"field_key": "goals", "edited_by": INTERNAL, "edited_at": _ago(5), "new_value": PROPS["fluency_goals"]},
    {"field_key": "property_amenities", "edited_by": INTERNAL, "edited_at": _ago(10), "new_value": "Pool\nGym"},
    {"field_key": "competitors", "edited_by": INTERNAL, "edited_at": _ago(100), "old_value": "",
     "new_value": "Parkline"},
    {"field_key": "pms", "edited_by": INTERNAL, "edited_at": _ago(200), "new_value": "Yardi"},
]


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    import bigquery_client
    import hubspot_client
    import property_brief_audit

    def _no_network(*a, **k):
        raise RuntimeError("network disabled in workspace tests")
    monkeypatch.setattr("requests.sessions.Session.request", _no_network)
    monkeypatch.setenv("WORKSPACE_ENABLED", "true")
    monkeypatch.delenv("PORTAL_STRICT_IDENTITY", raising=False)
    monkeypatch.delenv("TICKET_PROFILE_SYNC_ENABLED", raising=False)
    monkeypatch.setattr(property_brief_audit, "_table_id", lambda: "t-audit")
    monkeypatch.setattr(property_brief_audit, "recent_edits", lambda cid, limit=50: [dict(a) for a in AUDIT])
    monkeypatch.setattr(property_brief_audit, "log_edit", mock.Mock(return_value=True))
    monkeypatch.setattr(hubspot_client, "get_company", lambda cid, props=None: dict(PROPS))
    monkeypatch.setattr(bigquery_client, "is_bigquery_configured", lambda: False)
    monkeypatch.setattr(loop_writer, "_bq", lambda: None)
    monkeypatch.setattr(loop_writer, "record", mock.Mock(return_value="e1"))
    ctx = wi.PropertyContext(CID, "u-111", "LYV Broadway", {"uuid": "u-111", "name": "LYV Broadway"})
    monkeypatch.setattr(wi, "load_context", lambda cid: ctx)
    monkeypatch.setattr(feature_access, "_load_access_table", lambda: {
        CLIENT: {"role": "client", "beta_features": {"workspace"}, "companies": {CID}}})
    monkeypatch.setattr(feature_access, "_load_stage_table", lambda: {})
    feature_access.clear_cache()
    wpr.clear()
    fhr.clear()
    yield
    feature_access.clear_cache()
    wpr.clear()
    fhr.clear()


@pytest.fixture
def write_field(monkeypatch):
    fake = mock.Mock(side_effect=lambda cid, key, value, edited_by="": (True, value))
    monkeypatch.setattr(cb, "write_field", fake)
    return fake


@pytest.fixture
def client():
    app = Flask(__name__)
    app.register_blueprint(workspace_bp)
    return app.test_client()


def _post(client, path, body, email=CLIENT):
    return client.post(path, json={"company_id": CID, **body}, headers={"X-Portal-Email": email},
                       environ_overrides=VERIFIED)


def _profile(client, email=CLIENT):
    r = client.get(f"/api/workspace/profile?company_id={CID}", headers={"X-Portal-Email": email})
    assert r.status_code == 200
    body = r.get_json()
    contract.assert_shape(body, "profile")
    return body, {f["key"]: f for s in body["sections"] for f in s["fields"]}


class TestCheckin:
    def test_check_in_resets_staleness(self, client):
        import property_brief_audit
        body, fields = _profile(client)
        assert body["checkin"]["due"] is True
        assert set(body["checkin"]["stale_fields"]) == {"taglines", "competitors"}
        r = _post(client, "/api/workspace/profile/checkin", {"confirmed": ["taglines", "pms", "short_name", "zzz"]})
        assert r.status_code == 200, r.get_json()
        out = r.get_json()
        contract.assert_shape(out, "profile_checkin")
        assert out["confirmed"] == ["taglines"]
        assert {s["key"]: s["reason"] for s in out["skipped"]} == {
            "pms": "Internal field", "short_name": "No value to confirm", "zzz": "Unknown field"}
        kw = property_brief_audit.log_edit.call_args.kwargs
        assert kw["old_value"] == kw["new_value"] == PROPS["fluency_taglines"]
        assert kw["field_label"] == "Taglines (reviewed, no change)" and kw["edited_by"] == CLIENT
        assert out["checkin"]["stale_fields"] == ["competitors"] and out["checkin"]["due"] is False
        _, fields = _profile(client)
        assert fields["taglines"]["stale"] is False and fields["competitors"]["stale"] is True
        args, kw = loop_writer.record.call_args
        assert args == ("engage", wpr.CHECKIN_EVENT) and kw["payload"]["confirmed"] == ["taglines"]
        assert loop_writer.is_known_event_type(wpr.CHECKIN_EVENT)

    def test_check_in_writes_no_profile_value(self, client, write_field):
        _post(client, "/api/workspace/profile/checkin", {"confirmed": ["taglines"]})
        write_field.assert_not_called()

    def test_bad_body_and_preview(self, client):
        assert _post(client, "/api/workspace/profile/checkin", {"confirmed": "taglines"}).status_code == 400
        r = client.post("/api/workspace/profile/checkin", json={"company_id": CID, "confirmed": ["taglines"]},
                        headers={"X-Portal-Email": INTERNAL, "X-Workspace-Preview-Role": "client"},
                        environ_overrides=VERIFIED)
        assert r.status_code == 403

    def test_dashboard_card_is_a_to_do_not_an_approval(self, monkeypatch):
        from skills import workspace_scope as wscope
        prop = {"hubspot_company_id": CID, "name": "LYV Broadway", "uuid": "u-111"}
        monkeypatch.setattr(wscope, "properties_in_scope",
                            lambda email, internal: {"properties": [prop], "label": "Your properties · 1", "gaps": []})
        for name, value in (("_aptiq", lambda gaps: ({}, None)), ("scope_items", lambda *a, **k: [(prop, [])]),
                            ("leasing_kpis", lambda *a, **k: (None, None, {})),
                            ("activity_rows", lambda *a, **k: ([], None)), ("occupancy_kpi", lambda *a: None),
                            ("units_to_lease_kpi", lambda *a: None), ("visibility_kpi", lambda *a: None),
                            ("actions_taken_kpi", lambda *a: None), ("greeting_name", lambda *a: None)):
            monkeypatch.setattr(wdash, name, value)
        client_body = wdash.build_dashboard(CLIENT, internal=False)
        contract.assert_shape(client_body, "dashboard")
        cards = [w for w in client_body["waiting"] if w.get("kind") == "profile_checkin"]
        assert cards == [{"kind": "profile_checkin", "item_id": None, "company_id": CID,
                          "title": "Review your property profile",
                          "subtitle": "LYV Broadway · 2 fields not updated in 90+ days",
                          "category": None, "stale_count": 2}]
        assert client_body["kpis"]["waiting_on_you"]["value"] == 0
        staff_body = wdash.build_dashboard(INTERNAL, internal=True)
        assert not [w for w in staff_body["waiting"] if w.get("kind") == "profile_checkin"]
        row = client_body["properties"][0]
        assert row["profile_completeness"]["source"] == "community_brief"
        assert 0 < row["profile_completeness"]["value"] < 100
        assert contract.numbers_without_source(client_body) == []


class TestSuggestions:
    def _suggestion(self, client, key, email=CLIENT):
        _, fields = _profile(client, email)
        return fields[key]["suggestion"]

    def test_site_scrape_difference(self, client):
        s = self._suggestion(client, "property_amenities")
        assert s["source"] == "site_scrape" and s["value"] == "Rooftop pool\nFitness center\nDog park"
        assert s["reason"].startswith("We found “Rooftop pool") and s["reason"].endswith("— use it?")

    def test_accept_on_an_ad_facing_field_runs_the_same_review_flow(self, client, write_field):
        s = self._suggestion(client, "property_amenities")
        r = _post(client, f"/api/workspace/profile/suggestions/{s['id']}/accept", {})
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        contract.assert_shape(body, "profile_edit")
        assert body["outcome"] == "pending_review"
        write_field.assert_not_called()
        _, fields = _profile(client)
        assert fields["property_amenities"]["suggestion"] is None
        assert fields["property_amenities"]["pending"]["proposed_value"] == s["value"]

    def test_accept_on_a_context_field_saves(self, client, write_field):
        s = self._suggestion(client, "competitors")
        body = _post(client, f"/api/workspace/profile/suggestions/{s['id']}/accept", {}).get_json()
        assert body["outcome"] == "saved"
        write_field.assert_called_once_with(CID, "competitors", "Parkline\nThe Mercer", edited_by=CLIENT)

    def test_accept_is_blocked_by_fair_housing_like_any_edit(self, client, write_field, monkeypatch):
        import hubspot_client
        props = dict(PROPS, fluency_competitors="Adults only community next door")
        monkeypatch.setattr(hubspot_client, "get_company", lambda cid, p=None: dict(props))
        s = self._suggestion(client, "competitors")
        body = _post(client, f"/api/workspace/profile/suggestions/{s['id']}/accept", {}).get_json()
        assert body["outcome"] == "blocked"
        write_field.assert_not_called()

    def test_fair_housing_review_finding(self, client):
        fhr._latest[CID] = {"findings": [{"kind": "copy", "location": "Property profile: Taglines",
                                          "excerpt": "Perfect for young professionals.", "severity": "high",
                                          "reason": "x", "suggested_fix": "y"}]}
        s = self._suggestion(client, "taglines")
        assert s["source"] == "fair_housing" and s["value"] == "Live where Broadway meets the lake."

    def test_ticket_proposal(self, client, monkeypatch):
        import ticket_profile_sync
        monkeypatch.setattr(ticket_profile_sync, "enabled", lambda: True)
        monkeypatch.setattr(ticket_profile_sync, "list_proposals", lambda cid, uuid="", **kw: [
            {"proposal_id": "tp1", "task_id": "86b2", "field_key": "goals", "proposed_value": "Hit 96% by May"}])
        s = self._suggestion(client, "goals")
        assert s["source"] == "ticket" and s["value"] == "Hit 96% by May" and "86b2" in s["reason"]

    def test_geo_claims(self, client, monkeypatch):
        import bigquery_client
        _, fields = _profile(client, INTERNAL)
        body, _ = _profile(client, INTERNAL)
        assert any(g.get("field") == "suggestion.geo_claim" for g in body["gaps"])
        monkeypatch.setattr(bigquery_client, "is_bigquery_configured", lambda: True)
        monkeypatch.setattr(bigquery_client, "_dataset", lambda: "ds")
        monkeypatch.setattr(bigquery_client, "query", lambda sql, params: [
            {"claim_text": "LYV Broadway is in Uptown Dallas", "conflicts_brief_field": "neighborhood",
             "engine": "ChatGPT"}])
        s = self._suggestion(client, "neighborhood")
        assert s["source"] == "geo_claim" and s["reason"].startswith("ChatGPT says")
        monkeypatch.setattr(bigquery_client, "query", lambda sql, params: [])
        body, _ = _profile(client, INTERNAL)
        assert any(g.get("field") == "suggestion.geo_claim" and "No GEO claims" in g["message"] for g in body["gaps"])

    def test_dismiss_records_the_reason_and_hides_it(self, client):
        s = self._suggestion(client, "property_amenities")
        assert _post(client, f"/api/workspace/profile/suggestions/{s['id']}/dismiss", {}).status_code == 400
        r = _post(client, f"/api/workspace/profile/suggestions/{s['id']}/dismiss", {"reason": "The site is out of date"})
        assert r.status_code == 200
        contract.assert_shape(r.get_json(), "suggestion_dismissed")
        args, kw = loop_writer.record.call_args
        assert args == ("engage", wpr.DISMISSED_EVENT) and kw["payload"]["reason"] == "The site is out of date"
        assert self._suggestion(client, "property_amenities") is None
        assert _post(client, f"/api/workspace/profile/suggestions/{s['id']}/accept", {}).status_code == 404

    def test_clients_never_get_suggestions_for_internal_fields(self, client, monkeypatch):
        import ticket_profile_sync
        monkeypatch.setattr(ticket_profile_sync, "enabled", lambda: True)
        monkeypatch.setattr(ticket_profile_sync, "list_proposals", lambda cid, uuid="", **kw: [
            {"proposal_id": "tp2", "task_id": "1", "field_key": "pms", "proposed_value": "RealPage"}])
        sid = wpr.suggestion_id(CID, "pms", "ticket", "RealPage")
        assert _post(client, f"/api/workspace/profile/suggestions/{sid}/accept", {}).status_code == 404
        assert self._suggestion(client, "pms", email=INTERNAL)["value"] == "RealPage"


class TestHistory:
    def test_entries_for_one_field(self, client, write_field):
        _post(client, "/api/workspace/profile/checkin", {"confirmed": ["taglines"]})
        client.patch("/api/workspace/profile/field", json={"company_id": CID, "key": "taglines", "value": "New."},
                     headers={"X-Portal-Email": CLIENT}, environ_overrides=VERIFIED)
        r = client.get(f"/api/workspace/profile/history?company_id={CID}&key=taglines",
                       headers={"X-Portal-Email": CLIENT})
        assert r.status_code == 200
        body = r.get_json()
        contract.assert_shape(body, "profile_history")
        kinds = [e["kind"] for e in body["entries"]]
        assert kinds[:2] == ["proposed", "reviewed"] and kinds[-1] == "edit"
        assert body["entries"][0]["note"] == "Pending RPM review"

    def test_internal_keys_are_403_for_clients(self, client):
        r = client.get(f"/api/workspace/profile/history?company_id={CID}&key=pms", headers={"X-Portal-Email": CLIENT})
        assert r.status_code == 403
        r = client.get(f"/api/workspace/profile/history?company_id={CID}&key=pms", headers={"X-Portal-Email": INTERNAL})
        assert r.status_code == 200 and r.get_json()["entries"][0]["by"] == "Dana R."


class TestCompleteness:
    def test_property_detail_carries_it(self):
        from skills import workspace_property_overview as wpo
        gaps = []
        metric = wpo._profile_completeness(wi.load_context(CID), False, gaps)
        assert metric["source"] == "community_brief" and metric["weighted"] is True and gaps == []

    def test_cached_and_forgotten_on_save(self, client, write_field, monkeypatch):
        import hubspot_client
        first = wpr.completeness_metric(CID, False)["value"]
        monkeypatch.setattr(hubspot_client, "get_company", lambda cid, p=None: dict(PROPS, fluency_short_name_override="LYV"))
        assert wpr.completeness_metric(CID, False)["value"] == first          # cached
        client.patch("/api/workspace/profile/field", json={"company_id": CID, "key": "goals", "value": "More"},
                     headers={"X-Portal-Email": CLIENT}, environ_overrides=VERIFIED)
        assert wpr.completeness_metric(CID, False)["value"] > first
