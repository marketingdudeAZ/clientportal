"""The queue's own rules: what surfaces, what is held, what is refused outright.

The engine is the thing standing between a thousand machine findings and a
person's Tuesday. These tests pin the two promises that matter: a compliance
failure never reaches a human, and the queue stays short enough to read.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "webhook-server"))
sys.path.insert(0, str(ROOT))

from skills import reco_engine as engine  # noqa: E402

TODAY = date(2026, 9, 17)


def reco(**over):
    base = {
        "id": "waste_spend:555:2026-09-17",
        "company_id": "555",
        "property_name": "Test Property",
        "rule_key": "waste_spend",
        "category": "cost",
        "channels": ["paid_search"],
        "severity": "high",
        "confidence": 8,
        "found": "Seven search terms spent $1,240 last month with no leads.",
        "receipts": [{"label": "spend with no leads", "value": 1240,
                      "source": "google_ads", "as_of": "2026-09-16"}],
        "expect": "About $1,240 a month moves to terms that convert.",
        "if_skip": "The spend continues against terms that have never produced a lead.",
        "action": {"kind": "pause", "params": {"terms": 7}, "executor": "ninjacat",
                   "requires_signed_deal": False, "fair_housing_review": False},
        "start_by": "2026-09-24",
        "verify": {"metric": "cost per lead", "when": "2026-10-24"},
    }
    base.update(over)
    return base


class TestValidation:
    def test_a_good_recommendation_passes(self):
        assert engine.validate(reco()) is None

    @pytest.mark.parametrize("field", ["id", "company_id", "rule_key", "category",
                                       "severity", "confidence", "found", "action"])
    def test_every_required_field_is_required(self, field):
        assert engine.validate(reco(**{field: None})) is not None

    def test_an_action_without_receipts_is_dropped(self):
        """A card that asks someone to change live spend and shows no numbers is
        worse than no card."""
        assert "receipts" in engine.validate(reco(receipts=[]))

    def test_a_receipt_without_a_source_is_dropped(self):
        bad = reco(receipts=[{"label": "spend", "value": 1240}])
        assert "source" in engine.validate(bad)

    def test_unknown_vocabulary_is_rejected(self):
        assert engine.validate(reco(category="whatever")) is not None
        assert engine.validate(reco(severity="urgent")) is not None
        assert engine.validate(reco(action=dict(reco()["action"], kind="delete_all"))) is not None

    @pytest.mark.parametrize("value", [0, 11, "high", None])
    def test_confidence_must_be_one_to_ten(self, value):
        assert engine.validate(reco(confidence=value)) is not None

    def test_a_none_action_may_have_no_receipts(self):
        """An observation with nothing to do is allowed to be just an observation."""
        quiet = reco(action={"kind": "none", "params": {}, "executor": "portal",
                             "requires_signed_deal": False,
                             "fair_housing_review": False}, receipts=[])
        assert engine.validate(quiet) is None


class TestCompliance:
    """Housing is a Special Ad Category. These are refusals, not rankings."""

    @pytest.mark.parametrize("params", [
        {"radius_miles": 5},
        {"zip_codes": ["85224"]},
        {"audience": "lookalike"},
        {"exclude_audience": "past visitors"},
        {"retargeting": True},
        {"demographic": "25-34"},
    ])
    def test_any_targeting_change_is_refused(self, params):
        bad = reco(action=dict(reco()["action"], kind="new_ad_group", params=params,
                               requires_signed_deal=True))
        assert engine.compliance_refusal(bad) is not None

    def test_targeting_language_in_the_finding_is_refused_too(self):
        """A producer can smuggle it in prose as easily as in params."""
        bad = reco(found="Tighten the radius to 3 miles around the property.")
        assert engine.compliance_refusal(bad) is not None

    def test_spend_increase_without_a_signature_requirement_is_refused(self):
        bad = reco(action={"kind": "budget_change", "params": {"to": 5000},
                           "executor": "ninjacat", "requires_signed_deal": False,
                           "fair_housing_review": False})
        assert "signed deal" in engine.compliance_refusal(bad)

    def test_a_budget_change_that_requires_a_signature_is_allowed(self):
        ok = reco(action={"kind": "budget_change", "params": {"to": 5000},
                          "executor": "ninjacat", "requires_signed_deal": True,
                          "fair_housing_review": False})
        assert engine.compliance_refusal(ok) is None

    def test_refused_items_never_reach_the_surfaced_list(self):
        out = engine.rank_and_suppress(
            [reco(id="a", rule_key="r1", action=dict(reco()["action"],
                                                     params={"radius_miles": 3})),
             reco(id="b", rule_key="r2")], today=TODAY)
        assert [r["id"] for r in out["surfaced"]] == ["b"]
        assert out["counts"]["refused"] == 1
        assert out["refused"][0]["rule_key"] == "r1"


class TestRanking:
    def test_severity_and_confidence_both_move_the_score(self):
        high = engine.score(reco(severity="high", confidence=9))
        medium = engine.score(reco(severity="medium", confidence=9))
        unsure = engine.score(reco(severity="high", confidence=3))
        assert high > medium > 0 and high > unsure

    def test_more_rent_at_risk_outranks_the_same_finding_elsewhere(self):
        big = engine.score(reco(), {"units": 300, "exposure": 0.18})
        small = engine.score(reco(), {"units": 80, "exposure": 0.02})
        assert big > small

    def test_an_unmeasured_property_is_never_boosted_above_a_measured_one(self):
        """No availability data must not become an advantage in the ranking."""
        unknown = engine.exposure_weight({"units": None, "exposure": None})
        known = engine.exposure_weight({"units": 300, "exposure": 0.2})
        assert unknown == 1.0 and known > unknown

    def test_junk_numbers_do_not_crash_the_weighting(self):
        assert engine.exposure_weight({"units": "n/a", "exposure": "high"}) == 1.0
        assert engine.exposure_weight(None) == 1.0


class TestSuppression:
    def test_one_card_per_rule_per_property(self):
        out = engine.rank_and_suppress(
            [reco(id="old", confidence=4), reco(id="new", confidence=9)], today=TODAY)
        assert [r["id"] for r in out["surfaced"]] == ["new"]
        assert out["counts"]["deduped"] == 1

    def test_an_approved_rule_stops_asking(self):
        decisions = {"waste_spend:555:2026-09-01": [
            {"at": "2026-09-02T00:00:00Z", "action": "approve"}]}
        out = engine.rank_and_suppress([reco()], decisions=decisions, today=TODAY)
        assert out["surfaced"] == [] and out["counts"]["already_decided"] == 1

    def test_not_now_is_respected_for_its_cool_off_then_returns(self):
        recent = (TODAY - timedelta(days=5)).isoformat()
        stale = (TODAY - timedelta(days=45)).isoformat()
        quiet = {"waste_spend:555:x": [{"at": recent, "action": "not_now"}]}
        expired = {"waste_spend:555:x": [{"at": stale, "action": "not_now"}]}
        assert engine.rank_and_suppress([reco()], decisions=quiet, today=TODAY)["surfaced"] == []
        assert len(engine.rank_and_suppress([reco()], decisions=expired,
                                            today=TODAY)["surfaced"]) == 1

    def test_an_undone_decision_does_not_silence_the_rule(self):
        decisions = {"waste_spend:555:x": [
            {"at": "2026-09-02T00:00:00Z", "action": "approve", "undone": True}]}
        assert len(engine.rank_and_suppress([reco()], decisions=decisions,
                                            today=TODAY)["surfaced"]) == 1

    def test_the_rest_are_quiet_not_deleted(self):
        many = [reco(id=str(i), rule_key="rule_%d" % i, confidence=10 - i)
                for i in range(7)]
        out = engine.rank_and_suppress(many, today=TODAY, max_per_property=3)
        assert len(out["surfaced"]) == 3 and len(out["quiet"]) == 4
        assert out["counts"]["produced"] == 7

    def test_the_strongest_survive_the_cap(self):
        many = [reco(id="weak", rule_key="a", severity="low", confidence=2),
                reco(id="strong", rule_key="b", severity="high", confidence=10)]
        out = engine.rank_and_suppress(many, today=TODAY, max_per_property=1)
        assert out["surfaced"][0]["id"] == "strong"


class TestProducers:
    def test_a_missing_producer_is_a_gap_not_a_crash(self, monkeypatch):
        monkeypatch.setattr(engine, "_producer", lambda name: None)
        out = engine.for_property("555", today=TODAY)
        assert out["surfaced"] == []
        assert {g["source"] for g in out["gaps"]} == set(engine.PRODUCERS)

    def test_a_producer_that_raises_does_not_kill_the_others(self, monkeypatch):
        def fake(name):
            if name == "reco_digital":
                def boom(company_id, today=None):
                    raise RuntimeError("google ads is down")
                return boom
            if name == "reco_seo":
                return lambda company_id, today=None: {
                    "recommendations": [reco(rule_key="seo_thing")], "gaps": [],
                    "rules_run": ["seo_thing"], "rules_skipped": []}
            return lambda company_id, today=None: {
                "recommendations": [], "gaps": [], "rules_run": [],
                "rules_skipped": []}

        monkeypatch.setattr(engine, "_producer", fake)
        out = engine.for_property("555", today=TODAY)
        assert len(out["surfaced"]) == 1
        assert any(g["source"] == "reco_digital" for g in out["gaps"])
        assert out["rules_run"] == ["seo_thing"]

    def test_portfolio_ranks_across_properties_and_holds_the_rest(self, monkeypatch):
        def fake(name):
            if name != "reco_digital":
                return None

            def run(company_id, today=None):
                # The first carries no property_name, so the engine must fill it
                # from the row; the second names itself and must be left alone.
                first = reco(id="%s-a" % company_id, company_id=company_id, rule_key="a")
                first.pop("property_name")
                return {"recommendations": [
                    first,
                    reco(id="%s-b" % company_id, company_id=company_id, rule_key="b",
                         severity="low", confidence=2)], "gaps": []}
            return run

        monkeypatch.setattr(engine, "_producer", fake)
        props = [{"company_id": "big", "name": "Big", "units": 400, "exposure": 0.25},
                 {"company_id": "small", "name": "Small", "units": 60, "exposure": 0.01}]
        out = engine.for_portfolio(props, today=TODAY, max_portfolio=2)
        assert out["property_count"] == 2
        assert out["queue"][0]["company_id"] == "big"       # more rent at risk first
        assert out["held_back"] == 2
        assert out["queue"][0]["property_name"] == "Big"      # filled from the row
        named = [r for r in out["queue"] if r["id"].endswith("-b")]
        assert all(r["property_name"] == "Test Property" for r in named)  # not overwritten
        assert out["totals"]["produced"] == 4

    def test_a_property_with_no_company_id_is_skipped(self, monkeypatch):
        monkeypatch.setattr(engine, "_producer", lambda name: None)
        out = engine.for_portfolio([{"name": "nameless"}], today=TODAY)
        assert out["property_count"] == 0


class TestWorkItemShape:
    def test_it_renders_as_an_approvals_card(self):
        item = engine.to_work_item(reco())
        assert item["id"].startswith("reco:")
        assert item["source"] == "recommendation"
        assert item["found"] and item["expect"] and item["if_skip"]
        assert item["actions"] == {"approve": True, "not_now": True}
        assert item["receipts"]

    def test_an_observation_needs_no_approval(self):
        item = engine.to_work_item(reco(action={"kind": "none", "params": {},
                                                "executor": "portal",
                                                "requires_signed_deal": False,
                                                "fair_housing_review": False}))
        assert item["needs_approval"] is False
        assert item["actions"]["approve"] is False

    def test_copy_producing_actions_carry_the_fair_housing_flag(self):
        item = engine.to_work_item(reco(action={"kind": "content_brief", "params": {},
                                                "executor": "portal",
                                                "requires_signed_deal": False,
                                                "fair_housing_review": True}))
        assert item["fair_housing_review"] is True


class TestSignalsAdapter:
    """The shipped signals feed the same queue instead of living beside it."""

    def _built(self, monkeypatch, signals, gaps=None):
        """`from skills import workspace_signals` reads the package ATTRIBUTE,
        so patching sys.modules alone passes alone and fails in a full run once
        another test has already imported the real module."""
        import types

        import skills
        fake = types.SimpleNamespace(
            build_signals=lambda email, company_id=None, today=None: {
                "signals": signals, "gaps": gaps or []})
        monkeypatch.setattr(skills, "workspace_signals", fake, raising=False)
        monkeypatch.setitem(sys.modules, "skills.workspace_signals", fake)
        return engine.signals_producer("555", today=TODAY)

    def test_a_signal_becomes_a_contract_recommendation(self, monkeypatch):
        out = self._built(monkeypatch, [{
            "id": "stale_inventory:555:2026-09-17", "company_id": "555",
            "property_name": "Test Property", "kind": "stale_inventory",
            "severity": "warning", "title": "12 units past 90 days",
            "detail": "Twelve one-bedroom units have sat 90+ days.",
            "metric": {"value": 12, "source": "aptiq", "as_of": "2026-09-16"},
        }])
        rec = out["recommendations"][0]
        assert engine.validate(rec) is None
        assert rec["rule_key"] == "stale_inventory"
        assert rec["severity"] == "medium"            # "warning" maps across
        assert rec["action"]["kind"] == "new_ad_group"
        assert rec["action"]["requires_signed_deal"] is True
        assert rec["receipts"][0]["source"] == "aptiq"

    def test_a_signal_with_no_metric_becomes_an_observation(self, monkeypatch):
        """No receipt, no action. It can still be seen, it just cannot ask."""
        out = self._built(monkeypatch, [{
            "id": "data_stale:555:x", "kind": "data_stale", "severity": "info",
            "title": "Feed is behind", "detail": "The last report is 6 days old.",
            "metric": None}])
        rec = out["recommendations"][0]
        assert engine.validate(rec) is None
        assert rec["action"]["kind"] == "none" and rec["receipts"] == []

    def test_signal_failures_are_gaps_not_crashes(self, monkeypatch):
        import types

        def boom(email, company_id=None, today=None):
            raise RuntimeError("bigquery is down")

        import skills
        broken = types.SimpleNamespace(build_signals=boom)
        monkeypatch.setattr(skills, "workspace_signals", broken, raising=False)
        monkeypatch.setitem(sys.modules, "skills.workspace_signals", broken)
        out = engine.signals_producer("555", today=TODAY)
        assert out["recommendations"] == []
        assert out["gaps"][0]["source"] == "workspace_signals"

    def test_signals_are_a_registered_producer(self):
        assert "workspace_signals" in engine.PRODUCERS
        assert engine._producer("workspace_signals") is engine.signals_producer


class TestPortfolioFanOut:
    """Every property costs every producer a round of reads. A 109-property
    portfolio behind one page render is hundreds of calls, so a pass evaluates
    the properties with the most rent exposed and says what it skipped."""

    def _engine_with_one_reco(self, monkeypatch):
        seen = []

        def fake(name):
            if name != "reco_digital":
                return None

            def run(company_id, today=None):
                seen.append(company_id)
                return {"recommendations": [reco(id="%s-a" % company_id,
                                                 company_id=company_id,
                                                 rule_key="a")], "gaps": []}
            return run

        monkeypatch.setattr(engine, "_producer", fake)
        return seen

    def _props(self, n):
        # Later properties carry more unleased rent, so the cap must not simply
        # take the first N in the list.
        return [{"company_id": "p%02d" % i, "name": "P%02d" % i,
                 "units": 50 + i * 10, "exposure": 0.01 * i} for i in range(n)]

    def test_only_the_most_exposed_properties_are_evaluated(self, monkeypatch):
        seen = self._engine_with_one_reco(monkeypatch)
        out = engine.for_portfolio(self._props(30), today=TODAY, max_properties=5)
        assert len(seen) == 5
        assert set(seen) == {"p29", "p28", "p27", "p26", "p25"}
        assert out["property_count"] == 5
        assert out["not_evaluated"] == 25

    def test_what_was_skipped_is_reported_not_hidden(self, monkeypatch):
        self._engine_with_one_reco(monkeypatch)
        out = engine.for_portfolio(self._props(30), today=TODAY, max_properties=5)
        assert any("25 properties were not checked" in g["message"]
                   for g in out["gaps"])

    def test_a_nightly_job_can_evaluate_everything(self, monkeypatch):
        seen = self._engine_with_one_reco(monkeypatch)
        out = engine.for_portfolio(self._props(30), today=TODAY, max_properties=None)
        assert len(seen) == 30 and out["not_evaluated"] == 0
        assert not any("not checked" in g["message"] for g in out["gaps"])

    def test_a_small_portfolio_is_never_capped(self, monkeypatch):
        seen = self._engine_with_one_reco(monkeypatch)
        out = engine.for_portfolio(self._props(3), today=TODAY, max_properties=25)
        assert len(seen) == 3 and out["not_evaluated"] == 0

    def test_the_default_cap_is_conservative_enough_for_a_page_render(self):
        assert engine.MAX_PROPERTIES_EVALUATED <= 30
