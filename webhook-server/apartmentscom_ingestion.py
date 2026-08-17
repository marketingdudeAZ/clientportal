"""Apartments.com ILS ingestion (Layer 2 skill, ADR 0021).

Orchestrates: connector fetch → BigQuery landing → Loop Event Bus.

  ingest_date(date)  → fetch one day, append rows to apartmentscom_ils_daily,
                       emit a single ops run event summarizing the pull.

Layering: this module is the ONLY place apartments.com data crosses from
the Layer 1 connector into the warehouse + Loop. The connector stays
side-effect-free; the portal/API layer reads the resolved BQ view, never
the connector.

Idempotency: we always append and dedupe in apartmentscom_ils_resolved_v1
(latest ingested row per record_date × listing), mirroring aptiq_snapshots.
Re-running a date is safe — it just supersedes the prior rows in the view.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import apartmentscom_client as ac

logger = logging.getLogger(__name__)

# Metric columns summed for the run-level Loop event payload.
_IMPRESSION_COLS = (
    "search_result_impressions",
    "details_page_impressions",
    "total_impressions",
)
_LEAD_COLS = (
    "total_leads",
    "phone_leads",
    "email_leads",
    "property_website_leads",
    "request_to_tour_leads",
    "request_to_apply_leads",
    "unit_application_leads",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_rows(summary: dict, ingested_at: str) -> list[dict]:
    """Turn a connector summary into BQ rows for apartmentscom_ils_daily."""
    record_date = summary.get("record_date")
    pmc = summary.get("pmc")
    rows: list[dict] = []
    for item, raw in zip(summary["items"], summary["raw_items"]):
        row = dict(item)  # normalized snake_case fields
        row["record_date"] = record_date
        row["pmc"] = pmc
        row["ingested_at"] = ingested_at
        row["raw_payload"] = json.dumps(raw, separators=(",", ":"))
        rows.append(row)
    return rows


def _sum(rows: list[dict], cols) -> int:
    return sum(int(r.get(c) or 0) for r in rows for c in cols)


def ingest_date(date: str | None = None, *, emit_loop: bool = True) -> dict:
    """Fetch + land one date. Returns a summary dict.

    Raises the connector's exceptions on terminal API failures (auth, bad
    date, rate limit) so callers/cron can surface non-zero exit codes.
    """
    import bigquery_client as bq
    from config import BIGQUERY_APARTMENTSCOM_DAILY_TABLE

    summary = ac.fetch_daily_summary(date)
    record_date = summary.get("record_date") or date or ac.default_target_date()
    ingested_at = _now_iso()
    rows = _build_rows(summary, ingested_at)

    written = 0
    if rows:
        bq.insert_rows(BIGQUERY_APARTMENTSCOM_DAILY_TABLE, rows)
        written = len(rows)

    result = {
        "record_date": record_date,
        "listings": len(rows),
        "rows_written": written,
        "total_impressions": _sum(rows, ("total_impressions",)),
        "total_leads": _sum(rows, ("total_leads",)),
        "message": summary.get("message"),
        "pmc": summary.get("pmc"),
    }

    if emit_loop:
        _emit_run_event(result, rows)

    logger.info(
        "apartments.com ingest %s: %d listings, %d impressions, %d leads",
        record_date, result["listings"], result["total_impressions"],
        result["total_leads"],
    )
    return result


def _emit_run_event(result: dict, rows: list[dict]) -> None:
    """Emit ONE ops run-summary event to the Loop Event Bus per ingestion.

    We deliberately don't fan out per-listing events (700+/day would flood
    the timeline); the per-property metrics live in BQ and are read by the
    ILS page directly. Loop rollups per property can be a follow-up.
    Failures here never block ingestion (loop_writer swallows its own)."""
    try:
        import loop_writer
        loop_writer.record(
            stage="ops",
            event_type="job",
            source="apartments.com",
            trigger="cron",
            magnitude=float(result["total_leads"]),
            payload={
                "job": "apartmentscom_ingest",
                "record_date": result["record_date"],
                "listings": result["listings"],
                "total_impressions": result["total_impressions"],
                "total_leads": result["total_leads"],
                "impressions_breakdown": {c: _sum(rows, (c,)) for c in _IMPRESSION_COLS},
                "leads_breakdown": {c: _sum(rows, (c,)) for c in _LEAD_COLS},
            },
            status="completed",
        )
    except Exception as exc:  # never block ingestion on Loop write
        logger.warning("apartments.com Loop event emit failed: %s", exc)
