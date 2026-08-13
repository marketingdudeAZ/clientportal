"""Budget-sync internal API — /api/internal/budget-sync/* (plan §4e).

Endpoints, all internal-only (X-Internal-Key = INTERNAL_API_KEY):

  POST /api/internal/budget-sync/reconcile   read-only drift report vs a tab
  POST /api/internal/budget-sync/compare     three-way HubSpot vs live vs shadow
  POST /api/internal/budget-sync/flags       set/clear budget_discrepancy
  POST /api/internal/budget-sync/sync        converge a tab onto HubSpot

WHY THIS EXISTS
    Render Cron services in this codebase trigger /api/internal/* endpoints on
    a schedule (docs/ARCHITECTURE.md). The CLI in scripts/budget_sync_run.py
    covers operator-driven runs; this is the same work on a schedule, so the
    hourly comparison the parallel run depends on has something to call.

WHAT IS DELIBERATELY NOT HERE
    --bootstrap. Seeding the shadow tab exempts both write ceilings, and the
    only thing keeping that narrow is that a human runs it once, watching, from
    a shell. Putting it behind an HTTP endpoint would make "ceilings off"
    reachable by anything holding the internal key, including a
    mis-scheduled cron. It stays CLI-only.

WRITES ARE STILL DOUBLE-GATED
    Reaching a write endpoint is not the same as writing. /sync still requires
    BUDGET_SYNC_ENABLED, /flags still requires BUDGET_VARIANCE_FLAGS_ENABLED,
    and both default to dry-run unless ?apply=true. An endpoint that exists is
    not an endpoint that is armed.
"""

from __future__ import annotations

import logging
import os

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

budget_sync_api_bp = Blueprint("budget_sync_api", __name__)

_PREFIX = "/api/internal/budget-sync"


def _is_internal(req) -> bool:
    key = req.headers.get("X-Internal-Key", "")
    return bool(key and key == os.environ.get("INTERNAL_API_KEY", ""))


def _wants_apply(req) -> bool:
    """?apply=true. Absent means dry run — every accidental call is a no-op."""
    return str(req.args.get("apply", "")).lower() == "true"


def _guard():
    """None if authorized, else a Flask response tuple."""
    if not _is_internal(request):
        return jsonify({"error": "internal key required"}), 401
    return None


@budget_sync_api_bp.route(f"{_PREFIX}/reconcile", methods=["POST"])
def reconcile():
    """Read-only drift report. Never writes, under any parameter."""
    if (bad := _guard()):
        return bad
    import budget_reconcile
    tab = request.args.get("tab") or None
    return jsonify(budget_reconcile.reconcile(tab=tab))


@budget_sync_api_bp.route(f"{_PREFIX}/compare", methods=["POST"])
def compare():
    """Three-way comparison. Never writes.

    This is the endpoint the hourly cron calls during the parallel run. `ok`
    is false when the NEW system is wrong somewhere the old one is right —
    deliberately not "the tabs disagree", which is expected and not news.
    """
    if (bad := _guard()):
        return bad
    import budget_compare
    report = budget_compare.compare()
    if not report["ok"]:
        logger.error("budget compare: new system wrong on %d properties",
                     report["new_wrong_count"])
    return jsonify(report)


@budget_sync_api_bp.route(f"{_PREFIX}/flags", methods=["POST"])
def flags():
    """Set and clear budget_discrepancy on company records.

    Dry run unless ?apply=true, and even then a no-op without
    BUDGET_VARIANCE_FLAGS_ENABLED.
    """
    if (bad := _guard()):
        return bad
    import budget_variance_flags
    return jsonify(budget_variance_flags.run(dry_run=not _wants_apply(request)))


@budget_sync_api_bp.route(f"{_PREFIX}/sync", methods=["POST"])
def sync():
    """Converge a tab onto HubSpot.

    Dry run unless ?apply=true, and even then a no-op without
    BUDGET_SYNC_ENABLED. ?target=shadow|live overrides BUDGET_SYNC_TARGET —
    which itself defaults to shadow, so reaching this endpoint without
    thinking about it cannot touch what Fluency reads.

    bootstrap is not exposed. See the module docstring.
    """
    if (bad := _guard()):
        return bad
    import budget_sync
    report = budget_sync.sync(dry_run=not _wants_apply(request),
                              target=request.args.get("target") or None)
    if report.get("aborted"):
        logger.error("budget sync aborted: %s", report["aborted"])
    return jsonify(report)
