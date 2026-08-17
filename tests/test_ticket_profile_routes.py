"""Tests for the ticket → property-profile API (routes/ticket_profile.py).

Locks the gating that keeps this inert on main and the rule that a profile
write must name the person who made it:

  * every endpoint 404s while TICKET_PROFILE_LOOP_ENABLED is unset
  * reads accept either credential (X-Portal-Email or X-Internal-Key)
  * accept/reject REQUIRE a portal email — an internal key can read but may not
    action a proposal, because community_brief.write_field records the editor
  * ProposalError statuses (404 / 409 / 400 / 503) reach the client intact
  * a store or ClickUp failure degrades to an empty list, never a 500

ticket_profile_sync is mocked throughout — no BigQuery, HubSpot, or ClickUp.
"""

from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("HUBSPOT_API_KEY", "test-key")

import pytest  # noqa: E402
from flask import Flask  # noqa: E402

import ticket_profile_sync as tps  # noqa: E402
from routes import ticket_profile as tp  # noqa: E402

EMAIL = {"X-Portal-Email": "amy@rpmliving.com"}
PROPOSAL = {"proposal_id": "pid-1", "field_key": "advertised_name",
            "field_label": "Advertised Name",
            "proposed_value": "The Maple at Foxwood",
            "status": tps.STATUS_PENDING, "conflicts_with_override": False}


@pytest.fixture
def client():
    app = Flask(__name__)
    app.register_blueprint(tp.ticket_profile_bp)
    return app.test_client()


@pytest.fixture
def on(monkeypatch):
    """Flip the feature flag on for the duration of a test."""
    monkeypatch.setenv("TICKET_PROFILE_LOOP_ENABLED", "true")


# ── flag gating ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("method,path", [
    ("get",  "/api/ticket-profile/proposals?company_id=cid-1"),
    ("get",  "/api/ticket-profile/history?company_id=cid-1"),
    ("post", "/api/ticket-profile/proposals/accept"),
    ("post", "/api/ticket-profile/proposals/reject"),
])
def test_every_route_404s_while_the_flag_is_off(client, monkeypatch, method, path):
    monkeypatch.delenv("TICKET_PROFILE_LOOP_ENABLED", raising=False)
    resp = getattr(client, method)(path, headers=EMAIL, json={"proposal_id": "pid-1"})
    assert resp.status_code == 404


# ── auth ─────────────────────────────────────────────────────────────────────

def test_read_requires_a_credential(client, on):
    resp = client.get("/api/ticket-profile/proposals?company_id=cid-1")
    assert resp.status_code == 401


def test_internal_key_may_read(client, on, monkeypatch):
    monkeypatch.setenv("INTERNAL_API_KEY", "sekret")
    with mock.patch.object(tps, "list_proposals", return_value=[PROPOSAL]):
        resp = client.get("/api/ticket-profile/proposals?company_id=cid-1",
                          headers={"X-Internal-Key": "sekret"})
    assert resp.status_code == 200
    assert resp.get_json()["proposals"][0]["proposal_id"] == "pid-1"


def test_internal_key_may_not_action_a_proposal(client, on, monkeypatch):
    """A profile write is audit-logged against a person, so a machine
    credential can read the queue but never accept from it."""
    monkeypatch.setenv("INTERNAL_API_KEY", "sekret")
    with mock.patch.object(tps, "accept") as accept:
        resp = client.post("/api/ticket-profile/proposals/accept",
                           headers={"X-Internal-Key": "sekret"},
                           json={"proposal_id": "pid-1"})
    assert resp.status_code == 401
    accept.assert_not_called()


# ── proposals ────────────────────────────────────────────────────────────────

def test_proposals_requires_a_property(client, on):
    resp = client.get("/api/ticket-profile/proposals", headers=EMAIL)
    assert resp.status_code == 400


def test_proposals_returns_the_pending_queue(client, on):
    with mock.patch.object(tps, "list_proposals", return_value=[PROPOSAL]) as lp:
        resp = client.get("/api/ticket-profile/proposals?company_id=cid-1&uuid=u-1",
                          headers=EMAIL)
    assert resp.status_code == 200
    assert len(resp.get_json()["proposals"]) == 1
    lp.assert_called_once_with("cid-1", "u-1")


def test_store_failure_degrades_to_an_empty_queue(client, on):
    with mock.patch.object(tps, "list_proposals", side_effect=RuntimeError("bq down")):
        resp = client.get("/api/ticket-profile/proposals?company_id=cid-1",
                          headers=EMAIL)
    assert resp.status_code == 200
    assert resp.get_json()["proposals"] == []


# ── accept / reject ──────────────────────────────────────────────────────────

def test_accept_passes_the_actor_through(client, on):
    applied = dict(PROPOSAL, status=tps.STATUS_ACCEPTED)
    with mock.patch.object(tps, "accept", return_value=applied) as accept:
        resp = client.post("/api/ticket-profile/proposals/accept",
                           headers=EMAIL, json={"proposal_id": "pid-1"})
    assert resp.status_code == 200
    accept.assert_called_once_with("pid-1", "amy@rpmliving.com")
    assert resp.get_json()["proposal"]["status"] == tps.STATUS_ACCEPTED


def test_accept_requires_a_proposal_id(client, on):
    resp = client.post("/api/ticket-profile/proposals/accept", headers=EMAIL, json={})
    assert resp.status_code == 400


@pytest.mark.parametrize("status,message", [
    (404, "proposal not found"),
    (409, "proposal already accepted"),
    (400, "HubSpot 500"),
    (503, "proposal store unavailable"),
])
def test_proposal_errors_reach_the_client_intact(client, on, status, message):
    with mock.patch.object(tps, "accept",
                           side_effect=tps.ProposalError(status, message)):
        resp = client.post("/api/ticket-profile/proposals/accept",
                           headers=EMAIL, json={"proposal_id": "pid-1"})
    assert resp.status_code == status
    assert resp.get_json()["error"] == message


def test_reject_forwards_the_note(client, on):
    with mock.patch.object(tps, "reject",
                           return_value=dict(PROPOSAL, status=tps.STATUS_REJECTED)) as rej:
        resp = client.post("/api/ticket-profile/proposals/reject", headers=EMAIL,
                           json={"proposal_id": "pid-1", "note": "wrong name"})
    assert resp.status_code == 200
    rej.assert_called_once_with("pid-1", "amy@rpmliving.com", "wrong name")


def test_unexpected_failure_is_502_not_500(client, on):
    with mock.patch.object(tps, "accept", side_effect=RuntimeError("boom")):
        resp = client.post("/api/ticket-profile/proposals/accept",
                           headers=EMAIL, json={"proposal_id": "pid-1"})
    assert resp.status_code == 502


# ── history ──────────────────────────────────────────────────────────────────

def test_history_returns_tickets_with_their_profile_changes(client, on):
    rows = [{"id": "task-9", "subject": "Rebrand rollout", "status": "Done",
             "profile_changes": [{"field_key": "advertised_name",
                                  "field_label": "Advertised Name",
                                  "value": "The Maple at Foxwood",
                                  "actor": "amy@rpmliving.com"}],
             "pending_proposals": 0}]
    with mock.patch.object(tps, "ticket_history", return_value=rows) as hist:
        resp = client.get("/api/ticket-profile/history?company_id=cid-1&uuid=u-1",
                          headers=EMAIL)
    assert resp.status_code == 200
    body = resp.get_json()["tickets"]
    assert body[0]["profile_changes"][0]["field_key"] == "advertised_name"
    hist.assert_called_once_with("cid-1", "u-1", 25)


def test_history_limit_is_clamped(client, on):
    with mock.patch.object(tps, "ticket_history", return_value=[]) as hist:
        client.get("/api/ticket-profile/history?company_id=cid-1&limit=9999",
                   headers=EMAIL)
    assert hist.call_args[0][2] == 100


def test_history_failure_degrades_to_empty(client, on):
    with mock.patch.object(tps, "ticket_history", side_effect=RuntimeError("down")):
        resp = client.get("/api/ticket-profile/history?company_id=cid-1", headers=EMAIL)
    assert resp.status_code == 200
    assert resp.get_json()["tickets"] == []
