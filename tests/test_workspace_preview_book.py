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
        if row["item_id"].startswith("profile_update:"):
            # The API titles profile updates by field ("Profile update: Taglines"); the row names the property.
            it = pw.item(row["item_id"])
            assert it["company_id"] == p["company_id"]
            assert not [q["name"] for q in PROPS if q["company_id"] != p["company_id"] and q["name"] in _text(it)]
            continue
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
    approvals_waiting = [w for w in d["waiting"] if w.get("kind") != "profile_checkin"]
    assert k["waiting_on_you"]["value"] == pw.approvals()["waiting"] == len(pw.approvals()["batch"]["rows"]) == len(approvals_waiting)
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
        if it["source"] == "content_brief":
            assert it["for_whom"]["questions"], "content briefs name the renter questions they answer"


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


def test_every_property_has_a_report_defaulting_to_the_last_full_month():
    client = pw.app.test_client()
    for p in PROPS:
        resp = client.get(f"/api/workspace/report?company_id={p['company_id']}")
        assert resp.status_code == 200, f"{p['name']}: {resp.status_code}"
        d = resp.get_json()
        assert d["month"] == "2026-08", f"{p['name']} defaults to {d['month']}"
        assert d["property"]["name"] == p["name"]
        others = [q["name"] for q in PROPS if q["company_id"] != p["company_id"] and q["name"] in json.dumps(d)]
        assert not others, f"{p['name']}'s report mentions {others}"
    bromley = client.get("/api/workspace/report?company_id=26136316506&month=2026-06").get_json()
    assert bromley["month"] == "2026-06"
    assert client.get(f"/api/workspace/report?company_id={IDS[0]}&month=2026-09").status_code == 404



# ── Round 5: spend sheet and property profile ────────────────────────────────
# Shapes follow the merged API contract (tests/workspace_contract.py); the preview
# builds them with the API's own helpers, so these tests pin the preview's facts.

import sys as _sys  # noqa: E402

_sys.path.insert(0, str(ROOT / "tests"))
_sys.path.insert(0, str(ROOT / "webhook-server"))
import workspace_contract as wc  # noqa: E402
import community_brief as cb  # noqa: E402
from skills import workspace_profile as wpr  # noqa: E402

LYV = "18234410021"
PREVIEW_CLIENT = {"X-Workspace-Preview-Role": "client"}
CHANNEL_KEYS = {"search", "pmax", "paid_social", "display", "retargeting", "ctv", "geofence", "demand_gen", "youtube", "tiktok",
                "seo", "social_posting", "reputation", "eblast", "email_drip", "website_hosting"}
INTERNAL_SPEND_KEYS = {"mgmt_fee", "deal_id", "deal_name", "deal_stage", "deal_amount", "quote_status", "quote_title", "ple_status", "close_date"}


@pytest.fixture
def fresh_profiles():
    pw._reset_profiles()
    pw.SWITCHES["client_editor"] = False
    yield
    pw._reset_profiles()
    pw.SWITCHES["client_editor"] = False


def _spend_row(sheet, cid):
    return next(r for r in sheet["rows"] if r["company_id"] == cid)


def _package_amount(text):
    m = re.search(r"\$([\d,]+)", text or "")
    return int(m.group(1).replace(",", "")) if m else 0


def _channels(row):
    return sum(v for k, v in row["values"].items() if k in CHANNEL_KEYS and v is not None)


def test_spend_sheet_lyv_reconciles_with_plan_and_report():
    staff, client = _spend_row(pw.spend_sheet(page_size=100), LYV), _spend_row(pw.spend_sheet(page_size=100, client=True), LYV)
    plan = json.loads((FIXTURES / "plan.json").read_text(encoding="utf-8"))
    report = json.loads((FIXTURES / "report_lyv_broadway_2026_08.json").read_text(encoding="utf-8"))
    by_channel = {c["channel"]: c["monthly"] for c in plan["channels"]}
    v = staff["values"]
    assert v["search"] == by_channel["Paid search"] and v["pmax"] == by_channel["Performance Max"]
    assert v["seo"] == by_channel["SEO — Standard"] and v["paid_social"] == by_channel["Social — Meta"]
    assert v["zillow_per_month"] == by_channel["ILS — Zillow"]
    assert _package_amount(v["costar_package"]) == by_channel["ILS — Apartments.com"]
    assert _channels(staff) + v["zillow_per_month"] + _package_amount(v["costar_package"]) == plan["monthly_total"] == report["spend"]["total"]["value"]
    assert client["total"] == _channels(staff), "a client's total is channels only"
    assert staff["total"] == _channels(staff) + v["mgmt_fee"], "staff totals include the management fee"


def test_spend_rows_agree_with_the_book_and_totals_add_up():
    sheet = pw.spend_sheet(page_size=100)
    assert sheet["count"] == len(sheet["rows"]) == 35
    for r in sheet["rows"]:
        p = pw.prop(r["company_id"])
        assert all(r["values"][k] != 0 for k in CHANNEL_KEYS), f"{p['name']}: absent channels are null, never 0"
        if p["spend_last_month"] is None:
            assert r["total"] is None and all(r["values"][k] is None for k in CHANNEL_KEYS)
        elif r["company_id"] != "18234410087":
            spent = _channels(r) + (r["values"]["zillow_per_month"] or 0) + _package_amount(r["values"]["costar_package"])
            assert spent == p["spend_last_month"], p["name"]
    assert any("ApartmentList.com" in g["message"] for g in sheet["gaps"]), "Skye Reserve's uncolumned listing is a named gap"
    assert sheet["totals"]["total"] == sum(r["total"] for r in sheet["rows"] if r["total"] is not None)


def test_spend_sheet_strips_internal_columns_for_clients_and_preview_as_client():
    staff = pw.spend_sheet(page_size=100)
    assert not wc.check(staff, wc.SPEND_SHEET)
    assert INTERNAL_SPEND_KEYS - {"ple_status"} <= {c["key"] for c in staff["columns"]}
    route = pw.app.test_client().get("/api/workspace/spend-sheet?page_size=100", headers=PREVIEW_CLIENT)
    for sheet in (pw.spend_sheet(page_size=100, client=True), route.get_json()):
        assert not wc.check(sheet, wc.SPEND_SHEET)
        assert sheet["scope"] == "client" and sheet["filters"]["statuses"] == []
        assert not [c for c in sheet["columns"] if c["internal"] or c["key"] in INTERNAL_SPEND_KEYS]
        assert not [k for r in sheet["rows"] for k in r["values"] if k in INTERNAL_SPEND_KEYS]
        assert all(r["status"] is None for r in sheet["rows"])
        assert not set(sheet["totals"]["values"]) & INTERNAL_SPEND_KEYS
    assert pw.app.test_client().get("/api/workspace/spend-sheet?sort=status", headers=PREVIEW_CLIENT).status_code == 400


def test_spend_sheet_filters_sort_and_paging():
    dfw = pw.spend_sheet(market="Dallas–Fort Worth", page_size=100)
    assert dfw["count"] and all(r["market"] == "Dallas–Fort Worth" for r in dfw["rows"])
    totals = [r["total"] for r in pw.spend_sheet(sort="total", direction="desc", page_size=100)["rows"]]
    assert totals[-1] is None, "rows with no spend sort last"
    known = [x for x in totals if x is not None]
    assert known == sorted(known, reverse=True)
    page4 = pw.spend_sheet(page=4, page_size=10)
    assert page4["count"] == 35 and len(page4["rows"]) == 5 and page4["page"] == 4
    assert pw.spend_sheet(q="lyv")["count"] == 1


def _fields(prof):
    return {f["key"]: f for s in prof["sections"] for f in s["fields"]}


def test_lyv_profile_has_all_55_fields_and_every_required_state(fresh_profiles):
    prof = pw.profile(LYV)
    assert not wc.check(prof, wc.PROFILE)
    fields = _fields(prof)
    assert len(prof["sections"]) == 13 and len(fields) == 55
    assert sum(1 for f in fields.values() if f["internal"]) == 17
    assert [k for k, f in fields.items() if f["value"] is None], "some fields are empty"
    assert prof["completeness"]["pct"] < 100 and len(prof["completeness"]["top_missing"]) == 3
    assert prof["checkin"]["due"] and prof["checkin"]["stale_fields"]
    assert all(fields[k]["provenance"]["kind"] == "override" for k in prof["checkin"]["stale_fields"]), "only human-written values go stale"
    assert [k for k, f in fields.items() if f["pending"]] == ["taglines"]
    assert sorted(f["suggestion"]["source"] for f in fields.values() if f["suggestion"]) == ["geo_claim", "site_scrape"]
    flags = {k: f["fair_housing_review"] for k, f in fields.items() if f.get("fair_housing_review")}
    assert list(flags) == ["romance"] and flags["romance"]["severity"] == "low"
    assert fields["property_amenities"]["provenance"] == {"kind": "override", "by": "Dana R.", "at": "2026-01-15T15:00:00Z", "source": "profile_edit", "overrides": "site_scrape"}
    for k, f in fields.items():
        assert f["used_in"] == cb.used_in(k) and f["ad_facing"] == cb.is_ad_facing(k)


def test_client_profile_never_carries_internal_fields(fresh_profiles):
    route = pw.app.test_client().get(f"/api/workspace/profile?company_id={LYV}", headers=PREVIEW_CLIENT).get_json()
    for prof in (pw.profile(LYV, client=True), route):
        assert not wc.check(prof, wc.PROFILE)
        fields = _fields(prof)
        assert len(fields) == 38 and not [f for f in fields.values() if f["internal"]]
        assert "fair_housing_review" not in json.dumps(prof)
        assert "operations_tech" not in [s["key"] for s in prof["sections"]]


@pytest.mark.parametrize("company_id", IDS)
def test_every_profile_is_its_own_property(company_id, fresh_profiles):
    p, prof = pw.prop(company_id), pw.profile(company_id)
    assert prof["property"]["name"] == p["name"]
    assert not [q["name"] for q in PROPS if q["company_id"] != company_id and q["name"] in json.dumps(prof)]
    if company_id != LYV:
        assert prof["completeness"]["pct"] < pw.profile(LYV)["completeness"]["pct"], "other profiles are lighter"


def test_completeness_weights_ad_facing_over_context_fields(fresh_profiles):
    fields = list(_fields(pw.profile(LYV)).values())
    base = wpr.completeness(fields)["pct"]
    def with_filled(key):
        return wpr.completeness([dict(f, value="filled") if f["key"] == key else f for f in fields])["pct"]
    assert with_filled("unit_features") > with_filled("onsite_events") >= base


def _patch(client, key, value, headers=None):
    r = client.patch("/api/workspace/profile/field", json={"company_id": LYV, "key": key, "value": value}, headers=headers or {})
    return r.status_code, r.get_json()


def test_profile_edit_outcomes(fresh_profiles):
    c = pw.app.test_client()
    status, body = _patch(c, "short_name", "LYV")
    assert status == 200 and body["outcome"] == "saved" and body["field"]["value"] == "LYV", "staff edits save immediately"
    assert not wc.check(body, wc.PROFILE_EDIT)
    status, body = _patch(c, "voice_tier", ["lifestyle", "luxury"])
    assert body["outcome"] == "saved" and body["field"]["value"] == "lifestyle;luxury"
    status, body = _patch(c, "romance", "Perfect for young professionals starting out")
    assert body["outcome"] == "blocked" and body["fair_housing"]["result"] == "blocked" and "Fair Housing" in body["message"]
    assert _fields(pw.profile(LYV))["romance"]["value"].startswith("Bright white"), "a block leaves the live value"
    assert _patch(c, "short_name", "x", PREVIEW_CLIENT)[0] == 403
    assert _patch(c, "uuid", "x")[0] == 404, "R1: nothing writes uuid"
    assert _patch(c, "city", "Dallas")[0] == 400, "company-record fields aren't edited here"
    pw.SWITCHES["client_editor"] = True
    status, body = _patch(c, "unit_features", "In-home washer and dryer")
    assert body["outcome"] == "pending_review" and body["field"]["value"] is None
    assert body["field"]["pending"]["proposed_value"] == "In-home washer and dryer" and not wc.check(body, wc.PROFILE_EDIT)
    status, body = _patch(c, "onsite_events", "Monthly rooftop yoga")
    assert body["outcome"] == "saved" and body["field"]["value"] == "Monthly rooftop yoga"
    assert _patch(c, "pms", "Entrata")[0] == 403


def test_profile_update_approve_reject_and_undo(fresh_profiles):
    c = pw.app.test_client()
    item_id = "profile_update:0021-taglines"
    assert item_id in [r["item_id"] for r in pw.approvals(category="content")["batch"]["rows"]]
    assert item_id not in [r["item_id"] for r in pw.approvals(client=True)["batch"]["rows"]]
    it = pw.item(item_id)
    assert not wc.check(pw._public(it), wc.ITEM)
    assert it["category"] == "content" and not it["client_visible"]
    assert it["profile_update"]["current_value"] != it["profile_update"]["proposed_value"]
    assert c.post(f"/api/workspace/work/{item_id}/decision", json={"company_id": LYV, "action": "approve"}).status_code == 200
    taglines = _fields(pw.profile(LYV))["taglines"]
    assert taglines["value"] == "Modern Carrollton living, minutes from the Green Line." and taglines["pending"] is None
    assert taglines["review_outcome"]["status"] == "approved"
    latest = pw._profile_state(LYV)["taglines"]["entries"][0]
    assert latest["action"] == "approved" and "Morgan Lee" in latest["by"] and "Dana R." in latest["by"], "the audit names proposer and approver"
    assert c.post(f"/api/workspace/work/{item_id}/undo", json={"company_id": LYV}).get_json()["undone"]
    assert _fields(pw.profile(LYV))["taglines"]["pending"]
    c.post(f"/api/workspace/work/{item_id}/decision", json={"company_id": LYV, "action": "not_now", "reason": "wrong_data"})
    taglines = _fields(pw.profile(LYV))["taglines"]
    assert taglines["value"] == "Your space. Your pace. LYV Broadway." and taglines["review_outcome"]["status"] == "rejected"


def test_checkin_is_a_client_to_do_resets_staleness_and_is_never_an_approval(fresh_profiles):
    staff, client = pw.dashboard(), pw.dashboard(client=True)
    assert not wc.check(staff, wc.DASHBOARD) and not wc.check(client, wc.DASHBOARD)
    assert not [w for w in staff["waiting"] if w["kind"] == "profile_checkin"], "check-ins are client to-dos"
    checkins = [w for w in client["waiting"] if w["kind"] == "profile_checkin"]
    lyv = next(w for w in checkins if w["company_id"] == LYV)
    stale = pw.profile(LYV, client=True)["checkin"]["stale_fields"]
    assert lyv["stale_count"] == len(stale) and lyv["item_id"] is None and lyv["category"] is None
    assert client["kpis"]["waiting_on_you"]["value"] == len([w for w in client["waiting"] if w["kind"] == "approval"])
    assert not [r for r in pw.approvals()["batch"]["rows"] if "checkin" in r["item_id"]]
    c = pw.app.test_client()
    body = c.post("/api/workspace/profile/checkin", json={"company_id": LYV, "confirmed": stale[:2] + ["unit_features", "nope"]}).get_json()
    assert not wc.check(body, wc.PROFILE_CHECKIN)
    assert body["confirmed"] == stale[:2] and not set(stale[:2]) & set(body["checkin"]["stale_fields"])
    assert {s["key"]: s["reason"] for s in body["skipped"]} == {"unit_features": "No value to confirm", "nope": "Unknown field"}
    history = c.get(f"/api/workspace/profile/history?company_id={LYV}&key={stale[0]}").get_json()
    assert not wc.check(history, wc.PROFILE_HISTORY) and history["entries"][0]["kind"] == "reviewed"


def test_suggestions_accept_through_the_edit_flow_and_dismiss_with_a_reason(fresh_profiles):
    c = pw.app.test_client()
    pw.SWITCHES["client_editor"] = True
    body = c.post("/api/workspace/profile/suggestions/sug-0021-amenities/accept").get_json()
    assert body["outcome"] == "pending_review", "an ad-facing suggestion from a client goes to review like any edit"
    assert c.post("/api/workspace/profile/suggestions/sug-0021-landmarks/dismiss", json={}).status_code == 400
    body = c.post("/api/workspace/profile/suggestions/sug-0021-landmarks/dismiss", json={"reason": "Not near the property"}).get_json()
    assert not wc.check(body, wc.SUGGESTION_DISMISSED) and body == {"suggestion_id": "sug-0021-landmarks", "dismissed": True, "reason": "Not near the property"}
    assert pw._profile_state(LYV)["landmarks"]["entries"][0]["note"] == "Not near the property"


def test_properties_rows_and_overview_carry_profile_completeness(fresh_profiles):
    for row in pw.dashboard()["properties"]:
        metric = row["profile_completeness"]
        assert metric["value"] == pw.profile(row["company_id"])["completeness"]["pct"] and metric["source"]
    with pw.app.test_request_context():
        assert pw.property_overview(LYV)["profile_completeness"]["value"] == pw.profile(LYV)["completeness"]["pct"]


def test_save_messages_say_what_actually_happens(fresh_profiles):
    c = pw.app.test_client()
    assert _patch(c, "short_name", "LYV")[1]["message"] == "Saved · live in ads tomorrow", "ad-facing, saved immediately by staff"
    assert _patch(c, "onsite_events", "Monthly rooftop yoga")[1]["message"] == "Saved", "context fields aren't used in ads"
    assert _patch(c, "pms", "Entrata")[1]["message"] == "Saved", "internal fields just save"
    pw.SWITCHES["client_editor"] = True
    assert _patch(c, "unit_features", "In-home washer and dryer")[1]["message"].startswith("Sent to RPM for review")


def test_every_profile_update_item_matches_the_item_spec(fresh_profiles):
    items = pw.profile_update_items()
    assert items
    for it in items:
        assert not wc.check(pw._public(it), wc.ITEM), it["id"]
