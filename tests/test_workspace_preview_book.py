"""The preview book: 35 properties, each internally consistent.

Round 4 bugs 1 and 2: the preview served one property's detail data for every
property with only the name swapped, so drafts, emails and content opened the
wrong property. The preview now builds every screen from each property's own
facts. These tests walk every property's screens and assert that every linked
item, draft and report names that property, that dashboard totals agree with the
35-property book, and that the Round 4 rules hold (no pacing in Approvals, no
photo-shoot asks, plan notes never contradict a pending recommendation).
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "workspace"

_spec = importlib.util.spec_from_file_location("preview_workspace", ROOT / "scripts" / "preview_workspace.py")
pw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pw)

PROPS = pw.props()
IDS = [p["company_id"] for p in PROPS]
OWNERS = {"RPM Digital", "vendor", "you"}
CATEGORIES = {"cost", "vendor", "content", "creative", "compliance"}


def _text(it: dict) -> str:
    parts = [it.get("title"), it.get("found"), it.get("expect"), it.get("if_skip"),
             (it.get("why") or {}).get("text"), (it.get("for_whom") or {}).get("text"),
             (it.get("draft") or {}).get("title"), (it.get("draft") or {}).get("body")]
    parts += [s["label"] for s in it.get("approving_does") or []]
    return " ".join(p for p in parts if p)


def _assert_item_names_property(item_id: str, p: dict, where: str):
    it = pw.item(item_id)
    assert it is not None, f"{where}: {item_id} doesn't resolve"
    assert it["company_id"] == p["company_id"], f"{where}: {item_id} belongs to {it['company_id']}, not {p['name']}"
    assert p["name"] in it["title"], f"{where}: {item_id} title doesn't name {p['name']}"
    others = [q["name"] for q in PROPS if q["company_id"] != p["company_id"] and q["name"] in _text(it)]
    assert not others, f"{where}: {item_id} mentions other properties {others}"
    if it.get("draft"):
        assert p["name"] in it["draft"]["title"] + it["draft"]["body"], f"{where}: draft for {item_id} doesn't name {p['name']}"


def test_book_is_35_properties_and_me_agrees():
    assert len(PROPS) == 35
    assert len(set(IDS)) == 35
    me = pw.me_data()
    assert [c["company_id"] for c in me["companies"]] == IDS


@pytest.mark.parametrize("company_id", IDS)
def test_every_link_on_a_property_screen_is_that_propertys_own(company_id):
    p = pw.prop(company_id)
    ov = pw.property_overview(company_id)
    assert ov["name"] == p["name"]
    for k, link in ov["links"].items():
        assert company_id in link, f"overview link {k} doesn't point at {p['name']}"
    if ov["recommended_action"]:
        _assert_item_names_property(ov["recommended_action"]["item_id"], p, "recommended action")
    if ov["draft_email"]:
        _assert_item_names_property(ov["draft_email"]["item_id"], p, "draft email")
        assert p["name"] in ov["draft_email"]["subject"]
    for f in ov["findings"]:
        if f.get("item_id"):
            _assert_item_names_property(f["item_id"], p, "finding")
        assert not [q["name"] for q in PROPS if q["company_id"] != company_id and q["name"] in f["text"]]
    for step in ov["loop"]:
        if step.get("item_id"):
            _assert_item_names_property(step["item_id"], p, "loop step")

    for row in pw.content(company_id)["rows"]:
        _assert_item_names_property(row["item_id"], p, "content row")

    vis = pw.visibility(company_id)
    for w in vis["writing"]:
        _assert_item_names_property(w["item_id"], p, "what we're writing")
    for f in vis["fanout"]:
        if f["content"]:
            _assert_item_names_property(f["content"]["item_id"], p, "fan-out content")

    for a in pw.creative(company_id)["assets"]:
        assert a["name"].startswith(p["slug"] + "-"), f"asset {a['name']} isn't {p['name']}'s"

    for r in pw.requests_recent_data(company_id)["recent"]:
        assert p["name"] in r["title"]

    for note in pw.media_plan(company_id)["notes"]:
        assert not [q["name"] for q in PROPS if q["company_id"] != company_id and q["name"] in note]


def test_every_report_fixture_names_its_property():
    reports = sorted(FIXTURES.glob("report_*.json"))
    assert reports
    for path in reports:
        d = json.loads(path.read_text(encoding="utf-8"))
        p = pw.prop(str(d["property"]["company_id"]))
        assert p is not None, f"{path.name}: company {d['property']['company_id']} isn't in the preview book"
        assert d["property"]["name"] == p["name"], f"{path.name} names {d['property']['name']}, the book says {p['name']}"
        others = [q["name"] for q in PROPS if q["company_id"] != p["company_id"] and q["name"] in json.dumps(d)]
        assert not others, f"{path.name} mentions other properties {others}"


def test_every_approval_row_opens_its_own_propertys_item():
    for row in pw.approvals()["batch"]["rows"]:
        p = pw.prop(row["company_id"])
        assert row["property"] == p["name"]
        _assert_item_names_property(row["item_id"], p, "approval row")


def test_dashboard_totals_agree_with_the_book():
    d = pw.dashboard()
    known = [p for p in PROPS if p["occupied"] is not None]
    assert len(d["health_tiles"]) == len(d["properties"]) == d["loop_status"]["property_count"] == 35
    assert set(t["company_id"] for t in d["health_tiles"]) == set(IDS)
    k = d["kpis"]
    assert list(k) == ["occupancy", "units_to_lease_90d", "leases_this_month", "cost_per_lease", "ai_visibility", "actions_taken", "waiting_on_you"]
    assert "identified_savings" not in k and "overspend_per_year" not in json.dumps(d)
    assert k["occupancy"]["value"] == round(sum(p["occupied"] for p in known) / sum(p["units"] for p in known), 3)
    assert k["units_to_lease_90d"]["value"] == sum(p["units_to_lease_90d"] for p in known)
    assert k["leases_this_month"]["value"] == sum(p["leases_this_month"] for p in known)
    assert k["cost_per_lease"]["value"] == round(sum(p["spend_last_month"] for p in known) / sum(p["leases_last_month"] for p in known))
    assert k["waiting_on_you"]["value"] == pw.approvals()["waiting"] == len(pw.approvals()["batch"]["rows"]) == len(d["waiting"])
    for row in d["properties"]:
        p = pw.prop(row["company_id"])
        assert (row["occupancy"] or {}).get("value") == (None if p["occupied"] is None else round(p["occupied"] / p["units"], 3))


def test_approvals_have_no_pacing_and_group_by_what_is_approved():
    a = pw.approvals()
    assert not [i for i in a["interrupts"] if i["kind"] == "pacing"]
    assert "pause" not in json.dumps(a).lower()
    assert {r["category"] for r in a["batch"]["rows"]} <= CATEGORIES
    for row in a["batch"]["rows"]:
        it = pw.item(row["item_id"])
        assert it["why"] and it["why"]["text"] and isinstance(it["why"]["receipts"], list)
        assert it["for_whom"] and it["for_whom"]["text"] and isinstance(it["for_whom"]["questions"], list)
        assert it["approving_does"] and all(s["owner"] in OWNERS and s["label"] and s["when"] for s in it["approving_does"])
        if it["category"] == "content":
            assert it["for_whom"]["questions"], "content items name the renter questions they answer"


def test_creative_recommendations_never_ask_for_a_photo_shoot():
    for it in pw.all_items():
        text = _text(it).lower()
        assert not any(w in text for w in pw.PHOTO_SHOOT_WORDS), f"{it['id']} asks for a photo shoot"
    maddux = [it for it in pw.all_items() if it["category"] == "creative" and it["status"] == "to_do"]
    assert maddux and all("existing assets" in it["title"] for it in maddux)


def test_monthly_fair_housing_review_item():
    items = [it for it in pw.all_items() if it["source"] == "fair_housing_review"]
    assert items, "the preview carries a monthly Fair Housing review"
    for it in items:
        r = it["review"]
        p = pw.prop(it["company_id"])
        assert re.fullmatch(rf"Your monthly Fair Housing review for {re.escape(p['name'])} found {len(r['findings'])} items?", it["title"])
        assert it["category"] == "compliance"
        for k in ("property", "run_at", "next_run", "pages_checked", "assets_checked", "findings"):
            assert k in r
        for f in r["findings"]:
            assert f["kind"] in {"copy", "image"} and f["location"] and f["excerpt"] and f["reason"] and f["suggested_fix"]
    clean = [a for a in pw.dashboard()["activity"] if a["text"].startswith("Monthly Fair Housing review — no issues")]
    assert clean and all(a["company_id"] not in {it["company_id"] for it in items} for a in clean)


@pytest.mark.parametrize("company_id", IDS)
def test_media_plan_mix_and_notes_never_contradict_pending(company_id):
    p = pw.prop(company_id)
    plan = pw.media_plan(company_id)
    assert {c["mode"] for c in plan["channels"]} == {"always_on", "flighted"}
    for c in plan["channels"]:
        assert c["annual"] == sum(c["monthly"])
        if c["mode"] == "always_on":
            assert len(set(c["monthly"])) == 1, f"{c['channel']} is always-on and must be flat"
    pending = [it for it in pw.all_items() if it["company_id"] == company_id and it["status"] == "to_do"]
    pending_channels = {"Apartments.com"} if any("Apartments.com" in it["title"] for it in pending) else set()
    for note in plan["notes"]:
        for ch in pending_channels:
            if ch in note:
                assert "waits on your decision" in note, f"{p['name']}: note contradicts a pending {ch} recommendation: {note!r}"
                assert not re.search(r"all year|keep|continuous|stays? flat", note, re.I), note


@pytest.mark.parametrize("company_id", IDS)
def test_content_rows_exist_only_with_a_draft(company_id):
    for row in pw.content(company_id)["rows"]:
        assert row["status"] in {"draft_ready", "in_review", "published"}
        it = pw.item(row["item_id"])
        assert it["draft"] and it["draft"]["body"]
        assert it["for_whom"]["questions"]
