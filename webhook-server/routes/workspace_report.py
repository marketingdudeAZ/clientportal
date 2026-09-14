"""Workspace monthly report — page and API.

GET /api/workspace/report?company_id=&month=YYYY-MM
    The report JSON (contract: docs/handoffs/PORTAL_WORKSPACE_BUILD_PLAN.md,
    "Report contract"). 404 unless WORKSPACE_ENABLED; then the `workspace`
    feature gate and per-property access.

GET /workspace/report
    The self-contained page. Same flag. Identity comes from Clerk (Bearer) or
    the signed preview link the page forwards as X-Workspace-Link — never from
    `?email=`.

Routes never touch BigQuery, HubSpot or Claude; skills/workspace_report.py does.
Flags are read from os.environ directly: both config.py files belong to the
Workspace API workstream.
"""

from __future__ import annotations

import json
import logging
import os

from flask import Blueprint, Response, jsonify, request

from _route_utils import is_internal_caller, require_access, require_company_access

logger = logging.getLogger(__name__)

workspace_report_bp = Blueprint("workspace_report", __name__)

_PAGE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "portal_pages", "workspace_report.html")
_TRUTHY = {"1", "true", "yes", "on"}


def _enabled() -> bool:
    return os.environ.get("WORKSPACE_ENABLED", "").strip().lower() in _TRUTHY


def _not_found():
    return jsonify({"error": "not_found"}), 404


@workspace_report_bp.route("/api/workspace/report", methods=["GET"])
def workspace_report_api():
    if not _enabled():
        return _not_found()
    if not is_internal_caller():
        gate = require_access("workspace")
        if gate:
            return gate
    company_id = (request.args.get("company_id") or "").strip()
    gate = require_company_access(company_id)
    if gate:
        return gate

    from skills import workspace_report as wr

    month = (request.args.get("month") or "").strip() or None
    try:
        report = wr.build_report(company_id, month)
    except wr.InvalidMonth as exc:
        return jsonify({"error": "invalid_month", "detail": str(exc)}), 400
    except wr.MonthUnavailable as exc:
        return jsonify({"error": "month_unavailable", "detail": str(exc)}), 404
    except wr.PropertyUnavailable as exc:
        return jsonify({"error": "report_unavailable", "detail": str(exc)}), 404
    except wr.SourceError as exc:
        logger.error("workspace report source failed for %s %s: %s", company_id, month, exc)
        return jsonify({"error": "source_failed", "detail": str(exc)[:300]}), 502
    resp = jsonify(report)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@workspace_report_bp.route("/workspace/report", methods=["GET"])
def workspace_report_page():
    if not _enabled():
        return _not_found()
    try:
        with open(_PAGE, encoding="utf-8") as fh:
            html = fh.read()
    except OSError as exc:
        logger.error("workspace report page missing: %s", exc)
        return Response("Report page not found", status=500)
    pk = os.environ.get("CLERK_PUBLISHABLE_KEY", "").strip()
    if pk.startswith("pk_"):
        html = html.replace("window.__CLERK_PK__ = '';", f"window.__CLERK_PK__ = {json.dumps(pk)};", 1)
    resp = Response(html, mimetype="text/html")
    resp.headers["Cache-Control"] = "no-store"
    # The signed preview link arrives as ?t=; don't leak it to other origins.
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp
