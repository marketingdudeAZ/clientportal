"""AI visibility: the vendor's numbers, our history, and the honest gaps.

The payloads here are real responses, trimmed. That matters: the connector was
written against measured shapes rather than a guess at an API, so these tests
pin the normalization a live key will have to keep satisfying.
"""
from __future__ import annotations

import sys
import types
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "webhook-server"))
sys.path.insert(0, str(ROOT))

import searchable_client as sc  # noqa: E402
from skills import ai_visibility as av  # noqa: E402

# --- real payloads (trimmed) -----------------------------------------------

PROJECTS = {"count": 1, "projects": [{
    "id": "842c7c79-61ab-4b1d-8e3f-d895eee482e1", "name": "Vitriapartments",
    "domain": "vitriapartments.com", "isActive": True,
    "dataAvailableFrom": "2026-09-14T18:36:03.268Z"}]}

VISIBILITY = {
    "group_by": "platform",
    "summary": {"visibilityScore": 36.7, "scoreChange": 16.7,
                "scoreChangePeriod": "30d", "totalResponses": 60,
                "totalMentions": 322, "totalCitations": 134,
                "responseMentionRate": 28.3},
    "platforms": [
        {"platform": "chatgpt", "responses": 20, "responsesWithBrand": 7,
         "citations": 46, "brandCitations": 2, "visibilityRate": 35},
        {"platform": "google-ai-overview", "responses": 20, "responsesWithBrand": 3,
         "citations": 64, "brandCitations": 0, "visibilityRate": 15},
        {"platform": "Perplexity", "responses": 20, "responsesWithBrand": 7,
         "citations": 24, "brandCitations": 2, "visibilityRate": 35}],
    "trend": [{"date": "2026-09-14", "score": 20}, {"date": "2026-09-17", "score": 36.7}],
    "availableFilters": {"topics": [{"id": "1", "name": "Apartment Amenities"},
                                    {"id": "2", "name": "Tour Scheduling"}]},
    "dateRange": {"from": "2026-08-18T23:44:35.695Z", "to": "2026-09-17T23:44:35.695Z"},
}

OPPORTUNITIES = {"opportunities": [
    {"id": "o1", "title": "Missing visibility in \"Apartment Amenities\"",
     "description": "Your brand doesn't appear in any AI responses across 3 prompts.",
     "source": "mentions", "category": "missing_visibility", "impact": "high",
     "actionType": "analyze", "status": "active", "createdAt": "2026-09-17T00:58:29Z"},
    {"id": "o2", "title": "Counter \"additional fees\" perception",
     "description": "AI models flag fees as a weakness.", "source": "sentiment",
     "category": "negative_sentiment", "impact": "high", "actionType": "fix",
     "status": "active", "createdAt": "2026-09-17T00:58:29Z"}]}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    sc.clear_cache()
    monkeypatch.delenv("SEARCHABLE_API_KEY", raising=False)
    yield
    sc.clear_cache()


class TestNormalization:
    def test_visibility_keeps_the_numbers_a_client_would_see(self):
        out = sc.normalize_visibility(VISIBILITY)
        assert out["score"] == 36.7 and out["score_change"] == 16.7
        assert len(out["engines"]) == 3
        chatgpt = [e for e in out["engines"] if e["engine"] == "chatgpt"][0]
        assert chatgpt["visibility_rate"] == 35 and chatgpt["responses_with_brand"] == 7
        assert out["topics"] == ["Apartment Amenities", "Tour Scheduling"]
        assert out["trend"][-1]["score"] == 36.7

    def test_an_empty_or_odd_payload_is_none_not_a_zero(self):
        """A property with no reading must never render as a score of zero."""
        assert sc.normalize_visibility({}) is None
        assert sc.normalize_visibility(None) is None
        assert sc.normalize_visibility("nope") is None

    def test_opportunities_carry_impact_and_source(self):
        out = sc.normalize_opportunities(OPPORTUNITIES)
        assert len(out) == 2
        assert {o["impact"] for o in out} == {"high"}
        assert {o["source"] for o in out} == {"mentions", "sentiment"}

    @pytest.mark.parametrize("raw,expected", [
        ("https://www.vitriapartments.com/", "vitriapartments.com"),
        ("HTTP://VitriApartments.com", "vitriapartments.com"),
        ("vitriapartments.com/floorplans", "vitriapartments.com"),
        ("  www.vitriapartments.com ", "vitriapartments.com"),
        (None, ""),
    ])
    def test_domains_match_however_they_were_typed(self, raw, expected):
        """HubSpot stores websites however whoever typed them felt that day."""
        assert sc._clean_domain(raw) == expected


class TestTransportPolicy:
    def test_unconfigured_is_a_reason_not_an_exception(self):
        payload, reason = sc._get("/anything")
        assert payload is None and "not connected" in reason

    def test_project_lookup_without_a_website_says_so(self):
        project, reason = sc.project_for_domain("")
        assert project is None and "no website" in reason

    def test_an_unmeasured_domain_reads_as_not_measured_yet(self, monkeypatch):
        monkeypatch.setattr(sc, "list_projects",
                            lambda: (sc.normalize_projects(PROJECTS), None))
        out = sc.for_property("someotherproperty.com")
        assert out["measured"] is False
        assert "not measured for AI visibility yet" in out["reason"]

    def test_a_measured_domain_returns_the_reading(self, monkeypatch):
        monkeypatch.setattr(sc, "list_projects",
                            lambda: (sc.normalize_projects(PROJECTS), None))
        monkeypatch.setattr(sc, "visibility",
                            lambda pid, days=30: (sc.normalize_visibility(VISIBILITY), None))
        monkeypatch.setattr(sc, "opportunities",
                            lambda pid: (sc.normalize_opportunities(OPPORTUNITIES), None))
        out = sc.for_property("https://www.vitriapartments.com")
        assert out["measured"] is True
        assert out["visibility"]["score"] == 36.7
        assert len(out["opportunities"]) == 2

    def test_from_payload_builds_the_same_shape_without_a_key(self):
        """Rules and tests work before the API key exists; only transport changes."""
        out = sc.from_payload(PROJECTS, VISIBILITY, OPPORTUNITIES)
        assert out["measured"] is True
        assert out["visibility"]["score"] == 36.7
        assert out["domain"] == "vitriapartments.com"


class TestPerProperty:
    def _identity(self, **over):
        data = {"uuid": "u-1", "name": "Vitri Apartments",
                "domain": "vitriapartments.com", "company_id": "555"}
        data.update(over)
        return types.SimpleNamespace(to_dict=lambda: dict(data))

    def test_a_property_with_no_website_gaps_rather_than_scoring_zero(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "skills.property_resolver",
                            types.SimpleNamespace(resolve=lambda cid: self._identity(domain="", website="")))
        from skills import property_resolver as pr
        monkeypatch.setattr(pr, "resolve", lambda cid: self._identity(domain="", website=""))
        out = av.for_property("555")
        assert out["measured"] is False
        assert "no website" in out["gaps"][0]["message"]

    def test_a_measured_property_carries_engines_and_receipts(self, monkeypatch):
        from skills import property_resolver as pr
        monkeypatch.setattr(pr, "resolve", lambda cid: self._identity())
        monkeypatch.setattr(sc, "for_property", lambda domain, days=30: {
            "measured": True, "project_id": "p1",
            "visibility": sc.normalize_visibility(VISIBILITY),
            "opportunities": sc.normalize_opportunities(OPPORTUNITIES)})
        out = av.for_property("555")
        assert out["measured"] is True
        assert out["score"]["value"] == 36.7 and out["score"]["source"] == "searchable"
        assert {e["engine"] for e in out["engines"]} == {
            "ChatGPT", "Google AI Overview", "Perplexity"}
        assert len(out["vendor_opportunities"]) == 2

    def test_engine_names_are_normalized_so_history_does_not_fork(self):
        assert av.engine_label("google-ai-overview") == "Google AI Overview"
        assert av.engine_label("Perplexity") == "Perplexity"
        assert av.engine_label("chatgpt") == "ChatGPT"
        assert av.engine_label("some-new-engine") == "some-new-engine"


class TestHistoryIsOurs:
    """The trend is the product. It must survive a change of vendor."""

    def _reading(self):
        return {"measured": True, "uuid": "u-1", "company_id": "555",
                "domain": "vitriapartments.com",
                "score": {"value": 36.7},
                "engines": [{"engine": "ChatGPT", "visibility_rate": 35,
                             "responses": 20, "responses_with_brand": 7,
                             "citations": 46}],
                "gaps": []}

    def test_a_snapshot_writes_one_row_per_engine_plus_the_overall(self, monkeypatch):
        written = {}
        fake = types.SimpleNamespace(
            is_bigquery_configured=lambda: True,
            insert_rows=lambda table, rows: written.update(table=table, rows=rows))
        monkeypatch.setitem(sys.modules, "bigquery_client", fake)
        out = av.snapshot("555", on=date(2026, 9, 17), reading=self._reading())
        assert out["written"] == 2
        assert written["table"] == "ai_visibility_daily"
        engines = {r["engine"] for r in written["rows"]}
        assert engines == {"all", "ChatGPT"}
        overall = [r for r in written["rows"] if r["engine"] == "all"][0]
        assert overall["visibility_score"] == 36.7
        assert overall["reading_date"] == "2026-09-17"
        assert overall["vendor"] == "searchable"

    def test_an_unmeasured_property_writes_nothing(self, monkeypatch):
        fake = types.SimpleNamespace(is_bigquery_configured=lambda: True,
                                     insert_rows=lambda *a: pytest.fail("wrote anyway"))
        monkeypatch.setitem(sys.modules, "bigquery_client", fake)
        out = av.snapshot("555", on=date(2026, 9, 17),
                          reading={"measured": False, "gaps": []})
        assert out["written"] == 0 and out["skipped"] is True

    def test_a_failed_write_is_reported_not_swallowed(self, monkeypatch):
        """A missed day must be visible, or a hole in the trend reads as a flat line."""
        def boom(table, rows):
            raise RuntimeError("bigquery is down")

        fake = types.SimpleNamespace(is_bigquery_configured=lambda: True,
                                     insert_rows=boom)
        monkeypatch.setitem(sys.modules, "bigquery_client", fake)
        out = av.snapshot("555", on=date(2026, 9, 17), reading=self._reading())
        assert out["written"] == 0
        assert any("missing from the trend" in g["message"] for g in out["gaps"])

    def test_history_reads_our_warehouse_not_the_vendor(self, monkeypatch):
        captured = {}

        def fake_query(sql, params):
            captured["sql"] = sql
            return [{"reading_date": date(2026, 9, 14), "visibility_score": 20.0,
                     "visibility_rate": None},
                    {"reading_date": date(2026, 9, 17), "visibility_score": 36.7,
                     "visibility_rate": None}]

        fake = types.SimpleNamespace(is_bigquery_configured=lambda: True,
                                     query=fake_query, _dataset=lambda: "portal_bq")
        monkeypatch.setitem(sys.modules, "bigquery_client", fake)
        out = av.history("u-1", days=180)
        assert out["source"] == "portal_warehouse"
        assert [p["value"] for p in out["points"]] == [20.0, 36.7]
        assert "ai_visibility_daily" in captured["sql"]

    def test_no_recorded_readings_says_so(self, monkeypatch):
        fake = types.SimpleNamespace(is_bigquery_configured=lambda: True,
                                     query=lambda sql, params: [],
                                     _dataset=lambda: "portal_bq")
        monkeypatch.setitem(sys.modules, "bigquery_client", fake)
        out = av.history("u-1")
        assert out["points"] == []
        assert "No readings have been recorded" in out["gaps"][0]["message"]
