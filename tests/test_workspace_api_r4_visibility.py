"""Round 4 visibility: the audit on the main page — prompts, fan-out, what we're writing.

Offline; BigQuery, HubDB and the content briefs are mocked.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent / "webhook-server"))
sys.path.insert(0, str(TESTS))

import workspace_contract as contract  # noqa: E402
from skills import workspace_inbox as wi  # noqa: E402
from skills import workspace_visibility as wvis  # noqa: E402

CONTENT_ROWS = [
    {"id": "b1", "title": "Parking at LYV Broadway", "keyword": "parking lyv broadway", "status": "draft_ready",
     "item_id": "content_brief:b1"},
    {"id": "b2", "title": "Pet policy", "keyword": "pet policy", "status": "published", "item_id": None},
]


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    def _no_network(*a, **k):
        raise RuntimeError("network disabled in workspace tests")
    monkeypatch.setattr("requests.sessions.Session.request", _no_network)
    monkeypatch.setattr(wvis, "build_content", lambda ctx, internal=True: {"rows": [dict(r) for r in CONTENT_ROWS]})


def _ctx():
    return wi.PropertyContext("123", "u-123", "LYV Broadway", {"uuid": "u-123", "seo_tier": "premium"})


def _geo_query(sql, params):
    if "geo_prompts" in sql:
        return [
            {"prompt_id": "p1", "prompt_text": "Is there parking at LYV Broadway?", "topic": "Parking",
             "intent": "decide", "engine": "chatgpt", "named": True, "cited": False},
            {"prompt_id": "p1", "prompt_text": "Is there parking at LYV Broadway?", "topic": "Parking",
             "intent": "decide", "engine": "perplexity", "named": False, "cited": False},
            {"prompt_id": "p2", "prompt_text": "Best apartments in Carrollton", "topic": "Area",
             "intent": "explore", "engine": None, "named": None, "cited": None},
        ]
    if "geo_fanout" in sql:
        return [{"query_text": "lyv broadway parking garage cost", "engine": "perplexity", "n": 4},
                {"query_text": "carrollton apartments with pools", "engine": "chatgpt", "n": 2}]
    if "geo_plans" in sql:
        return [{"plan_id": "pl1", "prompt_ids": ["p1"], "status": "pending_approval", "diagnosis": "no_answer_page"},
                {"plan_id": "pl2", "prompt_ids": ["p2"], "status": "rejected", "diagnosis": "page_not_cited"}]
    raise AssertionError(sql)


class TestGeoAudit:
    def test_prompts_fanout_and_writing_from_geo_tables(self, monkeypatch):
        import bigquery_client
        monkeypatch.setattr(bigquery_client, "_dataset", lambda: "ds")
        monkeypatch.setattr(bigquery_client, "query", _geo_query)
        gaps = []
        out = wvis.build_audit(_ctx(), gaps, geo_present=True, internal=True)
        p1 = next(p for p in out["prompts"] if p["id"] == "p1")
        assert p1["engines"] == {"ChatGPT": {"named": True, "cited": False},
                                 "Perplexity": {"named": False, "cited": False}}
        assert (p1["topic"], p1["intent"]) == ("Parking", "decide")
        assert next(p for p in out["prompts"] if p["id"] == "p2")["engines"] == {}
        parking = out["fanout"][0]
        assert parking == {"query": "lyv broadway parking garage cost", "engine": "Perplexity", "count": 4,
                           "content": {"item_id": "content_brief:b1", "title": "Parking at LYV Broadway",
                                       "status": "draft_ready"}}
        assert out["fanout"][1]["content"] is None
        titles = {w["title"]: w for w in out["writing"]}
        assert titles["Answer page for “Is there parking at LYV Broadway?”"]["status"] == "in_review"
        assert not any("Best apartments" in t for t in titles)            # rejected plans are not writing
        brief = titles["Parking at LYV Broadway"]
        assert brief == {"title": "Parking at LYV Broadway", "status": "drafted", "item_id": "content_brief:b1",
                         "answers": ["parking lyv broadway", "lyv broadway parking garage cost"]}
        assert titles["Pet policy"]["status"] == "published"

    def test_geo_audit_failure_is_a_gap(self, monkeypatch):
        import bigquery_client
        monkeypatch.setattr(bigquery_client, "_dataset", lambda: "ds")

        def boom(sql, params):
            raise RuntimeError("Not found: geo_fanout")
        monkeypatch.setattr(bigquery_client, "query", boom)
        gaps = []
        out = wvis.build_audit(_ctx(), gaps, geo_present=True, internal=True)
        assert out["prompts"] == [] and out["fanout"] == []
        assert [w["title"] for w in out["writing"]] == ["Parking at LYV Broadway", "Pet policy"]
        assert any(g.get("field") == "prompts" for g in gaps)


class TestAiMentionsFallback:
    def test_prompts_from_the_audit_detail(self, monkeypatch):
        import config
        import hubdb_helpers
        monkeypatch.setattr(config, "HUBDB_AI_MENTIONS_TABLE_ID", "t-ai", raising=False)
        detail = {"chatgpt": {"prompts": [{"prompt": "best apartments in carrollton", "cited": True},
                                          {"prompt": "lyv broadway reviews", "cited": False}]},
                  "perplexity": {"prompts": [{"prompt": "best apartments in carrollton", "cited": False}]}}
        monkeypatch.setattr(hubdb_helpers, "read_rows", lambda t, filters=None, limit=30: [
            {"scanned_at": 1, "detail_json": "{}"}, {"scanned_at": 2, "detail_json": json.dumps(detail)}])
        gaps = []
        out = wvis.build_audit(_ctx(), gaps, geo_present=False, internal=True)
        best = next(p for p in out["prompts"] if p["text"] == "best apartments in carrollton")
        assert best["engines"] == {"ChatGPT": {"named": None, "cited": True},
                                   "Perplexity": {"named": None, "cited": False}}
        assert best["topic"] is None and len(best["id"]) == 12
        assert out["fanout"] == []
        fields = {g.get("field") for g in gaps}
        assert {"fanout", "prompts.topic"} <= fields

    def test_neither_source_is_gaps(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "HUBDB_AI_MENTIONS_TABLE_ID", "", raising=False)
        monkeypatch.setattr(wvis, "build_content", lambda ctx, internal=True: {"rows": []})
        gaps = []
        out = wvis.build_audit(_ctx(), gaps, geo_present=False, internal=True)
        assert out == {"prompts": [], "fanout": [], "writing": []}
        assert {"prompts", "writing", "fanout"} <= {g.get("field") for g in gaps}


class TestVisibilityPayload:
    def test_build_visibility_carries_the_audit_and_matches_the_contract(self, monkeypatch):
        import bigquery_client
        from skills import workspace_property_overview as wpo
        monkeypatch.setattr(bigquery_client, "is_bigquery_configured", lambda: False)
        monkeypatch.setattr(wpo, "ai_snapshot", lambda ctx, gaps: None)
        monkeypatch.setattr(wvis, "ai_mentions_prompts", lambda ctx, gaps: [
            {"id": "x1", "text": "lyv broadway reviews", "topic": None, "intent": None,
             "engines": {"ChatGPT": {"named": None, "cited": False}}}])
        body = wvis.build_visibility(_ctx(), internal=True)
        contract.assert_shape(body, "visibility")
        assert body["prompts"][0]["text"] == "lyv broadway reviews"
        assert [w["status"] for w in body["writing"]] == ["drafted", "published"]
        assert contract.numbers_without_source(body) == []
