#!/usr/bin/env python3
"""Preview the workspace page locally against fixture JSON.

    python3 scripts/preview_workspace.py
    open "http://127.0.0.1:5057/workspace?t=preview"

The page is served from webhook-server/portal_pages/workspace.html, and the API
paths it calls (/api/workspace/*, /api/ask/*) answer from tests/fixtures/workspace.
`?t=preview` puts the page in signed-link mode so it skips Clerk; this server
ignores the token. Nothing here imports or touches the production server.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, Response, abort, jsonify, request

REPO = Path(__file__).resolve().parent.parent
PAGE = REPO / "webhook-server" / "portal_pages" / "workspace.html"
REPORT_PAGE = REPO / "webhook-server" / "portal_pages" / "workspace_report.html"
FIXTURES = REPO / "tests" / "fixtures" / "workspace"
PORT = 5057

app = Flask(__name__)

# Demo switches for screenshots, flipped at /__preview/<name>?on=1|0.
SWITCHES = {"approvals_empty": False, "cannot_decide": False}


@app.get("/__preview/<name>")
def preview_switch(name: str):
    if name not in SWITCHES:
        abort(404)
    SWITCHES[name] = request.args.get("on", "1") in ("1", "true", "yes")
    return jsonify(SWITCHES)


def _fixture(name: str):
    with open(FIXTURES / f"{name}.json", encoding="utf-8") as fh:
        return json.load(fh)


PREVIEW_ROLE_HEADER = "X-Workspace-Preview-Role"
INTERNAL_ONLY_PATHS = ("/api/workspace/portfolio", "/api/workspace/signals")


def _as_client() -> bool:
    return request.headers.get(PREVIEW_ROLE_HEADER, "").strip().lower() == "client"


def _client_filter(obj):
    """What a client-role caller gets: no internal entries, no Fair Housing review flag."""
    if isinstance(obj, list):
        return [_client_filter(x) for x in obj if not (isinstance(x, dict) and x.get("visibility") == "internal")]
    if isinstance(obj, dict):
        return {k: _client_filter(v) for k, v in obj.items() if k != "fair_housing_review"}
    return obj


@app.before_request
def _preview_role_gate():
    # Mirrors the API contract: the header is honored on reads only, and /me ignores it.
    if request.method == "GET" and _as_client() and request.path.startswith(INTERNAL_ONLY_PATHS):
        return jsonify({"error": "forbidden", "detail": "Internal only."}), 403
    return None


@app.after_request
def _preview_role_filter(resp):
    if (request.method == "GET" and _as_client() and resp.is_json and resp.status_code == 200
            and request.path.startswith(("/api/workspace/", "/api/ask/")) and request.path != "/api/workspace/me"):
        resp.set_data(json.dumps(_client_filter(resp.get_json())))
    return resp


@app.get("/")
def root():
    return Response('<a href="/workspace?t=preview">Open the workspace preview</a>', mimetype="text/html")


@app.get("/workspace")
def workspace():
    # Read on every request so edits to the page show on reload.
    return Response(PAGE.read_text(encoding="utf-8"), mimetype="text/html", headers={"Cache-Control": "no-store"})


@app.get("/api/workspace/me")
def me():
    data = _fixture("me")
    if SWITCHES["cannot_decide"]:
        data["can_decide"] = False
    return jsonify(data)


@app.get("/api/workspace/portfolio")
def portfolio():
    return jsonify(_fixture("portfolio"))


def _all_items(work: dict) -> list[dict]:
    groups = work.get("groups") or {}
    return list(groups.get("late") or []) + list(groups.get("this_week") or [])


@app.get("/api/workspace/work")
def work():
    data = _fixture("work")
    status = request.args.get("status", "to_do")
    if status != "all":
        groups = data["groups"]
        for key in ("late", "this_week"):
            groups[key] = [it for it in groups.get(key) or [] if it.get("status") == status]
    return jsonify(data)


@app.get("/api/workspace/work/<path:item_id>")
def work_item(item_id: str):
    detail = _fixture("item")
    if item_id == detail["id"]:
        return jsonify(detail)
    for it in _all_items(_fixture("work")) + _fixture("approval_items")["items"]:
        if it["id"] == item_id:
            return jsonify(it)
    abort(404)


@app.post("/api/workspace/work/<path:item_id>/decision")
def decision(item_id: str):
    body = request.get_json(silent=True) or {}
    if body.get("action") not in ("approve", "not_now"):
        return jsonify({"error": "action must be approve or not_now"}), 400
    if body.get("action") == "not_now" and not body.get("reason"):
        return jsonify({"error": "reason is required when the action is not_now"}), 400
    data = copy.deepcopy(_fixture("decision"))
    now = datetime.now(timezone.utc)
    data["decided_at"] = now.isoformat()
    data["undo"]["until"] = (now + timedelta(minutes=10)).isoformat()
    for it in _all_items(_fixture("work")) + _fixture("approval_items")["items"]:
        if it["id"] == item_id:
            data["item"]["id"] = it["id"]
            data["item"]["title"] = it["title"]
    return jsonify(data)


@app.get("/api/workspace/property")
def property_():
    return jsonify(_fixture("property"))


@app.get("/api/workspace/performance")
def performance():
    return jsonify(_fixture("performance"))


@app.get("/api/workspace/plan")
def plan():
    return jsonify(_fixture("plan"))


@app.get("/api/workspace/client-view")
def client_view():
    return jsonify(_fixture("client_view"))


@app.get("/api/workspace/signals")
def signals():
    data = _fixture("signals")
    company_id = request.args.get("company_id")
    if company_id:
        data["signals"] = [s for s in data["signals"] if s["company_id"] == company_id]
        data["counts"] = {sev: sum(1 for s in data["signals"] if s["severity"] == sev) for sev in ("high", "medium", "low")}
    return jsonify(data)


@app.post("/api/workspace/signals/<path:signal_id>/start-work")
def start_work(signal_id: str):
    body = request.get_json(silent=True) or {}
    if not body.get("company_id"):
        return jsonify({"error": "company_id is required"}), 400
    if signal_id not in {s["id"] for s in _fixture("signals")["signals"]}:
        abort(404)
    return jsonify({"work_item_id": "hubdb_rec:991"})


@app.post("/api/workspace/requests/draft")
def request_draft():
    body = request.get_json(silent=True) or {}
    if not str(body.get("text") or "").strip():
        return jsonify({"error": "text is required"}), 400
    return jsonify(_fixture("request_draft"))


@app.post("/api/workspace/requests")
def request_file():
    body = request.get_json(silent=True) or {}
    tickets = body.get("tickets") or []
    if not tickets:
        return jsonify({"error": "tickets are required"}), 400
    created, failed = [], []
    for i, t in enumerate(tickets):
        if not str(t.get("title") or "").strip():
            failed.append({"draft_id": t.get("draft_id"), "reason": "A ticket needs a title."})
        else:
            created.append({"draft_id": t.get("draft_id"), "work_item_id": f"portal_ticket:{4471 + i}", "clickup_task_id": f"86b2k7x{i}"})
    return jsonify({"as_of": _fixture("request_created")["as_of"], "created": created, "failed": failed})


@app.get("/api/workspace/requests")
def requests_recent():
    return jsonify(_fixture("requests_recent"))


@app.get("/api/workspace/search")
def search():
    q = (request.args.get("q") or "").strip().lower()
    results = [r for r in _fixture("search")["results"] if q and q in f"{r['title']} {r['subtitle']}".lower()]
    return jsonify({"as_of": _fixture("search")["as_of"], "results": results[:20]})


@app.post("/api/workspace/work/<path:item_id>/undo")
def undo(item_id: str):
    body = request.get_json(silent=True) or {}
    if not body.get("company_id"):
        return jsonify({"error": "company_id is required"}), 400
    if item_id != _fixture("item")["id"]:
        # Demonstrates the 409 path: this source has already pushed its change out.
        return jsonify({"error": "not_undoable", "reason": "The listing change already went out to the feeds, so it can't be pulled back from here."}), 409
    return jsonify(_fixture("undo"))


def _company(company_id: str | None) -> dict:
    for c in _fixture("me")["companies"]:
        if c["company_id"] == company_id:
            return c
    return {}


@app.get("/api/workspace/dashboard")
def dashboard():
    return jsonify(_fixture("dashboard"))


@app.get("/api/workspace/approvals")
def approvals():
    data = _fixture("approvals_empty" if SWITCHES["approvals_empty"] else "approvals")
    category = request.args.get("category")
    if category:
        data["batch"]["rows"] = [r for r in data["batch"]["rows"] if r["category"] == category]
    return jsonify(data)


@app.get("/api/workspace/property-overview")
def property_overview():
    # One demo property's data serves every property; name and size follow the one asked for.
    data = _fixture("property_overview")
    company_id = request.args.get("company_id")
    c = _company(company_id)
    if c:
        data.update({k: c[k] for k in ("name", "city", "state", "units") if k in c})
        data["links"] = {k: v.replace("<id>", company_id) for k, v in data["links"].items()}
    return jsonify(data)


def _scoped(name: str) -> dict:
    return _fixture(name)


@app.get("/api/workspace/media-plan")
def media_plan():
    return jsonify(_scoped("media_plan"))


@app.get("/api/workspace/visibility")
def visibility():
    return jsonify(_scoped("visibility"))


@app.get("/api/workspace/content")
def content():
    return jsonify(_scoped("content"))


@app.get("/api/workspace/creative")
def creative():
    data = _scoped("creative")
    kind = request.args.get("type")
    tag = {"photos": "photo", "videos": "video", "ad_creative": "ad", "floor_plans": "floor plan", "documents": "document"}.get(kind or "")
    if tag:
        data["assets"] = [a for a in data["assets"] if tag in a["tags"]]
    return jsonify(data)


@app.get("/api/workspace/value")
def value():
    return jsonify(_fixture("value"))


# The monthly report page and its API, at the same paths production uses.
@app.get("/workspace/report")
def report_page():
    return Response(REPORT_PAGE.read_text(encoding="utf-8"), mimetype="text/html", headers={"Cache-Control": "no-store"})


@app.get("/api/workspace/report")
def report_api():
    company_id = (request.args.get("company_id") or "").strip()
    if not company_id:
        return jsonify({"error": "company_id required"}), 400
    reports = {}
    for path in sorted(FIXTURES.glob("report_*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        reports[(str(data["property"]["company_id"]), data["month"])] = data
    months = sorted(m for (cid, m) in reports if cid == company_id)
    if not months:
        return jsonify({"error": "report_unavailable", "detail": "No report for this property yet."}), 404
    month = (request.args.get("month") or "").strip() or months[-1]
    if (company_id, month) not in reports:
        return jsonify({"error": "month_unavailable", "detail": f"No report for {month}."}), 404
    return jsonify(reports[(company_id, month)])


@app.get("/api/ask/questions")
def ask_questions():
    return jsonify(_fixture("ask_questions"))


@app.post("/api/ask/<key>")
def ask_answer(key: str):
    keys = [q["key"] for q in _fixture("ask_questions")["questions"]]
    if key not in keys:
        return jsonify({"error": "Unknown question", "question": key, "available": keys}), 404
    data = _fixture("ask_answer")
    data["question"] = key
    data["label"] = next(q["label"] for q in _fixture("ask_questions")["questions"] if q["key"] == key)
    return jsonify(data)


if __name__ == "__main__":
    print(f"  workspace preview: http://127.0.0.1:{PORT}/workspace?t=preview")
    app.run(host="127.0.0.1", port=PORT, debug=False)
