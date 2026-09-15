"""Round 5 profile-update approvals: an internal-only item under Content, approve
writes through write_field with proposer and approver in the audit, reject
records the reason, undo follows the existing rules.

Offline: HubSpot, the audit log and loop events are mocked.
"""

from __future__ import annotations

import sys
from datetime import date
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
from skills import workspace_approvals as wapp  # noqa: E402
from skills import workspace_dashboard as wdash  # noqa: E402
from skills import workspace_decisions as wd  # noqa: E402
from skills import workspace_inbox as wi  # noqa: E402
from skills import workspace_profile as wpr  # noqa: E402

CID = "111"
INTERNAL = "dana.rogers@rpmliving.com"
CLIENT = "owner@acme.com"
VERIFIED = {"portal.identity_verified": True}
PROPS = {"name": "LYV Broadway", "fluency_taglines": "Live where Broadway meets the lake."}


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    import hubspot_client
    import property_brief_audit

    def _no_network(*a, **k):
        raise RuntimeError("network disabled in workspace tests")
    monkeypatch.setattr("requests.sessions.Session.request", _no_network)
    monkeypatch.setenv("WORKSPACE_ENABLED", "true")
    monkeypatch.delenv("PORTAL_STRICT_IDENTITY", raising=False)
    monkeypatch.setattr(property_brief_audit, "_table_id", lambda: "t-audit")
    monkeypatch.setattr(property_brief_audit, "recent_edits", lambda cid, limit=50: [])
    monkeypatch.setattr(hubspot_client, "get_company", lambda cid, props=None: dict(PROPS))
    monkeypatch.setattr(loop_writer, "_bq", lambda: None)
    monkeypatch.setattr(loop_writer, "record", mock.Mock(return_value="e1"))
    ctx = wi.PropertyContext(CID, "u-111", "LYV Broadway", {"uuid": "u-111", "name": "LYV Broadway"})
    monkeypatch.setattr(wi, "load_context", lambda cid: ctx)
    monkeypatch.setattr(feature_access, "_load_access_table", lambda: {
        CLIENT: {"role": "client", "beta_features": {"workspace"}, "companies": {CID}}})
    monkeypatch.setattr(feature_access, "_load_stage_table", lambda: {})
    feature_access.clear_cache()
    wpr.clear()
    wd._recent.clear()
    yield
    feature_access.clear_cache()
    wpr.clear()
    wd._recent.clear()


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


def _propose(client, value="Broadway living, lake views."):
    r = client.patch("/api/workspace/profile/field", json={"company_id": CID, "key": "taglines", "value": value},
                     headers={"X-Portal-Email": CLIENT}, environ_overrides=VERIFIED)
    assert r.status_code == 200 and r.get_json()["outcome"] == "pending_review"
    return r.get_json()["field"]["pending"]["item_id"]


def _decide(client, item_id, action, email=INTERNAL, reason=None):
    body = {"company_id": CID, "action": action}
    if reason:
        body["reason"] = reason
    return client.post(f"/api/workspace/work/{item_id}/decision", json=body,
                       headers={"X-Portal-Email": email}, environ_overrides=VERIFIED)


def _field(client, email, key="taglines"):
    body = client.get(f"/api/workspace/profile?company_id={CID}", headers={"X-Portal-Email": email}).get_json()
    return next(f for s in body["sections"] for f in s["fields"] if f["key"] == key)


class TestItem:
    def test_proposal_is_an_internal_only_content_item(self, client, write_field):
        item_id = _propose(client)
        ctx = wi.load_context(CID)
        items, _ = wi.collect(ctx, sources=["profile_update"], with_history=False)
        it = items[0]
        assert it["id"] == item_id and it["title"] == "Profile update: Taglines"
        assert it["client_visible"] is False and it["actions"] == {"approve": True, "not_now": True}
        assert wdash.category_for(it) == "content" and wi.SOURCE_LABELS["profile_update"] == "Profile update"
        view = wi.view_item(it, internal=True)
        contract.assert_shape(view, "item")
        pu = view["profile_update"]
        assert pu["current_value"] == "Live where Broadway meets the lake."
        assert pu["proposed_value"] == "Broadway living, lake views." and pu["proposed_by"] == CLIENT
        assert pu["used_in"] == ["ads", "website_faq", "ai_answers"]
        assert {d["op"] for d in pu["diff"]} == {"add", "remove"}
        assert view["for_whom"]["text"] == "Used in: Ads, Website FAQ, AI answers"
        assert view["evidence"]["columns"] == ["Current value", "Proposed value"]
        assert [s["owner"] for s in view["approving_does"]] == ["RPM Digital", "RPM Digital"]
        assert "profile_update" in wdash.ITEM_SOURCES[:-1]          # approvals reads it

    def test_default_collection_never_reads_it(self, client, write_field):
        _propose(client)
        items, _ = wi.collect(wi.load_context(CID), with_history=False)
        assert not [i for i in items if i["source"] == "profile_update"]

    def test_fair_housing_result_is_on_the_item(self, client, write_field):
        _propose(client, "Great for seniors who want quiet.")
        items, _ = wi.collect(wi.load_context(CID), sources=["profile_update"], with_history=False)
        assert items[0]["fair_housing_review"]["severity"] == "low"


class TestDecide:
    def test_approve_writes_with_proposer_and_approver(self, client, write_field):
        item_id = _propose(client)
        write_field.assert_not_called()
        r = _decide(client, item_id, "approve")
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        contract.assert_shape(body, "decision")
        args, kw = write_field.call_args
        assert args == (CID, "taglines", "Broadway living, lake views.")
        assert INTERNAL in kw["edited_by"] and CLIENT in kw["edited_by"]
        assert body["item"]["status"] == "done" and body["undo"]["available"] is False
        assert CLIENT in body["written_down"] and INTERNAL in body["written_down"]
        field = _field(client, CLIENT)
        assert field["pending"] is None
        assert field["review_outcome"]["status"] == "approved" and field["review_outcome"]["reason"] is None

    def test_reject_records_the_reason_and_writes_nothing(self, client, write_field):
        item_id = _propose(client)
        r = _decide(client, item_id, "not_now", reason="wrong_data")
        assert r.status_code == 200, r.get_json()
        write_field.assert_not_called()
        field = _field(client, CLIENT)
        assert field["pending"] is None
        assert field["review_outcome"] == {"status": "rejected", "at": field["review_outcome"]["at"],
                                           "reason": "The data is wrong",
                                           "proposed_value": "Broadway living, lake views."}
        assert field["value"] == "Live where Broadway meets the lake."

    def test_a_decided_update_cannot_be_decided_again(self, client, write_field):
        item_id = _propose(client)
        assert _decide(client, item_id, "approve").status_code == 200
        assert _decide(client, item_id, "approve").status_code == 400
        assert write_field.call_count == 1

    def test_undo_a_rejection_reopens_it_and_approval_is_final(self, client, write_field):
        item_id = _propose(client)
        _decide(client, item_id, "not_now", reason="not_priority")
        r = client.post(f"/api/workspace/work/{item_id}/undo", json={"company_id": CID},
                        headers={"X-Portal-Email": INTERNAL}, environ_overrides=VERIFIED)
        assert r.status_code == 200, r.get_json()
        assert _field(client, INTERNAL)["pending"]["item_id"] == item_id
        assert _decide(client, item_id, "approve").status_code == 200
        r = client.post(f"/api/workspace/work/{item_id}/undo", json={"company_id": CID},
                        headers={"X-Portal-Email": INTERNAL}, environ_overrides=VERIFIED)
        assert r.status_code == 409


class TestClientsNeverSeeIt:
    def test_detail_decision_and_undo_404_for_clients(self, client, write_field):
        item_id = _propose(client)
        headers = {"X-Portal-Email": CLIENT}
        assert client.get(f"/api/workspace/work/{item_id}?company_id={CID}", headers=headers).status_code == 404
        assert _decide(client, item_id, "approve", email=CLIENT).status_code == 404
        write_field.assert_not_called()
        r = client.get(f"/api/workspace/work/{item_id}?company_id={CID}", headers={"X-Portal-Email": INTERNAL})
        assert r.status_code == 200

    def test_preview_as_client_cannot_open_it(self, client, write_field):
        item_id = _propose(client)
        r = client.get(f"/api/workspace/work/{item_id}?company_id={CID}",
                       headers={"X-Portal-Email": INTERNAL, "X-Workspace-Preview-Role": "client"})
        assert r.status_code == 404

    def test_approvals_rows_are_staff_only(self, client, write_field, monkeypatch):
        _propose(client)
        from skills import workspace_scope as wscope
        prop = {"hubspot_company_id": CID, "name": "LYV Broadway", "uuid": "u-111"}
        monkeypatch.setattr(wscope, "properties_in_scope",
                            lambda email, internal: {"properties": [prop], "label": "x", "gaps": []})
        seen = {}

        def scope_items(props, today, gaps, sources=wdash.ITEM_SOURCES):
            seen["sources"] = sources
            items, _ = wi.collect(wi.load_context(CID), sources=["profile_update"], with_history=False)
            return [(prop, items)]
        monkeypatch.setattr(wdash, "scope_items", scope_items)
        staff = wapp.build_approvals(INTERNAL, internal=True, today=date(2026, 9, 15))
        assert "profile_update" in seen["sources"]
        assert [(r["action"], r["category"]) for r in staff["batch"]["rows"]] == [("Profile update: Taglines", "content")]
        assert wapp.build_approvals(CLIENT, internal=False, today=date(2026, 9, 15))["batch"]["rows"] == []
        assert wapp.build_approvals(INTERNAL, internal=True, category="content",
                                    today=date(2026, 9, 15))["batch"]["rows"]

    def test_client_dashboard_waiting_leaves_it_out(self, client, write_field):
        _propose(client)
        items, _ = wi.collect(wi.load_context(CID), sources=["profile_update"], with_history=False)
        assert wi.visible_items(items, internal=False) == []
        assert wi.visible_items(items, internal=True) == items
        source = (TESTS.parent / "webhook-server" / "skills" / "workspace_dashboard.py").read_text()
        assert "wi.visible_items(items, internal)" in source
