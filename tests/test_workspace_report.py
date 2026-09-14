"""Workspace monthly report — assembler, tone, receipts, polish and fair housing.

All I/O is mocked. The fixture under tests/fixtures/workspace/ was produced by
the same assemble() these tests exercise, from Hyly's dashboard export.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "webhook-server"))

from skills import workspace_report as wr  # noqa: E402
from skills import workspace_report_export as wx  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "workspace" / "report_bromley_2026_06.json"
PAGE = ROOT / "webhook-server" / "portal_pages" / "workspace_report.html"
AS_OF = "2026-06-30"

SECTIONS = ("occupancy", "funnel", "spend", "attribution", "website", "paid_search", "reputation", "listings")
TOP_KEYS = {"property", "month", "month_label", "available_months", "as_of", "summary", "key_numbers", *SECTIONS,
            "actions", "sources_note", "discrepancies", "gaps"}


@pytest.fixture(scope="module")
def fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def R(value, source="test.source"):
    return wr.receipt(value, source, AS_OF)


def mini_raw(**over) -> dict:
    """A small, complete raw dict with round numbers."""
    raw = {
        "property": {"company_id": "1", "name": "Test Property", "city": "Austin", "state": "TX", "units": R(200)},
        "month": "2026-08",
        "as_of": "2026-08-31",
        "available_months": ["2026-08"],
        "values": {
            "total_units": R(200), "available": R(10), "occupied": R(180), "vacant": R(20),
            "vacant_rented": R(10), "vacant_unrented": R(10), "leased_future": R(10),
            "move_ins": R(12), "move_outs": R(8), "delayed_move_ins": R(1), "avg_occupancy_rate": R(0.905),
            "created": R(100), "scheduled": R(40), "toured": R(20), "applied": R(10), "net_applied": R(9),
            "leased": R(5), "total_spend": R(10000),
            "days_created_to_scheduled": R(5.0), "days_scheduled_to_toured": R(2.0),
            "days_toured_to_applied": R(3.0), "days_applied_to_leased": R(6.0), "days_created_to_leased": R(16.0),
            "ga_sessions": R(1000), "ga_users": R(800), "ga_engaged_sessions": R(600), "ga_conversions": R(20),
            "ga_avg_engagement_seconds": R(40), "ga_floorplan_views": R(300),
            "ads_impressions": R(10000), "ads_clicks": R(400), "ads_ctr": R(0.04), "ads_cpc": R(5.0),
            "ads_conversions": R(12), "ads_cost_per_conversion": R(166.67), "ads_spend": R(2000),
        },
        "vendors": [
            {"name": "Google Ads", "first_touch_source": "Google PayPerClick (PPC)", "spend": R(2000)},
            {"name": "Zillow", "first_touch_source": "Zillow", "spend": R(1000)},
        ],
        "spend_by_source": [],
        "first_touch": {
            "created": [{"name": "Property Website", "count": R(60)}, {"name": "Google PayPerClick (PPC)", "count": R(10)},
                        {"name": "Zillow", "count": R(30)}],
            "created__complete": True,
            "leased": [{"name": "Property Website", "count": R(5)}],
            "leased__complete": True,
        },
        "by_medium": {"created": [{"name": "Organic", "count": R(70)}, {"name": "CPC", "count": R(31)}]},
        "influence": None,
        "website_sources": [
            {"name": "google", "sessions": R(800), "engaged_sessions": R(560), "conversions": R(18)},
            {"name": "Zillow", "sessions": R(200), "engaged_sessions": R(40), "conversions": R(0)},
        ],
        "floorplans": [{"code": "A1", "views_per_user": R(2.5)}, {"code": "B2", "views_per_user": R(3.1)}],
        "listings": None,
        "reputation": [{"name": "Google", "score": R(4.2), "reviews": R(80)}, {"name": "Yelp", "score": None,
                                                                                "reviews": None}],
        "benchmarks": None,
        "gaps": [],
    }
    for k, v in over.items():
        raw[k] = v
    return raw


def bare_numbers(obj, path=""):
    """Paths of numbers that are not the value of a receipt."""
    if isinstance(obj, bool):
        return []
    if isinstance(obj, (int, float)):
        return [path]
    if isinstance(obj, dict):
        is_receipt = {"value", "source", "as_of"} <= set(obj)
        out = []
        for k, v in obj.items():
            if (is_receipt and k == "value") or k == "rank":
                continue
            out += bare_numbers(v, f"{path}.{k}")
        return out
    if isinstance(obj, list):
        return [p for i, v in enumerate(obj) for p in bare_numbers(v, f"{path}[{i}]")]
    return []


# ── Contract shape ─────────────────────────────────────────────────────────

class TestContractShape:
    def test_fixture_has_every_top_level_key(self, fixture):
        assert TOP_KEYS <= set(fixture)

    def test_assembled_report_has_every_top_level_key(self):
        assert TOP_KEYS <= set(wr.assemble(mini_raw()))

    def test_every_section_has_a_takeaway_and_plain_label(self, fixture):
        for key in SECTIONS:
            sec = fixture[key]
            assert isinstance(sec["takeaway"], str) and sec["takeaway"].strip(), key
            assert sec["label"] == wr.SECTION_LABELS[key]

    def test_section_labels_are_the_calm_ones(self):
        assert list(wr.SECTION_LABELS.values()) == [
            "Occupancy", "Leasing funnel", "Spend and leases", "Where leads came from", "Website",
            "Paid search", "Reputation", "Listings", "Next month"]

    def test_summary_shape(self, fixture):
        s = fixture["summary"]
        assert set(s["lead"]) == {"value", "label", "sub"}
        assert s["lead"]["value"]["value"] == 8
        assert s["text"].startswith("8 leases in June at $1,964 average cost per lease.")
        assert 2 <= s["text"].count(". ") + 1 <= 4

    def test_key_numbers_are_one_row(self, fixture):
        assert 4 <= len(fixture["key_numbers"]) <= 6
        for k in fixture["key_numbers"]:
            assert {"key", "label", "value", "sub"} <= set(k)

    def test_funnel_stages(self, fixture):
        stages = fixture["funnel"]["stages"]
        assert [s["name"] for s in stages] == ["Leads created", "Scheduled", "Toured", "Applied", "Leased"]
        assert [s["count"]["value"] for s in stages] == [92, 33, 19, 14, 8]
        assert stages[0]["rate"] is None and stages[-1]["days_to_next"] is None
        for s in stages:
            assert {"name", "count", "rate", "days_to_next"} <= set(s)
        assert fixture["funnel"]["lead_to_lease_days"]["value"] == pytest.approx(32.8021, abs=1e-3)

    def test_spend_vendors(self, fixture):
        vendors = {v["name"]: v for v in fixture["spend"]["vendors"]}
        for v in vendors.values():
            assert {"name", "spend", "share", "leads", "cost_per_lead", "leases", "label"} <= set(v)
        assert vendors["Google Ads"]["spend"]["value"] == 12140
        assert vendors["Zillow"]["cost_per_lead"]["value"] == pytest.approx(312.5)
        assert vendors["Unallocated"]["spend"]["value"] == pytest.approx(500)
        assert vendors["Property website"]["label"] == "Unpaid"
        assert vendors["Paid social (Meta)"]["leads"] is None

    def test_attribution_sources(self, fixture):
        a = fixture["attribution"]
        assert a["prospects"]["created"]["value"] == 477
        for s in a["sources"]:
            assert {"name", "created", "influenced", "toured", "leased"} <= set(s)
        website = next(s for s in a["sources"] if s["name"] == "Property website")
        assert (website["created"]["value"], website["influenced"]["value"]) == (53, 422)
        assert a["by_medium"]["created"][0]["name"] == "Organic"

    def test_website_paid_reputation_listings(self, fixture):
        assert fixture["website"]["sessions"]["value"] == 2566
        assert {f["code"] for f in fixture["website"]["floorplans"]} >= {"B2", "A1"}
        assert fixture["paid_search"]["clicks"]["value"] == 1121
        assert set(fixture["paid_search"]["benchmark"]) == {"ctr_low", "ctr_high", "cost_per_conversion"}
        for p in fixture["reputation"]["platforms"]:
            assert {"name", "score", "reviews", "target", "status"} <= set(p)
        assert fixture["listings"]["placements"] is None or isinstance(fixture["listings"]["placements"], list)

    def test_actions(self, fixture):
        assert [a["rank"] for a in fixture["actions"]] == list(range(1, len(fixture["actions"]) + 1))
        for a in fixture["actions"]:
            assert set(a) == {"rank", "title", "detail", "stake_label", "lens", "work_item_id"}
            assert a["lens"] in wr.LENSES
            assert a["work_item_id"] is None

    def test_discrepancies_and_gaps(self, fixture):
        keys = {d["key"] for d in fixture["discrepancies"]}
        assert "google_ads_spend" in keys
        spend = next(d for d in fixture["discrepancies"] if d["key"] == "google_ads_spend")
        assert spend["text"] == ("Google Ads spend differs between the spend manager ($12,140) and the ad platform "
                                 "($5,269); we're reconciling before renewal.")
        for g in fixture["gaps"]:
            assert set(g) == {"section", "metric", "reason"}


# ── Receipts ───────────────────────────────────────────────────────────────

class TestReceipts:
    def test_fixture_has_no_number_without_a_source(self, fixture):
        assert bare_numbers(fixture) == []

    def test_assembled_report_has_no_number_without_a_source(self):
        assert bare_numbers(wr.assemble(mini_raw())) == []

    def test_every_receipt_names_a_source_and_date(self, fixture):
        for r in wr.iter_receipts(fixture):
            assert isinstance(r["source"], str) and r["source"]
            assert r["as_of"]

    def test_receipt_refuses_non_numbers(self):
        with pytest.raises(TypeError):
            wr.receipt("12", "x", AS_OF)
        with pytest.raises(TypeError):
            wr.receipt(True, "x", AS_OF)
        assert wr.receipt(None, "x", AS_OF) is None
        assert wr.receipt(float("nan"), "x", AS_OF) is None

    def test_missing_values_are_null_with_a_gap(self):
        raw = mini_raw()
        del raw["values"]["leased_future"]
        del raw["values"]["total_spend"]
        rep = wr.assemble(raw)
        assert rep["occupancy"]["future_leases"] is None
        assert rep["spend"]["total"] is None
        gap_keys = {(g["section"], g["metric"]) for g in rep["gaps"]}
        assert ("occupancy", "future_leases") in gap_keys
        assert ("spend", "total") in gap_keys
        assert bare_numbers(rep) == []


# ── Tone ───────────────────────────────────────────────────────────────────

class TestTone:
    @pytest.mark.parametrize("phrase", wr.BANNED_PHRASES)
    def test_banned_phrase_detector(self, phrase):
        assert wr.banned_phrases_in(f"Some text with {phrase.upper()} in it") == [phrase]

    def test_fixture_text_is_free_of_banned_phrases(self, fixture):
        hits = [(p, t) for p, t in wr.iter_strings(fixture) if wr.banned_phrases_in(t)]
        assert hits == []

    def test_generated_text_is_free_of_banned_phrases_in_every_branch(self):
        variants = [mini_raw()]
        worse = mini_raw()
        worse["values"]["move_outs"] = R(30)
        worse["values"]["ads_spend"] = R(900)
        worse["values"]["ads_conversions"] = R(90)
        variants.append(worse)
        empty = mini_raw(vendors=[], website_sources=[], floorplans=[], reputation=[], first_touch={}, by_medium={})
        empty["values"] = {}
        variants.append(empty)
        for raw in variants:
            rep = wr.assemble(raw)
            hits = [(p, t) for p, t in wr.iter_strings(rep) if wr.banned_phrases_in(t)]
            assert hits == []

    def test_page_static_copy_is_free_of_banned_phrases(self):
        page = PAGE.read_text(encoding="utf-8").lower()
        assert [p for p in wr.BANNED_PHRASES if p in page] == []

    def test_badges_are_neutral(self, fixture):
        labels = {v["label"] for v in fixture["spend"]["vendors"]} | {s["label"] for s in fixture["website"]["sources"]}
        labels.discard(None)
        assert labels <= {"Reviewing", "Unpaid", "Low engagement"}
        assert not labels & {"LEAK", "AUDIT", "REVIEW"}

    def test_summary_leads_with_results(self, fixture):
        text = fixture["summary"]["text"]
        assert text.index("leases") < text.index("Next month")
        assert "94.3% leased, with 16 signed residents moving in" in text
        assert "−12" in text  # the number stays, stated plainly

    def test_article_before_numbers(self):
        assert wr.article("85.5%") == "an" and wr.article("5.2%") == "a" and wr.article("11") == "an"


# ── LLM polish ─────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, text):
        self.text = text


class TestPolish:
    def test_validator_accepts_numbers_from_the_data(self, fixture):
        ok, problems = wr.validate_polished(
            "June brought 8 leases at $1,964 each. The property is 94.3% leased and 16 residents are moving in.",
            fixture)
        assert ok, problems

    def test_validator_rejects_an_invented_number(self, fixture):
        ok, problems = wr.validate_polished("June brought 37 leases at $2,873 each.", fixture)
        assert not ok
        assert "unsupported number: 37" in problems
        assert "unsupported number: $2,873" in problems

    def test_validator_does_not_let_a_dollar_match_a_percent(self, fixture):
        # 0.0569 exposure could pass as "$5.69" if every value were also tried ×100.
        ok, problems = wr.validate_polished("Exposure was $569.", fixture)
        assert not ok and problems == ["unsupported number: $569"]

    def test_validator_rejects_banned_phrases(self, fixture):
        ok, problems = wr.validate_polished("8 leases in June, and none of them came from paid.", fixture)
        assert not ok and "banned: none of them" in problems

    def test_validator_ignores_floor_plan_codes(self, fixture):
        ok, problems = wr.validate_polished("Floor plan B2 drew 3.74 views per user.", fixture)
        assert ok, problems

    def test_polish_off_by_default(self, monkeypatch, fixture):
        monkeypatch.delenv(wr.POLISH_FLAG, raising=False)
        called = []
        rep = json.loads(json.dumps(fixture))
        wr.polish_summary(rep, complete=lambda *a, **k: called.append(1))
        assert called == [] and rep["summary"]["polished"] is False

    def test_polish_falls_back_when_a_number_is_invented(self, monkeypatch, fixture):
        monkeypatch.setenv(wr.POLISH_FLAG, "true")
        rep = json.loads(json.dumps(fixture))
        before = rep["summary"]["text"]
        wr.polish_summary(rep, complete=lambda *a, **k: _Resp("June delivered 41 leases at $1,964."))
        assert rep["summary"]["text"] == before and rep["summary"]["polished"] is False

    def test_polish_kept_when_valid(self, monkeypatch, fixture):
        monkeypatch.setenv(wr.POLISH_FLAG, "1")
        rep = json.loads(json.dumps(fixture))
        new = "June closed with 8 leases at $1,964 each, and the property is 94.3% leased."
        wr.polish_summary(rep, complete=lambda *a, **k: _Resp(new))
        assert rep["summary"]["text"] == new and rep["summary"]["polished"] is True

    def test_polish_failure_keeps_template(self, monkeypatch, fixture):
        monkeypatch.setenv(wr.POLISH_FLAG, "true")
        rep = json.loads(json.dumps(fixture))
        before = rep["summary"]["text"]

        def boom(*a, **k):
            raise RuntimeError("gateway down")

        wr.polish_summary(rep, complete=boom)
        assert rep["summary"]["text"] == before

    def test_polish_goes_through_the_gateway(self, monkeypatch, fixture):
        monkeypatch.setenv(wr.POLISH_FLAG, "true")
        from skills import llm_gateway
        seen = {}
        monkeypatch.setattr(llm_gateway, "is_configured", lambda: True)
        monkeypatch.setattr(llm_gateway, "complete", lambda prompt, **kw: seen.update(kw) or _Resp("8 leases in June."))
        rep = json.loads(json.dumps(fixture))
        wr.polish_summary(rep)
        assert seen.get("purpose") == "workspace_report_polish"
        assert rep["summary"]["text"] == "8 leases in June."


# ── Fair housing on actions ────────────────────────────────────────────────

class TestFairHousing:
    def test_fixture_actions_pass(self, fixture):
        for a in fixture["actions"]:
            assert wr.action_fair_housing_hits(a) == []

    @pytest.mark.parametrize("title", [
        "Tighten the radius to 5 miles around the property",
        "Target ZIP codes 80601 and 80602",
        "Add audience layering for renters in market",
        "Use layered audiences on Meta",
        "Narrow the targeting to nearby neighborhoods",
        "Aim ads at families with children",
    ])
    def test_targeting_and_protected_class_actions_are_dropped(self, title):
        kept = wr.screen_actions([
            {"title": title, "detail": "", "stake_label": "", "lens": "amplify", "work_item_id": None},
            {"title": "Review floor plan B2 pricing", "detail": "", "stake_label": "", "lens": "tailor",
             "work_item_id": None},
        ])
        assert [a["title"] for a in kept] == ["Review floor plan B2 pricing"]
        assert kept[0]["rank"] == 1

    def test_generated_actions_never_propose_targeting_changes(self):
        for raw in (mini_raw(),):
            for a in wr.assemble(raw)["actions"]:
                text = f"{a['title']} {a['detail']}".lower()
                assert "radius" not in text and "zip" not in text and "layer" not in text


# ── Aggregation rules ──────────────────────────────────────────────────────

class TestAggregation:
    def test_pooled_rate_is_not_the_average_of_rates(self):
        groups = [(90, 100), (1, 10)]  # 90% and 10%
        assert wr.rate_from_components(groups) == pytest.approx(91 / 110)
        assert wr.rate_from_components(groups) != pytest.approx((0.9 + 0.1) / 2)

    def test_leased_rate_recomputed_from_components(self):
        rep = wr.assemble(mini_raw())
        assert rep["occupancy"]["leased_rate"]["value"] == pytest.approx((200 - 10) / 200)
        assert rep["occupancy"]["leased_rate"]["source"].startswith("derived:")
        assert rep["occupancy"]["occupied_rate"]["value"] == pytest.approx(180 / 200)

    def test_fixture_rates_match_the_export_to_two_decimals(self, fixture):
        occ = fixture["occupancy"]
        assert round(occ["leased_rate"]["value"] * 100, 2) == 94.31
        assert round(occ["occupied_rate"]["value"] * 100, 2) == 88.96
        assert round(occ["exposure_rate"]["value"] * 100, 2) == 5.69

    def test_engagement_is_pooled_and_bounce_recomputed_per_source(self):
        rep = wr.assemble(mini_raw())
        w = rep["website"]
        assert w["engagement_rate"]["value"] == pytest.approx(600 / 1000)
        zillow = next(s for s in w["sources"] if s["name"] == "Zillow")
        assert zillow["bounce_rate"]["value"] == pytest.approx((200 - 40) / 200)
        assert zillow["label"] == "Low engagement"

    def test_share_denominator_is_the_parent_metric_not_the_breakdown_sum(self):
        rep = wr.assemble(mini_raw())
        organic = rep["attribution"]["by_medium"]["created"][0]
        assert organic["share"]["value"] == pytest.approx(70 / 100)  # breakdown sums to 101

    def test_vendor_share_and_cost_per_lead_from_components(self):
        rep = wr.assemble(mini_raw())
        google = next(v for v in rep["spend"]["vendors"] if v["name"] == "Google Ads")
        assert google["share"]["value"] == pytest.approx(2000 / 10000)
        assert google["cost_per_lead"]["value"] == pytest.approx(2000 / 10)

    def test_funnel_rates_are_step_over_step(self):
        stages = wr.assemble(mini_raw())["funnel"]["stages"]
        assert [round(s["rate"]["value"], 4) if s["rate"] else None for s in stages] == [None, 0.4, 0.5, 0.5, 0.5]

    def test_cost_per_lease_is_total_spend_over_leases(self, fixture):
        cpl = next(k for k in fixture["key_numbers"] if k["key"] == "cost_per_lease")
        assert cpl["value"]["value"] == pytest.approx(15715 / 8)


# ── Live gathering (fake lake) ─────────────────────────────────────────────

class FakeLake:
    """Answers the assembler's queries by what they select."""

    def __init__(self):
        self.sql = []

    def __call__(self, sql, params):
        self.sql.append(sql)
        if "MIN(as_of_date)" in sql:
            return [{"first": date(2026, 6, 30)}]
        if "leased_future" in sql:
            return [{"as_of_date": date(2026, 6, 30), "total_units": 299, "rentable": 299, "occupied": 266, "vacant": 33,
                     "available": 17, "vacant_rented": 16, "vacant_unrented": 17, "notice_rented": 0,
                     "notice_unrented": 0, "leased_future": 16, "excluded": 0}]
        if "delayed_move_ins" in sql:
            return [{"as_of_date": date(2026, 6, 30), "delayed_move_ins": 3}]
        if "'move_in'" in sql:
            return [{"move_ins": 11, "move_outs": 23}]
        if "t_occupancy_rate" in sql:
            return [{"value": 0.9359}]
        if "AS created" in sql:
            return [{"created": 92, "scheduled": 33, "toured": 19, "applied": 14}]
        if "WITH ev" in sql:
            return [{"label": "Property Website", "value": 8}]
        if "pms_CancelApplication" in sql:
            return [{"value": 11}]
        if "h_ms_lease" in sql:
            return [{"value": 8}]
        if "GROUP BY label" in sql:
            return [{"label": "Property Website", "value": 53}, {"label": "Zillow", "value": 6}]
        return []


class _Ident:
    company_id = "26136316506"
    name = "The Bromley at Brighton Crossing"
    hyly_property_id = "1865695607790353330"
    uuid = None


class TestLive:
    def test_allowlist_is_enforced(self):
        lake = wr.LakeReader(query=FakeLake())
        with pytest.raises(wr.SourceError):
            lake.ref("vendor_spend_ledger")
        with pytest.raises(wr.SourceError):
            lake.ref("t_oc_agg_occupancy_property_bckup_")
        assert lake.ref("t_contact_activity").endswith(".t_contact_activity`")

    def test_reader_is_read_only(self):
        lake = wr.LakeReader(query=FakeLake())
        with pytest.raises(wr.SourceError):
            lake.query("DELETE FROM t WHERE 1=1", [])

    def test_query_failure_is_loud(self):
        def broken(sql, params):
            raise RuntimeError("403 access denied")

        lake = wr.LakeReader(query=broken)
        with pytest.raises(wr.SourceError):
            wr.gather_live(_Ident(), "2026-06", lake, today=date(2026, 9, 14))

    def test_gather_live_builds_receipts_and_gaps(self, monkeypatch):
        monkeypatch.delenv("BIGQUERY_PROJECT_ID", raising=False)
        fake = FakeLake()
        raw = wr.gather_live(_Ident(), "2026-06", wr.LakeReader(query=fake), today=date(2026, 9, 14))
        assert raw["values"]["occupied"] == {"value": 266, "source": wr.SRC_OCCUPANCY, "as_of": "2026-06-30"}
        assert raw["values"]["leased"]["value"] == 8
        rep = wr.assemble(raw)
        assert bare_numbers(rep) == []
        gap_sections = {g["section"] for g in rep["gaps"]}
        assert {"website", "paid_search", "reputation", "spend", "listings"} <= gap_sections
        assert rep["website"]["sessions"] is None and rep["paid_search"]["impressions"] is None
        assert all("vendor_spend_ledger" not in s for s in fake.sql)
        hits = [(p, t) for p, t in wr.iter_strings(rep) if wr.banned_phrases_in(t)]
        assert hits == []

    def test_build_report_months(self, monkeypatch):
        monkeypatch.setattr(wr, "_resolve", lambda cid: _Ident())
        monkeypatch.setattr(wr, "_city_state", lambda cid: ("Brighton", "Colorado"))
        lake = wr.LakeReader(query=FakeLake())
        rep = wr.build_report("26136316506", "2026-06", today=date(2026, 9, 14), lake=lake)
        assert rep["available_months"] == ["2026-06", "2026-07", "2026-08"]
        assert rep["property"]["city"] == "Brighton"
        default = wr.build_report("26136316506", None, today=date(2026, 9, 14), lake=lake)
        assert default["month"] == "2026-08"
        with pytest.raises(wr.InvalidMonth):
            wr.build_report("26136316506", "2026-13", today=date(2026, 9, 14), lake=lake)
        with pytest.raises(wr.MonthUnavailable):
            wr.build_report("26136316506", "2026-05", today=date(2026, 9, 14), lake=lake)

    def test_property_without_hyly_id_is_unavailable(self, monkeypatch):
        class NoHyly(_Ident):
            hyly_property_id = None

        from skills import property_resolver
        monkeypatch.setattr(property_resolver, "resolve", lambda cid: NoHyly())
        with pytest.raises(wr.PropertyUnavailable):
            wr._resolve("123")


# ── Export reader ──────────────────────────────────────────────────────────

EXPORT = """Dashboard,Data Lake CSV Dashabord,,,,,,,,,
Property,Test Place (ID: 123456789),,,,,,,,,
Date Range,2026-06-01 to 2026-06-30,,,,,,,,,
,,,,,,,,,,
### Summary Metrics ###,,,,,,,,,,
Section,Metric Name,Metric Field,Data Source,Date Range,Value,Trend,,,,
Occupancy,Total Units,total_units,All,"Jun, 2026",100,disabled,y,,,
Occupancy,Available Units,available,All,"Jun, 2026",5,disabled,y,,,
Occupancy,Occupied Units,occupied,All,"Jun, 2026",90,disabled,y,,,
Lead Generation,Newly Created Leads,created_contact,All,"Jun, 2026",10,disabled,y,,,
Spend Manager,Total Spend,total_spend,All Sources,"Jun, 2026","$1,500.00 ",disabled,y,,,
Multi-Touch Attribution,Created Prospects,created_prospect,All,"Jun, 2026",12,disabled,y,,,
Google Ads,Ad Impressions,,Google Ads,"Jun, 2026",21.5K,disabled,y,,,
,,,,,,,,,,
### Rankings by Dimension ###,,,,,,,,,,
Section,Metric Name,Data Source,Dimension (Breakdown),Top / Bottom,Date Range,Rank,Label,Value,% Share,
Lead Generation,Newly Created Leads,All,Lead Gen Sources,Top,"Jun, 2026",1,Property Website,7,70.00%,y
Lead Generation,Newly Created Leads,All,Lead Gen Sources,Top,"Jun, 2026",2,Zillow,3,30.00%,y
Multi-Touch Attribution,Created Prospects,All,Influencing Sources,Top,"Jun, 2026",1,Property Website,9,60.00%,y
Multi-Touch Attribution,Toured Prospects,All,Influencing Sources,Top,"Jun, 2026",1,Zillow,2,50.00%,y
Spend Manager,Total Spend,All,Vendors,Top,"Jun, 2026",1,Zillow per month,"$1,000.00 ",66.70%,y
,,,,,,,,,,
### Ratio Cards ###,,,,,,,,,,
Section,Metric Name,Metric Field,Label Type,Value,Trend,,,,,
Conversion Analytics,Created to Leased Contacts Velocity,created_to_leased_contact_velocity,days,20.5,disabled,Y,,,,
"""


class TestExportReader:
    @pytest.mark.parametrize("text,expected", [
        ("$15,715.00 ", 15715.0), ("5.69%", 0.0569), ("21.5K", 21500.0), ("55s", 55.0), ("-12", -12.0), ("", None),
    ])
    def test_parse_number(self, text, expected):
        value, _ = wx.parse_number(text)
        assert value == (pytest.approx(expected) if expected is not None else None)

    def test_raw_from_export(self, tmp_path):
        path = tmp_path / "export.csv"
        path.write_text(EXPORT, encoding="utf-8")
        raw = wx.raw_from_export(str(path), company_id="1")
        assert raw["month"] == "2026-06" and raw["property"]["hyly_property_id"] == "123456789"
        assert raw["values"]["total_units"] == {"value": 100, "source": wr.SRC_OCCUPANCY, "as_of": "2026-06-30"}
        assert raw["values"]["ads_impressions"]["approx"] is True
        assert raw["values"]["days_created_to_leased"]["value"] == 20.5
        assert raw["vendors"][0]["name"] == "Zillow" and raw["vendors"][0]["spend"]["value"] == 1000
        assert raw["first_touch"]["created__complete"] is True
        sources = {s["name"]: s for s in raw["influence"]["sources"]}
        # Toured shares sum to 50%: an absent source is unknown, not zero.
        assert sources["Property Website"]["toured"] is None
        # Created shares sum to 60%: likewise unknown.
        assert sources["Zillow"]["created"] is None
        rep = wr.assemble(raw)
        assert bare_numbers(rep) == []
