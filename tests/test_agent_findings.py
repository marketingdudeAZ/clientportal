"""The return path: an outside agent posts a finding, the portal decides.

What these pin is every refusal, because this is the one route where an
unattended machine writes content that reaches a person and, after an approval,
a live ad account. The happy path is one test; the rest are the door.

No credentials needed — HubSpot, BigQuery and the Fair Housing checker are all
stubbed.
"""
from __future__ import annotations

import os
import sys

import pytest
from flask import Flask

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import skills  # noqa: E402
from routes.agent_findings import agent_findings_bp  # noqa: E402
from skills import agent_findings as af  # noqa: E402
from skills.agent_findings import FindingRejected, FindingUnavailable  # noqa: E402

TOKEN = "agent-token-abc"
AUTH = {"Authorization": "Bearer " + TOKEN}
COMPANY = "30912193455"
UUID = "uuid-atwood-0001"
NAME = "The Atwood at Rivulon"

A_FINDING = {
    "rule_key": "search_budget_capped",
    "category": "cost",
    "severity": "high",
    "confidence": 8,
    "channels": ["paid_search"],
    "found": "Search lost 41% of impressions to budget over the last 30 days.",
    "expect": "Recovering that share should add roughly 120 clicks a month.",
    "if_skip": "The campaign keeps stopping before midday.",
    "receipts": [{"label": "Impression share lost to budget", "value": "41%",
                  "source": "Google Ads", "as_of": "2026-09-17"}],
    "action": {"kind": "budget_change", "params": {"daily_budget": 95},
               "executor": "ninjacat"},
}


class _Identity:
    company_id = COMPANY
    name = NAME

    def to_dict(self):
        return {"uuid": UUID, "company_id": COMPANY, "name": NAME}


@pytest.fixture(autouse=True)
def stub_world(monkeypatch):
    """A resolvable property, a clean Fair Housing check, a writable warehouse."""
    resolver = type("R", (), {"resolve": staticmethod(lambda ident, **kw: _Identity())})
    monkeypatch.setattr(skills, "property_resolver", resolver, raising=False)
    monkeypatch.setitem(sys.modules, "skills.property_resolver", resolver)

    common = type("C", (), {"fair_housing_review": staticmethod(lambda *t: None)})
    monkeypatch.setattr(skills, "workspace_common", common, raising=False)
    monkeypatch.setitem(sys.modules, "skills.workspace_common", common)

    written = []
    import loop_writer
    monkeypatch.setattr(loop_writer, "record",
                        lambda stage, event_type, **kw: written.append(
                            dict(kw, stage=stage, event_type=event_type))
                        or "evt-%d" % len(written))
    yield written


@pytest.fixture
def client(monkeypatch):
    # A labelled token, the way it is actually configured: `posted_by` on every
    # stored finding is this label, which is how a finding is attributed and how
    # readback is scoped.
    monkeypatch.setenv("MCP_TOKENS", "ninjacat:" + TOKEN)
    monkeypatch.delenv("MCP_BEARER_TOKEN", raising=False)
    monkeypatch.delenv("MCP_ENABLED", raising=False)
    app = Flask(__name__)
    app.register_blueprint(agent_findings_bp)
    app.config["TESTING"] = True
    return app.test_client()


def _norm(payload=None, **over):
    body = dict(A_FINDING, **(payload or {}))
    body.update(over)
    return af.normalize(body, company_id=COMPANY, property_name=NAME,
                        posted_by="ninjacat")


# --- the door --------------------------------------------------------------

class TestAccess:
    def test_unconfigured_endpoint_is_invisible(self, client, monkeypatch):
        """No token configured means the feature does not exist, not that it is
        open. A scanner should not even learn the route is here."""
        monkeypatch.delenv("MCP_TOKENS", raising=False)
        monkeypatch.delenv("MCP_BEARER_TOKEN", raising=False)
        assert client.post("/api/agent/findings", json=A_FINDING).status_code == 404
        assert client.get("/api/agent/findings?property=x").status_code == 404

    def test_no_token_is_refused(self, client):
        assert client.post("/api/agent/findings", json=A_FINDING).status_code == 401

    def test_wrong_token_is_refused(self, client):
        resp = client.post("/api/agent/findings", json=A_FINDING,
                           headers={"Authorization": "Bearer nope"})
        assert resp.status_code == 401

    def test_the_mcp_kill_switch_closes_this_too(self, client, monkeypatch):
        """One switch turns off everything the vendor can reach."""
        monkeypatch.setenv("MCP_ENABLED", "false")
        assert client.post("/api/agent/findings", json=A_FINDING,
                           headers=AUTH).status_code == 404

    def test_the_single_shared_token_also_works(self, client, monkeypatch):
        """MCP_BEARER_TOKEN is the one-token setup; it posts as "default"."""
        monkeypatch.delenv("MCP_TOKENS", raising=False)
        monkeypatch.setenv("MCP_BEARER_TOKEN", "tok-single")
        resp = client.post("/api/agent/findings",
                           json=dict(A_FINDING, property=COMPANY),
                           headers={"Authorization": "Bearer tok-single"})
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "accepted"

    def test_the_finding_is_attributed_to_the_token_that_posted_it(self, client,
                                                                   stub_world):
        client.post("/api/agent/findings", json=dict(A_FINDING, property=NAME),
                    headers=AUTH)
        assert stub_world[0]["payload"]["posted_by"] == "ninjacat"


# --- what it refuses -------------------------------------------------------

class TestRefusals:
    def test_an_action_with_no_receipts_is_refused(self):
        with pytest.raises(FindingRejected) as exc:
            _norm(receipts=[])
        assert "receipt" in str(exc.value).lower()

    def test_a_receipt_with_no_source_is_not_a_receipt(self):
        with pytest.raises(FindingRejected):
            _norm(receipts=[{"label": "Impression share", "value": "41%"}])

    def test_a_finding_with_no_action_needs_no_receipts(self):
        """An observation is allowed to be just an observation."""
        reco = _norm(receipts=[], action={"kind": "none"})
        assert reco["action"]["kind"] == "none"

    def test_missing_rule_key_is_refused(self):
        with pytest.raises(FindingRejected) as exc:
            af.normalize({"found": "something"}, company_id=COMPANY,
                         property_name=NAME, posted_by="ninjacat")
        assert "rule_key" in str(exc.value)

    def test_missing_found_is_refused(self):
        with pytest.raises(FindingRejected) as exc:
            af.normalize({"rule_key": "x"}, company_id=COMPANY,
                         property_name=NAME, posted_by="ninjacat")
        assert "found" in str(exc.value)

    def test_geographic_targeting_is_refused_whoever_asked(self):
        """Housing is a Special Ad Category. This is a compliance failure, not a
        preference, so it is refused rather than queued for someone to judge."""
        with pytest.raises(FindingRejected) as exc:
            _norm(action={"kind": "budget_change",
                          "params": {"radius_miles": 5},
                          "executor": "ninjacat"})
        assert "Refused" in str(exc.value)

    def test_audience_layering_is_refused(self):
        with pytest.raises(FindingRejected):
            _norm(found="Add a lookalike audience to the search campaign.")

    def test_an_agent_cannot_waive_the_signed_deal(self):
        """The agent sends requires_signed_deal=False; the portal overrides it."""
        reco = _norm(action=dict(A_FINDING["action"], requires_signed_deal=False))
        assert reco["action"]["requires_signed_deal"] is True

    def test_a_non_spend_action_does_not_demand_a_signed_deal(self):
        reco = _norm(action={"kind": "tracking_fix", "params": {},
                             "executor": "portal"})
        assert reco["action"]["requires_signed_deal"] is False

    def test_oversized_params_are_refused(self):
        with pytest.raises(FindingRejected):
            _norm(action=dict(A_FINDING["action"],
                              params={"blob": "x" * (af.MAX_PARAMS_BYTES + 100)}))

    def test_an_unknown_action_kind_is_refused(self):
        with pytest.raises(FindingRejected) as exc:
            _norm(action={"kind": "delete_account", "params": {},
                          "executor": "ninjacat"})
        assert "not usable" in str(exc.value)

    def test_long_text_is_clipped_not_refused(self):
        reco = _norm(found="A" * (af.MAX_TEXT + 500))
        assert len(reco["found"]) == af.MAX_TEXT

    def test_confidence_is_clamped_into_range(self):
        assert _norm(confidence=99)["confidence"] == 10
        assert _norm(confidence=-4)["confidence"] == 1
        assert _norm(confidence="not a number")["confidence"] == 6


class TestFairHousing:
    def test_high_severity_copy_is_refused_before_storage(self, monkeypatch):
        """Checked at the door, not at publish time: the prose is stored, read by
        staff and can reach a channel, so storing it at all is the exposure."""
        monkeypatch.setattr(skills.workspace_common, "fair_housing_review",
                            lambda *t: {"severity": "high", "terms": ["families"],
                                        "classes": ["familial_status"]})
        with pytest.raises(FindingRejected) as exc:
            _norm()
        assert "Fair Housing" in str(exc.value)
        assert "families" in str(exc.value)

    def test_low_severity_is_kept_but_flagged_for_a_person(self, monkeypatch):
        """Protected-class vocabulary alone is often innocent ("a single page"),
        so the finding survives and a human looks at it."""
        monkeypatch.setattr(skills.workspace_common, "fair_housing_review",
                            lambda *t: {"severity": "low", "terms": ["single"],
                                        "classes": ["familial_status"]})
        reco = _norm()
        assert reco["action"]["fair_housing_review"] is True

    def test_a_checker_outage_is_retryable_not_a_rejection(self, monkeypatch):
        """The helper fails closed by reporting high severity, so nothing is
        stored — but the copy was never judged, so this is OUR outage. Telling
        the agent "rejected" would make it stop posting a finding that was
        never wrong."""
        monkeypatch.setattr(
            skills.workspace_common, "fair_housing_review",
            lambda *t: {"severity": "high",
                        "terms": ["fair_housing_check_unavailable"]})
        with pytest.raises(FindingUnavailable) as exc:
            _norm()
        assert "could not run" in str(exc.value)
        assert not isinstance(exc.value, FindingRejected)

    def test_copy_actions_always_get_a_human_review(self):
        reco = _norm(action={"kind": "creative_refresh",
                             "params": {"headline": "New homes available now"},
                             "executor": "ninjacat"},
                     receipts=A_FINDING["receipts"])
        assert reco["action"]["fair_housing_review"] is True

    def test_params_are_checked_not_just_prose(self, monkeypatch):
        """The copy an agent proposes lives in params, so it must be in the
        haystack — checking only `found` would let ad text straight through."""
        seen = []
        monkeypatch.setattr(skills.workspace_common, "fair_housing_review",
                            lambda *t: seen.append(t) or None)
        _norm(action={"kind": "creative_refresh",
                      "params": {"headline": "Perfect for young professionals"},
                      "executor": "ninjacat"})
        assert any("young professionals" in str(t) for t in seen[0])


# --- identity of a finding -------------------------------------------------

class TestFingerprint:
    def test_the_same_finding_twice_is_one_card(self):
        a = af.fingerprint(COMPANY, "search_budget_capped", "Lost 41% to budget.")
        b = af.fingerprint(COMPANY, "search_budget_capped", "lost 41% to budget. ")
        assert a == b

    def test_a_different_property_is_a_different_finding(self):
        assert af.fingerprint(COMPANY, "r", "f") != af.fingerprint("999", "r", "f")

    def test_an_explicit_idempotency_key_wins(self):
        assert af.fingerprint(COMPANY, "r", "f", "key-1") == \
            af.fingerprint("other", "other", "other", "key-1")

    def test_numbers_drifting_overnight_do_not_create_a_second_card(self):
        """Deliberate: the receipts change nightly, the finding does not."""
        one = _norm(receipts=[dict(A_FINDING["receipts"][0], value="41%")])
        two = _norm(receipts=[dict(A_FINDING["receipts"][0], value="42%")])
        assert af.fingerprint(COMPANY, one["rule_key"], one["found"]) == \
            af.fingerprint(COMPANY, two["rule_key"], two["found"])


class TestStorage:
    def test_a_stored_finding_carries_its_fingerprint_and_uuid(self, stub_world):
        out = af.store(_norm(), property_uuid=UUID)
        assert out["fingerprint"] and out["event_id"] == "evt-1"
        written = stub_world[0]
        assert written["property_uuid"] == UUID
        assert written["event_type"] == af.EVENT_TYPE
        assert written["stage"] == af.LOOP_STAGE
        assert written["payload"]["fingerprint"] == out["fingerprint"]

    def test_a_failed_write_is_never_reported_as_accepted(self, monkeypatch):
        """An agent told "accepted" for a dropped finding keeps reporting it
        while nobody ever sees it. And a warehouse outage is retryable, not a
        rejection of the finding itself."""
        import loop_writer
        monkeypatch.setattr(loop_writer, "record",
                            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("bq down")))
        with pytest.raises(FindingUnavailable) as exc:
            af.store(_norm(), property_uuid=UUID)
        assert "not accepted" in str(exc.value)

    def test_the_event_type_is_registered(self):
        """An unregistered type still writes, but nothing downstream reads it."""
        import loop_writer
        assert af.EVENT_TYPE in loop_writer.LOOP_EVENT_TYPES
        assert af.LOOP_STAGE in loop_writer.LOOP_STAGES


# --- reading it back -------------------------------------------------------

def _event(fingerprint, *, when="2026-09-17", rule="search_budget_capped",
           posted_by="ninjacat"):
    return {"occurred_at": when + "T12:00:00Z",
            "payload": {"fingerprint": fingerprint, "rule_key": rule,
                        "found": "x", "posted_by": posted_by}}


class TestReadback:
    def test_an_unreadable_warehouse_is_a_gap_not_an_empty_list(self, monkeypatch):
        """The difference matters: "nothing posted" is a finding of its own."""
        history = type("H", (), {"property_events": staticmethod(lambda ids, **kw: None)})
        monkeypatch.setattr(skills, "workspace_history", history, raising=False)
        monkeypatch.setitem(sys.modules, "skills.workspace_history", history)
        found, gaps = af.recent(COMPANY, uuid=UUID)
        assert found == []
        assert gaps and gaps[0]["field"] == "agent_findings"

    def test_duplicates_collapse_to_one(self, monkeypatch):
        events = [_event("fp1"), _event("fp1", when="2026-09-16"), _event("fp2")]
        history = type("H", (), {"property_events":
                                 staticmethod(lambda ids, **kw: {UUID: events})})
        monkeypatch.setattr(skills, "workspace_history", history, raising=False)
        monkeypatch.setitem(sys.modules, "skills.workspace_history", history)
        found, gaps = af.recent(COMPANY, uuid=UUID,
                                today=__import__("datetime").date(2026, 9, 18))
        assert [f["fingerprint"] for f in found] == ["fp1", "fp2"]
        assert gaps == []

    def test_findings_past_the_ttl_stop_being_offered(self, monkeypatch):
        """An agent that stopped reporting something has withdrawn it."""
        import datetime
        events = [_event("fresh", when="2026-09-17"),
                  _event("stale", when="2026-06-01")]
        history = type("H", (), {"property_events":
                                 staticmethod(lambda ids, **kw: {UUID: events})})
        monkeypatch.setattr(skills, "workspace_history", history, raising=False)
        monkeypatch.setitem(sys.modules, "skills.workspace_history", history)
        found, _ = af.recent(COMPANY, uuid=UUID, today=datetime.date(2026, 9, 18))
        assert [f["fingerprint"] for f in found] == ["fresh"]

    def test_only_this_event_type_is_asked_for(self, monkeypatch):
        asked = {}
        history = type("H", (), {"property_events":
                                 staticmethod(lambda ids, **kw: asked.update(
                                     ids=ids, **kw) or {UUID: []})})
        monkeypatch.setattr(skills, "workspace_history", history, raising=False)
        monkeypatch.setitem(sys.modules, "skills.workspace_history", history)
        af.recent(COMPANY, uuid=UUID)
        assert asked["ids"] == [UUID]
        assert asked["types"] == (af.EVENT_TYPE,)


class TestProducer:
    def test_it_is_registered_so_findings_rank_in_the_same_queue(self):
        from skills import reco_engine
        assert "agent_findings" in reco_engine.PRODUCERS

    def test_run_resolves_the_uuid_because_events_are_uuid_keyed(self, monkeypatch):
        """Reading by company_id would return an empty list — a posted finding
        silently missing from the queue."""
        asked = {}
        history = type("H", (), {"property_events":
                                 staticmethod(lambda ids, **kw: asked.update(ids=ids)
                                              or {UUID: [_event("fp1")]})})
        monkeypatch.setattr(skills, "workspace_history", history, raising=False)
        monkeypatch.setitem(sys.modules, "skills.workspace_history", history)
        out = af.run(COMPANY, today=__import__("datetime").date(2026, 9, 18))
        assert asked["ids"] == [UUID]
        assert out["rules_run"] == ["search_budget_capped"]

    def test_an_unresolvable_property_is_a_gap_not_a_crash(self, monkeypatch):
        """One dead producer must not take down the whole queue."""
        resolver = type("R", (), {"resolve": staticmethod(
            lambda ident, **kw: (_ for _ in ()).throw(ValueError("no such property")))})
        monkeypatch.setattr(skills, "property_resolver", resolver, raising=False)
        monkeypatch.setitem(sys.modules, "skills.property_resolver", resolver)
        out = af.run("nope")
        assert out["recommendations"] == []
        assert out["gaps"][0]["source"] == "property_resolver"

    def test_a_property_with_no_uuid_says_so(self, monkeypatch):
        class NoUuid(_Identity):
            def to_dict(self):
                return {"uuid": None, "company_id": COMPANY, "name": NAME}
        resolver = type("R", (), {"resolve": staticmethod(lambda ident, **kw: NoUuid())})
        monkeypatch.setattr(skills, "property_resolver", resolver, raising=False)
        monkeypatch.setitem(sys.modules, "skills.property_resolver", resolver)
        out = af.run(COMPANY)
        assert "no uuid" in out["gaps"][0]["message"]


# --- the route -------------------------------------------------------------

class TestRoute:
    def test_a_good_finding_is_accepted_and_says_what_happens_next(self, client):
        resp = client.post("/api/agent/findings",
                           json=dict(A_FINDING, property=NAME), headers=AUTH)
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "accepted"
        assert body["property"] == NAME
        assert body["needs_signed_deal"] is True
        assert "Nothing runs until a person approves" in body["next"]

    def test_a_refused_finding_gets_a_status_code_and_a_reason(self, client):
        resp = client.post("/api/agent/findings",
                           json=dict(A_FINDING, property=NAME, receipts=[]),
                           headers=AUTH)
        assert resp.status_code == 422
        assert "receipt" in resp.get_json()["reason"].lower()

    def test_a_missing_property_is_named_as_the_problem(self, client):
        resp = client.post("/api/agent/findings", json=dict(A_FINDING), headers=AUTH)
        assert resp.status_code == 422
        assert "property is required" in resp.get_json()["reason"]

    def test_an_unknown_property_is_the_agents_error_to_fix(self, client, monkeypatch):
        resolver = type("R", (), {"resolve": staticmethod(
            lambda ident, **kw: (_ for _ in ()).throw(LookupError("nope")))})
        monkeypatch.setattr(skills, "property_resolver", resolver, raising=False)
        monkeypatch.setitem(sys.modules, "skills.property_resolver", resolver)
        resp = client.post("/api/agent/findings",
                           json=dict(A_FINDING, property="Nowhere"), headers=AUTH)
        assert resp.status_code == 422
        assert "No single property matches" in resp.get_json()["reason"]

    def test_our_outage_is_a_503_the_agent_should_retry(self, client, monkeypatch):
        """Regression: a HubSpot 401 used to come back as 422 "rejected" with the
        raw error echoed. That tells the agent to stop retrying something that
        was never wrong, and leaks our internal endpoints."""
        resolver = type("R", (), {"resolve": staticmethod(
            lambda ident, **kw: (_ for _ in ()).throw(
                RuntimeError("401 after token reload: POST https://api.hubapi.com/x")))})
        monkeypatch.setattr(skills, "property_resolver", resolver, raising=False)
        monkeypatch.setitem(sys.modules, "skills.property_resolver", resolver)
        resp = client.post("/api/agent/findings",
                           json=dict(A_FINDING, property=NAME), headers=AUTH)
        assert resp.status_code == 503
        body = resp.get_json()
        assert body["status"] == "unavailable" and body["retry"] is True
        assert "hubapi" not in resp.get_data(as_text=True)
        assert "401" not in resp.get_data(as_text=True)

    def test_a_warehouse_outage_is_a_503_not_a_rejection(self, client, monkeypatch):
        import loop_writer
        monkeypatch.setattr(loop_writer, "record",
                            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("bq down")))
        resp = client.post("/api/agent/findings",
                           json=dict(A_FINDING, property=NAME), headers=AUTH)
        assert resp.status_code == 503
        assert resp.get_json()["retry"] is True
        assert "bq down" not in resp.get_data(as_text=True)

    def test_a_batch_counts_outages_apart_from_rejections(self, client, monkeypatch):
        """A caller that retries a whole batch on any failure would re-post the
        genuinely bad findings forever, so the two are counted separately."""
        real = af.store
        calls = {"n": 0}

        def flaky(reco, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise FindingUnavailable("Retry shortly.")
            return real(reco, **kw)

        monkeypatch.setattr(af, "store", flaky)
        resp = client.post("/api/agent/findings", json={
            "property": NAME,
            "findings": [A_FINDING,
                         dict(A_FINDING, rule_key="two"),
                         dict(A_FINDING, rule_key="three", receipts=[])],
        }, headers=AUTH)
        body = resp.get_json()
        assert (body["accepted"], body["rejected"], body["unavailable"]) == (1, 1, 1)
        assert body["retry_unavailable"] is True

    def test_readback_during_an_outage_is_a_503(self, client, monkeypatch):
        resolver = type("R", (), {"resolve": staticmethod(
            lambda ident, **kw: (_ for _ in ()).throw(RuntimeError("hubspot down")))})
        monkeypatch.setattr(skills, "property_resolver", resolver, raising=False)
        monkeypatch.setitem(sys.modules, "skills.property_resolver", resolver)
        resp = client.get("/api/agent/findings?property=x", headers=AUTH)
        assert resp.status_code == 503
        assert "hubspot down" not in resp.get_data(as_text=True)

    def test_a_batch_reports_each_outcome_separately(self, client):
        resp = client.post("/api/agent/findings", json={
            "property": NAME,
            "findings": [A_FINDING, dict(A_FINDING, rule_key="other", receipts=[])],
        }, headers=AUTH)
        assert resp.status_code == 200
        body = resp.get_json()
        assert (body["accepted"], body["rejected"]) == (1, 1)
        assert body["results"][0]["status"] == "accepted"
        assert body["results"][1]["status"] == "rejected"

    def test_one_bad_finding_does_not_lose_the_good_ones(self, client, stub_world):
        client.post("/api/agent/findings", json={
            "property": NAME,
            "findings": ["not an object", A_FINDING],
        }, headers=AUTH)
        assert len(stub_world) == 1

    def test_an_oversized_batch_is_refused_whole(self, client, stub_world):
        resp = client.post("/api/agent/findings", json={
            "property": NAME,
            "findings": [A_FINDING] * (af.__dict__.get("MAX_BATCH", 25) + 30),
        }, headers=AUTH)
        assert resp.status_code == 413
        assert stub_world == []

    def test_non_json_is_refused(self, client):
        resp = client.post("/api/agent/findings", data="not json", headers=AUTH)
        assert resp.status_code == 400

    def test_the_idempotency_header_reaches_the_fingerprint(self, client, stub_world):
        client.post("/api/agent/findings", json=dict(A_FINDING, property=NAME),
                    headers=dict(AUTH, **{"Idempotency-Key": "run-2026-09-18"}))
        assert stub_world[0]["payload"]["fingerprint"] == \
            af.fingerprint(COMPANY, "x", "y", "run-2026-09-18")

    def test_readback_returns_what_the_queue_will_show(self, client, monkeypatch):
        events = [_event("fp1"), _event("fp2", posted_by="someone-else")]
        history = type("H", (), {"property_events":
                                 staticmethod(lambda ids, **kw: {UUID: events})})
        monkeypatch.setattr(skills, "workspace_history", history, raising=False)
        monkeypatch.setitem(sys.modules, "skills.workspace_history", history)
        resp = client.get("/api/agent/findings?property=%s" % COMPANY, headers=AUTH)
        assert resp.status_code == 200
        body = resp.get_json()
        # Scoped to the caller: one token's agent does not read another's.
        assert body["count"] == 1
        assert body["findings"][0]["fingerprint"] == "fp1"

    def test_readback_needs_a_property(self, client):
        assert client.get("/api/agent/findings", headers=AUTH).status_code == 400


def test_the_blueprint_is_registered_on_the_real_app():
    """Every test above is worthless if nothing wires the route up."""
    import server
    rules = {str(r.rule) for r in server.app.url_map.iter_rules()}
    assert "/api/agent/findings" in rules


def test_the_route_is_not_behind_the_workspace_identity_gate():
    """It authenticates with a machine token and has no portal user, so it must
    not sit under the prefix that demands a verified human session."""
    from _route_utils import workspace_proof_required
    assert workspace_proof_required("/api/agent/findings") is False
