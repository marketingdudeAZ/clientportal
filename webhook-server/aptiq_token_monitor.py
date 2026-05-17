"""AptIQ token expiry monitor (ADR 0012).

Reads the ApartmentIQ_Token env var, decodes the JWT to extract the `exp`
claim, computes days remaining, and emits Loop events:

  - aptiq_token_checked:   always, so we can see the monitor is running
  - aptiq_token_warning:   when <= 14 days remain
  - aptiq_token_critical:  when <= 3 days remain

Each warning/critical event also posts to Slack via the existing
notifier module if available.

Standby failover: if `ApartmentIQ_Token_Standby` env var is set, the
monitor decodes both and reports on both. Rotation flow per the runbook:
populate standby first → verify → promote to primary.

Invoke via a Render Cron Job (weekly is fine; daily is overkill but
harmless). The script is idempotent: safe to run any number of times.

Usage:
    python3 -m webhook-server.aptiq_token_monitor
or:
    python3 webhook-server/aptiq_token_monitor.py

CLI flags:
    --notify-on-status STATUS    Post to Slack on a specific status only
                                  (default: warning | critical post; ok stays silent)
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import sys
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

WARN_DAYS = 14
CRITICAL_DAYS = 3


def _decode_jwt_exp(token: str) -> int | None:
    """Extract the `exp` claim (epoch seconds) from a JWT. Returns None
    on malformed input."""
    if not token:
        return None
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        payload_b64 = parts[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)  # add padding
        decoded = base64.urlsafe_b64decode(payload_b64)
        payload = json.loads(decoded)
        exp = payload.get("exp")
        return int(exp) if exp else None
    except Exception as exc:
        logger.warning("JWT decode failed: %s", exc)
        return None


def check_token(token: str, label: str) -> dict:
    """Return diagnostic info about one token."""
    exp = _decode_jwt_exp(token)
    if not token:
        return {"label": label, "status": "missing", "days_left": None, "exp": None}
    if exp is None:
        return {"label": label, "status": "unparseable", "days_left": None, "exp": None}
    now = datetime.now(timezone.utc).timestamp()
    days_left = (exp - now) / 86400
    if days_left <= 0:
        status = "expired"
    elif days_left <= CRITICAL_DAYS:
        status = "critical"
    elif days_left <= WARN_DAYS:
        status = "warning"
    else:
        status = "ok"
    return {
        "label":     label,
        "status":    status,
        "days_left": round(days_left, 2),
        "exp_iso":   datetime.fromtimestamp(exp, timezone.utc).isoformat(),
    }


def notify_slack(payload: dict) -> None:
    """Best-effort Slack notification via notifier module. Silent on failure."""
    try:
        import notifier
        if not hasattr(notifier, "post_to_slack"):
            return
        notifier.post_to_slack(
            channel="#digital-ops",
            text=(f"⚠️ AptIQ token {payload['label']} status: *{payload['status'].upper()}* "
                  f"({payload['days_left']} days left, expires {payload['exp_iso']})"),
        )
    except Exception as exc:
        logger.debug("notify_slack skipped: %s", exc)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quiet", action="store_true",
                        help="Don't print human output (still emits Loop events)")
    args = parser.parse_args()

    primary = os.environ.get("ApartmentIQ_Token", "")
    standby = os.environ.get("ApartmentIQ_Token_Standby", "")

    results = [check_token(primary, "primary")]
    if standby:
        results.append(check_token(standby, "standby"))

    # Emit Loop events
    try:
        import loop_writer
        for r in results:
            event_type = "aptiq_token_checked"
            if r["status"] == "critical":
                event_type = "aptiq_token_critical"
            elif r["status"] in ("warning", "expired"):
                event_type = "aptiq_token_warning"
            loop_writer.record(
                stage="ops",
                event_type=event_type,
                source="aptiq_token_monitor",
                magnitude=r.get("days_left"),
                payload=r,
                trigger="cron",
            )
    except Exception as exc:
        logger.warning("token_monitor Loop event emit failed: %s", exc)

    # Slack notifications (only when actionable)
    for r in results:
        if r["status"] in ("warning", "critical", "expired"):
            notify_slack(r)

    if not args.quiet:
        for r in results:
            print(f"  {r['label']:10s} status={r['status']:10s} days_left={r['days_left']} exp={r.get('exp_iso')}")

    # Exit code: 0 ok | 1 warning | 2 critical/expired (useful for cron alerting)
    statuses = {r["status"] for r in results}
    if "critical" in statuses or "expired" in statuses:
        return 2
    if "warning" in statuses:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
