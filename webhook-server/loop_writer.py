"""Loop Event Bus writer skill (ADR 0010).

The single canonical way to write loop_events rows. Every Loop stage's
work — every cron, every webhook handler, every async job — goes
through this module. This is intentional: one writer means one log
format, one observability surface, one place to enforce taxonomy.

Public surface:

    record(stage, event_type, *, property_uuid=None, ...) -> event_id
        Write one row. Returns the event_id (so callers can chain
        parent_event_id on follow-ons). Idempotent if event_id is
        supplied.

    track_job(stage, event_type, *, ...) -> context manager
        Wraps a unit of work, auto-logs start/end/runtime/error.
        Usage:
            with track_job(stage='ops', event_type='aptiq_backfill',
                           property_uuid=uuid) as job:
                # do the work
                job.set_result({'rows_written': 13})
            # On exit, status='completed' (or 'failed' on exception).

Constants:

    LOOP_STAGES         set of valid stage values
    LOOP_EVENT_TYPES    registry of known event_type strings (extensible)

Schema version is `LOOP_EVENT_SCHEMA_VERSION` — bump on breaking changes.

Reads from env:
    BIGQUERY_PROJECT_ID
    BIGQUERY_DATASET_PROD

If BQ is unavailable (creds missing, network down), this module logs a
warning and DOES NOT raise — Loop events are non-load-bearing for
business operations; a failed write is observability, not data loss.
The exception is `track_job`, which still completes the work even if
the event write fails.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid as _uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Generator, Optional

logger = logging.getLogger(__name__)

# ── Schema + taxonomy ────────────────────────────────────────────────────────

LOOP_EVENT_SCHEMA_VERSION = 1

LOOP_STAGES = frozenset({
    "attract",            # Paid + organic acquisition
    "engage",             # Property page, content, AEO, reviews
    "convert",            # Lead → tour → application → lease
    "optimize",           # AI engine: forecasts, recommendations, reconfigs
    "ops",                # Internal: cron runs, backfills, errors
    # "delight_advocate" — reserved for partner team; not yet used
})

LOOP_EVENT_TYPES = {
    # attract
    "marquee_generated", "ad_variant_published",
    "keyword_rank_changed", "gbp_post_published",
    "fluency_provisioned", "ai_mention_index_changed",
    # engage
    "property_brief_published", "community_brief_published",
    "aeo_content_generated", "aeo_citation_detected",
    "review_received", "page_health_changed",
    # convert
    "lead_submitted", "tour_scheduled", "application_received",
    "lease_signed", "am_activity",
    # optimize
    "forecast_run", "recommendation_proposed",
    "recommendation_approved", "recommendation_rejected",
    "tier_changed", "loop_mode_changed",
    # ops
    "cron_started", "cron_completed", "backfill_completed",
    "aptiq_token_warning", "aptiq_token_critical", "aptiq_token_checked",
    "aptiq_history_backfill", "seo_refresh", "hyly_pull",
    "r1_violation",
    # operational job tracking
    "job",
}


def is_valid_stage(stage: str) -> bool:
    return stage in LOOP_STAGES


def is_known_event_type(event_type: str) -> bool:
    """Known types are documented; unknown types are allowed but logged."""
    return event_type in LOOP_EVENT_TYPES


# ── BigQuery write path ──────────────────────────────────────────────────────

_TABLE = "loop_events"
_MAX_PAYLOAD_BYTES = 60_000
_bq_cache: dict = {"client": None, "table_ref": None}

# Write-health counters (in-process; reset on restart). These make otherwise
# silent BQ drops visible — loop_analytics counts are a floor, not a guarantee,
# unless we track attempted vs succeeded vs failed. Surfaced via write_stats()
# and the /api/loop/analytics coverage report.
#   attempted     = record() calls that intended to persist
#   succeeded     = rows BQ accepted
#   failed        = BQ present but insert raised / returned row errors
#   skipped_no_bq = BQ not configured (dev/test) — not a real drop
#   deadletter_written = failed rows appended to LOOP_EVENTS_DEADLETTER_PATH
_write_stats: dict = {
    "attempted": 0, "succeeded": 0, "failed": 0, "skipped_no_bq": 0,
    "deadletter_written": 0, "last_error": None, "last_success_at": None,
}


def write_stats() -> dict:
    """Snapshot of in-process write health. attempted == succeeded + failed +
    skipped_no_bq. A nonzero `failed` with no `deadletter_written` means events
    were lost AND not captured (set LOOP_EVENTS_DEADLETTER_PATH to capture)."""
    return dict(_write_stats)


def reset_write_stats() -> None:
    """Test/ops helper — zero the counters."""
    _write_stats.update({
        "attempted": 0, "succeeded": 0, "failed": 0, "skipped_no_bq": 0,
        "deadletter_written": 0, "last_error": None, "last_success_at": None,
    })


def _deadletter(row: dict) -> None:
    """Best-effort: append a dropped event row to the dead-letter file so it can
    be replayed later. No-op when LOOP_EVENTS_DEADLETTER_PATH is unset."""
    path = os.environ.get("LOOP_EVENTS_DEADLETTER_PATH")
    if not path:
        return
    try:
        with open(path, "a") as fp:
            fp.write(json.dumps(row, default=str) + "\n")
        _write_stats["deadletter_written"] += 1
    except Exception as exc:  # noqa: BLE001 — dead-letter must never raise
        logger.debug("loop_writer deadletter append failed: %s", exc)


def _record_success() -> None:
    _write_stats["succeeded"] += 1
    _write_stats["last_success_at"] = _iso(_now())


def _record_failure(err: str, row: dict) -> None:
    _write_stats["failed"] += 1
    _write_stats["last_error"] = str(err)[:300]
    logger.warning("loop_writer BQ write failed: %s", str(err)[:200])
    _deadletter(row)


def _bq() -> Any:
    """Lazy BigQuery client. None when unavailable."""
    if _bq_cache["client"] is not None:
        return _bq_cache["client"]

    project = os.environ.get("BIGQUERY_PROJECT_ID")
    dataset = os.environ.get("BIGQUERY_DATASET_PROD")
    sa = os.environ.get("BIGQUERY_SERVICE_ACCOUNT_JSON", "")
    if not (project and dataset and sa):
        return None
    try:
        from google.cloud import bigquery
        from google.oauth2 import service_account
        if sa.strip().startswith("{"):
            info = json.loads(sa)
        else:
            with open(sa) as fp:
                info = json.load(fp)
        creds = service_account.Credentials.from_service_account_info(info)
        client = bigquery.Client(project=project, credentials=creds)
        _bq_cache["client"] = client
        _bq_cache["table_ref"] = f"{project}.{dataset}.{_TABLE}"
        return client
    except Exception as exc:
        logger.warning("loop_writer BQ init failed: %s", exc)
        return None


def _serialize_payload(payload: Optional[dict]) -> Optional[str]:
    if payload is None:
        return None
    try:
        s = json.dumps(payload, default=str)
    except (TypeError, ValueError) as exc:
        logger.warning("loop_writer payload serialize failed: %s", exc)
        return json.dumps({"_serialize_error": str(exc)})
    if len(s.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
        # Truncate; preserve top-level keys
        return s[: _MAX_PAYLOAD_BYTES - 50] + '..."_truncated":true}'
    return s


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def record(
    stage: str,
    event_type: str,
    *,
    event_id: Optional[str] = None,
    property_uuid: Optional[str] = None,
    company_id: Optional[str] = None,
    source: Optional[str] = None,
    source_id: Optional[str] = None,
    trigger: str = "api",
    occurred_at: Optional[datetime] = None,
    magnitude: Optional[float] = None,
    payload: Optional[dict] = None,
    status: Optional[str] = None,
    runtime_ms: Optional[int] = None,
    error_message: Optional[str] = None,
    parent_event_id: Optional[str] = None,
) -> str:
    """Write one Loop event row. Returns event_id.

    Validation: stage must be valid; event_type is logged-but-allowed
    if not in the registry (so callers can add new types without code
    changes here).

    Failures are LOGGED, not raised — Loop events must never block
    business operations.
    """
    if not is_valid_stage(stage):
        logger.warning("loop_writer.record bad stage=%r — using 'ops'", stage)
        stage = "ops"
    if not is_known_event_type(event_type):
        logger.info("loop_writer.record new event_type=%r (consider registering)",
                    event_type)

    if event_id is None:
        event_id = str(_uuid.uuid4())

    if occurred_at is None:
        occurred_at = _now()

    row = {
        "event_id":        event_id,
        "property_uuid":   property_uuid,
        "company_id":      company_id,
        "stage":           stage,
        "event_type":      event_type,
        "occurred_at":     _iso(occurred_at),
        "recorded_at":     _iso(_now()),
        "source":          source,
        "source_id":       source_id,
        "trigger":         trigger,
        "magnitude":       magnitude,
        "payload":         _serialize_payload(payload),
        "status":          status,
        "runtime_ms":      runtime_ms,
        "error_message":   error_message,
        "parent_event_id": parent_event_id,
    }

    _write_stats["attempted"] += 1
    client = _bq()
    if client is None:
        _write_stats["skipped_no_bq"] += 1
        logger.debug("loop_writer skipped (BQ unavailable): %s/%s/%s",
                     stage, event_type, property_uuid)
        return event_id

    try:
        errors = client.insert_rows_json(_bq_cache["table_ref"], [row])
        if errors:
            _record_failure(f"insert errors: {errors[:3]}", row)
        else:
            _record_success()
    except Exception as exc:
        _record_failure(str(exc), row)

    # Phase 2 — Slack notification for high-signal events. Best-effort;
    # never blocks. The notifier internally filters to NOTIFIABLE_EVENT_TYPES
    # so this call is cheap for the 90% of events that don't notify.
    try:
        import slack_notifier
        slack_notifier.post_loop_event({
            "event_id":      event_id,
            "event_type":    event_type,
            "stage":         stage,
            "property_uuid": property_uuid,
            "magnitude":     magnitude,
            "payload":       payload,
            "error_message": error_message,
            "status":        status,
        })
    except Exception as exc:
        logger.debug("loop_writer Slack post skipped: %s", exc)

    return event_id


# ── Job tracker (context manager) ────────────────────────────────────────────

class _JobHandle:
    """Returned by `track_job()`; lets the work set a result payload."""

    def __init__(self, event_id: str) -> None:
        self.event_id = event_id
        self.result: dict = {}

    def set_result(self, result: dict) -> None:
        """Attach a result payload. Merged onto the success event payload."""
        if isinstance(result, dict):
            self.result.update(result)


@contextmanager
def track_job(
    stage: str,
    event_type: str,
    *,
    property_uuid: Optional[str] = None,
    company_id: Optional[str] = None,
    source: Optional[str] = None,
    trigger: str = "cron",
    payload: Optional[dict] = None,
    parent_event_id: Optional[str] = None,
) -> Generator[_JobHandle, None, None]:
    """Context manager that records a start event, runs the body, then
    records completion/failure with runtime.

    Designed for cron jobs, async backfills, anything where you want
    "what's running" / "what failed" visible in loop_events.

    Example:
        with track_job(stage='ops', event_type='aptiq_history_backfill',
                       property_uuid=uuid, payload={'months': 13}) as job:
            ...do the work...
            job.set_result({'rows_written': 13})
    """
    start_event_id = str(_uuid.uuid4())
    start_payload = dict(payload or {})
    start_payload["_phase"] = "start"

    record(
        stage=stage, event_type=event_type, event_id=start_event_id,
        property_uuid=property_uuid, company_id=company_id, source=source,
        trigger=trigger, payload=start_payload, status="running",
        parent_event_id=parent_event_id,
    )

    handle = _JobHandle(start_event_id)
    t0 = time.monotonic()
    try:
        yield handle
    except Exception as exc:
        runtime_ms = int((time.monotonic() - t0) * 1000)
        end_payload = dict(payload or {})
        end_payload["_phase"] = "end"
        end_payload.update(handle.result or {})
        record(
            stage=stage, event_type=event_type,
            property_uuid=property_uuid, company_id=company_id, source=source,
            trigger=trigger, payload=end_payload, status="failed",
            runtime_ms=runtime_ms, error_message=str(exc)[:500],
            parent_event_id=start_event_id,
        )
        raise
    else:
        runtime_ms = int((time.monotonic() - t0) * 1000)
        end_payload = dict(payload or {})
        end_payload["_phase"] = "end"
        end_payload.update(handle.result or {})
        record(
            stage=stage, event_type=event_type,
            property_uuid=property_uuid, company_id=company_id, source=source,
            trigger=trigger, payload=end_payload, status="completed",
            runtime_ms=runtime_ms, parent_event_id=start_event_id,
        )


# ── Reader helpers used by /api/loop/* ───────────────────────────────────────
# Keeping these on the writer module since they share the same BQ client.

def query_recent(
    property_uuid: str,
    *,
    limit: int = 50,
    stage: Optional[str] = None,
    since: Optional[datetime] = None,
) -> list[dict]:
    """List recent loop_events for a property. Newest-first."""
    client = _bq()
    if client is None:
        return []
    project = os.environ.get("BIGQUERY_PROJECT_ID")
    dataset = os.environ.get("BIGQUERY_DATASET_PROD")

    from google.cloud import bigquery
    where = ["property_uuid = @uuid"]
    params = [bigquery.ScalarQueryParameter("uuid", "STRING", property_uuid)]
    if stage:
        where.append("stage = @stage")
        params.append(bigquery.ScalarQueryParameter("stage", "STRING", stage))
    if since:
        where.append("occurred_at >= @since")
        params.append(bigquery.ScalarQueryParameter("since", "TIMESTAMP", since))

    sql = f"""
      SELECT event_id, stage, event_type, occurred_at, source, magnitude,
             payload, status, runtime_ms, error_message, parent_event_id
      FROM `{project}.{dataset}.{_TABLE}`
      WHERE {' AND '.join(where)}
      ORDER BY occurred_at DESC
      LIMIT @lim
    """
    params.append(bigquery.ScalarQueryParameter("lim", "INT64", limit))

    try:
        cfg = bigquery.QueryJobConfig(query_parameters=params)
        rows = client.query(sql, job_config=cfg).result()
        out = []
        for r in rows:
            d = dict(r.items())
            # parse payload back to dict for caller convenience
            if d.get("payload"):
                try:
                    d["payload"] = json.loads(d["payload"])
                except (TypeError, ValueError):
                    pass
            # serialize timestamp
            if d.get("occurred_at"):
                d["occurred_at"] = d["occurred_at"].isoformat()
            out.append(d)
        return out
    except Exception as exc:
        logger.warning("loop_writer.query_recent failed: %s", exc)
        return []


def query_stage_status(property_uuid: str) -> dict:
    """Aggregate the latest event per stage for one property — feeds
    the portal's Loop Status panel.

    Returns:
      {
        "attract": {"last_event_type": "...", "last_at": "...", "magnitude": ...},
        "engage":  {...},
        "convert": {...},
        "optimize":{...},
      }
    Missing stages return None.
    """
    client = _bq()
    if client is None:
        return {stage: None for stage in ("attract", "engage", "convert", "optimize")}
    project = os.environ.get("BIGQUERY_PROJECT_ID")
    dataset = os.environ.get("BIGQUERY_DATASET_PROD")

    from google.cloud import bigquery
    sql = f"""
      WITH ranked AS (
        SELECT
          stage, event_type, occurred_at, magnitude, status,
          ROW_NUMBER() OVER (PARTITION BY stage ORDER BY occurred_at DESC) AS rn
        FROM `{project}.{dataset}.{_TABLE}`
        WHERE property_uuid = @uuid
          AND stage IN ('attract', 'engage', 'convert', 'optimize')
          AND occurred_at > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
      )
      SELECT * EXCEPT(rn) FROM ranked WHERE rn = 1
    """
    params = [bigquery.ScalarQueryParameter("uuid", "STRING", property_uuid)]
    cfg = bigquery.QueryJobConfig(query_parameters=params)
    try:
        result = {stage: None for stage in ("attract", "engage", "convert", "optimize")}
        for r in client.query(sql, job_config=cfg).result():
            result[r.stage] = {
                "last_event_type": r.event_type,
                "last_at": r.occurred_at.isoformat() if r.occurred_at else None,
                "magnitude": r.magnitude,
                "status": r.status,
            }
        return result
    except Exception as exc:
        logger.warning("loop_writer.query_stage_status failed: %s", exc)
        return {stage: None for stage in ("attract", "engage", "convert", "optimize")}
