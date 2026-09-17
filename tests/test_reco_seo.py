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


def question(text, engines, **extra):
    row = {"text": text, "engines": engines}
    row.update(extra)
    return row


class ShareOfAnswerTests(ContractTests):
    QUESTIONS = [
        question("apartments near the waterfront with a rooftop deck",
                 {"ChatGPT": {"named": False, "cited": False},
                  "Perplexity": {"named": False, "cited": False},
                  "AI Overviews": {"named": None, "cited": None}},
                 topic="Amenities", intent="commercial"),
        question("how do I schedule a tour at apartments in the quarter",
                 {"ChatGPT": {"named": True, "cited": False},
                  "Perplexity": {"named": False, "cited": False}},
                 topic="Tour Scheduling", intent="transactional"),
    ]

    def test_fires_and_counts_only_what_was_measured(self):
        data = base(questions=self.QUESTIONS, questions_source="searchable",
                    questions_as_of="2026-09-16",
                    fanout=[{"query": "pet policy at waterfront apartments",
                             "engine": "ChatGPT", "count": 4}])
        out = reco_seo.rule_share_of_answer_gap(data)
        self.assert_contract(out)
        reco = out[0]
        # 3 misses across 4 measured readings — the null AI Overviews reading
        # is not counted as a miss.
        self.assertIn("3 of the 4", reco["found"])
        self.assertEqual(reco["confidence"], 9)
        self.assertEqual(len(reco["action"]["params"]["questions"]), 2)
        self.assertEqual(reco["action"]["params"]["fanout"][0]["fact_required"],
                         "pet_policy")
        self.assertTrue(reco["action"]["params"]["differentiation_gate"])

    def test_citation_only_sources_lower_the_confidence(self):
        rows = [question("apartments near the waterfront",
                         {"ChatGPT": {"named": None, "cited": False},
                          "Perplexity": {"named": None, "cited": False}})]
        out = reco_seo.rule_share_of_answer_gap(
            base(questions=rows, questions_source="ai_mentions"))
        self.assertEqual(out[0]["confidence"], 6)

    def test_silent_when_every_engine_names_the_community(self):
        rows = [question("apartments near the waterfront",
                         {"ChatGPT": {"named": True, "cited": True}})]
        self.assertEqual(reco_seo.rule_share_of_answer_gap(base(questions=rows)), [])

    def test_silent_when_nothing_was_measured(self):
        rows = [question("apartments near the waterfront",
                         {"ChatGPT": {"named": None, "cited": None}})]
        self.assertEqual(reco_seo.rule_share_of_answer_gap(base(questions=rows)), [])

    def test_silent_without_tracked_questions(self):
        self.assertEqual(reco_seo.rule_share_of_answer_gap(base(questions=[])), [])


class StaleFactTests(ContractTests):
    CLAIMS = [{"claim_text": "two pets per home, 50 pound limit",
               "conflicts_brief_field": "pet_policy", "engine": "ChatGPT",
               "source_url": "https://example.com/pets", "as_of": "2026-09-15"}]
    FACTS = {"pet_policy": {"label": "pet policy", "value": "two pets per home, no weight limit",
                            "last_edited": "2026-07-02"}}

    def test_fires_when_an_engine_contradicts_the_brief(self):
        out = reco_seo.rule_answer_quotes_stale_fact(
            base(claims=self.CLAIMS, brief_facts=self.FACTS))
        self.assert_contract(out)
        self.assertEqual(out[0]["severity"], "high")
        self.assertEqual(out[0]["action"]["kind"], "page_fix")
        self.assertEqual(out[0]["action"]["params"]["corrections"][0]["correct"],
                         "two pets per home, no weight limit")

    def test_silent_when_the_engine_has_it_right(self):
        claims = [dict(self.CLAIMS[0],
                       claim_text="Two pets per home, no weight limit.")]
        self.assertEqual(
            reco_seo.rule_answer_quotes_stale_fact(base(claims=claims, brief_facts=self.FACTS)),
            [])

    def test_silent_when_the_brief_has_no_value_to_compare(self):
        self.assertEqual(
            reco_seo.rule_answer_quotes_stale_fact(
                base(claims=self.CLAIMS, brief_facts={"pet_policy": {"value": ""}})),
            [])

    def test_silent_without_claims(self):
        self.assertEqual(
            reco_seo.rule_answer_quotes_stale_fact(base(claims=[], brief_facts=self.FACTS)), [])


class FeeTransparencyTests(ContractTests):
    SIGNALS = [{"quote": "additional fees significantly raise the total monthly cost",
                "engine": "ChatGPT", "as_of": "2026-09-16"}]
    FEES = {"disclosed": False, "page_url": "https://example.com/pricing",
            "known_fees": [{"name": "valet trash", "amount": 35.0, "period": "monthly",
                            "source": "community_brief", "as_of": "2026-08-01"},
                           {"name": "technology package", "amount": 119.5,
                            "period": "monthly", "source": "community_brief"}]}

    def test_fires_and_totals_only_measured_charges(self):
        out = reco_seo.rule_fee_transparency_gap(base(fee_signals=self.SIGNALS, fees=self.FEES))
        self.assert_contract(out)
        self.assertIn("$154.50 a month", out[0]["expect"])
        self.assertEqual(out[0]["category"], "compliance")
        self.assertTrue(out[0]["action"]["fair_housing_review"])

    def test_silent_when_the_page_already_discloses_them(self):
        fees = dict(self.FEES, disclosed=True)
        self.assertEqual(
            reco_seo.rule_fee_transparency_gap(base(fee_signals=self.SIGNALS, fees=fees)), [])

    def test_silent_when_nobody_checked_the_page(self):
        fees = dict(self.FEES, disclosed=None)
        self.assertEqual(
            reco_seo.rule_fee_transparency_gap(base(fee_signals=self.SIGNALS, fees=fees)), [])

    def test_silent_when_no_engine_raised_cost(self):
        self.assertEqual(
            reco_seo.rule_fee_transparency_gap(base(fee_signals=[], fees=self.FEES)), [])

    def test_fires_without_amounts_but_makes_no_claim_about_the_total(self):
        fees = dict(self.FEES, known_fees=[])
        out = reco_seo.rule_fee_transparency_gap(base(fee_signals=self.SIGNALS, fees=fees))
        self.assert_contract(out)
        self.assertNotIn("$", out[0]["expect"])


class AnswerFormatGapTests(ContractTests):
    COVERAGE = [{"topic": "Apartment Amenities", "prompts": 3, "our_mentions": 0,
                 "competitor_mentions": 21,
                 "competitors": [{"name": "Broadstone Waterfront"}, {"name": "Huxley"}]},
                {"topic": "Leasing Information", "prompts": 2, "our_mentions": 4,
                 "competitor_mentions": 9, "competitors": []}]

    def test_fires_only_on_the_topic_we_are_absent_from(self):
        data = base(topic_coverage=self.COVERAGE,
                    cited_formats=[{"format": "ranked lists", "share": 0.6},
                                   {"format": "topic guides", "share": 0.3}],
                    our_formats=["topic guides"])
        out = reco_seo.rule_answer_format_gap(data)
        self.assert_contract(out)
        topics = [t["topic"] for t in out[0]["action"]["params"]["topics"]]
        self.assertEqual(topics, ["Apartment Amenities"])
        formats = [f["format"] for f in out[0]["action"]["params"]["formats_engines_cite"]]
        self.assertEqual(formats, ["ranked lists"])
        self.assertTrue(out[0]["action"]["params"]["differentiation_gate"])

    def test_silent_when_the_community_is_named_everywhere(self):
        coverage = [dict(self.COVERAGE[0], our_mentions=3)]
        self.assertEqual(reco_seo.rule_answer_format_gap(base(topic_coverage=coverage)), [])

    def test_silent_when_nobody_is_named_on_that_topic(self):
        coverage = [dict(self.COVERAGE[0], competitor_mentions=0)]
        self.assertEqual(reco_seo.rule_answer_format_gap(base(topic_coverage=coverage)), [])

    def test_silent_without_topic_coverage(self):
        self.assertEqual(reco_seo.rule_answer_format_gap(base(topic_coverage=[])), [])


TEMPLATE = ("Set in the heart of the neighborhood, the community offers "
            "thoughtfully designed homes with quartz counters, stainless "
            "appliances and generous closets, plus a resort style pool, a "
            "fitness studio open around the clock and a lounge built for "
            "working from home. Come see what elevated living looks like "
            "here, where every detail has been considered for you.")


class DifferentiationGateTests(ContractTests):
    DRAFTS = [{"company_id": "1001", "title": "Amenities at the community",
               "text": TEMPLATE}]
    CORPUS = [{"company_id": "2002", "property_name": "Another Community",
               "title": "Amenities", "text": TEMPLATE.replace("neighborhood", "district")}]

    def test_holds_a_draft_that_matches_another_property(self):
        out = reco_seo.rule_near_duplicate_page(base(drafts=self.DRAFTS, corpus=self.CORPUS))
        self.assert_contract(out)
        self.assertTrue(out[0]["action"]["params"]["publish_blocked"])
        self.assertEqual(out[0]["severity"], "medium")      # 86% overlap
        self.assertEqual(out[0]["action"]["params"]["pairs"][0]["at_property"],
                         "Another Community")

    def test_a_page_published_verbatim_elsewhere_is_high(self):
        corpus = [dict(self.CORPUS[0], text=TEMPLATE)]
        out = reco_seo.rule_near_duplicate_page(base(drafts=self.DRAFTS, corpus=corpus))
        self.assertEqual(out[0]["severity"], "high")

    def test_silent_when_the_draft_is_its_own_page(self):
        drafts = [{"company_id": "1001", "title": "Our two-bedroom homes",
                   "text": ("Our B2 plan is 1,080 square feet with a galley kitchen "
                            "that opens onto the living room, a covered balcony facing "
                            "the courtyard, and a second bathroom off the hall. Four of "
                            "them are open this month, and valet trash is billed at "
                            "thirty five dollars a month on top of rent.")}]
        self.assertEqual(
            reco_seo.rule_near_duplicate_page(base(drafts=drafts, corpus=self.CORPUS)), [])

    def test_ignores_a_draft_too_short_to_judge(self):
        drafts = [{"company_id": "1001", "title": "Short", "text": "Pool and gym."}]
        self.assertEqual(
            reco_seo.rule_near_duplicate_page(base(drafts=drafts, corpus=self.CORPUS)), [])

    def test_silent_without_anything_to_compare_against(self):
        self.assertEqual(
            reco_seo.rule_near_duplicate_page(base(drafts=self.DRAFTS, corpus=[])), [])

    def test_similarity_is_zero_on_an_empty_side(self):
        self.assertEqual(reco_seo.similarity(TEMPLATE, ""), 0.0)


class MoneyPageMetadataTests(ContractTests):
    PAGES = [page("https://example.com/", title="Home", meta_description="", h1="Welcome"),
             page("https://example.com/floorplans", title="", meta_description="Plans", h1=""),
             page("https://example.com/blog/spring", title="Spring", meta_description="x",
                  h1="Spring")]

    def test_fires_only_on_the_pages_a_renter_lands_on(self):
        out = reco_seo.rule_money_page_metadata_gap(base(pages=self.PAGES))
        self.assert_contract(out)
        urls = [p["url"] for p in out[0]["action"]["params"]["pages"]]
        self.assertEqual(sorted(urls), ["https://example.com/",
                                        "https://example.com/floorplans"])

    def test_catches_a_title_shared_between_two_pages(self):
        pages = [page("https://example.com/floorplans", title="Apartments",
                      meta_description="d", h1="h"),
                 page("https://example.com/availability", title="Apartments",
                      meta_description="d", h1="h")]
        out = reco_seo.rule_money_page_metadata_gap(base(pages=pages))
        self.assertIn("a title shared with another page here",
                      out[0]["action"]["params"]["pages"][0]["problems"])

    def test_silent_when_every_landing_page_is_complete(self):
        pages = [page("https://example.com/", title="Home", meta_description="d", h1="h")]
        self.assertEqual(reco_seo.rule_money_page_metadata_gap(base(pages=pages)), [])

    def test_silent_when_the_crawl_captured_no_metadata(self):
        pages = [{"url": "https://example.com/"}]
        self.assertEqual(reco_seo.rule_money_page_metadata_gap(base(pages=pages)), [])


class OrphanPageTests(ContractTests):
    PAGES = [page("https://example.com/", internal_links_in=0),
             page("https://example.com/floorplans/a1", internal_links_in=0),
             page("https://example.com/floorplans", internal_links_in=7)]

    def test_fires_on_the_unlinked_page_and_spares_the_homepage(self):
        out = reco_seo.rule_orphan_page(base(pages=self.PAGES))
        self.assert_contract(out)
        self.assertEqual(out[0]["action"]["params"]["pages"],
                         ["https://example.com/floorplans/a1"])
        self.assertEqual(out[0]["action"]["kind"], "internal_link")

    def test_silent_when_everything_is_linked(self):
        pages = [page("https://example.com/floorplans", internal_links_in=3)]
        self.assertEqual(reco_seo.rule_orphan_page(base(pages=pages)), [])

    def test_silent_when_the_crawl_never_counted_links(self):
        self.assertEqual(
            reco_seo.rule_orphan_page(base(pages=[page("https://example.com/x")])), [])


class LocalProfileTests(ContractTests):
    LOCAL = {"source": "business_profile", "as_of": "2026-09-10",
             "profile": {"primary_category": "", "hours": [], "phone": "(480) 555-0111",
                         "website": "https://example.com", "description": "A community."},
             "listings": [{"source": "the profile", "name": "Vitri Apartments",
                           "address": "15000 N Scottsdale Rd", "phone": "(480) 555-0111"},
                          {"source": "a listing site", "name": "Vitri Apartments",
                           "address": "15000 North Scottsdale Road", "phone": "(480) 555-0199"}]}

    def test_fires_on_empty_fields_and_facts_that_disagree(self):
        out = reco_seo.rule_local_profile_gap(base(local=self.LOCAL))
        self.assert_contract(out)
        fixes = out[0]["action"]["params"]["fix"]
        self.assertIn("no primary category", fixes)
        self.assertIn("no opening hours", fixes)
        self.assertIn("a phone number that differs between listings", fixes)
        # "N ... Rd" and "North ... Road" are one address written two ways.
        self.assertNotIn("a street address that differs between listings", fixes)

    def test_silent_when_the_profile_is_complete_and_consistent(self):
        local = {"profile": {"primary_category": "Apartment building",
                             "hours": ["Mon 9-6"], "phone": "(480) 555-0111",
                             "website": "https://example.com", "description": "A community."},
                 "listings": [{"source": "a", "name": "Vitri", "phone": "480-555-0111"},
                              {"source": "b", "name": "Vitri", "phone": "(480) 555-0111"}]}
        self.assertEqual(reco_seo.rule_local_profile_gap(base(local=local)), [])

    def test_silent_without_a_profile_or_a_second_listing(self):
        self.assertEqual(reco_seo.rule_local_profile_gap(base(local={"listings": []})), [])


class CoreWebVitalsTests(ContractTests):
    VITALS = [{"url": "https://example.com/floorplans", "lcp_ms": 4800, "inp_ms": 120,
               "cls": 0.04, "mobile_usable": True, "source": "crux_field",
               "as_of": "2026-09-14"},
              {"url": "https://example.com/", "lcp_ms": 1800, "inp_ms": 90, "cls": 0.02,
               "mobile_usable": True, "source": "crux_field", "as_of": "2026-09-14"}]

    def test_fires_on_the_measured_page_that_is_too_slow(self):
        out = reco_seo.rule_core_web_vitals_blocking(base(vitals=self.VITALS))
        self.assert_contract(out)
        self.assertEqual(out[0]["severity"], "high")          # past the poor threshold
        self.assertEqual(len(out[0]["action"]["params"]["pages"]), 1)

    def test_a_page_unusable_on_a_phone_is_always_high(self):
        vitals = [dict(self.VITALS[1], mobile_usable=False)]
        out = reco_seo.rule_core_web_vitals_blocking(base(vitals=vitals))
        self.assertEqual(out[0]["severity"], "high")

    def test_silent_when_every_measured_page_is_inside_the_thresholds(self):
        self.assertEqual(
            reco_seo.rule_core_web_vitals_blocking(base(vitals=[self.VITALS[1]])), [])

    def test_silent_without_measurements(self):
        self.assertEqual(reco_seo.rule_core_web_vitals_blocking(base(vitals=[])), [])


class BriefFreshnessTests(ContractTests):
    FACTS = {"pet_policy": {"label": "pet policy", "value": "two pets per home",
                            "last_edited": "2026-01-04"},
             "fees": {"label": "fees", "value": "valet trash $35",
                      "last_edited": "2026-09-01"},
             "parking": {"label": "parking", "value": "garage included",
                         "last_edited": None}}

    def test_fires_on_the_field_nobody_has_confirmed(self):
        out = reco_seo.rule_brief_fact_stale(base(brief_facts=self.FACTS))
        self.assert_contract(out)
        self.assertEqual(out[0]["action"]["params"]["confirm_or_update"], ["pet_policy"])
        self.assertEqual(out[0]["severity"], "high")          # over 180 days

    def test_a_field_with_no_recorded_edit_date_is_never_called_stale(self):
        facts = {"parking": self.FACTS["parking"]}
        self.assertEqual(reco_seo.rule_brief_fact_stale(base(brief_facts=facts)), [])

    def test_silent_when_everything_was_confirmed_recently(self):
        facts = {"fees": self.FACTS["fees"]}
        self.assertEqual(reco_seo.rule_brief_fact_stale(base(brief_facts=facts)), [])

    def test_silent_without_a_brief(self):
        self.assertEqual(reco_seo.rule_brief_fact_stale(base(brief_facts={})), [])


class VendorNormalizerTests(unittest.TestCase):
    """Against the shape the vendor actually returned for a real property."""

    VISIBILITY = {
        "as_of": "2026-09-17",
        "prompts": [
            {"id": "p1", "text": "What luxury apartments near the quarter have best reviews",
             "isBranded": False, "intentCategory": "commercial",
             "topics": [{"id": "t1", "name": "Luxury Apartments"}],
             "metrics": {"totalResponses": 3, "brandMentions": 3, "visibilityScore": 66.7},
             "platformBreakdown": [{"platform": "AI Overviews", "mentioned": False},
                                   {"platform": "ChatGPT", "mentioned": True},
                                   {"platform": "Perplexity", "mentioned": True}]},
            {"id": "p2", "text": "Which apartments have rooftop decks and modern kitchens",
             "isBranded": False, "intentCategory": "transactional",
             "topics": [{"id": "t2", "name": "Apartment Amenities"}],
             "metrics": {"totalResponses": 3, "brandMentions": 0, "visibilityScore": 0},
             "platformBreakdown": [{"platform": "ChatGPT", "mentioned": False},
                                   {"platform": "Perplexity", "mentioned": None}]},
        ],
        "topics": [{"topic": "Apartment Amenities", "prompts": 3, "our_mentions": 0,
                    "competitor_mentions": 21, "competitors": [{"name": "Huxley"}]}],
    }

    def test_maps_prompts_platforms_topics_and_intent(self):
        data = base()
        self.assertTrue(reco_seo.merge_searchable(data, visibility=self.VISIBILITY))
        self.assertEqual(data["questions_source"], "searchable")
        first = data["questions"][0]
        self.assertEqual(first["topic"], "Luxury Apartments")
        self.assertEqual(first["intent"], "commercial")
        self.assertEqual(first["engines"]["AI Overviews"]["named"], False)
        # An unmeasured platform stays None, never False.
        self.assertIsNone(data["questions"][1]["engines"]["Perplexity"]["named"])
        self.assertEqual(data["topic_coverage"][0]["competitor_mentions"], 21)

    def test_a_platform_with_no_reading_is_not_counted_as_a_miss(self):
        data = base()
        reco_seo.merge_searchable(data, visibility=self.VISIBILITY)
        out = reco_seo.rule_share_of_answer_gap(data)
        # 3 readings measured (AI Overviews no, ChatGPT yes, Perplexity yes) on
        # the first prompt plus 1 on the second; 2 of those 4 are misses.
        self.assertIn("2 of the 4", out[0]["found"])

    def test_sentiment_narrows_to_what_it_costs(self):
        data = base()
        reco_seo.merge_searchable(data, sentiment={"weaknesses": [
            {"quote": "additional fees significantly raise the total monthly cost",
             "platform": "ChatGPT"},
            {"quote": "residents mention slow maintenance response", "platform": "ChatGPT"}]})
        self.assertEqual(len(data["fee_signals"]), 1)

    def test_a_discovered_but_unaudited_page_carries_no_measurements(self):
        """The vendor lists URLs it has not audited; nulls must not become findings.

        This is the real payload for a project whose pages are discovered and
        queued: every score and every field is null. If those nulls were
        copied through, the metadata rule would report 12 pages with no title
        on a site it has never read.
        """
        data = base()
        reco_seo.merge_searchable(data, site_health={"pages": [
            {"url": "https://example.com/", "slug": "/", "type": None,
             "technicalScore": None, "aeoScore": None, "lastAuditedAt": None},
            {"url": "https://example.com/amenities", "slug": "/amenities",
             "type": None, "technicalScore": None, "aeoScore": None}]})
        self.assertEqual(len(data["pages"]), 2)
        for row in data["pages"]:
            self.assertEqual(sorted(row), ["url"])
        self.assertEqual(reco_seo.rule_money_page_metadata_gap(data), [])
        self.assertEqual(reco_seo.rule_orphan_page(data), [])

    def test_an_audited_page_keeps_what_it_measured(self):
        data = base()
        reco_seo.merge_searchable(data, site_health={"pages": [
            {"url": "https://example.com/floorplans", "title": "Floor plans",
             "metaDescription": "", "type": "floor_plans", "internal_links_in": 0}]})
        page_row = data["pages"][0]
        self.assertEqual(page_row["title"], "Floor plans")
        self.assertEqual(page_row["page_type"], "floor_plans")
        self.assertNotIn("meta_description", page_row)     # empty is not measured
        self.assertEqual(page_row["internal_links_in"], 0)

    def test_nothing_recognized_lands_nothing(self):
        data = base()
        self.assertFalse(reco_seo.merge_searchable(data, visibility={"prompts": []}))
        self.assertNotIn("questions", data)


class GatherTests(unittest.TestCase):
    """The production entry point, on a server where nothing is reachable."""

    def test_every_dead_source_becomes_a_named_gap_and_nothing_raises(self):
        from unittest.mock import patch

        with patch("skills.property_resolver.resolve", side_effect=RuntimeError("down")), \
                patch("community_brief.load_company_state", side_effect=RuntimeError("down")):
            data, gaps = reco_seo.gather("1001", today=TODAY)
        sources = {g["source"] for g in gaps}
        self.assertIn("property_resolver", sources)
        self.assertIn("community_brief", sources)
        self.assertIn("ai_answer_tracking", sources)
        self.assertIn("site_crawl", sources)
        for gap in gaps:
            self.assertEqual(sorted(gap), ["field", "message", "source"])
            self.assertTrue(gap["message"].endswith("."))
        # Nothing was invented to fill the silence.
        self.assertEqual(sorted(data), ["as_of", "company_id"])

    def test_run_without_data_reports_the_gaps_and_skips_every_rule(self):
        from unittest.mock import patch

        with patch("skills.property_resolver.resolve", side_effect=RuntimeError("down")), \
                patch("community_brief.load_company_state", side_effect=RuntimeError("down")):
            out = reco_seo.run("1001", today=TODAY)
        self.assertEqual(out["recommendations"], [])
        self.assertEqual(out["rules_run"], [])
        self.assertEqual(len(out["rules_skipped"]), len(reco_seo.RULES))
        self.assertTrue(out["gaps"])


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
        """Anything that writes words a renter reads goes to review — and
        anything that does not stays out of it, declaring so in its params.
        A performance ticket sitting in a copy queue is noise, and noise is
        how a review stops being read.
        """
        seen = set()
        for reco in _every_recommendation():
            seen.add(reco["rule_key"])
            action = reco["action"]
            writes_copy = reco["rule_key"] in reco_seo.COPY_PRODUCING_RULES
            self.assertEqual(action["fair_housing_review"], writes_copy,
                             "%s: the review flag disagrees with what it produces"
                             % reco["rule_key"])
            if not writes_copy:
                self.assertIs(action["params"].get("changes_copy"), False,
                              "%s skips review without declaring it writes no copy"
                              % reco["rule_key"])
        self.assertEqual(seen, set(reco_seo.RULES),
                         "a rule never fires in these fixtures, so nothing here checks it")

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
    base(questions=ShareOfAnswerTests.QUESTIONS, questions_source="searchable",
         fanout=[{"query": "pet policy at waterfront apartments", "engine": "ChatGPT",
                  "count": 4}]),
    base(claims=StaleFactTests.CLAIMS, brief_facts=StaleFactTests.FACTS),
    base(fee_signals=FeeTransparencyTests.SIGNALS, fees=FeeTransparencyTests.FEES),
    base(topic_coverage=AnswerFormatGapTests.COVERAGE,
         cited_formats=[{"format": "ranked lists", "share": 0.6}],
         our_formats=[]),
    base(drafts=DifferentiationGateTests.DRAFTS, corpus=DifferentiationGateTests.CORPUS),
    base(pages=MoneyPageMetadataTests.PAGES),
    base(pages=OrphanPageTests.PAGES),
    base(local=LocalProfileTests.LOCAL),
    base(vitals=CoreWebVitalsTests.VITALS),
    base(brief_facts=BriefFreshnessTests.FACTS),
]


if __name__ == "__main__":
    unittest.main()
