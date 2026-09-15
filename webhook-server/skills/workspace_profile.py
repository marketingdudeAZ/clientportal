"""Property profile — `/api/workspace/profile*` (Round 5).

The community brief (`community_brief.SECTIONS`, 13 sections, 55 fields) as the
Workspace profile, so property marketing clients can keep its context current.

Rules, all server-side:

  * Clients see and edit the non-internal fields. `BriefField.internal` fields
    are removed from every client and preview-as-client payload.
  * The live value is `community_brief.resolve_value` (override > resolved >
    empty). Provenance says which one it is and, for an override, who wrote it
    and when, from the profile audit log (`property_brief_audit`).
  * `ad_facing` and `used_in` come only from `community_brief.used_in()`.
  * Writes go only through `community_brief.write_field` (override properties).
    Nothing here writes `uuid` (R1).

Staleness: a field holding a human-written value (an override) is stale when
its newest audit entry (an edit or a "reviewed, no change" check-in) is 90+ days
old, or when the audit log has no entry for it. Resolved values are refreshed by
their pipeline and never go stale here. Without the audit table, staleness is
unknown (null).
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import datetime, timedelta

import community_brief
from skills import workspace_common as wc

logger = logging.getLogger(__name__)

STALE_AFTER = timedelta(days=90)
AUDIT_LIMIT = 100
# Completeness weights: fields that feed ads or AI answers count three times a
# context field. Internal fields count only for staff, because only staff see them.
HIGH_WEIGHT = 3
CONTEXT_WEIGHT = 1
TOP_MISSING = 3

APTIQ_RESOLVED = frozenset({"year_built", "floor_plans"})
HUBSPOT_RESOLVED = frozenset({"name", "address", "city", "state", "zip", "domain"})

_lock = threading.Lock()
_local_audit: dict = {}          # company_id → [audit row] written by this process
_checkins: dict = {}             # company_id → datetime of the latest check-in


def clear() -> None:
    with _lock:
        _local_audit.clear()
        _checkins.clear()


# ── reading ──────────────────────────────────────────────────────────────────

def section_key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")


def visible_sections(internal: bool) -> list:
    return [(title, [f for f in fields if internal or not f.internal])
            for title, fields in community_brief.SECTIONS]


def read_props(company_id: str) -> dict:
    """Every brief property, read once through hubspot_client."""
    import hubspot_client
    return dict(hubspot_client.get_company(company_id, community_brief._all_property_names()) or {})


def _row_at(row: dict) -> str | None:
    return wc.to_iso_ts(row.get("edited_at"))


def audit_rows(company_id: str, gaps: list) -> list | None:
    """Audit entries for the property, newest first, or None when unknown."""
    import property_brief_audit

    with _lock:
        local = [dict(r) for r in _local_audit.get(str(company_id), [])]
    if not property_brief_audit._table_id():
        gaps.append(wc.gap("last_updated", "Edit dates need the profile audit table (HUBDB_AUDIT_TABLE_ID), "
                                           "which is not configured here", source="property_brief_audit",
                           internal=True))
        return None
    try:
        remote = list(property_brief_audit.recent_edits(str(company_id), limit=AUDIT_LIMIT) or [])
    except Exception as exc:  # noqa: BLE001
        gaps.append(wc.gap("last_updated", f"The profile audit log could not be read ({type(exc).__name__})",
                           source="property_brief_audit", internal=True))
        return None
    if len(remote) >= AUDIT_LIMIT:
        gaps.append(wc.gap("stale", f"Only the latest {AUDIT_LIMIT} profile edits were read; a field last "
                                    "edited before them shows as not updated recently",
                           source="property_brief_audit"))
    seen, rows = set(), []
    for row in local + remote:
        at = _row_at(row)
        key = (row.get("field_key"), str(row.get("edited_by") or "").lower(), str(row.get("new_value") or ""),
               (at or "")[:15])
        if key in seen:
            continue
        seen.add(key)
        rows.append({**row, "at": at})
    rows.sort(key=lambda r: r["at"] or "", reverse=True)
    return rows


def display_name(edited_by: str | None) -> str | None:
    """"dana.rogers@rpmliving.com" → "Dana R."; non-email editors as written."""
    who = str(edited_by or "").strip().split(" ")[0]
    if not who:
        return None
    if "@" not in who:
        return who
    parts = [p for p in re.split(r"[._-]+", who.split("@")[0]) if p]
    if not parts:
        return None
    first = parts[0].capitalize()
    return f"{first} {parts[-1][0].upper()}." if len(parts) > 1 else first


def resolved_source(field) -> str:
    if field.key in APTIQ_RESOLVED:
        return "aptiq"
    if field.key in HUBSPOT_RESOLVED:
        return "hubspot_company"
    return "site_scrape"


def filled(field, value) -> bool:
    if not community_brief._nonblank(value):
        return False
    if field.type in community_brief.TABLE_TYPES:
        return bool(community_brief._parse_json_list(value))
    return True


def provenance(field, props: dict, last: dict | None) -> dict:
    override = bool(field.hs_override) and filled(field, props.get(field.hs_override))
    resolved = bool(field.hs_resolved) and filled(field, props.get(field.hs_resolved))
    if override:
        return {"kind": "override", "by": display_name(last.get("edited_by")) if last else None,
                "at": last["at"] if last else None, "source": "profile_edit",
                "overrides": resolved_source(field) if resolved else None}
    if resolved:
        return {"kind": "resolved", "by": None, "at": None, "source": resolved_source(field), "overrides": None}
    return {"kind": "empty", "by": None, "at": None, "source": None, "overrides": None}


def field_view(field, props: dict, entries: list, *, internal: bool, audit_known: bool, now: datetime,
               pending: dict | None = None, suggestion: dict | None = None, review: dict | None = None) -> dict:
    value = community_brief.resolve_value(props, field.hs_resolved, field.hs_override)
    has_value = filled(field, value)
    last = entries[0] if entries else None
    editable = bool(field.hs_override)
    source = provenance(field, props, last)
    stale = None
    if audit_known:
        # Only human-written values go stale: resolved values are refreshed by
        # their pipeline (site scrape, AptIQ, HubSpot), not by the audit log.
        last_at = wc.to_datetime(last["at"]) if last and last.get("at") else None
        stale = bool(source["kind"] == "override" and (last_at is None or now - last_at >= STALE_AFTER))
    out = {
        "key": field.key, "label": field.label, "hint": field.hint or None, "type": field.type,
        "options": list(field.options), "section": field.section,
        "value": value if has_value else None,
        "provenance": source,
        "ad_facing": community_brief.is_ad_facing(field.key),
        "used_in": community_brief.used_in(field.key),
        "internal": field.internal, "editable": editable,
        "stale": stale, "last_updated": last["at"] if last else None,
        "pending": pending, "suggestion": suggestion, "review_outcome": review,
    }
    if internal:
        text_field = field.type not in community_brief.TABLE_TYPES
        out["fair_housing_review"] = wc.fair_housing_review(value) if (has_value and text_field) else None
    return out


def weight(view: dict) -> int:
    return HIGH_WEIGHT if {"ads", "ai_answers"} & set(view["used_in"]) else CONTEXT_WEIGHT


def completeness(views: list) -> dict:
    """{pct, weighted: true, top_missing} over the fields the caller can see."""
    total = sum(weight(v) for v in views)
    have = sum(weight(v) for v in views if v["value"] is not None)
    order = {v["key"]: i for i, v in enumerate(views)}
    missing = sorted((v for v in views if v["value"] is None), key=lambda v: (-weight(v), order[v["key"]]))
    return {"pct": round(100 * have / total) if total else None, "weighted": True,
            "top_missing": [{"key": v["key"], "label": v["label"], "used_in": v["used_in"]}
                            for v in missing[:TOP_MISSING]]}


def checked_in_this_month(company_id: str, now: datetime) -> bool:
    with _lock:
        at = _checkins.get(str(company_id))
    return bool(at and (at.year, at.month) == (now.year, now.month))


def checkin_state(views: list, company_id: str, now: datetime) -> dict:
    stale = [v["key"] for v in views if v["stale"]]
    return {"due": bool(stale) and not checked_in_this_month(company_id, now), "stale_fields": stale}


# Filled in by the edit, approval and suggestion steps below.
def pending_by_key(ctx, gaps: list) -> dict:
    return {}


def reviews_by_key(ctx, gaps: list) -> dict:
    return {}


def suggestions_by_key(ctx, props: dict, views_by_key: dict, gaps: list) -> dict:
    return {}


def build_profile(ctx, *, internal: bool, props: dict | None = None, now: datetime | None = None) -> dict:
    now = now or wc.utc_now()
    gaps: list = []
    props = read_props(ctx.company_id) if props is None else props
    audit = audit_rows(ctx.company_id, gaps)
    entries: dict = {}
    for row in audit or []:
        entries.setdefault(row.get("field_key"), []).append(row)
    pending = pending_by_key(ctx, gaps)
    reviews = reviews_by_key(ctx, gaps)

    sections, views = [], []
    for title, fields in visible_sections(internal):
        section_views = [field_view(f, props, entries.get(f.key, []), internal=internal,
                                    audit_known=audit is not None, now=now,
                                    pending=pending.get(f.key), review=None if pending.get(f.key) else reviews.get(f.key))
                         for f in fields]
        views += section_views
        sections.append({"key": section_key(title), "title": title, "fields": section_views})
    suggestions = suggestions_by_key(ctx, props, {v["key"]: v for v in views}, gaps)
    for v in views:
        if not v["pending"]:
            v["suggestion"] = suggestions.get(v["key"])
    for s in sections:
        s["completeness"] = completeness(s["fields"])

    visible_keys = {v["key"] for v in views}
    stamps = [r["at"] for r in audit or [] if r.get("at") and r.get("field_key") in visible_keys]
    return {
        "property": {"company_id": ctx.company_id, "name": ctx.name or props.get("name") or None,
                     "last_updated": max(stamps) if stamps else None},
        "completeness": completeness(views),
        "checkin": checkin_state(views, ctx.company_id, now),
        "sections": sections,
        "gaps": wc.gaps_for(gaps, internal),
    }
