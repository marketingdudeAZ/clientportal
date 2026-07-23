"""Portal ticket page — per-type forms backed by ClickUp.

See docs/ticket-page-scope.md. The portal is the front door + status window;
ClickUp is where the work happens. Each ticket type renders the *exact* fields
of its real ClickUp intake form — pinned in portal_ticket_specs.py (order,
sections, helper text, required) — while dropdown OPTIONS resolve live from
ClickUp so they never drift. Identity fields the portal already knows are
pre-filled and hidden. On submit we also refresh the property profile with any
descriptive fields the ticket carries (the closed loop; see
portal_ticket_profile.py), surfacing conflicts for a human to resolve.

Everything degrades gracefully: with no ClickUp key, no configured list ids, or
no BigQuery, callers return empty results or a clear error rather than 500.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

import clickup_client
from config import (
    CLICKUP_WORKSPACE_ID,
    PORTAL_TICKET_STATUS_MAP,
    PORTAL_TICKET_TYPES,
)
from portal_ticket_specs import AM_BY_REGION, FORM_SPECS, PREFILL

logger = logging.getLogger(__name__)

_MAPPING_TABLE = "portal_tickets"

# ClickUp field type → portal input kind.
_INPUT_KIND = {
    "short_text": "text", "text": "textarea", "drop_down": "select",
    "labels": "multiselect", "number": "number", "currency": "currency",
    "date": "date", "checkbox": "checkbox", "email": "email", "url": "url",
    "phone": "phone", "automatic_progress": "text", "users": "text",
    "attachment": "text",
}

# When a form label doesn't equal the ClickUp field name, map it here (lowercased
# form label → lowercased ClickUp field name) so prefill/options still resolve.
_FIELD_ALIASES = {
    "digital region": "region",
    "market": "market",
    "property code": "new property code",
    "occupancy": "occupancy",
}


# ── ticket-type registry ─────────────────────────────────────────────────────

def _registry(key: str) -> dict | None:
    for t in PORTAL_TICKET_TYPES:
        if t["key"] == key:
            return t
    return None


def _list_id_for(key: str) -> str:
    t = _registry(key)
    return (os.getenv(t.get("list_env", ""), "") or "").strip() if t else ""


def configured_types(include_internal: bool = False) -> list[dict[str, Any]]:
    """Built ticket types (in portal_ticket_specs) that have a list id set."""
    out: list[dict[str, Any]] = []
    for key, spec in FORM_SPECS.items():
        t = _registry(key)
        if not t or not _list_id_for(key):
            continue
        if t.get("audience") != "client" and not include_internal:
            continue
        out.append({"key": key, "label": spec.get("title") or t["label"],
                    "audience": t.get("audience", "client")})
    return out


# ── field resolution (spec pinned + live options) ────────────────────────────

def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").strip().lower()).strip("_")[:60]


def _clean_label(name: str) -> str:
    return (name or "").rstrip("*").strip()


def _live_index(list_id: str) -> dict[str, dict]:
    return {(d.get("name") or "").strip().lower(): d
            for d in clickup_client.get_list_fields(list_id)}


def _match_live(name: str, index: dict[str, dict]) -> dict | None:
    key = (name or "").strip().lower()
    for cand in (key, key.rstrip("*").strip(), _FIELD_ALIASES.get(key, "")):
        if cand and cand in index:
            return index[cand]
    # loose contains, longest-name first to prefer the most specific field
    for lname in sorted(index, key=len, reverse=True):
        if key and (key in lname or lname in key):
            return index[lname]
    return None


def _options(live_def: dict | None) -> list[dict]:
    if not live_def or live_def.get("type") not in ("drop_down", "labels"):
        return []
    out = []
    for o in ((live_def.get("type_config") or {}).get("options") or []):
        out.append({"id": o.get("id"), "label": o.get("name") or o.get("label") or ""})
    return out


def _resolve_fields(type_key: str) -> list[dict]:
    """Each spec field augmented with its live ClickUp id/type/options + a stable
    `key` (the field id when matched, else a slug). Ordered as the form is."""
    spec = FORM_SPECS.get(type_key) or {}
    index = _live_index(_list_id_for(type_key))
    resolved = []
    for entry in spec.get("fields", []):
        live = _match_live(entry["name"], index)
        field_id = live.get("id") if live else None
        input_kind = (_INPUT_KIND.get(live.get("type"), "text") if live
                      else (entry.get("type") or "text"))
        resolved.append({
            **entry,
            "key": field_id or _slug(entry["name"]),
            "field_id": field_id,
            "live": live,
            "input": input_kind,
            "options": _options(live),
            "label": _clean_label(entry["name"]),
        })
    return resolved


def build_form(type_key: str) -> dict | None:
    """The client-facing form payload for one ticket type."""
    spec = FORM_SPECS.get(type_key)
    if not spec:
        return None
    resolved = _resolve_fields(type_key)
    by_name = {f["name"].strip().lower(): f for f in resolved}
    client_fields = []
    for f in resolved:
        if f["role"] != "client":
            continue
        show_if = None
        if f.get("show_if"):
            ctrl_name, values = f["show_if"]
            ctrl = by_name.get(ctrl_name.strip().lower())
            if ctrl:
                show_if = {"field": ctrl["key"], "values": values}
        client_fields.append({
            "key": f["key"], "label": f["label"], "input": f["input"],
            "required": f["required"], "helper": f["helper"],
            "section": f["section"], "options": f["options"], "show_if": show_if,
        })
    return {
        "key": type_key,
        "label": spec.get("title", type_key),
        "intro": spec.get("intro", ""),
        "updates_profile": bool(spec.get("updates_profile")),
        "fields": client_fields,
    }


def types_with_schema(include_internal: bool = False) -> list[dict[str, Any]]:
    out = []
    for t in configured_types(include_internal):
        form = build_form(t["key"])
        if form:
            form["audience"] = t["audience"]
            out.append(form)
    return out


# ── status mapping ───────────────────────────────────────────────────────────

def client_status(raw: str) -> str:
    s = (raw or "").strip().lower()
    if s in PORTAL_TICKET_STATUS_MAP:
        return PORTAL_TICKET_STATUS_MAP[s]
    return (raw or "").strip().title() or "Open"


# ── prefill (identity the portal already knows) ──────────────────────────────

def _prefill_values(type_key: str, company_id: str, submitter: str = "",
                    property_uuid: str = "") -> tuple[dict[str, str], dict]:
    """Return ({ClickUp field name: value}, raw_company_props) for the type's
    pre-filled identity fields. Best-effort."""
    spec = FORM_SPECS.get(type_key) or {}
    names = [f["name"] for f in spec.get("fields", []) if f["role"] == "prefill"]
    want = {n: PREFILL[n]["src"] for n in names if n in PREFILL}
    hs_props = sorted({s for s in want.values()
                       if not s.startswith("derive:") and s not in ("submitter", "uuid")}
                      | {"name", "market", "uuid"})
    data: dict[str, Any] = {}
    if company_id:
        try:
            import hubspot_client
            data = hubspot_client.get_company(company_id, hs_props) or {}
        except Exception as e:  # noqa: BLE001 — prefill never blocks a request
            logger.warning("portal ticket prefill fetch failed for %s: %s", company_id, e)
    region = (data.get("market") or "").strip().lower()
    out: dict[str, str] = {}
    for name, src in want.items():
        if src == "submitter":
            val = submitter
        elif src == "uuid":
            val = property_uuid or data.get("uuid") or ""
        elif src == "derive:am":
            val = AM_BY_REGION.get(region, "")
        else:
            val = data.get(src) or ""
        if val:
            out[name] = str(val)
    return out, data


# ── custom-field payload ─────────────────────────────────────────────────────

def _coerce(field_def: dict, value: Any) -> Any:
    if value in (None, ""):
        return None
    ftype = field_def.get("type")
    options = (field_def.get("type_config") or {}).get("options") or []
    if ftype == "drop_down":
        needle = str(value).strip().lower()
        for o in options:
            if (str(o.get("id")) == str(value) or str(o.get("orderindex")) == str(value)
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
        return value.strip().lower() in ("1", "true", "yes", "on", "checked") if isinstance(value, str) else bool(value)
    return str(value)


def _description(extra_lines: list[str], submitted_by: str,
                 company_id: str, property_uuid: str) -> str:
    lines = ["Submitted via the RPM client portal."]
    if submitted_by:
        lines.append(f"Requested by: {submitted_by}")
    ident = [p for p in (f"uuid={property_uuid}" if property_uuid else "",
                         f"hubspot_company={company_id}" if company_id else "") if p]
    if ident:
        lines.append("Property: " + " · ".join(ident))
    lines.extend(extra_lines)
    return "\n".join(lines)


# ── create + closed loop ─────────────────────────────────────────────────────

def create_ticket(
    company_id: str,
    type_key: str,
    *,
    subject: str,
    fields: dict | None = None,
    submitted_by: str = "",
    property_uuid: str = "",
) -> tuple[dict, int]:
    """Create a ClickUp task for a portal ticket, then refresh the property
    profile with any descriptive fields. Returns (body, http_status)."""
    spec = FORM_SPECS.get(type_key)
    if not spec:
        return {"ok": False, "error": "Unknown ticket type."}, 400
    if not _list_id_for(type_key):
        return {"ok": False, "error": "This ticket type isn't available yet."}, 503
    if not clickup_client.CLICKUP_API_KEY:
        return {"ok": False, "error": "Ticketing is temporarily unavailable."}, 503

    inputs = fields or {}
    resolved = _resolve_fields(type_key)
    prefill, _ = _prefill_values(type_key, company_id, submitted_by, property_uuid)

    # Build ClickUp custom_fields: identity prefill (by name) + client inputs (by key).
    index = _live_index(_list_id_for(type_key))
    by_name = {(d.get("name") or "").strip().lower(): d for d in index.values()}
    merged: dict[str, Any] = {}
    extra_lines: list[str] = []
    profile_values: dict[str, str] = {}

    for name, val in prefill.items():
        d = _match_live(name, index)
        if d and (cu := _coerce(d, val)) is not None:
            merged[d["id"]] = cu

    for f in resolved:
        if f["role"] != "client":
            continue
        val = inputs.get(f["key"])
        if val in (None, "", []):
            continue
        if f.get("field_id") and f.get("live"):
            cu = _coerce(f["live"], val)
            if cu is not None:
                merged[f["field_id"]] = cu
        else:
            extra_lines.append(f"{f['label']}: {val}")   # unmatched → into description
        if f.get("profile_key"):
            profile_values[f["profile_key"]] = val if isinstance(val, str) else str(val)

    subject = (subject or "").strip() or spec.get("title", type_key)
    description = _description(extra_lines, submitted_by, company_id, property_uuid)
    cf_payload = [{"id": fid, "value": v} for fid, v in merged.items()]

    task = clickup_client.create_task(
        _list_id_for(type_key), subject[:255], description=description,
        custom_fields=cf_payload or None, tags=["portal"],
    )
    if not task:
        return {"ok": False, "error": "Couldn't create the ticket. Please try again."}, 502

    _record_mapping(task.get("id"), company_id, property_uuid, type_key, submitted_by)

    # Closed loop — refresh the property profile (best-effort; never fails the ticket).
    profile = {"applied": [], "conflicts": []}
    if spec.get("updates_profile") and profile_values and company_id:
        try:
            import portal_ticket_profile
            profile = portal_ticket_profile.apply_updates(
                company_id, profile_values, task_id=task.get("id"))
        except Exception as e:  # noqa: BLE001
            logger.warning("closed-loop profile update failed for %s: %s", company_id, e)

    return {"ok": True, "ticket": _shape_task(task, type_key),
            "profile": profile}, 201


def list_tickets(company_id: str, *, property_uuid: str = "", limit: int = 50) -> list[dict]:
    refs = _read_mappings(company_id, property_uuid, limit)
    out: list[dict] = []
    for ref in refs:
        task = clickup_client.get_task(ref.get("task_id"))
        if task:
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
    spec = FORM_SPECS.get(type_key or "") or {}
    return {
        "id": task.get("id"),
        "type": type_key or "",
        "type_label": spec.get("title", ""),
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
    lists = clickup_client.discover_workspace_lists(CLICKUP_WORKSPACE_ID)
    by_name = {(l.get("name") or "").strip().lower(): l for l in lists if l.get("id")}
    matched, unmatched, env_lines = [], [], []
    for t in PORTAL_TICKET_TYPES:
        candidates = [t["label"], *(t.get("aliases") or [])]
        found = None
        for c in candidates:
            if c.strip().lower() in by_name:
                found = by_name[c.strip().lower()]
                break
        if not found:
            for name, lst in by_name.items():
                if any(c.strip().lower() in name or name in c.strip().lower() for c in candidates):
                    found = lst
                    break
        if found:
            matched.append({"key": t["key"], "label": t["label"], "list_id": found["id"],
                            "list_name": found["name"], "env": t["list_env"]})
            env_lines.append(f'{t["list_env"]}={found["id"]}')
        else:
            unmatched.append({"key": t["key"], "label": t["label"], "env": t["list_env"]})
    return {"workspace_id": CLICKUP_WORKSPACE_ID, "lists_found": len(lists),
            "matched": matched, "unmatched_types": unmatched, "env_block": "\n".join(env_lines)}
