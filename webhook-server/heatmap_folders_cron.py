"""Quarterly SEO heatmap folders — entry point for a Render Cron Job.

Schedule: `0 13 1 3,6,9,12 *` (6am Phoenix on Mar/Jun/Sep/Dec 1). Creates the
current quarter's folders weeks before the SEO team pulls screenshots.
Command:
  python webhook-server/heatmap_folders_cron.py            # this quarter
  python webhook-server/heatmap_folders_cron.py "Q4 26"    # explicit quarter

Required env vars on the Cron Job service:
  HUBSPOT_API_KEY        — read companies (seo_budget)
  HEATMAP_DRIVE_SA_JSON  — heatmap-bot service account JSON (Drive access to
                           the SEO Heatmaps folder)
  HEATMAP_FOLDERS_ENABLED=true — without it the job only dry-runs and reports
Optional:
  CLICKUP_API_KEY                  — needed to report/alert at all
  HEATMAP_FOLDERS_REPORT_TASK_ID   — task that gets the result comment (Ben's
                                     quarterly "Confirm heatmap folders" task)
  HEATMAP_DRIVE_PARENT_ID          — defaults to the SEO Heatmaps folder

Errors (any property that could not be created) open one task on the ClickUp
"FAILING ERRORS" list. Re-running is always safe: existing folders are skipped.

Exit codes: 0 ok, 1 errors (alert raised), 2 configuration error.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone

import requests

sys.path.insert(0, os.path.dirname(__file__))
import heatmap_folders as hf  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("heatmap_folders_cron")

CLICKUP_BASE = "https://api.clickup.com/api/v2"
ALERT_LIST_ID = os.environ.get("HEATMAP_FOLDERS_ALERT_LIST_ID", "901115437267")  # FAILING ERRORS
ENABLED = os.environ.get("HEATMAP_FOLDERS_ENABLED", "").lower() == "true"


def _cu(method: str, path: str, **kw) -> dict | None:
    token = os.environ.get("CLICKUP_API_KEY", "")
    if not token:
        logger.error("CLICKUP_API_KEY not set — cannot report")
        return None
    try:
        r = requests.request(method, f"{CLICKUP_BASE}/{path}",
                             headers={"Authorization": token}, timeout=20, **kw)
        return r.json() if r.ok else None
    except requests.RequestException as e:
        logger.error("ClickUp %s %s failed: %s", method, path, e)
        return None


def summary(rep: dict) -> str:
    mode = "DRY RUN (HEATMAP_FOLDERS_ENABLED is not true): nothing created" if rep["dry_run"] else "Created"
    lines = [f"Heatmap folders {rep['quarter']}: {mode}.",
             f"• Properties with an SEO budget: {rep['properties']}",
             f"• Quarter folders created: {rep['quarter_folders_created']}"
             f" (new property folders: {rep['company_folders_created']})",
             f"• Already existed: {rep['already_existed']}",
             f"• Errors: {len(rep['errors'])}"]
    if rep["dry_run"]:
        lines.append(f"• Would create: {rep.get('would_create_count', 0)}")
    lines += [f"  - {e}" for e in rep["errors"][:20]]
    lines.append("Safe to re-run any time: existing folders are skipped.")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    quarter = argv[1] if len(argv) > 1 else None
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    try:
        rep = hf.run(dry_run=not ENABLED, quarter=quarter)
    except RuntimeError as e:          # missing configuration
        logger.error("configuration error: %s", e)
        _cu("POST", f"list/{ALERT_LIST_ID}/task",
            json={"name": f"Heatmap folders failing: {e}"[:200], "priority": 1,
                  "description": f"Run at {stamp} could not start: {e}. No folders were created."})
        return 2
    text = summary(rep)
    logger.info(text)
    task = os.environ.get("HEATMAP_FOLDERS_REPORT_TASK_ID", "")
    if task:
        _cu("POST", f"task/{task}/comment", json={"comment_text": text, "notify_all": True})
    if rep["errors"]:
        _cu("POST", f"list/{ALERT_LIST_ID}/task",
            json={"name": f"Heatmap folders failing: {len(rep['errors'])} properties ({rep['quarter']})"[:200],
                  "priority": 1, "description": f"Run at {stamp}.\n\n{text}\n\nRe-run once fixed; completed folders are skipped."})
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
