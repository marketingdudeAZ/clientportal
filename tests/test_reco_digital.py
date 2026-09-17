"""Digital recommendation rules — one fixture per rule, plus the Fair Housing bar.

Every rule gets two tests: it fires on a real-shaped input, and it stays silent
on an empty one. Silence on missing data is the important half — a rule that
invents a finding from nothing is worse than a rule that never runs.

Two contract tests guard the seam with `skills/reco_engine.py`: everything these
rules emit must pass the engine's `validate`, and nothing may trip its
`compliance_refusal`. The Fair Housing test additionally proves the refusal is
enforced inside this module, so a targeting action cannot leave it at all.

No I/O: every fixture is a hand-built DigitalContext.
"""

from __future__ import annotations

import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("HUBSPOT_API_KEY", "test-key")

from skills import reco_digital as rd  # noqa: E402
from skills import reco_engine as re_engine  # noqa: E402

TODAY = date(2026, 9, 17)
ADS_AS_OF = "2026-09-16"
APTIQ_AS_OF = "2026-09-16"


# ── fixtures ─────────────────────────────────────────────────────────────────

def ctx(**over) -> rd.DigitalContext:
    base = {"company_id": "6001", "property_name": "The Atwood", "uuid": "u-1",
            "domain": "theatwood.com", "units": 320}
    base.update(over)
    return rd.DigitalContext(**base)


def empty_ctx() -> rd.DigitalContext:
    """Nothing connected: the 22 of 109 properties with no Google Ads id, on a
    day the AptIQ export did not arrive."""
    return ctx(gaps=[rd._gap("ads", "google_ads", "No Google Ads customer id.")])


def plans(*rows) -> dict:
    return {"as_of": APTIQ_AS_OF, "source": "aptiq_floor_plans", "floor_plans": list(rows)}


def plan(name, beds, available, rent=None, dom=None) -> dict:
    return {"name": name, "beds": beds, "baths": 1, "sqft": 700,
            "available_units": available, "asking_rent": rent, "days_on_market": dom}


def ads(**sections) -> dict:
    out = {"as_of": ADS_AS_OF, "customer_id": "4869803719", "available": True}
    out.update(sections)
    return out


def group(name, cost, *, impressions=1000, clicks=50, conversions=2,
          campaign="Search — Brand", status="ENABLED", urls=None) -> dict:
    return {"ad_group_name": name, "campaign_name": campaign, "status": status,
            "campaign_status": "ENABLED", "impressions": impressions, "clicks": clicks,
            "cost": cost, "conversions": conversions,
            "final_urls": urls if urls is not None else ["https://theatwood.com/floorplans"]}


# ── 1. spend not pointed at vacancy ──────────────────────────────────────────

def vacancy_ctx() -> rd.DigitalContext:
    """The Atwood shape: 244 vacant studio and one-bedroom units, and every
    dollar of spend in ad groups named for the two- and three-bedroom plans."""
    return ctx(
        availability=plans(plan("S1 Studio", 0, 96, rent=1450),
                           plan("A1", 1, 148, rent=1650),
                           plan("B2", 2, 4, rent=2100),
                           plan("C1", 3, 2, rent=2600)),
        ads=ads(ad_groups=[group("2 Bedroom Apartments", 2400.0),
                           group("Three Bedroom Homes", 1100.0),
                           group("Brand", 500.0, campaign="Search — Brand")],
                keywords=[{"keyword": "2 bedroom apartments austin",
                           "ad_group_name": "2 Bedroom Apartments", "cost": 2400.0,
                           "clicks": 300, "conversions": 10}]))


def test_spend_not_on_vacancy_fires_on_the_atwood_shape():
    out = rd.spend_not_on_vacancy(vacancy_ctx(), TODAY)
    assert len(out) == 1
    reco = out[0]
    assert reco["rule_key"] == "spend_not_on_vacancy"
    assert reco["id"] == "spend_not_on_vacancy:6001:2026-09-17"
    assert reco["severity"] == "high"
    assert reco["action"]["kind"] == "new_ad_group"
    assert reco["action"]["requires_signed_deal"] is True
    assert set(reco["action"]["params"]["floor_plan_buckets"]) == {"studio", "1_bed"}
    assert reco["action"]["params"]["vacant_units"] == 244
    assert "244" in reco["found"]
    assert any("Vacant units" in r["label"] for r in reco["receipts"])


def test_spend_not_on_vacancy_silent_without_data():
    assert rd.spend_not_on_vacancy(empty_ctx(), TODAY) == []
    # Availability but no ad data: no claim about where spend points.
    assert rd.spend_not_on_vacancy(ctx(availability=plans(plan("A1", 1, 90))), TODAY) == []


def test_spend_not_on_vacancy_silent_when_spend_already_follows_vacancy():
    c = ctx(availability=plans(plan("A1", 1, 40, rent=1600)),
            ads=ads(ad_groups=[group("1 Bedroom Apartments", 3000.0)]))
    assert rd.spend_not_on_vacancy(c, TODAY) == []


# ── 2. impression share lost to budget ───────────────────────────────────────

def is_lost_ctx(lost=0.34) -> rd.DigitalContext:
    return ctx(
        availability={"as_of": APTIQ_AS_OF, "available_units": 38, "floor_plans": []},
        ads=ads(campaigns=[{"campaign_name": "Search — Availability", "status": "ENABLED",
                            "cost": 3000.0, "clicks": 600, "impressions": 40000,
                            "conversions": 40, "search_budget_lost_is": lost}]))


def test_impression_share_lost_fires_and_quantifies_clicks():
    out = rd.impression_share_lost(is_lost_ctx(), TODAY)
    assert len(out) == 1
    reco = out[0]
    assert reco["severity"] == "high"
    assert reco["action"]["kind"] == "budget_change"
    assert reco["action"]["requires_signed_deal"] is True
    assert reco["action"]["params"]["additional_monthly_spend_usd"] > 0
    assert "more clicks" in reco["expect"]


def test_impression_share_lost_uses_the_funnel_when_the_property_has_one():
    c = is_lost_ctx()
    c.funnel = {"leads": 120.0, "clicks": 600.0, "as_of": "2026-08-31"}
    reco = rd.impression_share_lost(c, TODAY)[0]
    assert "more leads" in reco["expect"]
    assert any(r["source"] == "hyly_rollup" for r in reco["receipts"])


def test_impression_share_lost_silent_without_data_or_below_threshold():
    assert rd.impression_share_lost(empty_ctx(), TODAY) == []
    assert rd.impression_share_lost(is_lost_ctx(lost=0.04), TODAY) == []
    # No vacancy: a capped budget is not a problem worth spending more on.
    no_vacancy = is_lost_ctx()
    no_vacancy.availability = {"as_of": APTIQ_AS_OF, "available_units": 0, "floor_plans": []}
    assert rd.impression_share_lost(no_vacancy, TODAY) == []


# ── 3. wasted spend ──────────────────────────────────────────────────────────

def waste_ctx() -> rd.DigitalContext:
    return ctx(ads=ads(
        campaigns=[{"campaign_name": "Search", "cost": 4000.0, "clicks": 800,
                    "conversions": 30}],
        search_terms=[{"search_term": "apartments for rent near me", "cost": 620.0,
                       "clicks": 120, "conversions": 0},
                      {"search_term": "cheap apartments", "cost": 180.0, "clicks": 40,
                       "conversions": 0},
                      {"search_term": "the atwood apartments", "cost": 300.0,
                       "clicks": 90, "conversions": 12}],
        keywords=[{"keyword": "apartments", "ad_group_name": "Generic", "cost": 400.0,
                   "clicks": 90, "conversions": 0},
                  {"keyword": "apartments", "ad_group_name": "Broad", "cost": 200.0,
                   "clicks": 40, "conversions": 1},
                  {"keyword": "the atwood", "ad_group_name": "Brand", "cost": 120.0,
                   "clicks": 60, "conversions": 9}]))


def test_wasted_spend_fires_on_zero_conversion_terms():
    out = rd.wasted_spend(waste_ctx(), TODAY)
    assert len(out) == 1
    reco = out[0]
    assert reco["action"]["kind"] == "pause"
    assert "apartments for rent near me" in reco["action"]["params"]["add_negative_keywords"]
    assert "the atwood apartments" not in reco["action"]["params"]["add_negative_keywords"]
    assert reco["action"]["params"]["duplicate_keywords"] == ["apartments"]
    assert reco["action"]["params"]["estimated_monthly_recovery_usd"] == 800.0
    assert reco["severity"] == "high"            # $800 of $4,000 = 20% of the budget


def test_wasted_spend_withholds_a_search_term_that_reads_as_targeting():
    c = waste_ctx()
    c.ads["search_terms"].append({"search_term": "apartments zip 77002", "cost": 90.0,
                                  "clicks": 10, "conversions": 0})
    reco = rd.wasted_spend(c, TODAY)[0]
    params = reco["action"]["params"]
    assert "apartments zip 77002" not in params["add_negative_keywords"]
    assert params["terms_withheld_for_review"] == 1
    assert re_engine.compliance_refusal(reco) is None


def test_wasted_spend_silent_without_data():
    assert rd.wasted_spend(empty_ctx(), TODAY) == []
    clean = ctx(ads=ads(campaigns=[{"campaign_name": "Search", "cost": 4000.0,
                                    "clicks": 800, "conversions": 30}],
                        search_terms=[{"search_term": "the atwood", "cost": 300.0,
                                       "clicks": 90, "conversions": 12}],
                        keywords=[]))
    assert rd.wasted_spend(clean, TODAY) == []


# ── 4. ads landing on the homepage ───────────────────────────────────────────

def homepage_ctx() -> rd.DigitalContext:
    """Park 5 shape: the inventory ad groups exist, every ad lands on the root."""
    return ctx(
        availability=plans(plan("TH2", 2, 12, rent=2400), plan("TH3", 3, 6, rent=2900)),
        ads=ads(ad_groups=[
            group("2 Bedroom Townhomes", 1800.0, urls=["https://park5.com/"]),
            group("3 Bedroom Townhomes", 900.0, urls=["https://park5.com/?utm_source=g"]),
            group("Brand", 400.0, urls=["https://park5.com/contact"])]))


def test_homepage_landing_page_fires_when_inventory_ads_land_on_the_root():
    out = rd.homepage_landing_page(homepage_ctx(), TODAY)
    assert len(out) == 1
    reco = out[0]
    assert reco["action"]["kind"] == "landing_page"
    assert reco["severity"] == "high"          # the ad groups name a floor plan
    assert reco["confidence"] == 9
    assert len(reco["action"]["params"]["ad_groups"]) == 2
    assert reco["action"]["requires_signed_deal"] is False


def test_homepage_landing_page_silent_when_ads_point_at_real_pages():
    assert rd.homepage_landing_page(empty_ctx(), TODAY) == []
    good = ctx(ads=ads(ad_groups=[group("2 Bedroom", 1800.0,
                                        urls=["https://park5.com/floorplans/th2"])]))
    assert rd.homepage_landing_page(good, TODAY) == []


@pytest.mark.parametrize("url,expected", [
    ("https://park5.com", True),
    ("https://park5.com/", True),
    ("https://park5.com/?utm_campaign=x", True),
    ("https://park5.com/index.html", True),
    ("https://park5.com/floorplans", False),
    ("https://park5.com/floorplans/a1", False),
    ("", False),
])
def test_homepage_url_detection(url, expected):
    assert rd.is_homepage_url(url) is expected


# ── 5. conversion tracking broken or absent ──────────────────────────────────

def tracking_ctx() -> rd.DigitalContext:
    """The GTM onboarding gap: a cloned container that kept its placeholders."""
    return ctx(ga4={"property_id": None, "as_of": APTIQ_AS_OF},
               tags={"google_ads_conversion_id": "0", "ga4_measurement_id": "G-XXXXXXX",
                     "source": "gtm", "as_of": ADS_AS_OF, "unchanged_months": 8},
               ads=ads(campaigns=[{"campaign_name": "Search", "cost": 5400.0,
                                   "clicks": 900, "conversions": 0}]))


def test_conversion_tracking_broken_fires_on_placeholders_and_dead_conversions():
    out = rd.conversion_tracking_broken(tracking_ctx(), TODAY)
    assert len(out) == 1
    reco = out[0]
    assert reco["severity"] == "high"
    assert reco["confidence"] == 10
    assert reco["action"]["kind"] == "tracking_fix"
    assert reco["action"]["executor"] == "human"
    defects = reco["action"]["params"]["defects"]
    assert "spend with no tracked conversion" in defects
    assert "no GA4 property id on the record" in defects
    assert any("placeholder" in d for d in defects)
    assert "8 months" in reco["found"]


def test_conversion_tracking_broken_is_medium_when_only_the_ga4_id_is_missing():
    c = ctx(ga4={"property_id": None}, ads=ads(campaigns=[
        {"campaign_name": "Search", "cost": 2000.0, "clicks": 400, "conversions": 24}]))
    reco = rd.conversion_tracking_broken(c, TODAY)[0]
    assert reco["severity"] == "medium"


def test_conversion_tracking_broken_silent_when_measurement_is_healthy():
    assert rd.conversion_tracking_broken(empty_ctx(), TODAY) == []
    healthy = ctx(ga4={"property_id": "331234567"},
                  tags={"google_ads_conversion_id": "AW-987654321",
                        "ga4_measurement_id": "G-8QP2LMX41C", "source": "gtm"},
                  ads=ads(campaigns=[{"campaign_name": "Search", "cost": 2000.0,
                                      "clicks": 400, "conversions": 24}]))
    assert rd.conversion_tracking_broken(healthy, TODAY) == []


@pytest.mark.parametrize("value,expected", [
    ("0", True), ("", True), (None, True), ("AW-0", True), ("GTM-XXXXXX", True),
    ("AW-987654321", False), ("G-8QP2LMX41C", False), ("331234567", False),
])
def test_placeholder_id_detection(value, expected):
    assert rd.is_placeholder_id(value) is expected


# ── 6. conversion overcounting ───────────────────────────────────────────────

def overcount_ctx(ads_conversions=188.0, site=20.0) -> rd.DigitalContext:
    """Claire's shape: Google Ads at 9.4x the site's own total."""
    return ctx(ga4={"property_id": "331234567", "conversions": site, "sessions": 4200,
                    "as_of": ADS_AS_OF},
               ads=ads(campaigns=[{"campaign_name": "Search", "cost": 4000.0,
                                   "clicks": 700, "conversions": ads_conversions}]))


def test_conversion_overcounting_fires_and_does_not_celebrate_the_number():
    reco = rd.conversion_overcounting(overcount_ctx(), TODAY)[0]
    assert reco["severity"] == "high"
    assert reco["action"]["kind"] == "tracking_fix"
    assert "9.4 times as many" in reco["found"]
    assert reco["action"]["params"]["ratio"] == 9.4
    labels = [r["label"] for r in reco["receipts"]]
    assert "Cost per conversion against the site total" in labels


def test_conversion_overcounting_silent_without_ga4_or_below_ratio():
    assert rd.conversion_overcounting(empty_ctx(), TODAY) == []
    no_ga4 = ctx(ads=ads(campaigns=[{"campaign_name": "Search", "cost": 4000.0,
                                     "clicks": 700, "conversions": 188.0}]))
    assert rd.conversion_overcounting(no_ga4, TODAY) == []
    assert rd.conversion_overcounting(overcount_ctx(ads_conversions=24.0, site=20.0),
                                      TODAY) == []
    # Too few conversions to call it a pattern.
    assert rd.conversion_overcounting(overcount_ctx(ads_conversions=6.0, site=1.0),
                                      TODAY) == []


# ── Fair Housing ─────────────────────────────────────────────────────────────

def test_no_rule_can_emit_a_targeting_action():
    """The guard is inside _rec, so a rule cannot ship one even by mistake."""
    with pytest.raises(rd.FairHousingViolation):
        rd._rec(ctx(), TODAY, rule_key="test", category="cost", channels=["paid_search"],
                severity="high", confidence=9, found="Reach is too broad.",
                receipts=[rd._receipt("Spend", "$1", "google_ads", ADS_AS_OF)],
                expect="Fewer wasted clicks.", if_skip="Spend continues.",
                kind="budget_change",
                params={"radius_miles": 5}, executor="ninjacat",
                fair_housing_review=False, verify_metric="cpc")


@pytest.mark.parametrize("params", [
    {"audience": ["renters"]},
    {"note": "add a lookalike of past leads"},
    {"note": "layer retargeting on the availability campaign"},
    {"zip_codes": ["77002"]},
    {"note": "narrow the demographic to age range 25-34"},
])
def test_targeting_params_are_refused_whatever_form_they_take(params):
    with pytest.raises(rd.FairHousingViolation):
        rd._rec(ctx(), TODAY, rule_key="test", category="cost", channels=["paid_search"],
                severity="low", confidence=5, found="A finding.",
                receipts=[rd._receipt("Spend", "$1", "google_ads", ADS_AS_OF)],
                expect="Something.", if_skip="Something else.", kind="pause",
                params=params, executor="ninjacat", fair_housing_review=False,
                verify_metric="cpc")


def test_this_module_is_at_least_as_strict_as_the_engine():
    for term in re_engine.FORBIDDEN_ACTION_TERMS:
        assert any(term in mine for mine in rd.FORBIDDEN_TERMS), term


def test_spend_increasing_actions_always_require_a_signed_deal():
    reco = rd._rec(ctx(), TODAY, rule_key="test", category="cost", channels=["paid_search"],
                   severity="low", confidence=5, found="A finding.",
                   receipts=[rd._receipt("Spend", "$1", "google_ads", ADS_AS_OF)],
                   expect="Something.", if_skip="Something else.", kind="new_ad_group",
                   params={}, executor="ninjacat", fair_housing_review=False,
                   verify_metric="clicks", requires_signed_deal=False)
    assert reco["action"]["requires_signed_deal"] is True


# ── contract with reco_engine ────────────────────────────────────────────────

ALL_FIXTURES = {
    "spend_not_on_vacancy": vacancy_ctx,
    "impression_share_lost": is_lost_ctx,
    "wasted_spend": waste_ctx,
    "homepage_landing_page": homepage_ctx,
    "conversion_tracking_broken": tracking_ctx,
    "conversion_overcounting": overcount_ctx,
}


def test_every_rule_is_registered():
    assert set(rd.RULES) == set(ALL_FIXTURES)
    for rule_key, rule in rd.RULES.items():
        assert rule.__name__ == rule_key


def test_every_rule_output_passes_the_engine():
    for rule_key, build in ALL_FIXTURES.items():
        produced = rd.RULES[rule_key](build(), TODAY)
        assert produced, rule_key
        for reco in produced:
            assert re_engine.validate(reco) is None, (rule_key, re_engine.validate(reco))
            assert re_engine.compliance_refusal(reco) is None, rule_key
            assert reco["start_by"] >= TODAY.isoformat()
            assert reco["verify"]["when"] > TODAY.isoformat()
            assert reco["action"]["executor"] in rd.EXECUTORS


# ── run() ────────────────────────────────────────────────────────────────────

def test_run_returns_the_aggregator_shape_and_never_raises():
    out = rd.run("6001", today=TODAY, context=vacancy_ctx())
    assert set(out) >= {"company_id", "recommendations", "gaps", "rules_run",
                        "rules_skipped"}
    assert out["company_id"] == "6001"
    assert "spend_not_on_vacancy" in out["rules_run"]
    assert out["rules_skipped"] == []


def test_run_isolates_a_failing_rule(monkeypatch):
    def boom(context, today):
        raise RuntimeError("credentials expired")

    monkeypatch.setitem(rd.RULES, "wasted_spend", boom)
    out = rd.run("6001", today=TODAY, context=vacancy_ctx())
    assert [s["rule_key"] for s in out["rules_skipped"]] == ["wasted_spend"]
    assert "spend_not_on_vacancy" in out["rules_run"]
    assert any(g["field"] == "wasted_spend" for g in out["gaps"])


def test_run_can_be_narrowed_to_one_rule():
    out = rd.run("6001", today=TODAY, context=vacancy_ctx(), rules=["wasted_spend"])
    assert out["rules_run"] == ["wasted_spend"]
    assert out["recommendations"] == []
