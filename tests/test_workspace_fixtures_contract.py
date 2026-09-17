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
           "ticket_profile", "onboarding_gap", "portal_ticket", "service_ticket", "fair_housing_review"}
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
    "needs_approval": bool, "client_visible": bool,
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
        check(t, {"at": str, "actor": str, "text": str, "visibility": str}, f"{where}.trail[{i}]")
        assert t["visibility"] in {"client", "internal"}
    check(it["actions"], {"approve": bool, "not_now": bool}, f"{where}.actions")
    assert "internal_only" not in it, f"{where}: internal_only is gone (Phase 2)"
    assert it["client_visible"] is True
    for n in it.get("notes") or []:
        check(n, {"at": str, "actor": str, "text": str, "visibility": str}, f"{where}.notes[]")
        assert n["visibility"] in {"client", "internal"}
    if it.get("evidence") is not None:
        check(it["evidence"], {"columns": list, "rows": list, "more_count": int}, f"{where}.evidence")
        assert all(len(r) == len(it["evidence"]["columns"]) for r in it["evidence"]["rows"])
    if it.get("sparkline") is not None:
        check(it["sparkline"], {"label": str, "points": list, "highlight_index": (int, type(None))}, f"{where}.sparkline")
        for pt in it["sparkline"]["points"]:
            check(pt, {"x": (str, int, float), "y": NUM}, f"{where}.sparkline.points[]")
    if it.get("fair_housing_review") is not None:
        check(it["fair_housing_review"], {"severity": str, "terms": list}, f"{where}.fair_housing_review")
        assert it["fair_housing_review"]["severity"] in {"high", "low"}
    assert all(isinstance(c, str) for c in it["channels"])


def test_all_fixtures_present_and_parse():
    names = {"me", "portfolio", "work", "item", "decision", "property", "performance",
             "plan", "client_view", "ask_questions", "ask_answer"}
    assert names <= {p.stem for p in FIXTURES.glob("*.json")}
    for name in names:
        load(name)


def test_me():
    d = load("me")
    check(d, {"email": str, "role": str, "verified": bool, "can_decide": bool, "companies": list}, "me")
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
    check(d, {"changing": list, "done_this_quarter": list, "done_count": int}, "client_view")
    assert "hidden_open_count" not in d, "clients now see all work (Phase 2 amendment 7)"
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


# ── v3 rebuild endpoints ────────────────────────────────────────────────────
BANDS = {"healthy", "attention", "warning", "critical", "new"}
OPT_RECEIPT = (dict, type(None))


def check_gap_entries(d, where):
    assert isinstance(d.get("gaps"), list), f"{where}.gaps must be a list"
    for g in d["gaps"]:
        check(g, {"message": str}, f"{where}.gaps[]")
        for k in ("field", "source"):
            if k in g:
                assert isinstance(g[k], (str, type(None)))


def test_dashboard():
    d = load("dashboard")
    check(d, {"greeting_name": OPT_STR, "as_of": str, "kpis": dict, "health_tiles": list, "properties": list,
              "activity": list, "waiting": list, "loop_status": dict}, "dashboard")
    # Round 4: seven KPIs in this order, no lens, no identified savings.
    assert list(d["kpis"]) == ["occupancy", "units_to_lease_90d", "leases_this_month", "cost_per_lease",
                               "ai_visibility", "actions_taken", "waiting_on_you"]
    assert "lens" not in d and "kpi_order" not in d
    for k in d["kpis"]:
        check_receipt(d["kpis"][k], f"dashboard.kpis.{k}")
    for t in d["health_tiles"]:
        check(t, {"company_id": str, "name": str, "score": OPT_NUM, "band": str}, "dashboard.health_tiles[]")
        assert t["band"] in BANDS
    for p in d["properties"]:
        check(p, {"company_id": str, "name": str, "units": OPT_NUM, "occupancy": OPT_RECEIPT, "to_lease_90d": OPT_RECEIPT,
                  "leases_month": OPT_RECEIPT, "status": OPT_STR, "health": OPT_NUM, "band": str}, "dashboard.properties[]")
        assert "overspend_per_year" not in p
        for k in ("occupancy", "to_lease_90d", "leases_month"):
            check_receipt(p[k], f"dashboard.properties[].{k}")
        assert p["band"] in BANDS
    for a in d["activity"]:
        check(a, {"at": str, "text": str, "kind": str, "visibility": str}, "dashboard.activity[]")
        assert a["kind"] in {"audit", "draft", "check", "flag", "forecast", "decision", "publish"}
        assert a["visibility"] in {"client", "internal"}
    for w in d["waiting"]:
        check(w, {"item_id": str, "title": str, "subtitle": OPT_STR, "category": str}, "dashboard.waiting[]")
        assert w["category"] in {"cost", "vendor", "content", "creative", "compliance"}
    check(d["loop_status"], {"running": bool, "property_count": int, "last_pass": OPT_STR}, "dashboard.loop_status")
    check_gap_entries(d, "dashboard")


@pytest.mark.parametrize("name", ["approvals", "approvals_empty"])
def test_approvals(name):
    d = load(name)
    check(d, {"waiting": int, "interrupts_count": int, "approved_this_month": int, "interrupts": list,
              "batch": dict, "stats": dict}, name)
    for i in d["interrupts"]:
        check(i, {"id": str, "kind": str, "title": str, "detail": OPT_STR, "company_id": str, "item_id": OPT_STR,
                  "primary_action": dict, "secondary_action": (dict, type(None))}, f"{name}.interrupts[]")
        assert i["kind"] == "compliance", "clients never see pacing interrupts (Round 4)"
    check(d["batch"], {"label": str, "rows": list}, f"{name}.batch")
    for r in d["batch"]["rows"]:
        check(r, {"item_id": str, "company_id": str, "property": str, "action": str, "category": str,
                  "savings_per_year": OPT_RECEIPT, "can_edit": bool}, f"{name}.batch.rows[]")
        check_receipt(r["savings_per_year"], f"{name}.batch.rows[].savings_per_year")
    check(d["stats"], {"approval_rate": OPT_RECEIPT, "edit_rate": OPT_RECEIPT, "auto_approve_candidates": list}, f"{name}.stats")
    assert d["waiting"] == len(d["batch"]["rows"])
    check_gap_entries(d, name)


def test_property_overview():
    d = load("property_overview")
    check(d, {"name": str, "city": OPT_STR, "state": OPT_STR, "units": OPT_NUM, "objective": OPT_STR,
              "health": dict, "kpis": dict, "exposure_forecast": (dict, type(None)), "vendor_audit": list,
              "visibility_by_engine": list, "findings": list, "recommended_action": (dict, type(None)),
              "draft_email": (dict, type(None)), "loop": list, "links": dict}, "property_overview")
    check(d["health"], {"score": OPT_NUM, "band": str, "source": str, "as_of": OPT_STR}, "property_overview.health")
    for k in ("ai_visibility", "renewal_rate", "units_to_lease", "lead_to_lease"):
        check_receipt(d["kpis"].get(k), f"property_overview.kpis.{k}")
    if d["exposure_forecast"]:
        check(d["exposure_forecast"], {"months": list, "source": str, "as_of": OPT_STR}, "property_overview.exposure_forecast")
        for m in d["exposure_forecast"]["months"]:
            check(m, {"month": str, "units_to_lease": int}, "property_overview.exposure_forecast.months[]")
    for v in d["vendor_audit"]:
        check(v, {"vendor": str, "package": OPT_STR, "monthly": OPT_RECEIPT, "verdict": str, "basis": OPT_STR}, "property_overview.vendor_audit[]")
        assert v["verdict"] in {"over", "fair", "under", "unknown"}
        if v["verdict"] != "unknown":
            assert v["basis"], "a vendor verdict other than unknown needs a market-rate basis"
    if any(v["verdict"] == "unknown" for v in d["vendor_audit"]):
        assert any(g.get("field", "").startswith("vendor_audit") for g in d["gaps"]), "unknown verdicts come with a gap"
    for e in d["visibility_by_engine"]:
        check(e, {"engine": str, "score": OPT_RECEIPT}, "property_overview.visibility_by_engine[]")
    for f in d["findings"]:
        check(f, {"text": str, "receipts": list}, "property_overview.findings[]")
    for step in d["loop"]:
        check(step, {"lens": str, "status": str, "at": OPT_STR, "text": str}, "property_overview.loop[]")
        assert step["lens"] in LENSES and step["status"] in {"done", "waiting", "upcoming"}
    for k in ("media_plan", "visibility", "content", "creative", "report"):
        assert isinstance(d["links"].get(k), str)
    check_gap_entries(d, "property_overview")


def test_media_plan():
    d = load("media_plan")
    check(d, {"fiscal_year": str, "envelope": OPT_RECEIPT, "objective": OPT_STR, "generated_at": str,
              "months": list, "channels": list, "allocated": OPT_RECEIPT, "notes": list}, "media_plan")
    check_receipt(d["envelope"], "media_plan.envelope")
    assert len(d["months"]) == 12
    for m in d["months"]:
        check(m, {"month": str, "units_to_lease": int}, "media_plan.months[]")
    for c in d["channels"]:
        check(c, {"channel": str, "mode": str, "monthly": list, "monthly_avg": NUM, "annual": NUM, "share": NUM, "cpl_target": OPT_NUM}, "media_plan.channels[]")
        assert c["mode"] in {"always_on", "flighted"}
        assert len(c["monthly"]) == 12 and all(x is None or isinstance(x, (int, float)) for x in c["monthly"])
        assert c["annual"] == sum(x for x in c["monthly"] if x is not None)
        if c["mode"] == "always_on":
            assert len({x for x in c["monthly"] if x is not None}) <= 1, "always-on channels are flat"
    check_gap_entries(d, "media_plan")


def test_visibility():
    d = load("visibility")
    check(d, {"score": OPT_RECEIPT, "change": (dict, type(None)), "last_audit": OPT_STR, "next_audit": OPT_STR,
              "engines": list, "comp_stack": (dict, type(None)), "citation_sources": list,
              "recommendations": list, "alerts": list}, "visibility")
    check_receipt(d["score"], "visibility.score")
    for e in d["engines"]:
        check(e, {"engine": str, "score": OPT_RECEIPT, "queries_hit": int, "queries_total": int}, "visibility.engines[]")
    if d["comp_stack"]:
        check(d["comp_stack"], {"competitors": list, "rows": list}, "visibility.comp_stack")
        for r in d["comp_stack"]["rows"]:
            check(r, {"surface": str, "values": dict}, "visibility.comp_stack.rows[]")
    for s in d["citation_sources"]:
        check(s, {"source": str, "share": NUM}, "visibility.citation_sources[]")
    for r in d["recommendations"]:
        check(r, {"text": str, "action": (dict, type(None))}, "visibility.recommendations[]")
    for pr in d["prompts"]:
        check(pr, {"id": str, "text": str, "topic": OPT_STR, "intent": OPT_STR, "engines": dict}, "visibility.prompts[]")
        for e, r in pr["engines"].items():
            assert isinstance(r.get("named"), (bool, type(None))) and isinstance(r.get("cited"), (bool, type(None))), f"visibility.prompts[].engines.{e}"
    for f in d["fanout"]:
        check(f, {"query": str, "engine": OPT_STR, "count": int, "content": (dict, type(None))}, "visibility.fanout[]")
    for w in d["writing"]:
        check(w, {"title": str, "answers": list, "status": str, "item_id": OPT_STR}, "visibility.writing[]")
        assert w["status"] in {"drafted", "in_review", "published"}
    for a in d["alerts"]:
        check(a, {"kind": str, "text": str}, "visibility.alerts[]")
        assert a["kind"] in {"exposure", "competitor"}
    check_gap_entries(d, "visibility")


def test_content():
    d = load("content")
    check(d, {"counts": dict, "rows": list, "impact": list}, "content")
    check(d["counts"], {"recommendations": int, "published": int, "in_review": int}, "content.counts")
    for r in d["rows"]:
        check(r, {"id": str, "priority": str, "type": str, "title": str, "keyword": OPT_STR, "gap_source": OPT_STR, "status": str,
                  "published_at": OPT_STR, "item_id": OPT_STR, "why": (dict, type(None)), "for_whom": (dict, type(None)),
                  "approving_does": list}, "content.rows[]")
        assert r["priority"] in {"high", "med", "low", "done"}
        # Round 4: a row exists only once its draft does.
        assert r["status"] in {"draft_ready", "in_review", "published"}
        if r["status"] != "draft_ready":
            assert r["approving_does"] == []
    check_gap_entries(d, "content")


def test_creative():
    d = load("creative")
    check(d, {"counts": dict, "top": (dict, type(None)), "lowest": (dict, type(None)), "assets": list}, "creative")
    check(d["counts"], {"assets": int, "tracked_in_ads": int}, "creative.counts")
    for a in d["assets"]:
        check(a, {"id": str, "name": str, "thumbnail_url": OPT_STR, "tags": list, "origin": str,
                  "impressions": OPT_RECEIPT, "ctr": OPT_RECEIPT, "leads": OPT_RECEIPT, "flag": OPT_STR}, "creative.assets[]")
        assert a["origin"] in {"generated", "inherited", "uploaded"}
    check_gap_entries(d, "creative")


def test_value():
    d = load("value")
    check(d, {"period": str, "headline": dict, "rows": list, "totals": dict}, "value")
    for k in ("savings_captured", "savings_identified", "changes_shipped"):
        check_receipt(d["headline"].get(k), f"value.headline.{k}")
    for r in d["rows"]:
        check(r, {"change": str, "property": str, "annual_value": OPT_RECEIPT, "decided_by": str, "decided_at": str}, "value.rows[]")
        assert r["decided_by"] in {"you", "automatic"}
    check(d["totals"], {"annual_value": OPT_RECEIPT, "changes": int, "automatic_share": OPT_RECEIPT}, "value.totals")
    text = json.dumps(d).lower()
    assert "noi" not in text and "asset value" not in text, "no asset-value-at-multiple figure until Kyle confirms it"
    check_gap_entries(d, "value")



def test_decision_resolutions():
    d = load("decision")
    check(d["record"], {"label": str, "approved_unedited": int, "total": int, "threshold": NUM, "pct": NUM}, "decision.record")
    for m in d["in_motion"]:
        assert "note" in m and "action" in m
        if m["action"] is not None:
            check(m["action"], {"label": str, "href": str}, "decision.in_motion[].action")


def test_portfolio_and_property_resolutions():
    p = load("portfolio")
    assert isinstance(p["scope_label"], str)
    for prop in p["properties"]:
        assert isinstance(prop["top_item"]["needs_approval"], bool)
    pr = load("property")
    for k in ("hubspot_url", "brief_edit_url"):
        assert k in pr and isinstance(pr[k], (str, type(None)))


def test_plan_status_enum():
    for c in load("plan")["channels"]:
        assert c["status"] in {"running", "pending", "ended", "paused"}


def test_approval_items():
    d = load("approval_items")
    ids = set()
    for i, it in enumerate(d["items"]):
        check_item(it, f"approval_items.items[{i}]")
        ids.add(it["id"])
    for r in load("approvals")["batch"]["rows"]:
        assert r["item_id"] in ids, f"approval row {r['item_id']} has no item fixture"
    assert any(it.get("fair_housing_review") for it in d["items"])


WORKSPACE_FIXTURES = sorted(p.stem for p in FIXTURES.glob("*.json") if not p.stem.startswith("report_"))


@pytest.mark.parametrize("name", WORKSPACE_FIXTURES)
def test_gap_shape_everywhere(name):
    d = load(name)
    if isinstance(d, dict) and "gaps" in d:
        for g in d["gaps"]:
            assert isinstance(g, dict) and isinstance(g.get("message"), str), f"{name}: gaps are {{message, field?, source?}}"


def _without_hints(obj):
    # Profile field hints are editor guidance that names what never to target ("no age, family status …"); every value is still checked.
    if isinstance(obj, dict):
        return {k: _without_hints(v) for k, v in obj.items() if k != "hint"}
    if isinstance(obj, list):
        return [_without_hints(v) for v in obj]
    return obj


@pytest.mark.parametrize("name", WORKSPACE_FIXTURES)
def test_new_fixture_copy_avoids_targeting_language(name):
    text = json.dumps(_without_hints(load(name))).lower()
    for phrase in ("radius", "zip code", "zip targeting", "audience layer", "lookalike", "families", "family"):
        assert phrase not in text, f"{name}.json mentions {phrase!r}"
