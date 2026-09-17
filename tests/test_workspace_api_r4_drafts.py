"""Round 4 follow-up: items carry the draft the Review panel shows.

Only drafts a source already stores. Offline; HubDB and the SEO entitlement are
mocked, and `requests` is disabled, so nothing can be generated.
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
from skills import workspace_approvals as wapp  # noqa: E402
from skills import workspace_inbox as wi  # noqa: E402

OUTLINE = [
    {"h2": "Can I bring my pet to Parkline?", "h3_list": ["Breed rules", "Pet fees"],
     "paa_answered": ["Are there weight limits?"]},
    {"h2": "Dog park and pet wash", "h3_list": []},
]
BRIEF = {"brief_id": "b1", "hub_keyword": "pet friendly apartments tampa", "status": "generated",
         "h1": "Pets at Parkline", "meta_description": "Everything about living with pets at Parkline.",
         "outline_json": json.dumps(OUTLINE), "schema_types": "FAQPage, Article", "target_word_count": 1500,
         "generated_at": "2026-09-01T00:00:00Z"}
BARE = {"brief_id": "b2", "hub_keyword": "tampa lofts", "status": "generated", "h1": "Tampa lofts",
        "meta_description": "", "outline_json": ""}


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    import config
    import seo_entitlement

    def _no_network(*a, **k):
        raise RuntimeError("network disabled in workspace tests")
    monkeypatch.setattr("requests.sessions.Session.request", _no_network)
    monkeypatch.setattr(config, "HUBDB_CONTENT_BRIEFS_TABLE_ID", "t-b", raising=False)
    monkeypatch.setattr(seo_entitlement, "has_feature", lambda tier, f: True)


def _rows(monkeypatch, rows):
    import hubdb_helpers
    monkeypatch.setattr(hubdb_helpers, "read_rows", lambda t, filters=None, limit=500: [dict(r) for r in rows])


def _ctx():
    return wi.PropertyContext("123", "u-123", "Parkline", {"uuid": "u-123", "seo_tier": "premium"})


def _collect(monkeypatch, rows):
    _rows(monkeypatch, rows)
    return wi.collect(_ctx(), sources=["content_brief"], with_history=False)


class TestFilled:
    def test_brief_record_becomes_the_draft(self, monkeypatch):
        items, gaps = _collect(monkeypatch, [BRIEF])
        it = wi.view_item(items[0], internal=False)
        contract.assert_shape(it, "item")
        assert it["draft"]["kind"] == "faq" and it["draft"]["title"] == "Pets at Parkline"
        assert it["draft"]["body"] == (
            "Everything about living with pets at Parkline.\n\n"
            "Can I bring my pet to Parkline?\n- Breed rules\n- Pet fees\nAnswers: Are there weight limits?\n\n"
            "Dog park and pet wash")
        assert not [g for g in gaps if g.get("field") == "draft"]

    def test_page_kind_and_unverifiable_numbers_dropped(self):
        row = dict(BRIEF, schema_types="Article",
                   meta_description="Parkline is 12 minutes from downtown. Pets are welcome.")
        draft = wi.brief_draft(row)
        assert draft["kind"] == "page"
        assert draft["body"].startswith("Pets are welcome.")
        assert "12 minutes" not in draft["body"]

    def test_fixture_drafts_match_the_contract_shape(self):
        items = json.loads((TESTS / "fixtures" / "workspace" / "approval_items.json").read_text())
        items = items if isinstance(items, list) else items["items"]
        drafts = [i["draft"] for i in items if i.get("draft")]
        assert drafts
        for d in drafts:
            assert set(d) == set(contract.ITEM_DRAFT)
            assert d["kind"] in contract.ITEM_DRAFT["kind"].values


class TestNullAndGap:
    def test_brief_without_stored_fields_has_no_draft_and_a_gap(self, monkeypatch):
        items, gaps = _collect(monkeypatch, [BARE])
        assert items[0]["draft"] is None
        assert any(g.get("field") == "draft" and "b2" in g["message"] for g in gaps)
        contract.assert_shape(wi.view_item(items[0], internal=False), "item")

    def test_non_brief_items_default_to_null(self):
        assert wi._new_item("hubdb_rec", "1", "Step down Apartments.com")["draft"] is None

    def test_the_api_always_sends_the_key(self, monkeypatch):
        items, _ = _collect(monkeypatch, [BRIEF, BARE])
        for it in items:
            for internal in (True, False):
                assert "draft" in wi.view_item(it, internal=internal)
        rec = wi.finalize(wi._new_item("hubdb_rec", "1", "Step down Apartments.com"))
        assert "draft" in wi.view_item(rec, internal=False)

    def test_contract_rejects_a_malformed_draft(self):
        bad = {"kind": "memo", "title": None, "body": "x"}
        assert contract.check(bad, contract.ITEM_DRAFT)
        assert contract.check({"kind": "faq", "title": None}, contract.ITEM_DRAFT)

    def test_vendor_items_without_a_draft_name_the_gap(self):
        vendor = wi._new_item("call_prep", "r1", "Send the Apartments.com rate email", needs_approval=True)
        cost = wi._new_item("hubdb_rec", "r2", "Cut paid search", needs_approval=True)
        gaps = wapp.draft_gaps([("vendor", vendor), ("cost", cost)])
        assert len(gaps) == 1 and gaps[0]["field"] == "draft" and "rate email" in gaps[0]["message"]
        vendor["draft"] = {"kind": "email", "title": "x", "body": "y"}
        assert wapp.draft_gaps([("vendor", vendor)]) == []


class TestClientVisibility:
    def test_clean_draft_is_visible_to_clients(self, monkeypatch):
        items, _ = _collect(monkeypatch, [BRIEF])
        assert wi.view_item(items[0], internal=False)["draft"] is not None
        assert wi.view_item(items[0], internal=True)["draft"] is not None

    def test_high_severity_draft_is_hidden_from_clients_only(self, monkeypatch):
        outline = OUTLINE + [{"h2": "Adults only community with no kids", "h3_list": []}]
        items, gaps = _collect(monkeypatch, [dict(BRIEF, outline_json=json.dumps(outline))])
        item = items[0]
        assert item["_fh_high"] is True
        assert "Adults only" in wi.view_item(item, internal=True)["draft"]["body"]
        client = wi.view_item(item, internal=False)
        assert client["draft"] is None
        assert "fair_housing_review" not in client
        assert any(g.get("field") == "fair_housing_review" for g in gaps)
