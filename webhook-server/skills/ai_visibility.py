"""Layer 2 — AI visibility per property, and the history we keep ourselves.

WHY THE HISTORY LIVES HERE
    The product we sell is the TREND: "your visibility went from 20 to 37 and
    here is what we did." If that trend lives only in the vendor's database,
    then changing vendors — or losing one at renewal — resets every client's
    chart to zero, and the work we did stops being provable. This business is
    already paying that bill somewhere else, so every reading gets written to
    our own warehouse on the way through.

    The vendor measures. We keep the record, and we decide what to do about it.

SHAPE
    * `for_property()` reads the current measurement and always answers, with
      `measured: False` plus a reason where a property has no project yet —
      which today is 99 of 100 websites, and knowing WHICH is useful.
    * `snapshot()` writes one row per property per day into
      `ai_visibility_daily`, and is idempotent per (uuid, date, engine) so a
      re-run does not double-count.
    * `history()` reads our own rows, never the vendor's, so the trend a client
      sees survives the contract.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

TABLE = "ai_visibility_daily"

# Engine names we normalize to, so a vendor renaming "google-ai-overview"
# does not fork the history.
ENGINE_NAMES = {
    "chatgpt": "ChatGPT",
    "google-ai-overview": "Google AI Overview",
    "googleaioverview": "Google AI Overview",
    "perplexity": "Perplexity",
    "claude": "Claude",
    "copilot": "Copilot",
    "gemini": "Gemini",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _gap(field: str, source: str, message: str) -> Dict[str, str]:
    return {"field": field, "source": source, "message": message}


def engine_label(raw: Any) -> str:
    key = str(raw or "").strip().lower().replace(" ", "-")
    return ENGINE_NAMES.get(key, str(raw or "").strip() or "unknown")


def for_property(company_id: str, *, days: int = 30) -> Dict[str, Any]:
    """Current AI visibility for one property, with receipts and honest gaps."""
    from skills import property_resolver as pr

    try:
        identity = pr.resolve(company_id)
    except Exception as exc:  # noqa: BLE001
        logger.info("ai_visibility: could not resolve %s (%s)", company_id, exc)
        return {"company_id": company_id, "measured": False,
                "gaps": [_gap("ai_visibility", "hubspot",
                              "This property could not be resolved.")]}

    website = (identity.to_dict().get("domain")
               or identity.to_dict().get("website") or "")
    out: Dict[str, Any] = {
        "company_id": company_id,
        "uuid": identity.to_dict().get("uuid"),
        "property_name": identity.to_dict().get("name"),
        "domain": website,
        "measured": False,
        "as_of": _now_iso(),
        "gaps": [],
    }
    if not website:
        out["gaps"].append(_gap("ai_visibility", "hubspot",
                                "This property has no website on its record, so "
                                "its AI visibility cannot be measured."))
        return out

    import searchable_client as sc

    reading = sc.for_property(website, days=days)
    if not reading.get("measured"):
        out["gaps"].append(_gap("ai_visibility", "searchable",
                                reading.get("reason") or
                                "AI visibility is not measured for this property yet."))
        return out

    vis = reading["visibility"]
    out.update({
        "measured": True,
        "project_id": reading.get("project_id"),
        "score": {"value": vis.get("score"), "source": "searchable",
                  "as_of": out["as_of"]},
        "change": {"value": vis.get("score_change"),
                   "period": vis.get("change_period"), "source": "searchable"},
        "engines": [{"engine": engine_label(e.get("engine")),
                     "visibility_rate": e.get("visibility_rate"),
                     "responses": e.get("responses"),
                     "responses_with_brand": e.get("responses_with_brand"),
                     "citations": e.get("citations")}
                    for e in vis.get("engines") or []],
        "topics": vis.get("topics") or [],
        "trend": vis.get("trend") or [],
        "vendor_opportunities": reading.get("opportunities") or [],
    })
    return out


def _rows_for_snapshot(reading: Dict[str, Any], on: date) -> List[Dict[str, Any]]:
    """One row per engine, plus an `all` row carrying the overall score."""
    if not reading.get("measured"):
        return []
    day = on.isoformat()
    base = {"property_uuid": reading.get("uuid"),
            "company_id": reading.get("company_id"),
            "domain": reading.get("domain"),
            "reading_date": day,
            "vendor": "searchable",
            "captured_at": _now_iso()}
    rows = [dict(base, engine="all",
                 visibility_score=(reading.get("score") or {}).get("value"),
                 visibility_rate=None, responses=None,
                 responses_with_brand=None, citations=None)]
    for engine in reading.get("engines") or []:
        rows.append(dict(base, engine=engine.get("engine"),
                         visibility_score=None,
                         visibility_rate=engine.get("visibility_rate"),
                         responses=engine.get("responses"),
                         responses_with_brand=engine.get("responses_with_brand"),
                         citations=engine.get("citations")))
    return rows


def snapshot(company_id: str, *, on: Optional[date] = None,
             reading: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Write today's reading into our own warehouse. Idempotent per day.

    Returns what happened rather than raising: a warehouse that is down must not
    stop a dashboard from rendering, but it must be visible that the day was
    missed, or a gap in the trend silently becomes a flat line.
    """
    on = on or date.today()
    reading = reading if reading is not None else for_property(company_id)
    result = {"company_id": company_id, "date": on.isoformat(),
              "written": 0, "skipped": False, "gaps": list(reading.get("gaps") or [])}

    rows = _rows_for_snapshot(reading, on)
    if not rows:
        result["skipped"] = True
        return result

    import bigquery_client as bq

    if not bq.is_bigquery_configured():
        result["skipped"] = True
        result["gaps"].append(_gap("ai_visibility_history", "bigquery",
                                   "The warehouse is not configured here, so "
                                   "today's reading was not recorded."))
        return result
    try:
        bq.insert_rows(TABLE, rows)
        result["written"] = len(rows)
    except Exception as exc:  # noqa: BLE001
        logger.error("ai_visibility: snapshot write failed for %s: %s",
                     company_id, exc, exc_info=True)
        result["gaps"].append(_gap("ai_visibility_history", "bigquery",
                                   "Today's reading could not be recorded, so it "
                                   "will be missing from the trend."))
    return result


def history(property_uuid: str, *, days: int = 180,
            engine: str = "all") -> Dict[str, Any]:
    """Our own trend, read from our own warehouse — never the vendor's."""
    out: Dict[str, Any] = {"property_uuid": property_uuid, "engine": engine,
                           "points": [], "source": "portal_warehouse", "gaps": []}
    import bigquery_client as bq

    if not bq.is_bigquery_configured():
        out["gaps"].append(_gap("ai_visibility_history", "bigquery",
                                "The warehouse is not configured here."))
        return out
    try:
        from google.cloud import bigquery as gbq
        sql = """
          SELECT reading_date, visibility_score, visibility_rate
          FROM `{dataset}.{table}`
          WHERE property_uuid = @uuid AND engine = @engine
            AND reading_date >= DATE_SUB(CURRENT_DATE(), INTERVAL @days DAY)
          ORDER BY reading_date
        """.format(dataset=bq._dataset(), table=TABLE)
        rows = bq.query(sql, [
            gbq.ScalarQueryParameter("uuid", "STRING", str(property_uuid)),
            gbq.ScalarQueryParameter("engine", "STRING", engine),
            gbq.ScalarQueryParameter("days", "INT64", int(days)),
        ])
    except Exception as exc:  # noqa: BLE001
        logger.error("ai_visibility: history read failed for %s: %s",
                     property_uuid, exc, exc_info=True)
        out["gaps"].append(_gap("ai_visibility_history", "bigquery",
                                "The stored trend could not be read."))
        return out

    for row in rows or []:
        value = row.get("visibility_score")
        if value is None:
            value = row.get("visibility_rate")
        day = row.get("reading_date")
        out["points"].append({"date": day.isoformat() if hasattr(day, "isoformat")
                              else str(day), "value": value})
    if not out["points"]:
        out["gaps"].append(_gap("ai_visibility_history", "portal_warehouse",
                                "No readings have been recorded for this property yet."))
    return out
