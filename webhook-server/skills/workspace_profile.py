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

import difflib
import hashlib
import itertools
import json
import logging
import re
import threading
import time
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
_proposals: dict = {}            # company_id → {proposal_id: record}

_sequence = itertools.count(1)   # orders proposals made within the same second
PROPOSAL_EVENT = "workspace_profile_update_proposed"
CHECKIN_EVENT = "workspace_profile_checkin"
DISMISSED_EVENT = "workspace_profile_suggestion_dismissed"
_dismissed: dict = {}            # company_id → {suggestion_id: reason}
_completeness: dict = {}         # (company_id, internal) → (monotonic time, metric)
COMPLETENESS_TTL = 1800.0
ITEM_SOURCE = "profile_update"
STORED_TTL = 120.0               # seconds a property's stored proposals and decisions are reused
_stored: dict = {}               # company_id → (monotonic time, [payload], decision history | None)


def clear() -> None:
    with _lock:
        _local_audit.clear()
        _checkins.clear()
        _proposals.clear()
        _stored.clear()
        _dismissed.clear()
        _completeness.clear()


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
    rows.sort(key=lambda r: (r["at"] or "", r.get("_seq") or 0), reverse=True)
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


def checked_in_this_month(company_id: str, now: datetime, stored: list | None = None) -> bool:
    with _lock:
        local = _checkins.get(str(company_id))
    return any(at and (at.year, at.month) == (now.year, now.month) for at in [local] + list(stored or []))


def checkin_state(views: list, company_id: str, now: datetime, stored: list | None = None) -> dict:
    """Due once a month per property while any field is stale."""
    stale = [v["key"] for v in views if v["stale"]]
    return {"due": bool(stale) and not checked_in_this_month(company_id, now, stored), "stale_fields": stale}


# ── profile updates: proposals awaiting RPM review ───────────────────────────
#
# Storage. `ticket_profile_proposals` (migration 0015) is unapplied in
# production, so profile updates are stored the way Workspace decisions are:
# each proposal is a `workspace_profile_update_proposed` loop event, and its
# approval or rejection is the ordinary `workspace_decision` event on
# `profile_update:<proposal_id>` (net of `workspace_decision_undone`). This
# process also keeps the records it wrote, so the flow works without BigQuery.

def item_id_for(proposal_id: str) -> str:
    return f"{ITEM_SOURCE}:{proposal_id}"


def _proposal_id(company_id: str, key: str, value: str, at: str) -> str:
    return hashlib.sha1(f"{company_id}|{key}|{value}|{at}".encode("utf-8")).hexdigest()[:12]


def propose(ctx, field, current: str, value: str, actor: str, now: datetime) -> dict:
    import loop_writer

    at = wc.to_iso_ts(now)
    pid = _proposal_id(ctx.company_id, field.key, value, at)
    record = {
        "proposal_id": pid, "company_id": ctx.company_id, "property_uuid": ctx.uuid or None,
        "field_key": field.key, "field_label": field.label,
        "current_value": current, "proposed_value": value,
        "proposed_by": actor, "proposed_at": at, "used_in": community_brief.used_in(field.key),
        "status": "pending", "decided_by": None, "decided_at": None, "reason": None,
    }
    stored = {**record, "_seq": next(_sequence)}
    with _lock:
        for other in _proposals.get(ctx.company_id, {}).values():
            if other["field_key"] == field.key and other["status"] == "pending":
                other["status"] = "superseded"
        _proposals.setdefault(ctx.company_id, {})[pid] = stored
        _stored.pop(ctx.company_id, None)
    try:
        loop_writer.record("engage", PROPOSAL_EVENT, property_uuid=ctx.uuid or None, company_id=ctx.company_id,
                           source="workspace", source_id=item_id_for(pid), trigger="client_action",
                           payload=dict(record))
    except Exception as exc:  # noqa: BLE001 — the in-process record still stands
        logger.warning("profile update event not written for %s: %s", ctx.company_id, exc)
    return dict(record)


def _stored_events(ctx, gaps: list) -> dict:
    """What other servers (or this one before a restart) stored for the property:
    proposals, dismissed suggestions, check-ins and decision history. Reused for
    STORED_TTL seconds; `history` is None when it is unknown."""
    import loop_writer
    from skills import workspace_inbox as wi

    empty = {"proposals": [], "dismissed": {}, "checkins": [], "history": None}
    if loop_writer._bq() is None:
        gaps.append(wc.gap("pending", "Profile updates, check-ins and dismissed suggestions from before this server "
                                      "started need BigQuery loop events, which are not configured here",
                           source="loop_events", internal=True))
        return empty
    if not ctx.uuid:
        return empty
    with _lock:
        hit = _stored.get(ctx.company_id)
    if hit and time.monotonic() - hit[0] < STORED_TTL:
        return hit[1]
    out = {"proposals": [], "dismissed": {}, "checkins": [], "history": None}
    try:
        for ev in loop_writer.query_recent(ctx.uuid, limit=500):
            payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else None
            if not payload:
                continue
            etype = ev.get("event_type")
            if etype == PROPOSAL_EVENT and payload.get("proposal_id"):
                out["proposals"].append(payload)
            elif etype == DISMISSED_EVENT and payload.get("suggestion_id"):
                out["dismissed"][payload["suggestion_id"]] = payload.get("reason") or ""
            elif etype == CHECKIN_EVENT:
                at = wc.to_datetime(ev.get("occurred_at"))
                if at:
                    out["checkins"].append(at)
        out["history"] = wi.decision_history(ctx, gaps)
    except Exception as exc:  # noqa: BLE001
        gaps.append(wc.gap("pending", f"Stored profile updates could not be read ({type(exc).__name__})",
                           source="loop_events", internal=True))
        return empty
    with _lock:
        _stored[ctx.company_id] = (time.monotonic(), out)
    return out


def prime_stored(events_by_company: dict) -> None:
    """Fill the stored-events cache for many properties from one batched read,
    building exactly what _stored_events would have built per property."""
    from skills import workspace_inbox as wi

    now = time.monotonic()
    with _lock:
        for cid, events in events_by_company.items():
            out = {"proposals": [], "dismissed": {}, "checkins": [],
                   "history": wi.history_from_events(events)}
            for ev in events:
                payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else None
                if not payload:
                    continue
                etype = ev.get("event_type")
                if etype == PROPOSAL_EVENT and payload.get("proposal_id"):
                    out["proposals"].append(payload)
                elif etype == DISMISSED_EVENT and payload.get("suggestion_id"):
                    out["dismissed"][payload["suggestion_id"]] = payload.get("reason") or ""
                elif etype == CHECKIN_EVENT:
                    at = wc.to_datetime(ev.get("occurred_at"))
                    if at:
                        out["checkins"].append(at)
            _stored[str(cid)] = (now, out)


def proposals(ctx, gaps: list) -> list:
    """Every profile update for the property, newest first, with its status."""
    with _lock:
        by_id = {pid: dict(r) for pid, r in _proposals.get(ctx.company_id, {}).items()}
    stored = _stored_events(ctx, gaps)
    for payload in stored["proposals"]:
        by_id.setdefault(payload["proposal_id"], {**payload, "status": "pending"})
    apply_decided(by_id, stored["history"])
    rows = sorted(by_id.values(), key=lambda r: (r.get("proposed_at") or "", r.get("_seq") or 0), reverse=True)
    newest_pending: set = set()
    for r in rows:
        if r["status"] != "pending":
            continue
        if r["field_key"] in newest_pending:
            r["status"] = "superseded"
        newest_pending.add(r["field_key"])
    return rows


def apply_decided(by_id: dict, history: dict | None) -> None:
    """Status from the `workspace_decision` events on `profile_update:<id>`.

    A record this process decided keeps the status it set; the stored decisions
    fill in the rest (another server's decision, or a restart).
    """
    from skills import workspace_inbox as wi

    for pid, rec in by_id.items():
        if rec.get("_seq") and rec["status"] != "pending":
            continue
        effective = [d for d in (history or {}).get(item_id_for(pid), [])
                     if d.get("outcome", "ok") in ("ok", "partial") and not d.get("undone")]
        if not effective:
            continue
        last = effective[-1]
        rec.update({"decided_by": last.get("actor"), "decided_at": wc.to_iso_ts(last.get("at"))})
        if last.get("action") == "approve":
            rec.update({"status": "approved", "reason": None})
        elif last.get("action") == "not_now":
            rec.update({"status": "rejected", "reason": wi.REASONS.get(last.get("reason"), last.get("reason"))})


def reviews_by_key(ctx, gaps: list, rows: list | None = None) -> dict:
    """The latest RPM decision per field: what the client sees on the field."""
    out = {}
    for r in proposals(ctx, gaps) if rows is None else rows:
        if r["status"] in ("approved", "rejected") and r["field_key"] not in out:
            out[r["field_key"]] = {"status": r["status"], "at": r.get("decided_at"),
                                   "reason": r.get("reason") if r["status"] == "rejected" else None,
                                   "proposed_value": r.get("proposed_value") or ""}
    return out


def pending_by_key(ctx, gaps: list, rows: list | None = None) -> dict:
    out = {}
    for r in proposals(ctx, gaps) if rows is None else rows:
        if r["status"] == "pending" and r["field_key"] not in out:
            out[r["field_key"]] = {"proposed_value": r["proposed_value"], "by": display_name(r["proposed_by"]),
                                   "at": r["proposed_at"], "item_id": item_id_for(r["proposal_id"])}
    return out


def build_profile(ctx, *, internal: bool, props: dict | None = None, now: datetime | None = None) -> dict:
    now = now or wc.utc_now()
    gaps: list = []
    props = read_props(ctx.company_id) if props is None else props
    audit = audit_rows(ctx.company_id, gaps)
    entries: dict = {}
    for row in audit or []:
        entries.setdefault(row.get("field_key"), []).append(row)
    rows = proposals(ctx, gaps)
    pending = pending_by_key(ctx, gaps, rows)
    reviews = reviews_by_key(ctx, gaps, rows)

    sections, views = [], []
    for title, fields in visible_sections(internal):
        section_views = [field_view(f, props, entries.get(f.key, []), internal=internal,
                                    audit_known=audit is not None, now=now,
                                    pending=pending.get(f.key), review=None if pending.get(f.key) else reviews.get(f.key))
                         for f in fields]
        views += section_views
        sections.append({"key": section_key(title), "title": title, "fields": section_views})
    suggestions = suggestions_by_key(ctx, props, {v["key"]: v for v in views}, gaps, internal=internal)
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
        "checkin": checkin_state(views, ctx.company_id, now, _stored_events(ctx, [])["checkins"]),
        "sections": sections,
        "gaps": wc.gaps_for(gaps, internal),
    }


# ── editing ──────────────────────────────────────────────────────────────────

SAVED = "saved"
PENDING = "pending_review"
BLOCKED = "blocked"


def normalize_value(field, value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        if field.type in community_brief.TABLE_TYPES:
            return json.dumps(value)
        parts = [str(v).strip() for v in value if str(v).strip()]
        return ";".join(parts) if field.type == "multiselect" else "\n".join(parts)
    if isinstance(value, (dict, bool)):
        raise wc.WorkspaceError(400, "Invalid value", "a string or a list")
    return str(value)


def validate(field, value: str) -> str:
    """The checks `community_brief.write_field` makes, without writing. Raises 400."""
    if field.options and value:
        if field.type == "multiselect":
            selected = [v.strip() for v in value.split(";") if v.strip()]
            bad = [v for v in selected if v not in field.options]
            if bad:
                raise wc.WorkspaceError(400, "Invalid value", f"{', '.join(bad)} is not one of the options")
            return ";".join(selected)
        if value not in field.options:
            raise wc.WorkspaceError(400, "Invalid value", f"{value} is not one of the options")
    if field.type in community_brief.TABLE_TYPES and value not in ("", "[]"):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            raise wc.WorkspaceError(400, "Invalid value", "value must be valid JSON")
        if not isinstance(parsed, list):
            raise wc.WorkspaceError(400, "Invalid value", "value must be a JSON array")
    return value


def fair_housing_result(review: dict | None, internal: bool) -> dict:
    """Blocked is shown to everyone, with the words; a low flag only to staff."""
    if not review:
        return {"result": "clear"}
    if review["severity"] == "high":
        return {"result": "blocked", "severity": "high", "terms": list(review["terms"])}
    if internal:
        return {"result": "flagged", "severity": review["severity"], "terms": list(review["terms"])}
    return {"result": "clear"}


def blocked_message(review: dict) -> str:
    words = ", ".join(f"“{t}”" for t in review["terms"] if t) or "some of this wording"
    return (f"This wasn't saved. {words} describes who can live here, which Fair Housing rules don't allow in "
            "housing marketing. Describe the property, its amenities or its location instead.")


def _remember_edit(ctx, field, old: str, new: str, actor: str, now: datetime, label: str | None = None) -> None:
    with _lock:
        _local_audit.setdefault(ctx.company_id, []).append({
            "field_key": field.key, "field_label": label or field.label, "old_value": old, "new_value": new,
            "edited_by": actor, "edited_at": wc.to_iso_ts(now), "_seq": next(_sequence)})


def one_field(ctx, field, props: dict, *, internal: bool, now: datetime) -> dict:
    gaps: list = []
    audit = audit_rows(ctx.company_id, gaps)
    entries = [r for r in audit or [] if r.get("field_key") == field.key]
    rows = proposals(ctx, gaps)
    pending = pending_by_key(ctx, gaps, rows).get(field.key)
    return field_view(field, props, entries, internal=internal, audit_known=audit is not None, now=now,
                      pending=pending, review=None if pending else reviews_by_key(ctx, gaps, rows).get(field.key))


def edit_field(ctx, key: str, value, actor: str, *, staff: bool, props: dict | None = None,
               now: datetime | None = None) -> dict:
    """The one edit flow: Fair Housing, then review or save. Raises WorkspaceError."""
    now = now or wc.utc_now()
    field = community_brief.FIELDS.get(key)
    if field is None:
        raise wc.WorkspaceError(404, "Unknown field")
    if field.internal and not staff:
        raise wc.WorkspaceError(403, "This field is internal")
    if not field.hs_override:
        raise wc.WorkspaceError(400, f"{field.label} comes from HubSpot and isn't edited here")
    value = validate(field, normalize_value(field, value))
    props = read_props(ctx.company_id) if props is None else props
    current = community_brief.resolve_value(props, field.hs_resolved, field.hs_override)

    review = wc.fair_housing_review(value) if (value and field.type not in community_brief.TABLE_TYPES) else None
    result = fair_housing_result(review, staff)
    if review and review["severity"] == "high":
        logger.info("profile edit blocked by Fair Housing: %s/%s terms=%s", ctx.company_id, key, review["terms"])
        return {"field": one_field(ctx, field, props, internal=staff, now=now), "outcome": BLOCKED,
                "fair_housing": result, "message": blocked_message(review)}

    if not staff and community_brief.is_ad_facing(key):
        propose(ctx, field, current, value, actor, now)
        return {"field": one_field(ctx, field, props, internal=staff, now=now), "outcome": PENDING,
                "fair_housing": result,
                "message": "Sent to RPM for review. The current value stays live until RPM approves it."}

    ok, written = community_brief.write_field(ctx.company_id, key, value, edited_by=actor)
    if not ok:
        status = 502 if (written == "network error" or str(written).startswith("HubSpot")) else 400
        raise wc.WorkspaceError(status, "The field could not be saved", written)
    _after_write(ctx, field, props, current, written, actor, now)
    _forget_completeness(ctx.company_id)
    return {"field": one_field(ctx, field, props, internal=staff, now=now), "outcome": SAVED,
            "fair_housing": result, "message": "Saved · live in ads tomorrow" if community_brief.is_ad_facing(key) else "Saved"}


def _after_write(ctx, field, props: dict, old: str, new: str, actor: str, now: datetime) -> None:
    props[field.hs_override] = new
    _remember_edit(ctx, field, old, new, actor, now)
    try:
        import hubspot_client
        hubspot_client.invalidate(ctx.company_id)
    except Exception:  # noqa: BLE001
        pass


# ── RPM review of a profile update (item source `profile_update`) ────────────

def diff_lines(current: str | None, proposed: str | None) -> list:
    out = []
    for line in difflib.ndiff(str(current or "").splitlines(), str(proposed or "").splitlines()):
        op = {"  ": "same", "+ ": "add", "- ": "remove"}.get(line[:2])
        if op:
            out.append({"op": op, "text": line[2:]})
    return out


def proposal_items(ctx, gaps: list) -> list:
    """Workspace items for RPM staff. Internal only (INTERNAL_ONLY_SOURCES)."""
    from skills import workspace_inbox as wi

    items = []
    for r in proposals(ctx, gaps):
        if r["status"] == "superseded":
            continue
        pid, label, proposer = r["proposal_id"], r["field_label"], r.get("proposed_by") or None
        current, proposed = r.get("current_value") or "", r.get("proposed_value") or ""
        status = "to_do" if r["status"] == "pending" else "done"
        used = [community_brief.USED_IN_LABELS[u] for u in r.get("used_in") or [] if u in community_brief.USED_IN_LABELS]
        receipts = [wi._receipt(f"Proposed by {proposer or 'a client'}", "workspace_profile_update", r.get("proposed_at"))]
        items.append(wi._new_item(
            ITEM_SOURCE, pid, f"Profile update: {label}",
            found=f"Proposed {label}: {wc.truncate(proposed)}" if proposed else f"Proposed clearing {label}",
            if_skip=f"The profile keeps: {wc.truncate(current)}" if current else f"{label} stays empty",
            receipts=receipts,
            status=status,
            needs_approval=status == "to_do",
            client_visible=False,
            steps=[wi._step("Written to the property profile", "auto"),
                   wi._step("Reaches ads on the next daily feed sync", "queued")],
            evidence={"columns": ["Current value", "Proposed value"], "rows": [[current or None, proposed or None]],
                      "more_count": 0},
            trail=[wi.trail(r.get("proposed_at"), proposer, "Proposed this change", "internal")],
            for_whom={"text": "Used in: " + ", ".join(used) if used else "Not used in ads or reports", "questions": []},
            profile_update={
                "field_key": r["field_key"], "field_label": label, "current_value": current or None,
                "proposed_value": proposed or None, "proposed_by": proposer, "proposed_at": r.get("proposed_at"),
                "used_in": list(r.get("used_in") or []), "diff": diff_lines(current, proposed),
                "status": r["status"], "decided_by": r.get("decided_by"), "decided_at": r.get("decided_at"),
                "reason": r.get("reason"),
            },
            _created=r.get("proposed_at"),
            _closed=r.get("decided_at") if status == "done" else None,
            _closed_as="not_now" if r["status"] == "rejected" else None,
            _raw={"proposal_id": pid, "field_key": r["field_key"], "proposed_by": proposer},
        ))
    return items


def _find(ctx, proposal_id: str) -> dict:
    rec = next((r for r in proposals(ctx, []) if r["proposal_id"] == proposal_id), None)
    if rec is None:
        raise wc.WorkspaceError(404, "Item not found")
    return rec


def _set_status(ctx, rec: dict, **changes) -> None:
    with _lock:
        stored = _proposals.setdefault(ctx.company_id, {}).get(rec["proposal_id"])
        if stored is None:
            stored = {k: v for k, v in rec.items()}
            stored["_seq"] = next(_sequence)
            _proposals[ctx.company_id][rec["proposal_id"]] = stored
        stored.update(changes)
        _stored.pop(ctx.company_id, None)


def approve(ctx, proposal_id: str, actor: str, *, now: datetime | None = None) -> dict:
    """Write the proposed value through write_field. Raises WorkspaceError."""
    now = now or wc.utc_now()
    rec = _find(ctx, proposal_id)
    if rec["status"] != "pending":
        raise wc.WorkspaceError(409, f"This profile update is already {rec['status']}")
    field = community_brief.FIELDS.get(rec["field_key"])
    if field is None or not field.hs_override:
        raise wc.WorkspaceError(409, "This field can no longer be edited")
    proposed = rec.get("proposed_value") or ""
    review = wc.fair_housing_review(proposed) if field.type not in community_brief.TABLE_TYPES else None
    if review and review["severity"] == "high":
        raise wc.WorkspaceError(409, "Fair Housing", blocked_message(review))
    editor = f"{actor} (approved; proposed by {rec.get('proposed_by') or 'a client'})"
    ok, written = community_brief.write_field(ctx.company_id, field.key, proposed, edited_by=editor)
    if not ok:
        status = 502 if (written == "network error" or str(written).startswith("HubSpot")) else 400
        raise wc.WorkspaceError(status, "The profile could not be updated", written)
    _set_status(ctx, rec, status="approved", decided_by=actor, decided_at=wc.to_iso_ts(now), reason=None)
    _forget_completeness(ctx.company_id)
    _remember_edit(ctx, field, rec.get("current_value") or "", written, editor, now)
    try:
        import hubspot_client
        hubspot_client.invalidate(ctx.company_id)
    except Exception:  # noqa: BLE001
        pass
    return {**rec, "status": "approved", "decided_by": actor}


def reject(ctx, proposal_id: str, actor: str, reason: str, *, now: datetime | None = None) -> dict:
    """Not applied. The profile is untouched. Raises WorkspaceError."""
    now = now or wc.utc_now()
    rec = _find(ctx, proposal_id)
    if rec["status"] != "pending":
        raise wc.WorkspaceError(409, f"This profile update is already {rec['status']}")
    _set_status(ctx, rec, status="rejected", decided_by=actor, decided_at=wc.to_iso_ts(now), reason=reason)
    return {**rec, "status": "rejected", "decided_by": actor, "reason": reason}


def reopen(ctx, proposal_id: str) -> None:
    """Undo a rejection: the update waits for review again."""
    rec = _find(ctx, proposal_id)
    if rec["status"] != "rejected":
        raise wc.WorkspaceError(409, "not_undoable", f"The profile update is now {rec['status']}")
    _set_status(ctx, rec, status="pending", decided_by=None, decided_at=None, reason=None)


# ── suggestions from data ────────────────────────────────────────────────────
#
# One-tap proposed edits beside the field they affect. Sources, in priority order
# when a field has more than one: the monthly Fair Housing review (profile copy),
# an existing ticket-to-profile proposal, a GEO claim that conflicts with the
# field, and the auto-resolved value (site scrape / AptIQ) differing from the
# human override. Accepting runs `edit_field`; dismissing records the reason.

SUGGESTION_SOURCES = ("fair_housing", "ticket", "geo_claim", "site_scrape")


def suggestion_id(company_id: str, key: str, source: str, value: str) -> str:
    return hashlib.sha1(f"{company_id}|{key}|{source}|{value}".encode("utf-8")).hexdigest()[:16]


def _same(field, a, b) -> bool:
    if field.type in community_brief.TABLE_TYPES:
        return community_brief._parse_json_list(a) == community_brief._parse_json_list(b)
    return " ".join(str(a or "").split()).lower() == " ".join(str(b or "").split()).lower()


def _from_site(props: dict, visible: set) -> list:
    out = []
    for key in visible:
        f = community_brief.FIELDS[key]
        if not (f.hs_override and f.hs_resolved):
            continue
        override, resolved = props.get(f.hs_override), props.get(f.hs_resolved)
        if filled(f, override) and filled(f, resolved) and not _same(f, override, resolved):
            origin = "AptIQ" if key in APTIQ_RESOLVED else "the property website"
            out.append({"key": key, "value": str(resolved), "source": "site_scrape",
                        "reason": f"We found “{wc.truncate(resolved, 120)}” on {origin} — use it?"})
    return out


def _from_geo_claims(ctx, visible: set, gaps: list) -> list:
    """`geo_claims` rows whose `conflicts_brief_field` names a profile field."""
    import bigquery_client
    if not bigquery_client.is_bigquery_configured() or not ctx.uuid:
        gaps.append(wc.gap("suggestion.geo_claim", "GEO claim checks need BigQuery, which is not configured here",
                           source="geo_claims", internal=True))
        return []
    try:
        from google.cloud import bigquery
        base = f"{bigquery_client.BIGQUERY_PROJECT_ID}.{bigquery_client._dataset()}"
        rows = bigquery_client.query(f"""
            SELECT claim_text, conflicts_brief_field, engine, created_at FROM `{base}.geo_claims`
            WHERE property_uuid = @uuid AND conflicts_brief_field IS NOT NULL
            ORDER BY created_at DESC LIMIT 50""", [bigquery.ScalarQueryParameter("uuid", "STRING", ctx.uuid)])
    except Exception as exc:  # noqa: BLE001
        gaps.append(wc.gap("suggestion.geo_claim", f"GEO claims could not be read ({type(exc).__name__})",
                           source="geo_claims", internal=True))
        return []
    by_label = {f.label.lower(): k for k, f in community_brief.FIELDS.items()}
    out = []
    for r in rows:
        named = str(r.get("conflicts_brief_field") or "").strip()
        key = named if named in community_brief.FIELDS else by_label.get(named.lower())
        claim = str(r.get("claim_text") or "").strip()
        if key in visible and claim:
            engine = r.get("engine") or "An AI answer engine"
            out.append({"key": key, "value": claim, "source": "geo_claim",
                        "reason": f"{engine} says “{wc.truncate(claim, 120)}” — use it?"})
    if not rows:
        gaps.append(wc.gap("suggestion.geo_claim", "No GEO claims conflict with this profile yet", source="geo_claims"))
    return out


def _from_fair_housing(ctx, props: dict, visible: set, gaps: list) -> list:
    """The monthly review's findings on profile copy, as the value without the flagged sentence."""
    from skills import workspace_fair_housing_review as fhr
    review = fhr.latest(ctx, gaps) or {}
    by_label = {f.label: k for k, f in community_brief.FIELDS.items()}
    out = []
    for finding in review.get("findings") or []:
        location = str(finding.get("location") or "")
        if not location.startswith("Property profile: "):
            continue
        key = by_label.get(location.split(": ", 1)[1])
        excerpt = str(finding.get("excerpt") or "").rstrip("…").strip()
        if key not in visible or not excerpt:
            continue
        f = community_brief.FIELDS[key]
        current = community_brief.resolve_value(props, f.hs_resolved, f.hs_override)
        if excerpt not in current:
            continue
        cleaned = re.sub(r"[ \t]{2,}", " ", current.replace(excerpt, "")).strip()
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        if not cleaned or cleaned == current:
            continue
        out.append({"key": key, "value": cleaned, "source": "fair_housing",
                    "reason": f"The monthly Fair Housing review flagged “{wc.truncate(excerpt, 120)}” — use this "
                              "version without it?"})
    return out


def _from_tickets(ctx, visible: set) -> list:
    import ticket_profile_sync
    if not ticket_profile_sync.enabled():
        return []
    out = []
    for r in ticket_profile_sync.list_proposals(ctx.company_id, ctx.uuid):
        key, value = r.get("field_key"), str(r.get("proposed_value") or "")
        if key in visible and value:
            out.append({"key": key, "value": value, "source": "ticket", "ticket_proposal_id": r.get("proposal_id"),
                        "reason": f"A completed ticket ({r.get('task_id')}) suggests “{wc.truncate(value, 120)}” "
                                  "— use it?"})
    return out


def all_suggestions(ctx, props: dict, *, internal: bool, gaps: list) -> list:
    visible = {f.key for _, fields in visible_sections(internal) for f in fields}
    found = (_from_fair_housing(ctx, props, visible, gaps) + _from_tickets(ctx, visible)
             + _from_geo_claims(ctx, visible, gaps) + _from_site(props, visible))
    with _lock:
        dismissed = dict(_dismissed.get(ctx.company_id, {}))
    dismissed.update(_stored_events(ctx, [])["dismissed"])
    out = []
    for s in found:
        f = community_brief.FIELDS[s["key"]]
        current = community_brief.resolve_value(props, f.hs_resolved, f.hs_override)
        s["id"] = suggestion_id(ctx.company_id, s["key"], s["source"], s["value"])
        if s["id"] in dismissed or _same(f, current, s["value"]):
            continue
        out.append(s)
    return out


def suggestions_by_key(ctx, props: dict, views_by_key: dict, gaps: list, *, internal: bool = False) -> dict:
    out: dict = {}
    for s in sorted(all_suggestions(ctx, props, internal=internal, gaps=gaps),
                    key=lambda s: SUGGESTION_SOURCES.index(s["source"])):
        if s["key"] in views_by_key and s["key"] not in out:
            out[s["key"]] = {"id": s["id"], "value": s["value"], "source": s["source"], "reason": s["reason"]}
    return out


def _suggestion(ctx, props: dict, sid: str, internal: bool) -> dict:
    hit = next((s for s in all_suggestions(ctx, props, internal=internal, gaps=[]) if s["id"] == sid), None)
    if hit is None:
        raise wc.WorkspaceError(404, "Suggestion not found")
    return hit


def accept_suggestion(ctx, sid: str, actor: str, *, staff: bool, now: datetime | None = None) -> dict:
    """The same edit flow as a typed edit: Fair Housing, then review or save."""
    props = read_props(ctx.company_id)
    s = _suggestion(ctx, props, sid, staff)
    result = edit_field(ctx, s["key"], s["value"], actor, staff=staff, props=props, now=now)
    if result["outcome"] != BLOCKED:
        with _lock:
            _dismissed.setdefault(ctx.company_id, {})[sid] = "accepted"
    if result["outcome"] == SAVED and s.get("ticket_proposal_id"):
        try:
            import ticket_profile_sync
            row = ticket_profile_sync.get_proposal(s["ticket_proposal_id"])
            if row:
                ticket_profile_sync._transition(row, ticket_profile_sync.STATUS_ACCEPTED, actor, "workspace")
        except Exception as exc:  # noqa: BLE001 — the profile write already happened
            logger.warning("ticket proposal %s not marked accepted: %s", s["ticket_proposal_id"], exc)
    return {**result, "suggestion_id": sid}


def dismiss_suggestion(ctx, sid: str, actor: str, reason, *, staff: bool) -> dict:
    import loop_writer

    reason = str(reason or "").strip()
    if not reason:
        raise wc.WorkspaceError(400, "reason is required")
    reason = wc.truncate(reason, 500)
    props = read_props(ctx.company_id)
    s = _suggestion(ctx, props, sid, staff)
    with _lock:
        _dismissed.setdefault(ctx.company_id, {})[sid] = reason
        _stored.pop(ctx.company_id, None)
    try:
        loop_writer.record("engage", DISMISSED_EVENT, property_uuid=ctx.uuid or None, company_id=ctx.company_id,
                           source="workspace", source_id=sid, trigger="client_action",
                           payload={"suggestion_id": sid, "field_key": s["key"], "source": s["source"],
                                    "value": wc.truncate(s["value"], 500), "reason": reason, "actor": actor})
    except Exception as exc:  # noqa: BLE001
        logger.warning("suggestion dismissal event not written for %s: %s", ctx.company_id, exc)
    return {"suggestion_id": sid, "dismissed": True, "reason": reason}


# ── monthly check-in ─────────────────────────────────────────────────────────

REVIEWED_LABEL = "(reviewed, no change)"


def checkin(ctx, confirmed, actor: str, *, staff: bool, now: datetime | None = None) -> dict:
    """Record "reviewed, no change" for each confirmed field; that resets its clock."""
    import loop_writer
    import property_brief_audit

    if not isinstance(confirmed, list) or not all(isinstance(k, str) for k in confirmed):
        raise wc.WorkspaceError(400, "confirmed must be a list of field keys")
    now = now or wc.utc_now()
    props = read_props(ctx.company_id)
    done, skipped = [], []
    for key in dict.fromkeys(confirmed):
        field = community_brief.FIELDS.get(key)
        if field is None:
            skipped.append({"key": key, "reason": "Unknown field"})
            continue
        if field.internal and not staff:
            skipped.append({"key": key, "reason": "Internal field"})
            continue
        value = community_brief.resolve_value(props, field.hs_resolved, field.hs_override)
        if not filled(field, value):
            skipped.append({"key": key, "reason": "No value to confirm"})
            continue
        label = f"{field.label} {REVIEWED_LABEL}"
        property_brief_audit.log_edit(company_id=ctx.company_id, company_name=ctx.name or "", field_key=key,
                                      field_label=label, old_value=value, new_value=value, edited_by=actor)
        _remember_edit(ctx, field, value, value, actor, now, label=label)
        done.append(key)
    if done:
        with _lock:
            _checkins[ctx.company_id] = now
        try:
            loop_writer.record("engage", CHECKIN_EVENT, property_uuid=ctx.uuid or None, company_id=ctx.company_id,
                               source="workspace", source_id=f"profile_checkin:{ctx.company_id}",
                               trigger="client_action",
                               payload={"confirmed": done, "actor": actor, "month": now.strftime("%Y-%m")})
        except Exception as exc:  # noqa: BLE001
            logger.warning("check-in event not written for %s: %s", ctx.company_id, exc)
    profile = build_profile(ctx, internal=staff, props=props, now=now)
    return {"company_id": ctx.company_id, "confirmed": done, "skipped": skipped, "checkin": profile["checkin"]}


def checkin_card(ctx, *, now: datetime | None = None) -> dict | None:
    """The client dashboard's "Review your property profile" to-do, or None."""
    now = now or wc.utc_now()
    gaps: list = []
    props = read_props(ctx.company_id)
    audit = audit_rows(ctx.company_id, gaps)
    if audit is None:
        return None
    entries: dict = {}
    for row in audit:
        entries.setdefault(row.get("field_key"), []).append(row)
    views = [field_view(f, props, entries.get(f.key, []), internal=False, audit_known=True, now=now)
             for _, fields in visible_sections(False) for f in fields]
    state = checkin_state(views, ctx.company_id, now, _stored_events(ctx, [])["checkins"])
    if not state["due"]:
        return None
    n = len(state["stale_fields"])
    return {"kind": "profile_checkin", "item_id": None, "company_id": ctx.company_id,
            "title": "Review your property profile",
            "subtitle": f"{ctx.name or 'Property'} · {n} field{'s' if n != 1 else ''} not updated in 90+ days",
            "category": None, "stale_count": n, "stale_fields": state["stale_fields"]}


# ── history ──────────────────────────────────────────────────────────────────

def history(ctx, key: str, *, internal: bool) -> dict:
    field = community_brief.FIELDS.get(key)
    if field is None:
        raise wc.WorkspaceError(404, "Unknown field")
    if field.internal and not internal:
        raise wc.WorkspaceError(403, "This field is internal")
    gaps: list = []
    entries = []
    for r in audit_rows(ctx.company_id, gaps) or []:
        if r.get("field_key") != key:
            continue
        editor = str(r.get("edited_by") or "")
        kind = ("reviewed" if REVIEWED_LABEL in str(r.get("field_label") or "")
                else "approved" if "(approved; proposed by" in editor else "edit")
        entries.append({"at": r.get("at"), "by": display_name(editor), "kind": kind,
                        "old_value": r.get("old_value") or None, "new_value": r.get("new_value") or None,
                        "note": None, "_seq": r.get("_seq") or 0})
    for p in proposals(ctx, gaps):
        if p["field_key"] != key or p["status"] == "superseded":
            continue
        entries.append({"at": p.get("proposed_at"), "by": display_name(p.get("proposed_by")), "kind": "proposed",
                        "old_value": p.get("current_value") or None, "new_value": p.get("proposed_value") or None,
                        "note": "Pending RPM review" if p["status"] == "pending" else None,
                        "_seq": p.get("_seq") or 0})
        if p["status"] == "rejected":
            entries.append({"at": p.get("decided_at"), "by": "RPM", "kind": "rejected", "old_value": None,
                            "new_value": p.get("proposed_value") or None, "note": p.get("reason"),
                            "_seq": p.get("_seq") or 0})
    # Same-second entries keep the order this process recorded them in.
    entries.sort(key=lambda e: (e["at"] or "", e["_seq"]), reverse=True)
    for e in entries:
        e.pop("_seq", None)
    return {"company_id": ctx.company_id, "key": key, "label": field.label, "entries": entries,
            "gaps": wc.gaps_for(gaps, internal)}


# ── completeness for lists (Properties rows, Property detail) ────────────────

def _forget_completeness(company_id: str) -> None:
    with _lock:
        for k in [k for k in _completeness if k[0] == str(company_id)]:
            _completeness.pop(k, None)


def completeness_metric(company_id: str, internal: bool) -> dict | None:
    """`{value: pct, source, as_of, weighted}` for one property, cached 30 minutes."""
    key = (str(company_id), bool(internal))
    with _lock:
        hit = _completeness.get(key)
    if hit and time.monotonic() - hit[0] < COMPLETENESS_TTL:
        return hit[1]
    props = read_props(str(company_id))
    views = [{"key": f.key, "label": f.label, "used_in": community_brief.used_in(f.key),
              "value": "x" if filled(f, community_brief.resolve_value(props, f.hs_resolved, f.hs_override)) else None}
             for _, fields in visible_sections(internal) for f in fields]
    metric = wc.metric(completeness(views)["pct"], "community_brief", wc.now_iso(), weighted=True)
    with _lock:
        _completeness[key] = (time.monotonic(), metric)
    return metric
