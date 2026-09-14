"""The workspace fixtures match the API contract in PORTAL_WORKSPACE_BUILD_PLAN.md.

The UI is built and previewed against these files, and the API branch is
checked against them at integration, so they are the contract in executable
form. Shapes below are transcribed from the plan's "API contract" section.
"""

from __future__ import annotations

import json
import pathlib

import pytest

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "workspace"

NUM = (int, float)
OPT_STR = (str, type(None))
OPT_NUM = (int, float, type(None))

SOURCES = {"hubdb_rec", "loop_rec", "call_prep", "content_brief", "video_variant",
           "ticket_profile", "onboarding_gap", "portal_ticket", "service_ticket"}
LENSES = {"express", "tailor", "amplify", "evolve"}
KINDS = {"auto", "queued", "person"}
STATUSES = {"to_do", "in_motion", "done"}


def load(name):
    with open(FIXTURES / f"{name}.json", encoding="utf-8") as fh:
        return json.load(fh)


def check(obj, shape, where):
    assert isinstance(obj, dict), f"{where} is not an object"
    for key, types in shape.items():
        assert key in obj, f"{where} is missing {key!r}"
        assert isinstance(obj[key], types), f"{where}.{key} is {type(obj[key]).__name__}, expected {types}"


def check_receipt(obj, where, extra=None):
    """A number with its receipt: {value, source, as_of} — or null."""
    if obj is None:
        return
    check(obj, {"value": OPT_NUM, "source": str, "as_of": OPT_STR, **(extra or {})}, where)


def check_gaps(obj, where):
    assert isinstance(obj.get("gaps"), list), f"{where}.gaps must be a list"


ITEM = {
    "id": str, "source": str, "source_id": str, "title": str,
    "found": OPT_STR, "expect": OPT_STR, "if_skip": OPT_STR,
    "receipts": list, "channels": list, "lens": str,
    "start_by": OPT_STR, "due": OPT_STR, "status": str,
    "needs_approval": bool, "client_visible": bool, "internal_only": bool,
    "owner": OPT_STR, "comments_count": int, "cost_note": OPT_STR,
    "steps": (list, type(None)), "trail": list, "actions": dict,
}


def check_item(it, where):
    check(it, ITEM, where)
    assert it["id"] == f"{it['source']}:{it['source_id']}", f"{where}.id is not source:source_id"
    assert it["source"] in SOURCES, f"{where}.source {it['source']!r}"
    assert it["lens"] in LENSES, f"{where}.lens {it['lens']!r}"
    assert it["status"] in STATUSES, f"{where}.status {it['status']!r}"
    for i, r in enumerate(it["receipts"]):
        check(r, {"label": str, "source": str, "as_of": OPT_STR}, f"{where}.receipts[{i}]")
    for i, s in enumerate(it["steps"] or []):
        check(s, {"when": OPT_STR, "label": str, "channel": OPT_STR, "kind": str, "status": str}, f"{where}.steps[{i}]")
        assert s["kind"] in KINDS, f"{where}.steps[{i}].kind {s['kind']!r}"
    for i, t in enumerate(it["trail"]):
        check(t, {"at": str, "actor": str, "text": str}, f"{where}.trail[{i}]")
    check(it["actions"], {"approve": bool, "not_now": bool}, f"{where}.actions")
    assert all(isinstance(c, str) for c in it["channels"])


def test_all_fixtures_present_and_parse():
    names = {"me", "portfolio", "work", "item", "decision", "property", "performance",
             "plan", "client_view", "ask_questions", "ask_answer"}
    assert names <= {p.stem for p in FIXTURES.glob("*.json")}
    for name in names:
        load(name)


def test_me():
    d = load("me")
    check(d, {"email": str, "role": str, "verified": bool, "companies": list}, "me")
    assert d["companies"]
    for i, c in enumerate(d["companies"]):
        check(c, {"company_id": str, "uuid": str, "name": str, "city": OPT_STR, "state": OPT_STR, "units": OPT_NUM}, f"me.companies[{i}]")


def test_portfolio():
    d = load("portfolio")
    check(d, {"as_of": str, "property_count": int, "item_count": int, "starting_this_week": int,
              "properties": list, "quiet_count": int, "gaps": list}, "portfolio")
    for i, p in enumerate(d["properties"]):
        where = f"portfolio.properties[{i}]"
        check(p, {"company_id": str, "name": str, "city": OPT_STR, "state": OPT_STR, "units": OPT_NUM,
                  "occupancy": (dict, type(None)), "top_item": (dict, type(None)), "more_items": int,
                  "start_by": OPT_STR, "overdue": bool, "units_at_risk": (dict, type(None))}, where)
        check_receipt(p["occupancy"], where + ".occupancy")
        check_receipt(p["units_at_risk"], where + ".units_at_risk")
        if p["top_item"] is not None:
            check(p["top_item"], {"id": str, "title": str}, where + ".top_item")


def test_work():
    d = load("work")
    check(d, {"summary": dict, "counts": dict, "groups": dict, "hidden_count": int, "gaps": list}, "work")
    check(d["summary"], {"open": int, "late": int, "next_deadline": OPT_STR}, "work.summary")
    check(d["counts"], {"to_do": int, "in_motion": int, "done": int}, "work.counts")
    check(d["groups"], {"late": list, "this_week": list, "later": dict}, "work.groups")
    check(d["groups"]["later"], {"count": int, "titles": list}, "work.groups.later")
    items = d["groups"]["late"] + d["groups"]["this_week"]
    for i, it in enumerate(items):
        check_item(it, f"work.items[{i}]")


def test_work_exercises_hiding_and_gaps():
    d = load("work")
    items = d["groups"]["late"] + d["groups"]["this_week"]
    assert any(it["found"] is None and it["expect"] is None and it["steps"] is None for it in items), \
        "at least one item must have null found/expect/steps so the UI's hiding is exercised"
    assert d["gaps"], "at least one gaps[] entry must be present"


def test_item():
    check_item(load("item"), "item")


def test_decision():
    d = load("decision")
    check(d, {"item": dict, "decided_by": str, "decided_at": str, "in_motion": list,
              "written_down": OPT_STR, "check_back": (dict, type(None))}, "decision")
    check_item(d["item"], "decision.item")
    for i, m in enumerate(d["in_motion"]):
        check(m, {"label": str, "kind": str, "status": str, "detail": OPT_STR}, f"decision.in_motion[{i}]")
        assert m["kind"] in KINDS
    if d["check_back"] is not None:
        check(d["check_back"], {"date": str, "text": str}, "decision.check_back")


def test_property():
    d = load("property")
    check(d, {"name": str, "address": OPT_STR, "domain": OPT_STR, "managed_since": OPT_STR,
              "brief": (dict, type(None)), "floorplans": list, "people": list, "connections": list, "gaps": list}, "property")
    check(d["brief"], {"text": str, "curated": bool, "edited_by": OPT_STR, "edited_at": OPT_STR}, "property.brief")
    for i, f in enumerate(d["floorplans"]):
        check(f, {"code": str, "beds": OPT_NUM, "sqft": OPT_NUM, "available": OPT_NUM}, f"property.floorplans[{i}]")
    for i, p in enumerate(d["people"]):
        check(p, {"name": str, "role": str}, f"property.people[{i}]")
    for i, c in enumerate(d["connections"]):
        check(c, {"name": str, "status": str, "synced_at": OPT_STR}, f"property.connections[{i}]")


def test_performance():
    d = load("performance")
    check(d, {"occupied": dict, "available_now": dict, "coming_open_90d": dict, "coming_by_week": list,
              "monthly_plan": dict, "note": OPT_STR, "gaps": list}, "performance")
    check_receipt(d["occupied"], "performance.occupied", {"units": OPT_NUM, "total": OPT_NUM, "target": OPT_NUM})
    check_receipt(d["available_now"], "performance.available_now", {"stale_90_plus": OPT_NUM})
    check_receipt(d["coming_open_90d"], "performance.coming_open_90d", {"one_bed": OPT_NUM})
    check_receipt(d["monthly_plan"], "performance.monthly_plan", {"by_channel": dict})
    for i, w in enumerate(d["coming_by_week"]):
        check(w, {"week_start": str, "one_bed": int, "two_bed": int, "other": int}, f"performance.coming_by_week[{i}]")


def test_plan():
    d = load("plan")
    check(d, {"monthly_total": NUM, "channel_count": int, "pending_changes": int, "channels": list,
              "caveat": OPT_STR, "gaps": list}, "plan")
    for i, c in enumerate(d["channels"]):
        check(c, {"channel": str, "monthly": NUM, "share": NUM, "cost_per_lease": OPT_NUM,
                  "pointed_at": OPT_STR, "status": str, "status_note": OPT_STR}, f"plan.channels[{i}]")
        assert 0 <= c["share"] <= 1


def test_client_view():
    d = load("client_view")
    check(d, {"changing": list, "done_this_quarter": list, "done_count": int, "hidden_open_count": int}, "client_view")
    for key in ("changing", "done_this_quarter"):
        for i, row in enumerate(d[key]):
            check(row, {"date": str, "title": str, "note": OPT_STR, "status": str}, f"client_view.{key}[{i}]")


def test_ask_questions():
    d = load("ask_questions")
    check(d, {"free_text": bool, "questions": list}, "ask_questions")
    assert d["free_text"] is False
    for i, q in enumerate(d["questions"]):
        check(q, {"key": str, "label": str}, f"ask_questions.questions[{i}]")


def test_ask_answer():
    d = load("ask_answer")
    check(d, {"question": str, "label": str, "answered": bool, "headline": OPT_STR, "summary": OPT_STR,
              "findings": list, "evidence": list, "missing_inputs": list, "caveats": list}, "ask_answer")
    keys = {q["key"] for q in load("ask_questions")["questions"]}
    assert d["question"] in keys
    for i, f in enumerate(d["findings"]):
        check(f, {"title": str, "detail": str, "evidence": list}, f"ask_answer.findings[{i}]")
        for ev in f["evidence"]:
            assert ev in d["evidence"], "a finding cites evidence that is not in evidence[]"


@pytest.mark.parametrize("name", ["me", "portfolio", "work", "item", "decision", "property",
                                  "performance", "plan", "client_view", "ask_answer"])
def test_fixture_copy_avoids_targeting_language(name):
    """Housing is a Special Ad Category: sample copy never suggests radius, ZIP or audience targeting."""
    text = json.dumps(load(name)).lower()
    for phrase in ("radius", "zip code", "zip targeting", "audience layer", "lookalike"):
        assert phrase not in text, f"{name}.json mentions {phrase!r}"


SIGNAL_KINDS = {"occupancy_drop", "stale_inventory", "lease_wave", "lead_drop", "spend_pacing",
                "reputation_drop", "tracking_break", "data_stale"}


def test_signals():
    d = load("signals")
    check(d, {"as_of": str, "counts": dict, "signals": list, "gaps": list}, "signals")
    check(d["counts"], {"high": int, "medium": int, "low": int}, "signals.counts")
    for i, s in enumerate(d["signals"]):
        where = f"signals.signals[{i}]"
        check(s, {"id": str, "company_id": str, "property_name": str, "kind": str, "severity": str,
                  "title": str, "detail": OPT_STR, "metric": (dict, type(None)), "change": (dict, type(None)),
                  "detected_at": str, "work_item_id": OPT_STR}, where)
        assert s["kind"] in SIGNAL_KINDS, f"{where}.kind {s['kind']!r}"
        assert s["severity"] in {"high", "medium", "low"}
        check_receipt(s["metric"], where + ".metric")
        if s["change"] is not None:
            check(s["change"], {"from": NUM, "to": NUM, "window_days": int}, where + ".change")
    assert sum(d["counts"].values()) == len(d["signals"])
    for g in d["gaps"]:
        check(g, {"message": str}, "signals.gaps[]")


REQUEST_CATEGORIES = {"creative", "web", "paid", "seo", "listing", "reputation", "other"}


def test_request_draft():
    d = load("request_draft")
    check(d, {"tickets": list, "gaps": list}, "request_draft")
    assert d["tickets"]
    for i, t in enumerate(d["tickets"]):
        where = f"request_draft.tickets[{i}]"
        check(t, {"draft_id": str, "title": str, "category": str, "team": OPT_STR, "needed_by": OPT_STR,
                  "needed_by_reason": OPT_STR, "attached_context": list, "warnings": list}, where)
        assert t["category"] in REQUEST_CATEGORIES, f"{where}.category {t['category']!r}"
    assert any(t["warnings"] for t in d["tickets"]), "one drafted ticket should carry a warning"


def test_request_created():
    d = load("request_created")
    check(d, {"created": list, "failed": list}, "request_created")
    for c in d["created"]:
        check(c, {"draft_id": str, "work_item_id": str, "clickup_task_id": str}, "request_created.created[]")
    for f in d["failed"]:
        check(f, {"draft_id": str, "reason": str}, "request_created.failed[]")


def test_requests_recent():
    d = load("requests_recent")
    check(d, {"recent": list}, "requests_recent")
    for r in d["recent"]:
        check(r, {"title": str, "status": str, "status_date": OPT_STR, "work_item_id": OPT_STR}, "requests_recent.recent[]")
        assert r["status"] in {"done", "in_progress", "new"}


def test_search():
    d = load("search")
    check(d, {"results": list}, "search")
    assert 0 < len(d["results"]) <= 20
    for i, r in enumerate(d["results"]):
        check(r, {"type": str, "id": str, "title": str, "subtitle": OPT_STR, "company_id": OPT_STR, "href": str}, f"search.results[{i}]")
        assert r["type"] in {"property", "work_item", "report", "question"}
        assert r["href"].startswith("#/"), "search hrefs are in-page routes"


def test_decision_undo():
    d = load("decision")
    check(d["undo"], {"available": bool, "until": OPT_STR, "reason": OPT_STR}, "decision.undo")


def test_undo():
    d = load("undo")
    check(d, {"item": dict, "undone": bool}, "undo")
    assert d["undone"] is True
    check_item(d["item"], "undo.item")
