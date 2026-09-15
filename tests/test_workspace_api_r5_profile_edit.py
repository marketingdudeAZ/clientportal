"""Round 5 profile edit: Fair Housing first, client ad-facing edits wait for RPM,
context and staff edits save now through write_field, and nothing writes uuid.

Offline: HubSpot, the audit log and loop events are mocked; `requests` is disabled
except where a test records the exact HubSpot payloads write_field would send.
"""

from __future__ import annotations

import sys
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
from skills import workspace_inbox as wi  # noqa: E402
from skills import workspace_profile as wpr  # noqa: E402

CID = "111"
INTERNAL = "dana.rogers@rpmliving.com"
CLIENT = "owner@acme.com"
VERIFIED = {"portal.identity_verified": True}
URL = "/api/workspace/profile/field"

PROPS = {
    "name": "LYV Broadway",
    "fluency_taglines": "Live where Broadway meets the lake.",
    "fluency_goals": "Reach 95% occupancy by spring",
}


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
    ctx = wi.PropertyContext(CID, "u-111", "LYV Broadway", {"uuid": "u-111"})
    monkeypatch.setattr(wi, "load_context", lambda cid: ctx)
    monkeypatch.setattr(feature_access, "_load_access_table", lambda: {
        CLIENT: {"role": "client", "beta_features": {"workspace"}, "companies": {CID}}})
    monkeypatch.setattr(feature_access, "_load_stage_table", lambda: {})
    feature_access.clear_cache()
    wpr.clear()
    yield
    feature_access.clear_cache()
    wpr.clear()


@pytest.fixture
def write_field(monkeypatch):
    fake = mock.Mock(side_effect=lambda cid, key, value, edited_by="": (True, value))
    monkeypatch.setattr(cb, "write_field", fake)
    return fake


@pytest.fixture
def events(monkeypatch):
    rec = mock.Mock(return_value="e1")
    monkeypatch.setattr(loop_writer, "record", rec)
    return rec


@pytest.fixture
def client():
    app = Flask(__name__)
    app.register_blueprint(workspace_bp)
    return app.test_client()


def _patch(client, key, value, email=CLIENT, verified=True, preview=False):
    headers = {"X-Portal-Email": email}
    if preview:
        headers["X-Workspace-Preview-Role"] = "client"
    return client.patch(URL, json={"company_id": CID, "key": key, "value": value}, headers=headers,
                        environ_overrides=VERIFIED if verified else {})


def _ok(r):
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    contract.assert_shape(body, "profile_edit")
    return body


class TestClientAdFacing:
    def test_becomes_pending_and_does_not_write(self, client, write_field, events):
        body = _ok(_patch(client, "taglines", "Broadway living, lake views."))
        assert body["outcome"] == "pending_review"
        assert body["message"].startswith("Sent to RPM for review")
        write_field.assert_not_called()
        pending = body["field"]["pending"]
        assert pending["proposed_value"] == "Broadway living, lake views." and pending["by"] == "Owner"
        assert pending["item_id"].startswith("profile_update:")
        assert body["field"]["value"] == "Live where Broadway meets the lake."      # current stays live
        args, kw = events.call_args
        assert args == ("engage", wpr.PROPOSAL_EVENT) and kw["payload"]["proposed_by"] == CLIENT
        assert loop_writer.is_known_event_type(wpr.PROPOSAL_EVENT)

    def test_the_profile_shows_the_pending_update(self, client, write_field, events):
        _ok(_patch(client, "taglines", "Broadway living, lake views."))
        r = client.get(f"/api/workspace/profile?company_id={CID}", headers={"X-Portal-Email": CLIENT})
        fields = {f["key"]: f for s in r.get_json()["sections"] for f in s["fields"]}
        assert fields["taglines"]["pending"]["proposed_value"] == "Broadway living, lake views."
        assert fields["goals"]["pending"] is None

    def test_a_newer_proposal_supersedes_the_older(self, client, write_field, events):
        _ok(_patch(client, "taglines", "First try."))
        _ok(_patch(client, "taglines", "Second try."))
        ctx = wi.load_context(CID)
        rows = wpr.proposals(ctx, [])
        assert [r["status"] for r in rows] == ["pending", "superseded"]
        assert wpr.pending_by_key(ctx, [])["taglines"]["proposed_value"] == "Second try."


class TestSavesNow:
    def test_context_field_saves_through_write_field(self, client, write_field, events):
        body = _ok(_patch(client, "goals", "Reach 96% occupancy by spring"))
        write_field.assert_called_once_with(CID, "goals", "Reach 96% occupancy by spring", edited_by=CLIENT)
        assert body["outcome"] == "saved" and body["message"] == "Saved"  # goals is context-only, not in ads
        assert body["field"]["value"] == "Reach 96% occupancy by spring"
        assert body["field"]["last_updated"] is not None and body["field"]["stale"] is False

    def test_staff_edit_to_an_ad_facing_field_saves_immediately(self, client, write_field, events):
        body = _ok(_patch(client, "taglines", "Broadway living, lake views.", email=INTERNAL))
        assert body["outcome"] == "saved"
        write_field.assert_called_once_with(CID, "taglines", "Broadway living, lake views.", edited_by=INTERNAL)
        assert body["field"]["pending"] is None

    def test_staff_edit_to_an_internal_field(self, client, write_field, events):
        body = _ok(_patch(client, "pms", "Yardi", email=INTERNAL))
        assert body["outcome"] == "saved" and body["message"] == "Saved"

    def test_lists_are_joined_for_the_stored_format(self, client, write_field, events):
        _ok(_patch(client, "voice_tier", ["lifestyle", "luxury"], email=INTERNAL))
        assert write_field.call_args.args[2] == "lifestyle;luxury"


class TestFairHousing:
    def test_high_severity_blocks_the_save(self, client, write_field, events):
        body = _ok(_patch(client, "goals", "Adults only community, no kids."))
        assert body["outcome"] == "blocked" and body["fair_housing"]["result"] == "blocked"
        assert "Fair Housing" in body["message"] and "wasn't saved" in body["message"]
        write_field.assert_not_called()
        events.assert_not_called()

    def test_high_severity_blocks_staff_and_ad_facing_proposals_too(self, client, write_field, events):
        assert _ok(_patch(client, "taglines", "Adults only living.", email=INTERNAL))["outcome"] == "blocked"
        assert _ok(_patch(client, "taglines", "Adults only living."))["outcome"] == "blocked"
        write_field.assert_not_called()
        assert wpr.proposals(wi.load_context(CID), []) == []

    def test_low_severity_saves_with_an_internal_only_flag(self, client, write_field, events):
        body = _ok(_patch(client, "goals", "Great for seniors who want quiet."))
        assert body["outcome"] == "saved" and body["fair_housing"] == {"result": "clear"}
        assert "fair_housing_review" not in body["field"]
        write_field.assert_called_once()
        staff = _ok(_patch(client, "goals", "Great for seniors who want quiet.", email=INTERNAL))
        assert staff["fair_housing"]["result"] == "flagged" and staff["fair_housing"]["severity"] == "low"


class TestRefusals:
    def test_client_cannot_touch_internal_keys(self, client, write_field):
        assert _patch(client, "marketing_budget", "$9,000").status_code == 403
        write_field.assert_not_called()

    def test_preview_as_client_is_read_only(self, client, write_field):
        r = _patch(client, "goals", "x", email=INTERNAL, preview=True)
        assert r.status_code == 403 and r.get_json()["error"] == "preview_read_only"

    def test_needs_verified_identity(self, client, write_field):
        assert _patch(client, "goals", "x", verified=False).status_code == 401

    def test_other_companies_are_refused(self, client, write_field):
        r = client.patch(URL, json={"company_id": "999", "key": "goals", "value": "x"},
                         headers={"X-Portal-Email": CLIENT}, environ_overrides=VERIFIED)
        assert r.status_code == 403

    @pytest.mark.parametrize("key,value", [("name", "New name"), ("voice_tier", "cheap"),
                                           ("floor_plans", "{not json"), ("nope", "x")])
    def test_invalid_edits(self, client, write_field, key, value):
        r = _patch(client, key, value, email=INTERNAL)
        assert r.status_code in (400, 404)
        write_field.assert_not_called()

    def test_value_is_required(self, client, write_field):
        r = client.patch(URL, json={"company_id": CID, "key": "goals"}, headers={"X-Portal-Email": CLIENT},
                         environ_overrides=VERIFIED)
        assert r.status_code == 400


class TestR1:
    def test_no_field_maps_to_uuid(self):
        import hubspot_client
        overrides = {f.hs_override for f in cb.FIELDS.values() if f.hs_override}
        assert "uuid" not in overrides
        assert not overrides & set(hubspot_client.IMMUTABLE_COMPANY_PROPERTIES)

    def test_nothing_the_edit_flow_sends_writes_uuid(self, client, events, monkeypatch):
        sent = []

        class _Resp:
            def __init__(self, status, payload):
                self.status_code, self._payload, self.text = status, payload, ""

            def json(self):
                return self._payload

        monkeypatch.setattr(cb.requests, "get", lambda url, **kw: _Resp(200, {"properties": {"name": "LYV"}}))

        def fake_patch(url, json=None, **kw):
            sent.append(json)
            return _Resp(200, {})
        monkeypatch.setattr(cb.requests, "patch", fake_patch)
        monkeypatch.setattr("property_brief_audit.log_edit", lambda **kw: True)
        monkeypatch.setattr("brief_hooks.on_field_written", lambda **kw: None, raising=False)
        with mock.patch("hubspot_client.patch_company") as patch_company:
            for key, value in (("goals", "Reach 96%"), ("taglines", "Lake views."), ("pms", "Yardi")):
                _ok(_patch(client, key, value, email=INTERNAL))
            _ok(_patch(client, "taglines", "Client idea."))                    # pending: no write at all
        assert len(sent) == 3
        for payload in sent:
            assert "uuid" not in payload["properties"]
            assert set(payload["properties"]) <= {f.hs_override for f in cb.FIELDS.values()}
        patch_company.assert_not_called()
