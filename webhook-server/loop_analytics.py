"""Loop event analytics — portfolio-wide, time-windowed reads of loop_events.

The per-property readers live in `loop_writer` (query_recent,
query_stage_status) and feed the client's Loop panel. THIS module answers the
roadmap question: across the whole book, over time, what is the event mix, and
where are the two profit levers?

  - efficiency_targets()    → what the team/system spends time on and where it
                              fails (the automation roadmap, ranked by impact).
  - productization_signal() → where client value concentrates (convert +
                              optimize volume) + the AI-trust gauge
                              (recommendation approved vs rejected).
  - event_mix()             → the raw "% of events by dimension" answer.
  - coverage_report()       → trust check: which registered event types are
                              never seen (taxonomy blind spots) + ingest lag.

All reads degrade gracefully: BigQuery unavailable → empty/safe result, never
raises. Reuses loop_writer's cached BQ client so there is one init path.

CAVEAT (read before betting roadmap on the numbers): loop_writer drops events
silently when BQ is down (logs, never raises). coverage_report() surfaces the
BQ-side trust signals (never-seen types, ingest lag), but TRUE silent-drop
detection needs a write-success counter at the writer — see loop_writer. The
counts here are a floor, not a guarantee.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ── BQ plumbing (reuse loop_writer's client + table) ─────────────────────────


def _client_and_table() -> tuple[Any, Optional[str]]:
    """(client, fully-qualified table) or (None, None) when BQ is unavailable."""
    import os

    from loop_writer import _bq  # reuse the cached client — one init path
    client = _bq()
    if client is None:
        return None, None
    project = os.environ.get("BIGQUERY_PROJECT_ID")
    dataset = os.environ.get("BIGQUERY_DATASET_PROD")
    return client, f"{project}.{dataset}.loop_events"


def _run(client: Any, sql: str, params: list[tuple]) -> list[dict]:
    """Execute a parameterized query → list of plain dicts. Never raises."""
    from google.cloud import bigquery
    qp = [bigquery.ScalarQueryParameter(n, t, v) for (n, t, v) in (params or [])]
    cfg = bigquery.QueryJobConfig(query_parameters=qp)
    try:
        return [dict(r.items()) for r in client.query(sql, job_config=cfg).result()]
    except Exception as exc:  # noqa: BLE001 — analytics must never break a caller
        logger.warning("loop_analytics query failed: %s", exc)
        return []


def _window_where(
    since_days: int,
    *,
    stage: Optional[str] = None,
    stages: Optional[list[str]] = None,
    company_id: Optional[str] = None,
) -> tuple[str, list[tuple]]:
    """Build the shared WHERE clause + params (plain tuples; _run types them)."""
    where = ["occurred_at > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)"]
    params: list[tuple] = [("days", "INT64", int(since_days))]
    if stage:
        where.append("stage = @stage")
        params.append(("stage", "STRING", stage))
    if stages:
        names = []
        for i, s in enumerate(stages):
            key = f"st{i}"
            names.append(f"@{key}")
            params.append((key, "STRING", s))
        where.append(f"stage IN ({', '.join(names)})")
    if company_id:
        where.append("company_id = @cid")
        params.append(("cid", "STRING", company_id))
    return " AND ".join(where), params


_MIX_DIMENSIONS = ("event_type", "stage", "trigger", "source")


# ── Public readers ───────────────────────────────────────────────────────────


def event_mix(
    *,
    since_days: int = 90,
    dimension: str = "event_type",
    stage: Optional[str] = None,
    company_id: Optional[str] = None,
) -> list[dict]:
    """Event share by `dimension` over the window. [{key, count, pct}], desc."""
    dim = dimension if dimension in _MIX_DIMENSIONS else "event_type"
    client, table = _client_and_table()
    if not client:
        return []
    where, params = _window_where(since_days, stage=stage, company_id=company_id)
    sql = (
        f"SELECT {dim} AS k, COUNT(*) AS n FROM `{table}` "
        f"WHERE {where} GROUP BY k ORDER BY n DESC"
    )
    rows = _run(client, sql, params)
    total = sum(int(r.get("n", 0) or 0) for r in rows)
    return [
        {
            "key": r.get("k"),
            "count": int(r.get("n", 0) or 0),
            "pct": round(100.0 * int(r.get("n", 0) or 0) / total, 1) if total else 0.0,
        }
        for r in rows
    ]


def efficiency_targets(*, since_days: int = 90, limit: int = 25) -> list[dict]:
    """Ops events ranked by volume, with avg runtime, failure rate, and manual
    trigger count — the automation roadmap, ordered by where hours go."""
    client, table = _client_and_table()
    if not client:
        return []
    where, params = _window_where(since_days, stage="ops")
    params.append(("lim", "INT64", int(limit)))
    sql = f"""
      SELECT event_type,
             COUNT(*) AS n,
             AVG(runtime_ms) AS avg_ms,
             COUNTIF(status = 'failed') AS fails,
             COUNTIF(trigger = 'manual') AS manual
      FROM `{table}`
      WHERE {where}
      GROUP BY event_type
      ORDER BY n DESC
      LIMIT @lim
    """
    out = []
    for r in _run(client, sql, params):
        n = int(r.get("n", 0) or 0)
        fails = int(r.get("fails", 0) or 0)
        avg_ms = r.get("avg_ms")
        out.append({
            "event_type": r.get("event_type"),
            "count": n,
            "avg_runtime_ms": round(float(avg_ms), 1) if avg_ms is not None else None,
            "fail_count": fails,
            "fail_rate_pct": round(100.0 * fails / n, 1) if n else 0.0,
            "manual_count": int(r.get("manual", 0) or 0),
        })
    return out


def productization_signal(*, since_days: int = 90) -> dict:
    """Where client value concentrates (convert + optimize volume by week) plus
    the AI-trust gauge (recommendation approved vs rejected)."""
    client, table = _client_and_table()
    if not client:
        return {"weekly": [], "recommendation_trust": None}

    w1, p1 = _window_where(since_days, stages=["convert", "optimize"])
    sql_weekly = f"""
      SELECT DATE_TRUNC(DATE(occurred_at), WEEK) AS wk, stage, COUNT(*) AS n
      FROM `{table}` WHERE {w1}
      GROUP BY wk, stage ORDER BY wk
    """
    weekly = [
        {"week": str(r.get("wk")), "stage": r.get("stage"), "count": int(r.get("n", 0) or 0)}
        for r in _run(client, sql_weekly, p1)
    ]

    w2, p2 = _window_where(since_days)
    sql_rec = f"""
      SELECT event_type, COUNT(*) AS n FROM `{table}`
      WHERE {w2}
        AND event_type IN ('recommendation_approved', 'recommendation_rejected')
      GROUP BY event_type
    """
    counts = {r.get("event_type"): int(r.get("n", 0) or 0) for r in _run(client, sql_rec, p2)}
    approved = counts.get("recommendation_approved", 0)
    rejected = counts.get("recommendation_rejected", 0)
    denom = approved + rejected
    return {
        "weekly": weekly,
        "recommendation_trust": {
            "approved": approved,
            "rejected": rejected,
            "approval_rate_pct": round(100.0 * approved / denom, 1) if denom else None,
        },
    }


def coverage_report(*, since_days: int = 90) -> dict:
    """Trust check: which registered event types are never seen (blind spots),
    plus average ingest lag (recorded_at − occurred_at)."""
    import loop_writer
    registered = set(loop_writer.LOOP_EVENT_TYPES)

    client, table = _client_and_table()
    if not client:
        return {
            "seen": [],
            "never_seen": sorted(registered),
            "registered_total": len(registered),
            "seen_total": 0,
            "avg_ingest_lag_ms": None,
            "write_health": loop_writer.write_stats(),
        }

    where, params = _window_where(since_days)
    sql_seen = f"SELECT event_type, COUNT(*) AS n FROM `{table}` WHERE {where} GROUP BY event_type"
    seen = {r.get("event_type"): int(r.get("n", 0) or 0) for r in _run(client, sql_seen, params)}

    sql_lag = f"""
      SELECT AVG(TIMESTAMP_DIFF(recorded_at, occurred_at, MILLISECOND)) AS lag_ms
      FROM `{table}` WHERE {where}
    """
    lag_rows = _run(client, sql_lag, params)
    lag = lag_rows[0].get("lag_ms") if lag_rows else None

    return {
        "seen": [{"event_type": k, "count": v}
                 for k, v in sorted(seen.items(), key=lambda kv: -kv[1])],
        "never_seen": sorted(registered - set(seen.keys())),
        "registered_total": len(registered),
        "seen_total": len(seen),
        "avg_ingest_lag_ms": round(float(lag), 1) if lag is not None else None,
        "write_health": loop_writer.write_stats(),
    }
