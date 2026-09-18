"""Layer 1 — AI-visibility measurement (Searchable).

WHAT THIS IS FOR
    How often the major AI assistants name a property when someone asks the
    questions a renter actually asks, which engines cite us, what those answers
    say about us, and which competitors get named instead. There is no
    first-party source for that — you cannot see what an assistant said without
    asking it — so the measurement is rented and the judgement stays here.

WHAT WE OWN REGARDLESS OF THE VENDOR
    * The property↔project mapping (by website domain, on the HubSpot record).
    * The HISTORY. Every reading is written to BigQuery by
      `skills.ai_visibility.snapshot()`, because visibility TREND is the product
      we sell; if the trend lives only in the vendor, changing vendors resets
      every client's chart to zero. That mistake is already being paid for
      elsewhere in this business.
    * The recommendations. The vendor measures; the portal decides, checks Fair
      Housing, and keeps the receipt.

TRANSPORT
    Normalization comes first and transport second, on purpose. The payload
    shapes below were taken from live responses, so rules and tests can be
    written against them today; when the API key lands, only `_get()` changes.
    Endpoint paths are configurable (SEARCHABLE_API_BASE, SEARCHABLE_API_KEY)
    and MUST be confirmed against the vendor's API docs before first use —
    they are the one part of this file not verified against a live response.

FAILURE POLICY
    Nothing here raises. A missing key, a property with no project, an HTTP
    error or a shape we do not recognize all return `(None, reason)` so callers
    degrade to a gap. An unmeasured property must read as "not measured yet",
    never as a zero score.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_BASE = (os.environ.get("SEARCHABLE_API_BASE")
         or "https://api.searchable.ai/v1").rstrip("/")
_TIMEOUT = float(os.environ.get("SEARCHABLE_TIMEOUT", "20"))
_CACHE_TTL = float(os.environ.get("SEARCHABLE_CACHE_TTL", "900"))  # 15 minutes

# Endpoint map — confirm against the vendor's API docs before first live use.
ENDPOINTS = {
    "projects": "/projects",
    "visibility": "/projects/{project_id}/visibility",
    "opportunities": "/projects/{project_id}/opportunities",
    "sentiment": "/projects/{project_id}/sentiment",
    "sources": "/projects/{project_id}/sources",
    "site_health": "/projects/{project_id}/site-health",
    "prompts": "/projects/{project_id}/prompts",
}

_cache: Dict[str, Tuple[float, Any]] = {}


def _api_key() -> str:
    return (os.environ.get("SEARCHABLE_API_KEY") or "").strip()


def is_configured() -> bool:
    return bool(_api_key())


def clear_cache() -> None:
    _cache.clear()


def _cached(key: str):
    hit = _cache.get(key)
    if hit and (time.time() - hit[0]) < _CACHE_TTL:
        return hit[1]
    return None


def _store(key: str, value: Any) -> Any:
    _cache[key] = (time.time(), value)
    return value


def _get(path: str, params: Optional[Dict[str, Any]] = None) -> Tuple[Optional[dict], Optional[str]]:
    """One GET. Returns (payload, None) or (None, reason). Never raises."""
    if not is_configured():
        return None, "AI-visibility measurement is not connected on this server."
    import requests

    url = _BASE + path
    try:
        resp = requests.get(
            url,
            headers={"Authorization": "Bearer %s" % _api_key(),
                     "Accept": "application/json"},
            params=params or {}, timeout=_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 — network, DNS, TLS
        logger.warning("searchable: %s unreachable (%s)", path, type(exc).__name__)
        return None, "The AI-visibility source is temporarily unreachable."
    if resp.status_code == 404:
        return None, "That project is not known to the AI-visibility source."
    if resp.status_code in (401, 403):
        logger.error("searchable: credentials refused on %s (%s)", path, resp.status_code)
        return None, "The AI-visibility source refused our credentials."
    if resp.status_code == 429:
        return None, "The AI-visibility source is rate-limiting us right now."
    if resp.status_code >= 400:
        logger.warning("searchable: %s returned %s", path, resp.status_code)
        return None, "The AI-visibility source returned an error."
    try:
        return resp.json(), None
    except ValueError:
        return None, "The AI-visibility source returned something that is not JSON."


# --- normalizers -----------------------------------------------------------
# Written against live payloads so rules can be built and tested before the API
# key exists. `from_payload` accepts either an API response or one captured by
# hand, which is what made these shapes knowable in the first place.

def normalize_projects(payload: Any) -> List[Dict[str, Any]]:
    rows = (payload or {}).get("projects") if isinstance(payload, dict) else payload
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        out.append({
            "project_id": row.get("id"),
            "name": row.get("name"),
            "domain": _clean_domain(row.get("domain")),
            "active": bool(row.get("isActive", True)),
            "data_from": row.get("dataAvailableFrom"),
        })
    return out


def normalize_visibility(payload: Any) -> Optional[Dict[str, Any]]:
    """Score, trend and per-engine rates — the numbers a client sees."""
    if not isinstance(payload, dict):
        return None
    summary = payload.get("summary") or {}
    platforms = []
    for row in payload.get("platforms") or []:
        if not isinstance(row, dict):
            continue
        platforms.append({
            "engine": row.get("platform") or row.get("platformId"),
            "visibility_rate": row.get("visibilityRate"),
            "responses": row.get("responses"),
            "responses_with_brand": row.get("responsesWithBrand"),
            "citations": row.get("citations"),
            "brand_citations": row.get("brandCitations"),
        })
    score = summary.get("visibilityScore")
    if score is None and not platforms:
        return None
    return {
        "score": score,
        "score_change": summary.get("scoreChange"),
        "change_period": summary.get("scoreChangePeriod"),
        "responses": summary.get("totalResponses"),
        "mentions": summary.get("totalMentions"),
        "citations": summary.get("totalCitations"),
        "mention_rate": summary.get("responseMentionRate"),
        "engines": platforms,
        "trend": payload.get("trend") or [],
        "topics": [t.get("name") for t in
                   ((payload.get("availableFilters") or {}).get("topics") or [])
                   if isinstance(t, dict)],
        "range": payload.get("dateRange"),
    }


def normalize_opportunities(payload: Any) -> List[Dict[str, Any]]:
    """The vendor's own findings. We re-rank these ourselves; theirs is a
    starting point, not our priority order."""
    rows = (payload or {}).get("opportunities") if isinstance(payload, dict) else payload
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        out.append({
            "id": row.get("id"),
            "title": row.get("title"),
            "detail": row.get("description"),
            "source": row.get("source"),
            "category": row.get("category"),
            "impact": row.get("impact"),
            "action_type": row.get("actionType"),
            "status": row.get("status"),
            "created": row.get("createdAt"),
        })
    return out


def _clean_domain(value: Any) -> str:
    text = str(value or "").strip().lower()
    for prefix in ("https://", "http://"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    if text.startswith("www."):
        text = text[4:]
    return text.split("/")[0].strip()


# --- reads -----------------------------------------------------------------

def list_projects() -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    cached = _cached("projects")
    if cached is not None:
        return cached, None
    payload, reason = _get(ENDPOINTS["projects"])
    if payload is None:
        return None, reason
    return _store("projects", normalize_projects(payload)), None


def project_for_domain(domain: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """The project measuring this property's website, if one exists.

    Domains are matched loosely (scheme, www and path stripped) because HubSpot
    stores websites however whoever typed them felt that day.
    """
    wanted = _clean_domain(domain)
    if not wanted:
        return None, "This property has no website on its record."
    projects, reason = list_projects()
    if projects is None:
        return None, reason
    for project in projects:
        if project["domain"] and project["domain"] == wanted:
            return project, None
    return None, ("This property's website is not measured for AI visibility yet. "
                  "Add it as a project to start measuring.")


def visibility(project_id: str, days: int = 30) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    key = "vis:%s:%s" % (project_id, days)
    cached = _cached(key)
    if cached is not None:
        return cached, None
    payload, reason = _get(ENDPOINTS["visibility"].format(project_id=project_id),
                           {"group_by": "platform", "days": days})
    if payload is None:
        return None, reason
    normalized = normalize_visibility(payload)
    if normalized is None:
        return None, "The AI-visibility source returned no readings for this property."
    return _store(key, normalized), None


def opportunities(project_id: str) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    key = "opp:%s" % project_id
    cached = _cached(key)
    if cached is not None:
        return cached, None
    payload, reason = _get(ENDPOINTS["opportunities"].format(project_id=project_id))
    if payload is None:
        return None, reason
    return _store(key, normalize_opportunities(payload)), None


def for_property(domain: str, days: int = 30) -> Dict[str, Any]:
    """Everything we measure for one property, shaped for a recommendation rule.

    Always returns a dict. `measured` is False with a `reason` when the property
    has no project, which is the common case today: one project exists across a
    hundred websites, and "not measured yet" is a useful thing for a rule to be
    able to say.
    """
    out: Dict[str, Any] = {"domain": _clean_domain(domain), "measured": False,
                           "reason": None, "project_id": None,
                           "visibility": None, "opportunities": []}
    project, reason = project_for_domain(domain)
    if project is None:
        out["reason"] = reason
        return out
    out["project_id"] = project["project_id"]
    out["project_name"] = project.get("name")
    vis, vis_reason = visibility(project["project_id"], days=days)
    if vis is None:
        out["reason"] = vis_reason
        return out
    opps, _ = opportunities(project["project_id"])
    out.update({"measured": True, "visibility": vis, "opportunities": opps or []})
    return out


def from_payload(projects: Any = None, visibility_payload: Any = None,
                 opportunities_payload: Any = None) -> Dict[str, Any]:
    """Build the same shape from payloads captured outside this client.

    Rules and tests are written against this, so the module is useful before the
    API key exists and the switch to live transport changes nothing downstream.
    """
    out: Dict[str, Any] = {"measured": False, "reason": None,
                           "visibility": None, "opportunities": []}
    normalized = normalize_visibility(visibility_payload)
    if normalized is None:
        out["reason"] = "No visibility reading in the supplied payload."
        return out
    rows = normalize_projects(projects)
    if rows:
        out["project_id"] = rows[0]["project_id"]
        out["project_name"] = rows[0]["name"]
        out["domain"] = rows[0]["domain"]
    out.update({"measured": True, "visibility": normalized,
                "opportunities": normalize_opportunities(opportunities_payload)})
    return out
