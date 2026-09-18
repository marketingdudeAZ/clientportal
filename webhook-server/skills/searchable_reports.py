"""Layer 2 — white-labelled AI-search reports, published once and remembered.

WHAT THIS IS FOR
    Searchable can publish a shareable, white-labelled report for a property and
    hand back a public link. That is the fastest way to put real AI-search
    reporting in front of a client: their charts, our branding, no build.

WHY THE LINK IS STORED RATHER THAN REGENERATED
    Publishing is a write against the vendor's account and it costs a round trip
    of several seconds. A page render must never do that. So a link is published
    deliberately — by a person, or a job — and written to the Loop event stream;
    every later read is our own warehouse, and the portal shows the newest link
    for a property with the date it was published beside it.

WHAT TO KNOW BEFORE PUBLISHING ONE
    The link is PUBLIC and unauthenticated: anyone who has it can read the
    report, with no sign-in. That is what makes it shareable and it is also the
    whole risk. The portal keeps these links behind its own sign-in and never
    emails them, and `publish()` refuses to run without an explicit confirm, so
    nothing here publishes as a side effect of rendering a screen.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

EVENT_TYPE = "ai_report_published"
LOOP_STAGE = "engage"

# What a report covers by default. "combined" is visibility AND sentiment —
# the score alone reads as a scoreboard; the sentiment is what makes it a
# finding someone can act on.
DEFAULT_REPORT_TYPE = "combined"
DEFAULT_TIME_RANGE = "30d"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _gap(field: str, source: str, message: str) -> Dict[str, str]:
    return {"field": field, "source": source, "message": message}


def _identity(company_id: str):
    from skills import property_resolver as pr
    return pr.resolve(company_id)


def preview(company_id: str, *, report_type: str = DEFAULT_REPORT_TYPE,
            time_range: str = DEFAULT_TIME_RANGE) -> Dict[str, Any]:
    """A dry run: what WOULD be published, without publishing it."""
    return _run(company_id, report_type=report_type, time_range=time_range,
                confirm=False)


def publish(company_id: str, *, report_type: str = DEFAULT_REPORT_TYPE,
            time_range: str = DEFAULT_TIME_RANGE,
            confirm: bool = False) -> Dict[str, Any]:
    """Publish for real. Refuses without `confirm=True`.

    The refusal is deliberate: this mints a public link, and a function that
    publishes by default would eventually be called by something that only
    meant to look.
    """
    if not confirm:
        return {"company_id": company_id, "published": False,
                "reason": "Publishing was not confirmed, so nothing was published.",
                "gaps": []}
    return _run(company_id, report_type=report_type, time_range=time_range,
                confirm=True)


def _run(company_id: str, *, report_type: str, time_range: str,
         confirm: bool) -> Dict[str, Any]:
    import searchable_mcp as sm

    out: Dict[str, Any] = {"company_id": company_id, "published": False,
                           "link": None, "reason": None, "gaps": []}
    try:
        identity = _identity(company_id)
    except Exception as exc:  # noqa: BLE001
        out["reason"] = "This property could not be resolved."
        logger.info("searchable_reports: cannot resolve %s (%s)", company_id, exc)
        return out
    data = identity.to_dict()
    out["property_name"] = identity.name
    out["uuid"] = data.get("uuid")
    domain = (data.get("domain") or data.get("website") or "").strip()
    if not domain:
        out["reason"] = "This property has no website on its record."
        return out

    project, reason = sm.project_for_domain(domain)
    if project is None:
        out["reason"] = reason
        return out
    out["project_id"] = project["id"]

    result, reason = sm.generate_report(
        project["id"], report_type=report_type, time_range=time_range,
        title="%s — AI search visibility" % identity.name,
        white_label=True, confirm=confirm)
    if result is None:
        out["reason"] = reason
        return out

    out["link"] = result.get("link")
    out["published"] = bool(result.get("published") and result.get("link"))
    if not confirm:
        out["reason"] = "Dry run — nothing was published."
        return out

    stored = _remember(out, report_type=report_type, time_range=time_range)
    out["stored"] = stored
    if not stored:
        out["gaps"].append(_gap("ai_report", "loop_events",
                                "The report was published but the link could not "
                                "be recorded, so the portal may not show it."))
    return out


def _remember(result: Dict[str, Any], *, report_type: str,
              time_range: str) -> bool:
    """Write the published link to the Loop stream. Returns whether it landed."""
    try:
        from loop_writer import record
        record(LOOP_STAGE, EVENT_TYPE,
               property_uuid=result.get("uuid"),
               company_id=str(result.get("company_id")),
               source="searchable",
               payload={"link": result.get("link"),
                        "report_type": report_type,
                        "time_range": time_range,
                        "project_id": result.get("project_id"),
                        "property_name": result.get("property_name"),
                        "published_at": _now_iso(),
                        # Said in the record itself, so nobody reading this row
                        # later has to guess how exposed the link is.
                        "visibility": "public_unauthenticated"})
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("searchable_reports: could not record link for %s: %s",
                     result.get("company_id"), exc, exc_info=True)
        return False


def latest_for_property(company_id: str, *, uuid: Optional[str] = None
                        ) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, str]]]:
    """The newest published report for one property, read from our warehouse."""
    key = str(uuid or company_id)
    unreadable = [_gap("ai_report", "loop_events",
                       "Published reports could not be read, so any that exist "
                       "are not shown.")]
    try:
        from skills import workspace_history
        by_property = workspace_history.property_events([key], types=(EVENT_TYPE,))
    except Exception as exc:  # noqa: BLE001
        logger.info("searchable_reports: history unavailable for %s: %s",
                    company_id, exc)
        return None, unreadable
    if by_property is None:
        return None, unreadable
    for event in by_property.get(key) or []:      # newest first
        payload = event.get("payload") or {}
        if isinstance(payload, dict) and payload.get("link"):
            return {"link": payload["link"],
                    "report_type": payload.get("report_type"),
                    "published_at": payload.get("published_at")
                    or str(event.get("occurred_at") or "")[:19],
                    "property_name": payload.get("property_name")}, []
    return None, []


def links_for(uuids: List[str]) -> Dict[str, Dict[str, Any]]:
    """{uuid: newest report} for a screen listing many properties, in ONE read."""
    ids = sorted({str(u) for u in uuids if u})
    if not ids:
        return {}
    try:
        from skills import workspace_history
        by_property = workspace_history.property_events(ids, types=(EVENT_TYPE,))
    except Exception as exc:  # noqa: BLE001
        logger.info("searchable_reports: bulk history unavailable: %s", exc)
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for key, events in (by_property or {}).items():
        for event in events or []:
            payload = event.get("payload") or {}
            if isinstance(payload, dict) and payload.get("link"):
                out[key] = {"link": payload["link"],
                            "report_type": payload.get("report_type"),
                            "published_at": payload.get("published_at")
                            or str(event.get("occurred_at") or "")[:19]}
                break
    return out
