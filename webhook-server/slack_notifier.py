"""Slack notification layer for Loop events (Phase 2 / ADR 0019).

Pure post-to-webhook. No SDK. Reads webhook URL from env var
`SLACK_DIGITAL_OPS_WEBHOOK` (set in Render). Silently no-ops if unset
— never blocks business operations.

Module surface:

  post(channel_key, text, *, blocks=None) -> bool
      Low-level post to a named channel webhook.

  post_loop_event(event) -> bool
      Render a loop_events row into a Slack message. Tight signal:
      only forecast_deviation, recommendation_proposed, token_warning,
      first_lease, and r1_violation get posted by default.

  alert(text, *, level='warning') -> bool
      Generic ops alert with level prefix (info|warning|critical).

Channel registry (each channel has its own env var so they can route
to different Slack channels):

  digital_ops   — SLACK_DIGITAL_OPS_WEBHOOK
  am_team       — SLACK_AM_TEAM_WEBHOOK
  client_wins   — SLACK_CLIENT_WINS_WEBHOOK

Best-effort: any failure logs a warning and returns False; never raises.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

import requests

logger = logging.getLogger(__name__)


# Map channel key → env var holding the Slack incoming-webhook URL
CHANNELS = {
    "digital_ops":  "SLACK_DIGITAL_OPS_WEBHOOK",
    "am_team":      "SLACK_AM_TEAM_WEBHOOK",
    "client_wins":  "SLACK_CLIENT_WINS_WEBHOOK",
}

# Loop event types that get auto-posted via post_loop_event(). Tight
# list to avoid alert fatigue (Phase 2 success criteria: < 5/day).
NOTIFIABLE_EVENT_TYPES = {
    # ops — high signal
    "aptiq_token_warning":      "digital_ops",
    "aptiq_token_critical":     "digital_ops",
    "r1_violation":             "digital_ops",
    # convert — celebration
    "first_lease_signed":       "client_wins",
    # optimize — needs review (co-pilot mode)
    "recommendation_proposed":  "am_team",
    "forecast_deviation":       "am_team",
}


def _webhook_url(channel_key: str) -> Optional[str]:
    env_name = CHANNELS.get(channel_key)
    if not env_name:
        return None
    return os.environ.get(env_name) or None


def post(channel_key: str, text: str, *, blocks: list | None = None) -> bool:
    """Post a message to the named channel's incoming webhook.

    Returns True on success, False on any error (network, missing webhook,
    Slack rejection). Never raises.
    """
    url = _webhook_url(channel_key)
    if not url:
        # Webhook not configured — log once at debug level, treat as no-op
        logger.debug("slack_notifier: %s webhook unset — skipping post", channel_key)
        return False

    payload: dict = {"text": text}
    if blocks:
        payload["blocks"] = blocks

    try:
        r = requests.post(url, json=payload, timeout=5)
    except requests.RequestException as exc:
        logger.warning("slack_notifier %s network error: %s", channel_key, exc)
        return False

    if r.status_code != 200:
        logger.warning("slack_notifier %s -> %s %s",
                       channel_key, r.status_code, r.text[:200])
        return False
    return True


def alert(text: str, *, level: str = "warning",
          channel_key: str = "digital_ops") -> bool:
    """Generic alert with level prefix."""
    icon = {"info": "ℹ️", "warning": "⚠️", "critical": "🚨"}.get(level, "•")
    return post(channel_key, f"{icon} {text}")


# ── Loop event → Slack ──────────────────────────────────────────────────────

def post_loop_event(event: dict) -> bool:
    """Render a loop_events row into a Slack post if its event_type is
    in NOTIFIABLE_EVENT_TYPES. Returns True if posted, False otherwise.

    `event` is the shape returned by loop_writer.query_recent — at
    minimum needs event_type, stage, occurred_at, payload, magnitude.
    """
    et = (event or {}).get("event_type", "")
    channel = NOTIFIABLE_EVENT_TYPES.get(et)
    if not channel:
        return False

    text = _render_event(event)
    return post(channel, text)


def _render_event(event: dict) -> str:
    """Render a loop_event into a human-readable Slack message."""
    et = event.get("event_type", "")
    payload = event.get("payload") or {}
    mag     = event.get("magnitude")
    uuid    = event.get("property_uuid") or "(no uuid)"

    if et == "aptiq_token_warning":
        days = payload.get("days_left", "?")
        return f"⚠️ AptIQ token expires in {days} days — rotation needed soon."

    if et == "aptiq_token_critical":
        days = payload.get("days_left", "?")
        return (f"🚨 *AptIQ token critical:* {days} days until expiry. "
                f"Rotate today. See `docs/RUNBOOKS/aptiq-token-rotation.md`.")

    if et == "r1_violation":
        return (f"🚨 *R1 violation* detected: {event.get('error_message','(no detail)')[:200]}"
                f"\nPayload: ```{json.dumps(payload)[:500]}```")

    if et == "first_lease_signed":
        return (f"🎉 First lease signed for property `{uuid}` since Loop "
                f"tracking started — Convert stage closing the loop.")

    if et == "recommendation_proposed":
        action = payload.get("action", "?")
        reason = payload.get("reason", "")
        impact = payload.get("forecast_impact")
        impact_str = f"+{impact} leases" if impact else "impact unknown"
        return (f"💡 *Loop recommendation* for `{uuid}`: *{action}* — "
                f"_{reason}_. Projected {impact_str}. "
                f"<https://digital.rpmliving.com/staging/portal-dashboard?uuid={uuid}&view=loop&tab=plan|Open Plan tab>")

    if et == "forecast_deviation":
        prev = payload.get("prior_forecast")
        curr = payload.get("new_forecast")
        return (f"📊 *Forecast deviation* for `{uuid}`: "
                f"prior={prev} → new={curr}. Check inputs.")

    # Fallback
    return f"Loop event: `{et}` (uuid={uuid}, magnitude={mag})"
