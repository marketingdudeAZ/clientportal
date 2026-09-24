"""Manual trigger for the quarterly heatmap folders (internal-only).

POST /api/heatmap-folders/run            dry run: reports what would be created
POST /api/heatmap-folders/run?apply=true creates the folders (idempotent)
     optional JSON body {"quarter": "Q4 26"}

Header: X-Internal-Key = INTERNAL_API_KEY. This is the "re-run" button for the
SEO team when a quarter's folders are missing; the scheduled job is
heatmap_folders_cron.py.
"""

from __future__ import annotations

import os

from flask import Blueprint, jsonify, request

heatmap_folders_api_bp = Blueprint("heatmap_folders_api", __name__)


def _is_internal(req) -> bool:
    key = req.headers.get("X-Internal-Key", "")
    return bool(key and key == os.environ.get("INTERNAL_API_KEY", ""))


@heatmap_folders_api_bp.route("/api/heatmap-folders/run", methods=["POST"])
def run_folders():
    if not _is_internal(request):
        return jsonify({"error": "internal key required"}), 401
    import heatmap_folders as hf
    apply = (request.args.get("apply") or "").lower() == "true"
    quarter = (request.get_json(silent=True) or {}).get("quarter")
    try:
        return jsonify(hf.run(dry_run=not apply, quarter=quarter))
    except RuntimeError as e:
        return jsonify({"ok": False, "error": str(e)}), 503
