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


def _description(applied: dict, submitted_by: str, company_id: str,
                 property_uuid: str, extra: dict | None) -> str:
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
) -> tuple[dict, int]:
    """Create a ClickUp task for a portal ticket. Returns (body, http_status)."""
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
    description = _description(applied, submitted_by, company_id, property_uuid, fields)

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
