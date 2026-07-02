"""Post-write hooks for the community brief (the brief-change fan-out).

WHY THIS EXISTS
    community_brief.write_field is the single write path for a brief field.
    When a field actually changes, downstream systems want to know:
      - Bridge 2: push the change to Fluency now (per-property feed upsert)
                  instead of waiting for the nightly batch.
      - Bridge 3: tell the fulfillment team in ClickUp (added later).

    Keeping this OUT of write_field keeps the write path focused and lets each
    leg self-guard and fail independently. Every leg is best-effort and runs
    off the request thread — a Fluency or ClickUp hiccup must never fail (or
    slow) a client's save.

CONTRACT
    on_field_written(...) is called only on a REAL change (old != new). It
    returns immediately; the legs run in a daemon thread. Each leg no-ops
    cleanly when its own feature flag / config is unset, so importing this
    module changes no behavior until a flag is turned on.
"""

from __future__ import annotations

import logging
import os
import threading

logger = logging.getLogger(__name__)


def _fluency_realtime_enabled() -> bool:
    return os.getenv("FLUENCY_REALTIME_SYNC", "").strip().lower() in ("1", "true", "yes")


def _leg_fluency(company_id: str, **_ctx) -> None:
    """Bridge 2 — push this property's brief to the Fluency sheet now."""
    if not _fluency_realtime_enabled():
        return
    try:
        import fluency_feed
        result = fluency_feed.sync_company(company_id)
        logger.info("brief_hooks.fluency company=%s -> %s", company_id, result)
    except Exception:
        logger.exception("brief_hooks.fluency failed for company %s", company_id)


def _leg_clickup_notice(company_id: str, **ctx) -> None:
    """Bridge 3 — tell the fulfillment team in ClickUp the brief changed.
    Self-gated (BRIEF_CLICKUP_NOTICE); no-ops when disabled."""
    try:
        import brief_change_notifier
        brief_change_notifier.leg(company_id, **ctx)
    except Exception:
        logger.exception("brief_hooks.clickup_notice failed for company %s", company_id)


# Ordered list of legs. Each self-guards on its own flag/config.
_LEGS = [_leg_fluency, _leg_clickup_notice]


def on_field_written(
    company_id: str,
    field_key: str,
    field_label: str,
    old_value: str,
    new_value: str,
    edited_by: str = "",
) -> None:
    """Fan a real brief change out to downstream legs, off the request thread."""
    company_id = str(company_id or "").strip()
    if not company_id:
        return
    ctx = {
        "field_key": field_key,
        "field_label": field_label,
        "old_value": old_value,
        "new_value": new_value,
        "edited_by": edited_by,
    }

    def _run():
        for leg in _LEGS:
            try:
                leg(company_id, **ctx)
            except Exception:
                logger.exception("brief_hooks leg %s failed for company %s",
                                 getattr(leg, "__name__", "?"), company_id)

    threading.Thread(target=_run, daemon=True).start()
