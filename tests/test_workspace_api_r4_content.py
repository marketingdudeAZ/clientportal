"""Round 4 content: three statuses, rows only once a draft exists, and the
review-panel fields.

Offline; HubDB and the SEO entitlement are mocked.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent / "webhook-server"))
sys.path.insert(0, str(TESTS))

import workspace_contract as contract  # noqa: E402
from skills import workspace_inbox as wi  # noqa: E402
from skills import workspace_visibility as wvis  # noqa: E402

BRIEFS = [
    {"brief_id": "b1", "hub_keyword": "parking in tampa", "status": "generated", "h1": "Parking at Parkline",
     "generated_at": "2026-09-01T00:00:00Z"},
    {"brief_id": "b2", "hub_keyword": "pet policy", "status": "in_production"},
    {"brief_id": "b3", "hub_keyword": "tampa lofts", "status": "complete"},
    {"brief_id": "b4", "hub_keyword": "rooftop pool", "status": "queued"},
    {"brief_id": "b5", "hub_keyword": "gym hours", "status": ""},
]


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    import config
    import hubdb_helpers
    import seo_entitlement

    def _no_network(*a, **k):
        raise RuntimeError("network disabled in workspace tests")
    monkeypatch.setattr("requests.sessions.Session.request", _no_network)
    monkeypatch.setattr(config, "HUBDB_CONTENT_BRIEFS_TABLE_ID", "t-b", raising=False)
    monkeypatch.setattr(seo_entitlement, "has_feature", lambda tier, f: True)
    monkeypatch.setattr(hubdb_helpers, "read_rows", lambda t, filters=None, limit=500: [dict(b) for b in BRIEFS])


def _ctx():
    return wi.PropertyContext("123", "u-123", "Parkline", {"uuid": "u-123", "seo_tier": "premium"})


def test_three_statuses_and_no_row_without_a_draft():
    body = wvis.build_content(_ctx(), internal=False)
    contract.assert_shape(body, "content")
    assert {r["id"]: r["status"] for r in body["rows"]} == {"b1": "draft_ready", "b2": "in_review",
                                                            "b3": "published"}
    assert body["counts"] == {"recommendations": 1, "published": 1, "in_review": 1}
    assert "not_started" not in contract.SHAPES["content"]["rows"][0]["status"].values


def test_review_panel_fields():
    rows = {r["id"]: r for r in wvis.build_content(_ctx(), internal=False)["rows"]}
    b1 = rows["b1"]
    assert b1["why"] == {"text": "Written for renters searching “parking in tampa”.",
                         "receipts": [{"label": "Brief generated for “parking in tampa”", "source": "content_briefs",
                                       "as_of": "2026-09-01T00:00:00Z"}]}
    assert b1["for_whom"] == {"text": "Renters searching for “parking in tampa”", "questions": ["parking in tampa"]}
    assert b1["approving_does"] == [
        {"label": "SEO content task opened in ClickUp", "owner": "RPM Digital", "when": "When you approve"},
        {"label": "Account manager task logged in HubSpot", "owner": "RPM Digital", "when": "After you approve"}]
    assert rows["b2"]["approving_does"] == [] and rows["b3"]["approving_does"] == []


def test_screen_and_approval_item_describe_approval_the_same_way():
    items = wi._content_briefs(_ctx(), [], None)
    item = wi.enrich(next(i for i in items if i["id"] == "content_brief:b1"), _ctx())
    row = next(r for r in wvis.build_content(_ctx(), internal=False)["rows"] if r["id"] == "b1")
    assert item["approving_does"] == row["approving_does"]
    assert item["for_whom"] == row["for_whom"]
