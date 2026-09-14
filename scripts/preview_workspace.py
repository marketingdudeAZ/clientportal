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
from pathlib import Path

from flask import Flask, Response, abort, jsonify, request

REPO = Path(__file__).resolve().parent.parent
PAGE = REPO / "webhook-server" / "portal_pages" / "workspace.html"
FIXTURES = REPO / "tests" / "fixtures" / "workspace"
PORT = 5057

app = Flask(__name__)


def _fixture(name: str):
    with open(FIXTURES / f"{name}.json", encoding="utf-8") as fh:
        return json.load(fh)


@app.get("/")
def root():
    return Response('<a href="/workspace?t=preview">Open the workspace preview</a>', mimetype="text/html")


@app.get("/workspace")
def workspace():
    # Read on every request so edits to the page show on reload.
    return Response(PAGE.read_text(encoding="utf-8"), mimetype="text/html", headers={"Cache-Control": "no-store"})


@app.get("/api/workspace/me")
def me():
    return jsonify(_fixture("me"))


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
    for it in _all_items(_fixture("work")):
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
    for it in _all_items(_fixture("work")):
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
