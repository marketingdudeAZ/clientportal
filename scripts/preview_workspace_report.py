"""Preview the Workspace monthly report page against its fixtures.

    python3 scripts/preview_workspace_report.py
    open http://127.0.0.1:5058/

Serves webhook-server/portal_pages/workspace_report.html at /workspace/report
and every tests/fixtures/workspace/report_*.json at /api/workspace/report,
keyed by the fixture's company_id and month. It does not import the production
server, touch any credential, or call any live system.
"""

from __future__ import annotations

import json
import pathlib

from flask import Flask, Response, jsonify, redirect, request

ROOT = pathlib.Path(__file__).resolve().parents[1]
PAGE = ROOT / "webhook-server" / "portal_pages" / "workspace_report.html"
FIXTURES = ROOT / "tests" / "fixtures" / "workspace"
PORT = 5058

app = Flask(__name__)


def _fixtures() -> dict[tuple[str, str], dict]:
    out = {}
    for path in sorted(FIXTURES.glob("report_*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        out[(str(data["property"]["company_id"]), data["month"])] = data
    return out


@app.get("/")
def index():
    fixtures = _fixtures()
    if not fixtures:
        return Response("No report fixtures found in tests/fixtures/workspace/", status=404)
    company_id, month = sorted(fixtures)[-1]
    return redirect(f"/workspace/report?company_id={company_id}&month={month}")


@app.get("/workspace/report")
def page():
    return Response(PAGE.read_text(encoding="utf-8"), mimetype="text/html")


@app.get("/api/workspace/report")
def api():
    company_id = (request.args.get("company_id") or "").strip()
    if not company_id:
        return jsonify({"error": "company_id required"}), 400
    fixtures = _fixtures()
    months = sorted(m for (cid, m) in fixtures if cid == company_id)
    if not months:
        return jsonify({"error": "report_unavailable", "detail": "No fixture for this property."}), 404
    month = (request.args.get("month") or "").strip() or months[-1]
    data = fixtures.get((company_id, month))
    if data is None:
        return jsonify({"error": "month_unavailable", "detail": f"No fixture for {month}."}), 404
    return jsonify(data)


if __name__ == "__main__":
    print(f"Workspace report preview: http://127.0.0.1:{PORT}/")
    app.run(host="127.0.0.1", port=PORT, debug=False)
