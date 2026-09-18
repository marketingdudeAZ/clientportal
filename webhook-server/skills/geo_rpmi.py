"""Layer 2 — the RPMI GEO/AEO roll-up: who the assistants name, and who they don't.

WHAT THIS ANSWERS
    When a renter asks an AI assistant for apartments in this market, does our
    property get named? That is the whole product. The portfolio view is the
    honest version of it: every RPMI property, each either measured with a
    score or explicitly not measured yet — because "no data" and "score zero"
    are opposite findings and only one of them is bad news.

WHY EVERY PROPERTY APPEARS, MEASURED OR NOT
    Coverage IS the finding right now. One Searchable project exists across 98
    RPMI websites, and a roll-up that quietly listed only the measured ones
    would show a tidy screen and hide the real state of the programme. The
    count of unmeasured properties is the first number a reader should see.

    A project also only starts collecting the day it is created, so
    `data_available_from` is carried through: a property measured since
    yesterday has a score but no trend, and saying so prevents a one-point line
    being read as a flat one.

WHAT IT DOES NOT DO
    No recommendations are invented here. The vendor's opportunities are passed
    through as the vendor's, and anything the portal proposes goes through
    reco_engine with the Fair Housing check in front of it, like every other
    recommendation.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Bounded because each measured property is one vendor round trip, and a
# portfolio screen must not fan out to a hundred at once.
MAX_WORKERS = 6

# How many properties to read visibility for in one page render. Everything
# beyond this is still listed and counted — it just shows its status without a
# score, rather than making the screen wait.
MAX_MEASURED_READS = 40


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _gap(field: str, source: str, message: str) -> Dict[str, str]:
    return {"field": field, "source": source, "message": message}


def _roster(gaps: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    from skills import rpmi_roster
    try:
        return rpmi_roster.get_roster()
    except Exception as exc:  # noqa: BLE001
        logger.error("geo_rpmi: roster unavailable: %s", exc, exc_info=True)
        gaps.append(_gap("properties", "hubspot",
                         "The RPMI roster could not be read, so this list is "
                         "not complete."))
        return []


def _projects(gaps: List[Dict[str, str]]) -> Tuple[Dict[str, Dict[str, Any]], bool]:
    """{domain: project}, and whether the vendor answered at all."""
    import searchable_mcp as sm

    if not sm.is_configured():
        gaps.append(_gap("ai_visibility", "searchable",
                         "AI-visibility measurement is not connected on this "
                         "server, so nothing can be measured."))
        return {}, False
    projects, reason = sm.list_projects()
    if projects is None:
        gaps.append(_gap("ai_visibility", "searchable",
                         reason or "The AI-visibility source could not be read."))
        return {}, False
    return {p["domain"]: p for p in projects if p.get("domain")}, True


def _read_one(row: Dict[str, Any], project: Dict[str, Any],
              days: int) -> Dict[str, Any]:
    import searchable_mcp as sm

    vis, reason = sm.visibility(project["id"], days=days)
    out = {"project_id": project["id"], "project_name": project.get("name"),
           "data_available_from": project.get("data_available_from")}
    if vis is None:
        out.update({"measured": False, "reason": reason})
        return out
    out.update({
        "measured": True,
        "score": vis.get("score"),
        "score_change": vis.get("score_change"),
        "change_period": vis.get("change_period"),
        "totals": vis.get("totals") or {},
        "engines": [{"engine": e.get("engine"),
                     "visibility_rate": e.get("visibility_rate"),
                     "responses": e.get("responses"),
                     "responses_with_brand": e.get("responses_with_brand")}
                    for e in vis.get("engines") or []],
        "topics": vis.get("topics") or [],
        "trend_points": len(vis.get("trend") or []),
    })
    return out


def build(*, days: int = 30, max_reads: int = MAX_MEASURED_READS,
          include_opportunities: bool = False) -> Dict[str, Any]:
    """Every RPMI property, with its AI visibility or the reason there is none."""
    gaps: List[Dict[str, str]] = []
    roster = _roster(gaps)
    projects, vendor_ok = _projects(gaps)

    rows: List[Dict[str, Any]] = []
    to_read: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for row in roster:
        domain = (row.get("domain") or "").strip().lower()
        entry: Dict[str, Any] = {
            "company_id": row.get("company_id"),
            "uuid": row.get("uuid"),
            "name": row.get("name"),
            "domain": domain or None,
            "city": row.get("city"),
            "state": row.get("state"),
            "units": row.get("units"),
            "measured": False,
            "reason": None,
        }
        project = projects.get(domain) if domain else None
        if not domain:
            entry["reason"] = "No website on the HubSpot record, so there is nothing to measure."
        elif not vendor_ok:
            entry["reason"] = "The AI-visibility source could not be reached."
        elif not project:
            entry["reason"] = "No Searchable project exists for this website yet."
        else:
            to_read.append((entry, project))
        rows.append(entry)

    deferred = 0
    if to_read:
        if len(to_read) > max_reads:
            deferred = len(to_read) - max_reads
            for entry, project in to_read[max_reads:]:
                entry.update({"project_id": project["id"],
                              "reason": "Measured, but not read on this page."})
            to_read = to_read[:max_reads]
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            results = list(pool.map(
                lambda pair: (pair[0], _read_one(pair[0], pair[1], days)), to_read))
        for entry, reading in results:
            entry.update(reading)

    # Published report links, in ONE warehouse read for the whole screen rather
    # than one per row. A property with no report is not an error — most have
    # none until someone publishes one.
    try:
        from skills import searchable_reports
        links = searchable_reports.links_for([r.get("uuid") for r in rows])
    except Exception as exc:  # noqa: BLE001
        logger.info("geo_rpmi: published report links unavailable: %s", exc)
        links = {}
    for row in rows:
        report = links.get(str(row.get("uuid") or ""))
        row["report"] = report or None

    measured = [r for r in rows if r.get("measured")]
    scores = [r["score"] for r in measured if isinstance(r.get("score"), (int, float))]
    # Ranked so the screen opens on the properties with the most room to move:
    # measured and weakest first, then everything still waiting on a project.
    rows.sort(key=lambda r: (not r.get("measured"),
                             r.get("score") if isinstance(r.get("score"), (int, float)) else 999,
                             (r.get("name") or "").lower()))

    if deferred:
        gaps.append(_gap("ai_visibility", "searchable",
                         "%d measured propert%s not read on this page."
                         % (deferred, "y was" if deferred == 1 else "ies were")))
    if vendor_ok and not measured:
        gaps.append(_gap("ai_visibility", "searchable",
                         "No RPMI property has a Searchable project yet, so there "
                         "is nothing to measure. Creating a project starts the "
                         "measurement from that day — history does not backfill."))

    return {
        "as_of": _now_iso(),
        "days": days,
        "property_count": len(rows),
        "measured_count": len(measured),
        "unmeasured_count": len(rows) - len(measured),
        "average_score": round(sum(scores) / len(scores), 1) if scores else None,
        "vendor_project_count": len(projects),
        "properties": rows,
        "gaps": gaps,
    }


def for_property(company_id: str, *, days: int = 30) -> Dict[str, Any]:
    """One property in full, for the panel behind a row on the roll-up."""
    from skills import ai_visibility

    reading = ai_visibility.for_property(company_id, days=days)
    if reading.get("measured"):
        history = ai_visibility.history(reading.get("uuid") or "", days=180)
        reading["our_trend"] = history.get("points") or []
        reading["our_trend_gaps"] = history.get("gaps") or []
    return reading
