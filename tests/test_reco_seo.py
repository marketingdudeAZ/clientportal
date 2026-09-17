"""Tests for webhook-server/skills/reco_seo.py.

Every rule gets the same two questions: does it fire on input shaped like the
real thing, and does it stay silent when the measurement is missing. The
silence half is the important one — a rule that invents a finding from an
empty source is worse than a rule that never runs.

The last class is the one that must never be deleted: no rule may emit
targeting language or protected-class language, whatever the input.
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

from skills import reco_engine  # noqa: E402
from skills import reco_seo  # noqa: E402

TODAY = date(2026, 9, 17)


def base(**extra):
    """The minimum context every rule reads, plus whatever the case needs."""
    data = {"company_id": "1001", "property_name": "Muse at Winter Garden",
            "as_of": "2026-09-17", "_today": TODAY}
    data.update(extra)
    return data


def page(url, **extra):
    row = {"url": url, "title": None, "meta_description": None, "h1": None}
    row.update(extra)
    return row


class ContractTests(unittest.TestCase):
    """Whatever a rule emits has to survive the ranking engine."""

    def assert_contract(self, recos):
        self.assertTrue(recos, "expected at least one recommendation")
        for reco in recos:
            self.assertIsNone(reco_engine.validate(reco),
                              "validate rejected %s: %s" % (reco.get("rule_key"),
                                                            reco_engine.validate(reco)))
            self.assertIsNone(reco_engine.compliance_refusal(reco),
                              "refused %s" % reco.get("rule_key"))
            self.assertEqual(reco["id"],
                             "%s:%s:2026-09-17" % (reco["rule_key"], reco["company_id"]))
            for receipt in reco["receipts"]:
                self.assertEqual(sorted(receipt), ["as_of", "label", "source", "value"])
            self.assertIn("metric", reco["verify"])
            self.assertIn("when", reco["verify"])


class MissingFloorPlanPageTests(ContractTests):
    PLANS = [{"name": "A1", "beds": 1, "baths": 1.0, "sqft": 712, "available": 4},
             {"name": "B2", "beds": 2, "baths": 2.0, "sqft": 1080, "available": 0}]
    PAGES = [page("https://example.com/"), page("https://example.com/floorplans/b2")]

    def test_fires_on_the_plan_with_no_page(self):
        data = base(floor_plans=self.PLANS, pages=self.PAGES,
                    floor_plans_as_of="2026-09-16")
        out = reco_seo.rule_missing_floor_plan_page(data)
        self.assert_contract(out)
        self.assertEqual(len(out), 1)
        names = [p["floor_plan"] for p in out[0]["action"]["params"]["pages"]]
        self.assertEqual(names, ["A1"])
        self.assertEqual(out[0]["severity"], "high")          # 4 homes open
        self.assertTrue(out[0]["action"]["fair_housing_review"])

    def test_silent_when_every_plan_has_a_page(self):
        pages = self.PAGES + [page("https://example.com/floorplans/a1")]
        self.assertEqual(
            reco_seo.rule_missing_floor_plan_page(base(floor_plans=self.PLANS, pages=pages)),
            [])

    def test_silent_without_a_read_of_the_site(self):
        self.assertEqual(
            reco_seo.rule_missing_floor_plan_page(base(floor_plans=self.PLANS, pages=[])), [])

    def test_silent_without_floor_plans(self):
        self.assertEqual(
            reco_seo.rule_missing_floor_plan_page(base(floor_plans=[], pages=self.PAGES)), [])


class AvailabilityMarkupTests(ContractTests):
    PAGES = [page("https://example.com/availability", jsonld_types=["WebPage"]),
             page("https://example.com/floorplans", jsonld_types=[])]

    def test_fires_when_nothing_carries_readable_markup(self):
        data = base(pages=self.PAGES,
                    availability={"available_units": 22, "asking_rent": 1895.0,
                                  "source": "aptiq_api", "as_of": "2026-09-16"})
        out = reco_seo.rule_availability_not_machine_readable(data)
        self.assert_contract(out)
        self.assertEqual(out[0]["action"]["kind"], "schema")
        labels = [r["label"] for r in out[0]["receipts"]]
        self.assertIn("Homes open now", labels)
        self.assertIn("Asking rent recorded", labels)

    def test_silent_when_the_markup_is_already_there(self):
        pages = [page("https://example.com/floorplans", jsonld_types=["Apartment", "Offer"])]
        data = base(pages=pages, availability={"available_units": 22, "source": "aptiq_api"})
        self.assertEqual(reco_seo.rule_availability_not_machine_readable(data), [])

    def test_silent_when_the_crawl_never_looked_for_markup(self):
        pages = [page("https://example.com/floorplans")]          # no jsonld_types key
        data = base(pages=pages, availability={"available_units": 22, "source": "aptiq_api"})
        self.assertEqual(reco_seo.rule_availability_not_machine_readable(data), [])

    def test_silent_without_a_measured_availability_figure(self):
        data = base(pages=self.PAGES, availability={"source": "aptiq_api"})
        self.assertEqual(reco_seo.rule_availability_not_machine_readable(data), [])

    def test_never_publishes_a_price_it_did_not_measure(self):
        data = base(pages=self.PAGES,
                    availability={"available_units": 5, "source": "aptiq_daily_csv"})
        out = reco_seo.rule_availability_not_machine_readable(data)
        self.assertNotIn("price", out[0]["action"]["params"]["fields"])


class RunTests(unittest.TestCase):
    def test_reports_the_rules_it_could_not_run(self):
        out = reco_seo.run("1001", today=TODAY, data=base())
        self.assertEqual(out["company_id"], "1001")
        self.assertEqual(out["recommendations"], [])
        self.assertEqual(out["rules_run"], [])
        skipped = {s["rule_key"]: s for s in out["rules_skipped"]}
        self.assertEqual(set(skipped), set(reco_seo.RULES))
        for entry in skipped.values():
            self.assertTrue(entry["missing"])
            self.assertTrue(entry["reason"])

    def test_runs_only_the_rules_asked_for(self):
        data = base(floor_plans=MissingFloorPlanPageTests.PLANS,
                    pages=MissingFloorPlanPageTests.PAGES)
        out = reco_seo.run("1001", today=TODAY, data=data,
                           rules=["seo_missing_floor_plan_page"])
        self.assertEqual(out["rules_run"], ["seo_missing_floor_plan_page"])
        self.assertEqual(len(out["recommendations"]), 1)

    def test_a_rule_that_raises_is_reported_not_propagated(self):
        broken = dict(reco_seo.RULES)
        key = "seo_missing_floor_plan_page"
        original = broken[key]

        def explode(_data):
            raise RuntimeError("boom")

        reco_seo.RULES[key] = explode
        try:
            out = reco_seo.run("1001", today=TODAY,
                               data=base(floor_plans=[{"name": "A1"}],
                                         pages=[page("https://example.com/")]),
                               rules=[key])
        finally:
            reco_seo.RULES[key] = original
        self.assertEqual(out["recommendations"], [])
        self.assertEqual(out["rules_skipped"][0]["rule_key"], key)

    def test_every_rule_is_registered_with_its_requirements(self):
        self.assertEqual(set(reco_seo.RULES), set(reco_seo.REQUIREMENTS))
        for key, spec in reco_seo.REQUIREMENTS.items():
            self.assertTrue(spec["needs"], key)
            self.assertTrue(spec["why"], key)


class NoTargetingOrProtectedClassTests(unittest.TestCase):
    """The rule of the house, asserted against every rule's real output.

    Housing is a Special Ad Category: a recommendation that steers by
    geography or by a grouping of people is a compliance failure, not a
    ranking question. This walks everything the module can emit.
    """

    def test_nothing_emits_targeting_language(self):
        for reco in _every_recommendation():
            self.assertIsNone(reco_engine.compliance_refusal(reco),
                              "%s emits targeting language" % reco["rule_key"])

    def test_nothing_emits_protected_class_language(self):
        from skills import workspace_common as wc
        for reco in _every_recommendation():
            texts = [reco["found"], reco["expect"], reco["if_skip"],
                     reco["verify"]["metric"]]
            texts += reco_seo._copy_params(reco["action"])
            texts += [r["label"] for r in reco["receipts"]]
            review = wc.fair_housing_review(*texts)
            self.assertIsNone(review, "%s: %s" % (reco["rule_key"], review))

    def test_copy_producing_actions_all_route_to_review(self):
        copy_kinds = {"content_brief", "page_fix", "listing_change"}
        for reco in _every_recommendation():
            if reco["action"]["kind"] in copy_kinds:
                self.assertTrue(reco["action"]["fair_housing_review"],
                                "%s produces copy without review" % reco["rule_key"])

    def test_a_rule_cannot_smuggle_targeting_through_params(self):
        """The emitter drops it even if a future rule builds one."""
        out = reco_seo._emit(
            "seo_test_only", base(), category="content", channels=["website"],
            severity="low", confidence=5, found="A page is missing.",
            receipts=[reco_seo._receipt("Pages read", 1, "site_crawl", "2026-09-17")],
            expect="A page exists.", if_skip="It does not.",
            action={"kind": "page_fix", "executor": "human", "requires_signed_deal": False,
                    "fair_housing_review": True,
                    "params": {"note": "tighten the radius around the property"}},
            verify_metric="pages live", verify_days=30)
        self.assertEqual(out, [])


def _every_recommendation():
    """Run every rule against input built to make it fire."""
    out = []
    for data in FIRING_CASES:
        out.extend(reco_seo.run("1001", today=TODAY, data=data)["recommendations"])
    return out


FIRING_CASES = [
    base(floor_plans=MissingFloorPlanPageTests.PLANS,
         pages=MissingFloorPlanPageTests.PAGES),
    base(pages=AvailabilityMarkupTests.PAGES,
         availability={"available_units": 22, "asking_rent": 1895.0,
                       "source": "aptiq_api", "as_of": "2026-09-16"}),
]


if __name__ == "__main__":
    unittest.main()
