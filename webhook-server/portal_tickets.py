"""Portal ticket page — per-type forms backed by ClickUp.

See docs/ticket-page-scope.md. The model: the **portal is the front door and
status window; ClickUp is where the work happens** (internal). A requester
picks a ticket type, fills a form whose fields come LIVE from that type's
ClickUp list (so a field your team adds in ClickUp appears in the portal with
no redeploy), and we create the task in that list — pre-filling the property
fields the portal already knows (Property URL, Market, Property Code, AM, uuid)
so nobody re-types them. We record `task_id ↔ company_id` so "what's open for
this property" is an exact lookup, not fuzzy matching.

Everything degrades gracefully: with no ClickUp key, no configured list ids, or
no BigQuery, the callers return empty results or a clear error rather than 500.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

import clickup_client
import config  # attribute access for the brief-gap knobs — see _cap()/_gaps_enabled()
from config import (
    CLICKUP_WORKSPACE_ID,
    PORTAL_TICKET_PREFILL_FIELDS,
    PORTAL_TICKET_PREFILL_SOURCES,
    PORTAL_TICKET_STATUS_MAP,
    PORTAL_TICKET_TYPES,
)

logger = logging.getLogger(__name__)

# Append-only BigQuery table holding the task↔company mapping (one row per
# portal-created ticket). See migrations/ for the schema.
_MAPPING_TABLE = "portal_tickets"

# ClickUp field type → the input kind the portal form renders.
_INPUT_KIND = {
    "short_text": "text",
    "text": "textarea",
    "drop_down": "select",
    "labels": "multiselect",
    "number": "number",
    "currency": "currency",
    "date": "date",
    "checkbox": "checkbox",
    "email": "email",
    "url": "url",
    "phone": "phone",
}

_PREFILL_LOWER = {f.strip().lower() for f in PORTAL_TICKET_PREFILL_FIELDS}


# ── ticket-type registry ─────────────────────────────────────────────────────

def _list_id_for(t: dict) -> str:
    """The configured ClickUp list id for a ticket type, or '' if unset."""
    return (os.getenv(t.get("list_env", ""), "") or "").strip()


def configured_types(include_internal: bool = False) -> list[dict[str, Any]]:
    """Ticket types that have a list id configured, in registry order.

    Client-facing types only unless `include_internal`. A type whose list id
    is unset is silently omitted, so the picker lights up type-by-type as IDs
    are filled in.
    """
    out: list[dict[str, Any]] = []
    for t in PORTAL_TICKET_TYPES:
        list_id = _list_id_for(t)
        if not list_id:
            continue
        if t.get("audience") != "client" and not include_internal:
            continue
        out.append({
            "key": t["key"],
            "label": t["label"],
            "audience": t.get("audience", "client"),
            "list_id": list_id,
        })
    return out


def _type_by_key(key: str) -> dict | None:
    for t in PORTAL_TICKET_TYPES:
        if t["key"] == key:
            return t
    return None


# ── form schema (rendered live from ClickUp field defs) ──────────────────────

def _is_prefill(field_name: str | None) -> bool:
    return (field_name or "").strip().lower() in _PREFILL_LOWER


def _shape_field(f: dict) -> dict[str, Any]:
    """One ClickUp custom-field definition → a portal form-field schema."""
    ftype = f.get("type")
    type_config = f.get("type_config") or {}
    options: list[dict] = []
    if ftype in ("drop_down", "labels"):
        for o in (type_config.get("options") or []):
            options.append({
                "id": o.get("id"),
                "label": o.get("name") or o.get("label") or "",
            })
    return {
        "id": f.get("id"),
        "name": f.get("name"),
        "input": _INPUT_KIND.get(ftype, "text"),
        "clickup_type": ftype,
        "required": bool(f.get("required")),
        "options": options,
    }


def form_schema(list_id: str) -> list[dict[str, Any]]:
    """Client-facing form fields for a list — ClickUp fields minus the ones we
    pre-fill from the property record."""
    return [
        _shape_field(f)
        for f in clickup_client.get_list_fields(list_id)
        if not _is_prefill(f.get("name"))
    ]


def types_with_schema(include_internal: bool = False) -> list[dict[str, Any]]:
    """The picker payload: every available type with its live form schema."""
    out = []
    for t in configured_types(include_internal):
        out.append({
            "key": t["key"],
            "label": t["label"],
            "audience": t["audience"],
            "fields": form_schema(t["list_id"]),
        })
    return out


# ── status mapping (internal ClickUp → client-safe) ──────────────────────────

def client_status(raw: str) -> str:
    """Map an internal ClickUp status to a clean client-facing label. Unknown
    statuses fall through to a title-cased form rather than leaking a slug."""
    s = (raw or "").strip().lower()
    if s in PORTAL_TICKET_STATUS_MAP:
        return PORTAL_TICKET_STATUS_MAP[s]
    return (raw or "").strip().title() or "Open"


# ── prefill (property fields the portal already knows) ───────────────────────

def _prefill_values(company_id: str, property_uuid: str = "") -> dict[str, str]:
    """{ClickUp field name: value} for the prefilled fields, from HubSpot.

    Best-effort — a HubSpot fetch failure or a missing property just yields an
    empty/partial map; the requester fills whatever's blank.
    """
    want = {
        cu_name: PORTAL_TICKET_PREFILL_SOURCES[cu_name]
        for cu_name in PORTAL_TICKET_PREFILL_FIELDS
        if cu_name in PORTAL_TICKET_PREFILL_SOURCES
    }
    if not company_id or not want:
        return {name: property_uuid for name, src in want.items() if src == "uuid" and property_uuid}
    props = sorted(set(want.values()) | {"name", "uuid"})
    data: dict[str, Any] = {}
    try:
        import hubspot_client
        data = hubspot_client.get_company(company_id, props) or {}
    except Exception as e:  # noqa: BLE001 — prefill is never load-bearing
        logger.warning("portal ticket prefill fetch failed for %s: %s", company_id, e)
    out: dict[str, str] = {}
    for cu_name, src in want.items():
        val = data.get(src)
        if not val and src == "uuid":
            val = property_uuid
        if val:
            out[cu_name] = str(val)
    return out


# ── brief gaps (docs/ticket-brief-gaps-scope.md) ─────────────────────────────
#
# The ticket form is the highest-intent moment we get with property marketing.
# Spend it on the gaps only: read the community brief, show back what we
# already know, and ask for at most PORTAL_TICKET_BRIEF_MAX_ASK fields that are
# still empty. Nothing here is ever required, and nothing here can fail a
# ticket — every path degrades to "no questions".

# community_brief field type → the input kind the portal form renders. Same
# vocabulary as _INPUT_KIND above so the UI has one renderer to reason about.
_BRIEF_INPUT = {
    "text": "text",
    "textarea": "textarea",
    "dropdown": "select",
    "multiselect": "multiselect",
}

_PREVIEW_MAX = 120
_HINT_MAX = 120


def _cap() -> int:
    """The live question cap. Read at call time so env/tests can move it."""
    return max(1, int(getattr(config, "PORTAL_TICKET_BRIEF_MAX_ASK", 5)))


def _gaps_enabled() -> bool:
    """Feature flag, read at call time — going live is a config flip."""
    return bool(getattr(config, "PORTAL_TICKET_BRIEF_GAPS_ENABLED", False))


def _mapped_keys(type_key: str) -> list[str]:
    return list(getattr(config, "PORTAL_TICKET_BRIEF_FIELDS", {}).get(type_key) or [])


def _is_askable(field) -> bool:
    """Can this brief field be answered in one inline form row?

    Needs somewhere to write (hs_override), and must not be a structured table
    (floor plans / tracking / documents) or a machine-owned readonly field.
    Client-safety (internal=True) is enforced by the mapping tests, not here —
    an internal field must never be in a client-facing mapping in the first place.
    """
    import community_brief as cb
    if not getattr(field, "hs_override", ""):
        return False
    if field.type in cb.TABLE_TYPES:
        return False
    return field.type in _BRIEF_INPUT


def _truncate(text: str, limit: int) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= limit else s[:limit].rstrip() + "…"


def _preview(value: str) -> str:
    """One-line, length-capped rendering of a stored brief value."""
    return _truncate(str(value or "").replace("\n", " · "), _PREVIEW_MAX)


def _ask_row(field) -> dict[str, Any]:
    hint = _truncate(field.hint, _HINT_MAX)
    placeholder = ""
    if field.type == "textarea" and "one per line" in (field.hint or "").lower():
        placeholder = "One per line"
    return {
        "key": field.key,
        "label": field.label,
        "input": _BRIEF_INPUT.get(field.type, "text"),
        "hint": hint,
        "options": list(field.options or []),
        "placeholder": placeholder,
    }


def _empty_gaps(company_id: str, type_key: str, mapped_count: int = 0) -> dict[str, Any]:
    return {
        "ticket_type": type_key,
        "company_id": company_id,
        "property_name": "",
        "ask": [],
        "known": [],
        "deferred": [],
        "counts": {"mapped": mapped_count, "known": 0, "asked": 0, "deferred": 0},
    }


def brief_gaps(company_id: str, type_key: str) -> dict[str, Any]:
    """What this ticket type needs, split into ask / known / deferred.

    `ask` holds at most _cap() fields that are still empty on the property
    record, in mapping (priority) order. `known` is what we already have, so
    the requester is never asked twice. `deferred` is mapped-but-not-asked,
    tagged over_cap or not_askable.

    Emptiness is decided by community_brief.resolve_value — the same
    override > resolved > empty rule the portal display and the Fluency feed
    use, so a whitespace-only override counts as a gap here too.

    Never raises. If the profile can't be read we return NO questions with
    degraded=True rather than every question: asking for what we already have
    is a worse failure than asking for nothing.
    """
    mapped = _mapped_keys(type_key)
    if not mapped:
        return _empty_gaps(company_id, type_key)

    import community_brief as cb

    try:
        props = cb.load_company_state(company_id) or {}
    except Exception as e:  # noqa: BLE001 — the ticket form must still render
        logger.warning("brief gaps: profile read failed for %s: %s", company_id, e)
        props = {}
    if not props:
        out = _empty_gaps(company_id, type_key, mapped_count=len(mapped))
        out["degraded"] = True
        return out

    cap = _cap()
    ask: list[dict] = []
    known: list[dict] = []
    deferred: list[dict] = []

    for key in mapped:
        field = cb.FIELDS.get(key)
        if not field or not _is_askable(field):
            label = getattr(field, "label", "") or key
            deferred.append({"key": key, "label": label, "reason": "not_askable"})
            continue
        value = cb.resolve_value(props, field.hs_resolved, field.hs_override)
        if value:
            known.append({
                "key": key,
                "label": field.label,
                "preview": _preview(value),
                "source": "override" if cb._nonblank(props.get(field.hs_override)) else "resolved",
            })
        elif len(ask) < cap:
            ask.append(_ask_row(field))
        else:
            deferred.append({"key": key, "label": field.label, "reason": "over_cap"})

    return {
        "ticket_type": type_key,
        "company_id": company_id,
        "property_name": str(props.get("name") or ""),
        "ask": ask,
        "known": known,
        "deferred": deferred,
        "counts": {
            "mapped": len(mapped),
            "known": len(known),
            "asked": len(ask),
            "deferred": len(deferred),
        },
    }


# ── custom-field payload building ────────────────────────────────────────────

def _coerce(field_def: dict, value: Any) -> Any:
    """Coerce a form value into the shape ClickUp's API expects for the field.

    drop_down/labels resolve option *labels* back to option ids; number/currency
    become floats; checkbox becomes bool. Returns None to skip the field.
    """
    if value in (None, ""):
        return None
    ftype = field_def.get("type")
    options = (field_def.get("type_config") or {}).get("options") or []
    if ftype == "drop_down":
        needle = str(value).strip().lower()
        for o in options:
            if (str(o.get("id")) == str(value)
                    or str(o.get("orderindex")) == str(value)
                    or (o.get("name") or "").strip().lower() == needle):
                return o.get("id") if o.get("id") is not None else o.get("orderindex")
        return None
    if ftype == "labels":
        vals = value if isinstance(value, list) else [value]
        ids = []
        for v in vals:
            needle = str(v).strip().lower()
            for o in options:
                if (str(o.get("id")) == str(v)
                        or (o.get("label") or o.get("name") or "").strip().lower() == needle):
                    ids.append(o.get("id"))
                    break
        return ids or None
    if ftype in ("number", "currency"):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    if ftype == "checkbox":
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on", "checked")
        return bool(value)
    return str(value)


def _build_custom_fields(
    list_id: str, inputs: dict | None, prefill: dict | None
) -> tuple[list[dict], dict]:
    """Build ClickUp's `[{id, value}]` custom-fields payload.

    `inputs` keys may be field ids OR names (the dynamic form sends ids);
    `prefill` keys are ClickUp field names. Inputs win over prefill on overlap.
    Returns (payload, applied) where `applied` is {field name: value} used for
    the human-readable identity stamp in the task description.
    """
    defs = clickup_client.get_list_fields(list_id)
    by_id = {d.get("id"): d for d in defs}
    by_name = {(d.get("name") or "").strip().lower(): d for d in defs}
    merged: dict[str, Any] = {}   # field_id -> coerced value
    applied: dict[str, Any] = {}  # field name -> original value

    def resolve(key: Any, value: Any) -> None:
        d = by_id.get(key) or by_name.get(str(key).strip().lower())
        if not d:
            return
        cu_val = _coerce(d, value)
        if cu_val is None:
            return
        merged[d.get("id")] = cu_val
        applied[d.get("name")] = value

    for name, value in (prefill or {}).items():
        resolve(name, value)
    for key, value in (inputs or {}).items():
        resolve(key, value)

    payload = [{"id": fid, "value": v} for fid, v in merged.items()]
    return payload, applied


# ── brief write-back ─────────────────────────────────────────────────────────

_ECHO_MAX = 500


def _apply_brief_answers(company_id: str, type_key: str, answers: dict | None,
                         submitted_by: str = "") -> dict[str, Any]:
    """Write gap answers onto the property profile. Bounded, and never raises.

    Returns {"saved": [labels], "skipped": [labels], "failed": [{label, value, error}]}
    for the provenance stamp on the ClickUp task.

    Four gates on what can be written, so a crafted POST can't reach an
    arbitrary property:
      1. the key must be in THIS ticket type's mapping (askable, non-internal);
      2. community_brief.write_field only ever PATCHes field.hs_override — uuid
         is not a brief field and has no override, so R1 holds by construction;
      3. anti-clobber — re-read the profile and skip anything that got filled
         in while the ticket form sat open (a human edit outranks a stale form);
      4. blank / whitespace-only answers are dropped before any write.

    Bounded by the same cap as the ask, and every write is individually
    wrapped: a HubSpot outage produces `failed` entries, never an exception,
    so it can degrade profile capture but never ticket creation.
    """
    result: dict[str, Any] = {"saved": [], "skipped": [], "failed": []}
    if not company_id or not answers or not isinstance(answers, dict):
        return result
    if not _gaps_enabled():
        return result

    import community_brief as cb

    mapped = _mapped_keys(type_key)
    if not mapped:
        return result

    # Gates 1 + 4, in mapping order (so the cap keeps the same priority the
    # form used) — never trust the order or contents of the posted object.
    todo = []
    for key in mapped:
        if key not in answers:
            continue
        field = cb.FIELDS.get(key)
        if not field or not _is_askable(field):
            continue
        raw = answers[key]
        value = ";".join(str(v) for v in raw if str(v).strip()) if isinstance(raw, list) else str(raw or "")
        if not value.strip():
            continue
        todo.append((key, field, value))
        if len(todo) >= _cap():
            break
    if not todo:
        return result

    # Gate 3 — one fresh read for the anti-clobber check.
    try:
        props = cb.load_company_state(company_id) or {}
    except Exception as e:  # noqa: BLE001
        logger.warning("brief write-back: profile re-read failed for %s: %s", company_id, e)
        props = {}

    for key, field, value in todo:
        try:
            if props and cb.resolve_value(props, field.hs_resolved, field.hs_override):
                result["skipped"].append(field.label)
                continue
            ok, detail = cb.write_field(company_id, key, value, edited_by=submitted_by)
            if ok:
                result["saved"].append(field.label)
            else:
                result["failed"].append({"label": field.label, "value": value, "error": str(detail)})
        except Exception as e:  # noqa: BLE001 — one bad field can't take the rest down
            logger.warning("brief write-back failed for %s/%s: %s", company_id, key, e)
            result["failed"].append({"label": field.label, "value": value, "error": str(e)[:120]})

    if result["failed"]:
        logger.warning("brief write-back: %s of %s fields failed for %s",
                       len(result["failed"]), len(todo), company_id)
    return result


def _brief_lines(brief: dict | None) -> list[str]:
    """The provenance block for the ClickUp description.

    Failures echo the submitted values underneath, so nothing the requester
    typed is lost even when every HubSpot write failed — the assignee can put
    them on the profile by hand.
    """
    if not brief:
        return []
    lines: list[str] = []
    if brief.get("saved"):
        lines.append("Property profile updated from this request: " + ", ".join(brief["saved"]))
    if brief.get("skipped"):
        lines.append("Skipped (filled in on the profile while this form was open): "
                     + ", ".join(brief["skipped"]))
    if brief.get("failed"):
        lines.append("⚠ Could NOT save to the property profile — please update manually: "
                     + ", ".join(f["label"] for f in brief["failed"]))
        for f in brief["failed"]:
            lines.append(f"   {f['label']}: {_truncate(str(f['value']).replace(chr(10), ' · '), _ECHO_MAX)}")
    return lines


def _description(applied: dict, submitted_by: str, company_id: str,
                 property_uuid: str, extra: dict | None,
                 brief: dict | None = None) -> str:
    """A provenance + identity stamp so the recap automation can match the task
    back to the property with confidence."""
    lines = ["Submitted via the RPM client portal."]
    if submitted_by:
        lines.append(f"Requested by: {submitted_by}")
    ident = []
    if property_uuid:
        ident.append(f"uuid={property_uuid}")
    if company_id:
        ident.append(f"hubspot_company={company_id}")
    if ident:
        lines.append("Property: " + " · ".join(ident))
    # Surface any free-text the form collected that isn't a mapped custom field.
    for k, v in (extra or {}).items():
        if k in ("subject", "name") or not v:
            continue
        if k not in applied:
            lines.append(f"{k}: {v}")
    brief_lines = _brief_lines(brief)
    if brief_lines:
        lines.append("")
        lines.extend(brief_lines)
    return "\n".join(lines)


# ── create + track ───────────────────────────────────────────────────────────

def create_ticket(
    company_id: str,
    type_key: str,
    *,
    subject: str,
    fields: dict | None = None,
    submitted_by: str = "",
    property_uuid: str = "",
    brief_answers: dict | None = None,
) -> tuple[dict, int]:
    """Create a ClickUp task for a portal ticket. Returns (body, http_status).

    `brief_answers` are the optional property-profile gap answers from the
    ticket form ({brief field key: value}). They are written to HubSpot BEFORE
    the task is created, because the description — the only place the internal
    team sees what was captured — has to name what saved and what didn't.
    That write is bounded and cannot raise, so a HubSpot outage degrades
    profile capture without ever costing us the ticket.
    """
    t = _type_by_key(type_key)
    if not t:
        return {"ok": False, "error": "Unknown ticket type."}, 400
    list_id = _list_id_for(t)
    if not list_id:
        return {"ok": False, "error": "This ticket type isn't available yet."}, 503
    if not clickup_client.CLICKUP_API_KEY:
        return {"ok": False, "error": "Ticketing is temporarily unavailable."}, 503

    subject = (subject or "").strip() or t["label"]
    prefill = _prefill_values(company_id, property_uuid)
    cf_payload, applied = _build_custom_fields(list_id, fields, prefill)
    brief = _apply_brief_answers(company_id, type_key, brief_answers, submitted_by)
    description = _description(applied, submitted_by, company_id, property_uuid, fields, brief)

    task = clickup_client.create_task(
        list_id,
        subject[:255],
        description=description,
        custom_fields=cf_payload or None,
        tags=["portal"],
    )
    if not task:
        return {"ok": False, "error": "Couldn't create the ticket. Please try again."}, 502

    _record_mapping(task.get("id"), company_id, property_uuid, type_key, submitted_by)
    return {"ok": True, "ticket": _shape_task(task, type_key)}, 201


def list_tickets(company_id: str, *, property_uuid: str = "", limit: int = 50) -> list[dict]:
    """Open + recent tickets for a property, newest first. Reads the stored
    mapping, then fetches live status from ClickUp."""
    refs = _read_mappings(company_id, property_uuid, limit)
    out: list[dict] = []
    for ref in refs:
        task = clickup_client.get_task(ref.get("task_id"))
        if not task:
            continue
        out.append(_shape_task(task, ref.get("ticket_type")))
    out.sort(key=lambda x: x.get("created_ts") or 0, reverse=True)
    return out


def _age_days(created_ms: Any) -> int | None:
    try:
        created = datetime.fromtimestamp(int(created_ms) / 1000, tz=timezone.utc)
    except (TypeError, ValueError):
        return None
    return max(0, (datetime.now(timezone.utc) - created).days)


def _shape_task(task: dict, type_key: str | None = None) -> dict[str, Any]:
    status = ((task.get("status") or {}).get("status")) or ""
    created = task.get("date_created")
    t = _type_by_key(type_key) if type_key else None
    return {
        "id": task.get("id"),
        "type": type_key or "",
        "type_label": (t or {}).get("label", ""),
        "subject": task.get("name"),
        "status": client_status(status),
        "raw_status": status,
        "created_ts": int(created) if created else None,
        "age_days": _age_days(created),
        "url": task.get("url"),
    }


# ── mapping store (BigQuery, append-only) ────────────────────────────────────

def _record_mapping(task_id, company_id, property_uuid, type_key, submitted_by) -> None:
    if not task_id:
        return
    try:
        import bigquery_client
        if not bigquery_client.is_bigquery_configured():
            return
        bigquery_client.insert_rows(_MAPPING_TABLE, [{
            "task_id": str(task_id),
            "company_id": str(company_id or ""),
            "property_uuid": str(property_uuid or ""),
            "ticket_type": type_key or "",
            "submitted_by": submitted_by or "",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }])
    except Exception as e:  # noqa: BLE001 — a mapping-write failure must not fail the ticket
        logger.warning("portal ticket mapping write failed for %s: %s", task_id, e)


def _read_mappings(company_id: str, property_uuid: str, limit: int) -> list[dict]:
    try:
        import bigquery_client
        if not bigquery_client.is_bigquery_configured():
            return []
        from google.cloud import bigquery
        from config import BIGQUERY_PROJECT_ID
        dataset = bigquery_client._dataset()
        sql = f"""
            SELECT task_id, ticket_type, submitted_by, created_at
            FROM `{BIGQUERY_PROJECT_ID}.{dataset}.{_MAPPING_TABLE}`
            WHERE company_id = @cid OR (@uuid != '' AND property_uuid = @uuid)
            ORDER BY created_at DESC
            LIMIT @lim
        """
        params = [
            bigquery.ScalarQueryParameter("cid", "STRING", str(company_id or "")),
            bigquery.ScalarQueryParameter("uuid", "STRING", str(property_uuid or "")),
            bigquery.ScalarQueryParameter("lim", "INT64", int(limit)),
        ]
        return bigquery_client.query(sql, params)
    except Exception as e:  # noqa: BLE001 — tracking is best-effort
        logger.warning("portal ticket mapping read failed for %s: %s", company_id, e)
        return []


# ── admin: discover the ClickUp list ids by name ─────────────────────────────

def discover_list_ids() -> dict[str, Any]:
    """Walk the workspace and match ClickUp lists to ticket types by name/alias.

    Returns a paste-ready `env_block` plus per-type match detail, so the real
    numeric list ids can be pulled without hand-decoding ClickUp form URLs.
    """
    lists = clickup_client.discover_workspace_lists(CLICKUP_WORKSPACE_ID)
    by_name = {(l.get("name") or "").strip().lower(): l for l in lists if l.get("id")}

    matched, unmatched, env_lines = [], [], []
    for t in PORTAL_TICKET_TYPES:
        candidates = [t["label"], *(t.get("aliases") or [])]
        found = None
        for c in candidates:
            key = c.strip().lower()
            if key in by_name:
                found = by_name[key]
                break
        if not found:  # loose contains-match as a fallback
            for name, lst in by_name.items():
                if any(c.strip().lower() in name or name in c.strip().lower() for c in candidates):
                    found = lst
                    break
        if found:
            matched.append({"key": t["key"], "label": t["label"],
                            "list_id": found["id"], "list_name": found["name"],
                            "env": t["list_env"]})
            env_lines.append(f'{t["list_env"]}={found["id"]}')
        else:
            unmatched.append({"key": t["key"], "label": t["label"], "env": t["list_env"]})

    return {
        "workspace_id": CLICKUP_WORKSPACE_ID,
        "lists_found": len(lists),
        "matched": matched,
        "unmatched_types": unmatched,
        "env_block": "\n".join(env_lines),
    }
