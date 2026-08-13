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
import time
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


def form_schema(list_id: str, prefill: dict | None = None) -> "list[dict[str, Any]] | None":
    """Client-facing form fields for a list — ClickUp fields minus the ones we
    can actually pre-fill from the property record.

    `prefill` is optional: with none supplied, no field is hidden, which is the
    safe direction (the requester sees a field we would have filled, rather than
    a field nobody fills).

    None means ClickUp would not tell us the schema. That must propagate all the
    way to `available: false`; collapsing it into an empty form lets a requester
    submit into a task missing every required field.
    """
    defs = clickup_client.get_list_fields(list_id)
    if defs is None:
        return None
    # `resolved` is the prefill map we WILL actually stamp on the task. A
    # prefill field is hidden only when we can genuinely fill it.
    #
    # This used to strip every prefill-NAMED field unconditionally, before
    # knowing whether prefill would resolve anything — and _prefill_values
    # swallows failures by design. So a mapping that broke (Account Manager
    # pointed at a HubSpot property that does not exist) left the field neither
    # on the form NOR on the task: permanently blank on every ticket reaching
    # the services team, with no error. That is strictly worse than the ClickUp
    # form this replaces, and the failure is generic — it fires for any prefill
    # mapping that breaks later.
    resolved = {k.strip().lower() for k, v in (prefill or {}).items() if v}
    return [
        _shape_field(f) for f in defs
        if not (_is_prefill(f.get("name"))
                and (f.get("name") or "").strip().lower() in resolved)
    ]


_REASON_NOT_CONFIGURED = ("This request type isn't online in the portal yet — "
                          "use the form for now.")
_REASON_SCHEMA_DOWN = ("We can't load this form right now. Try again in a few "
                       "minutes, or use the form.")


def _form_url(t: dict) -> str | None:
    """Public ClickUp form URL for a type, or None if we cannot build a real one.

    `form_slug` in the registry is a SLUG, not a URL (config.py says so), and no
    full form URL has ever been exercised by this codebase. So this returns None
    unless CLICKUP_FORM_BASE_URL is set — a dead "use the form instead" link is
    worse than no link, because it strands the requester twice. The per-type
    override wins so a URL that does not follow the base pattern can be pinned
    without a code change.
    """
    override = (os.getenv(f"CLICKUP_FORM_URL_{t['key'].upper()}", "") or "").strip()
    if override:
        return override
    base = (os.getenv("CLICKUP_FORM_BASE_URL", "") or "").rstrip("/")
    slug = (t.get("form_slug") or "").strip()
    return f"{base}/{slug}" if (base and slug) else None


def types_with_schema(include_internal: bool = False, company_id: str = "",
                      property_uuid: str = "") -> list[dict[str, Any]]:
    """EVERY audience-appropriate registry type, each marked available or not.

    `configured_types()` omits unconfigured types, which made the endorsed
    "lights up type-by-type" rollout the WORST case: with 3 of 6 showing, a
    requester cannot tell "my type isn't on yet" from "I'm misreading these
    labels", so they pick the nearest wrong one — the exact silent misroute the
    picker exists to prevent. Absence is not a message. This is.

    SECURITY: internal types are still OMITTED ENTIRELY for client callers, not
    listed as unavailable. Marking them unavailable would enumerate
    `dispo_cancel` and `new_business` to every portal user — the disclosure that
    create_ticket's identical-error-for-unknown-and-internal branch is
    specifically written to prevent.
    """
    # Resolved ONCE for the whole picker, not per type: prefill depends on the
    # property, not the ticket type, and this is what lets an unresolvable field
    # render on the form instead of vanishing from it (see form_schema).
    prefill = _prefill_values(company_id, property_uuid) if company_id else {}

    out: list[dict[str, Any]] = []
    for idx, t in enumerate(PORTAL_TICKET_TYPES):
        audience = t.get("audience", "client")
        if audience != "client" and not include_internal:
            continue
        entry = {
            "key": t["key"], "label": t["label"], "audience": audience,
            "order": idx, "available": False, "reason": None,
            "reason_code": None, "form_url": _form_url(t), "fields": [],
        }
        list_id = _list_id_for(t)
        if not list_id:
            entry["reason_code"] = "not_configured"
            entry["reason"] = _REASON_NOT_CONFIGURED
            out.append(entry)
            continue
        schema = form_schema(list_id, prefill)
        if schema is None:
            entry["reason_code"] = "schema_unavailable"
            entry["reason"] = _REASON_SCHEMA_DOWN
            out.append(entry)
            continue
        entry["available"] = True
        entry["fields"] = schema
        out.append(entry)
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

_OWNER_CACHE: dict[str, tuple[float, dict[str, str]]] = {}
_OWNER_CACHE_TTL = 900.0


def _owner_name(owner_id: str) -> str:
    """HubSpot owner id → "First Last". Cached 15 min; "" if unresolvable.

    The owners list is small and near-static, and `GET /crm/v3/owners` returns
    it whole — so caching turns a per-ticket round trip into one per quarter
    hour. Modelled on ticket_manager._get_owner_names, which does the same
    lookup uncached in the module A4 will retire.
    """
    if not owner_id:
        return ""
    now = time.monotonic()
    hit = _OWNER_CACHE.get("all")
    if not hit or (now - hit[0]) >= _OWNER_CACHE_TTL:
        owners: dict[str, str] = {}
        try:
            # Through hubspot_client, the central Layer-1 connector: it owns the
            # token, the 401/429 handling and the backoff, so this does not
            # become a 40th place that hand-rolls HubSpot auth.
            import hubspot_client
            r = hubspot_client._request(
                "GET", f"{hubspot_client.API_BASE}/crm/v3/owners")
            for o in r.json().get("results", []):
                name = f"{o.get('firstName','')} {o.get('lastName','')}".strip()
                owners[str(o["id"])] = name or o.get("email", "")
        except Exception as e:  # noqa: BLE001 — prefill is never load-bearing
            logger.warning("portal ticket owner lookup failed: %s", e)
            # Cache the failure briefly too, so a HubSpot outage doesn't cost a
            # round trip on every single ticket render.
            _OWNER_CACHE["all"] = (now, (hit[1] if hit else {}))
            return (hit[1] if hit else {}).get(owner_id, "")
        _OWNER_CACHE["all"] = (now, owners)
        hit = _OWNER_CACHE["all"]
    return hit[1].get(str(owner_id), "")


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
        # An owner id is a number; nobody wants "1284471" on their ticket.
        if val and src == "hubspot_owner_id":
            val = _owner_name(str(val)) or ""
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
) -> "tuple[list[dict], set, dict] | None":
    """Build ClickUp's `[{id, value}]` custom-fields payload.

    `inputs` keys may be field ids OR names (the dynamic form sends ids);
    `prefill` keys are ClickUp field names. Inputs win over prefill on overlap.

    Returns None when the list schema is unavailable. Silently returning an
    empty payload is worse than failing: the task is created with ZERO
    structured fields, and every value the requester typed is echoed into the
    description behind a raw ClickUp field uuid (via _description's `extra`
    loop, since applied_keys and label_by_key are both empty). Marketing reads
    that as a sloppy requester rather than a degraded fetch.

    Returns (payload, applied_keys, label_by_key):
      * `applied_keys` holds BOTH the field id and the lowercased field name for
        every field that made it onto the task. The description filter tests
        membership against this, and the form posts ids while prefill posts
        names — so a name-only set would never match and every mapped value
        would be duplicated into the description (the bug this shape prevents).
      * `label_by_key` maps either key space to the human field name, so any
        leftover value renders as "Priority: High" and not as a raw field uuid.
    """
    defs = clickup_client.get_list_fields(list_id)
    if defs is None:
        return None
    by_id = {d.get("id"): d for d in defs}
    by_name = {(d.get("name") or "").strip().lower(): d for d in defs}
    merged: dict[str, Any] = {}      # field_id -> coerced value
    applied_keys: set = set()        # {field id, lowercased field name}
    label_by_key: dict[str, str] = {}

    for d in defs:
        name = (d.get("name") or "").strip()
        if d.get("id"):
            label_by_key[str(d["id"])] = name
        if name:
            label_by_key[name.lower()] = name

    def resolve(key: Any, value: Any) -> None:
        d = by_id.get(key) or by_name.get(str(key).strip().lower())
        if not d:
            return
        cu_val = _coerce(d, value)
        if cu_val is None:
            return
        merged[d.get("id")] = cu_val
        applied_keys.add(d.get("id"))
        applied_keys.add((d.get("name") or "").strip().lower())

    for name, value in (prefill or {}).items():
        resolve(name, value)
    for key, value in (inputs or {}).items():
        resolve(key, value)

    payload = [{"id": fid, "value": v} for fid, v in merged.items()]
    return payload, applied_keys, label_by_key


def _description(applied_keys: set, submitted_by: str, company_id: str,
                 property_uuid: str, extra: dict | None,
                 label_by_key: dict | None = None) -> str:
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
    # `extra` is keyed by ClickUp field IDs; `applied_keys` carries ids AND
    # lowercased names, so a value that landed on the task is never repeated here.
    labels = label_by_key or {}
    for k, v in (extra or {}).items():
        if k in ("subject", "name") or not v:
            continue
        key = str(k).strip()
        if key in applied_keys or key.lower() in applied_keys:
            continue
        lines.append(f"{labels.get(key) or labels.get(key.lower()) or key}: {v}")
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
    internal: bool = False,
) -> tuple[dict, int]:
    """Create a ClickUp task for a portal ticket. Returns (body, http_status).

    `internal` mirrors the read-side audience filter in `configured_types()`.
    Without it a portal user could POST any registry key — including
    `dispo_cancel`, the list that governs whether a property gets cancelled —
    because knowing the key was the only thing standing in the way. Callers
    pass the SAME internal signal they use to decide `include_internal`.
    """
    t = _type_by_key(type_key)
    if not t:
        return {"ok": False, "error": "Unknown ticket type."}, 400
    # Deliberately the identical response to an unknown key: a client caller
    # must not be able to probe which internal ticket types exist.
    if t.get("audience") != "client" and not internal:
        logger.warning(
            "portal ticket: non-internal caller %r attempted internal type %r",
            submitted_by or "anonymous", type_key,
        )
        return {"ok": False, "error": "Unknown ticket type."}, 400
    list_id = _list_id_for(t)
    if not list_id:
        return {"ok": False, "error": "This ticket type isn't available yet."}, 503
    if not clickup_client.CLICKUP_API_KEY:
        return {"ok": False, "error": "Ticketing is temporarily unavailable."}, 503

    subject = (subject or "").strip() or t["label"]
    prefill = _prefill_values(company_id, property_uuid)
    built = _build_custom_fields(list_id, fields, prefill)
    if built is None:
        # Fail closed. After the field cache landed this branch fires only when
        # the schema was never obtainable — the /types call that rendered this
        # form seconds ago would otherwise have warmed it — so a retry is
        # genuinely the right advice. 503 mirrors the unconfigured-type response
        # above: transient, try again.
        logger.warning("portal ticket create aborted: schema unavailable for "
                       "list %s (type=%s, by=%s)",
                       list_id, type_key, submitted_by or "anonymous")
        return {"ok": False, "error": "We can't load this request form right "
                                      "now. Please try again in a few minutes."}, 503
    cf_payload, applied_keys, labels = built
    description = _description(applied_keys, submitted_by, company_id, property_uuid,
                               fields, labels)

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
    _emit_filed(task.get("id"), company_id, property_uuid, type_key, submitted_by)
    return {"ok": True, "ticket": _shape_task(task, type_key)}, 201


_FETCH_WORKERS = int(os.getenv("PORTAL_TICKETS_FETCH_WORKERS", "4"))
_FETCH_BUDGET = float(os.getenv("PORTAL_TICKETS_FETCH_BUDGET", "12"))


def _fetch_tasks(task_ids: list[str]) -> dict[str, dict]:
    """{task_id: task}, fetched concurrently under one wall-clock budget.

    ClickUp v2 has no bulk task-by-id read — `GET /task/{id}` is one task per
    call, and `GET /list/{id}/task` cannot be filtered to a set of ids. So N
    calls are unavoidable; what IS avoidable is making them sequential on a
    request thread. 50 serial calls at a 10s timeout is 500 seconds holding one
    of a handful of server threads, which is how four colleagues opening this
    tab took the whole portal down.

    _FETCH_WORKERS is 4, not 10, on purpose: it multiplies against the server's
    thread count, so 16 × 4 = 64 concurrent ClickUp sockets is the process-wide
    worst case. Anything unfetched inside the budget becomes a visible
    placeholder, never a silent omission.
    """
    import concurrent.futures
    out: dict[str, dict] = {}
    if not task_ids:
        return out
    pool = concurrent.futures.ThreadPoolExecutor(
        max_workers=min(_FETCH_WORKERS, len(task_ids)),
        thread_name_prefix="portal-tickets")
    try:
        futures = {pool.submit(clickup_client.get_task, tid): tid for tid in task_ids}
        done, pending = concurrent.futures.wait(futures, timeout=_FETCH_BUDGET)
        for fut in done:
            try:
                task = fut.result()
            except Exception as e:  # noqa: BLE001 — one bad task ≠ a blank page
                logger.warning("portal ticket fetch failed for %s: %s", futures[fut], e)
                continue
            if task:
                out[futures[fut]] = task
        if pending:
            logger.warning("portal ticket fetch budget exhausted: %d of %d unresolved",
                           len(pending), len(task_ids))
    finally:
        # wait=False + cancel_futures, NOT `with ...:`. __exit__ calls
        # shutdown(wait=True), which blocks until every straggler returns and
        # makes the budget above a lie.
        pool.shutdown(wait=False, cancel_futures=True)
    return out


def list_tickets(company_id: str, *, property_uuid: str = "", limit: int = 50) -> list[dict]:
    """Open + recent tickets for a property, newest first.

    Every mapping row produces exactly one row in the output. A task ClickUp
    would not resolve becomes a placeholder, never a silent drop: partial
    rendered as complete is the worst failure available on a status surface, and
    the mapping row is our own write receipt that the request exists.
    """
    refs: list[dict] = []
    seen: set = set()
    for ref in _read_mappings(company_id, property_uuid, limit):
        tid = str(ref.get("task_id") or "")
        # The mapping table is append-only and mapping_for_task already assumes
        # duplicate task_id rows, so dedupe here or the same ticket renders twice.
        if not tid or tid in seen:
            continue
        seen.add(tid)
        refs.append(ref)

    tasks = _fetch_tasks([str(r["task_id"]) for r in refs])
    out = [
        _shape_task(tasks[str(r["task_id"])], r.get("ticket_type"))
        if str(r["task_id"]) in tasks else _placeholder_task(r)
        for r in refs
    ]
    out.sort(key=lambda x: x.get("created_ts") or 0, reverse=True)
    return out


def _created_ms(created_at: Any) -> int | None:
    """BigQuery TIMESTAMP (datetime or ISO string) → epoch ms, so a placeholder
    sorts correctly and shows its REAL filing date — we wrote that row."""
    if not created_at:
        return None
    try:
        dt = (created_at if isinstance(created_at, datetime)
              else datetime.fromisoformat(str(created_at).replace("Z", "+00:00")))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except (TypeError, ValueError):
        return None


def _placeholder_task(ref: dict) -> dict[str, Any]:
    """A request we KNOW exists, whose live state ClickUp would not give us.

    `unresolved` drives both the card styling and the list-level banner.
    """
    t = _type_by_key(ref.get("ticket_type")) or {}
    created = _created_ms(ref.get("created_at"))
    tid = str(ref.get("task_id") or "")
    return {
        "id": tid,
        "type": ref.get("ticket_type") or "",
        "type_label": t.get("label", ""),
        "subject": f"{t.get('label') or 'Request'} · {tid[-6:]}" if tid else "Request",
        "status": "Status unavailable",
        "raw_status": "",
        "created_ts": created,
        "age_days": _age_days(created),
        "url": None,
        "unresolved": True,
    }


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
        "unresolved": False,
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


def _emit_filed(task_id, company_id, property_uuid, type_key, submitted_by) -> None:
    """Best-effort funnel event. Never let instrumentation fail a ticket."""
    if not task_id:
        return
    try:
        import loop_ticket_events
        loop_ticket_events.record_ticket_filed(
            property_uuid or None,
            company_id or None,
            task_id=str(task_id),
            ticket_type=type_key or "",
            submitted_by=submitted_by or None,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("portal ticket loop event failed for %s: %s", task_id, e)


def mapping_for_task(task_id: str) -> dict | None:
    """The stored {company_id, property_uuid, ticket_type, submitted_by} for a
    portal-created task, or None if this task didn't come from the portal.

    This is the exact-lookup counterpart to `clickup_recap`'s domain/name
    guessing: the portal wrote this row when it created the task, so for its
    own tickets there is nothing to infer.
    """
    if not task_id:
        return None
    try:
        import bigquery_client
        if not bigquery_client.is_bigquery_configured():
            return None
        from google.cloud import bigquery
        from config import BIGQUERY_PROJECT_ID
        dataset = bigquery_client._dataset()
        sql = f"""
            SELECT company_id, property_uuid, ticket_type, submitted_by
            FROM `{BIGQUERY_PROJECT_ID}.{dataset}.{_MAPPING_TABLE}`
            WHERE task_id = @tid
            ORDER BY created_at DESC
            LIMIT 1
        """
        params = [bigquery.ScalarQueryParameter("tid", "STRING", str(task_id))]
        rows = bigquery_client.query(sql, params)
        return rows[0] if rows else None
    except Exception as e:  # noqa: BLE001 — fall back to the search path
        logger.warning("portal ticket mapping lookup failed for %s: %s", task_id, e)
        return None


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

    matched, unmatched, env_lines, internal_env_lines = [], [], [], []
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
        audience = t.get("audience", "client")
        if found:
            matched.append({"key": t["key"], "label": t["label"],
                            "list_id": found["id"], "list_name": found["name"],
                            "env": t["list_env"], "audience": audience})
            line = f'{t["list_env"]}={found["id"]}'
            (env_lines if audience == "client" else internal_env_lines).append(line)
        else:
            unmatched.append({"key": t["key"], "label": t["label"],
                              "env": t["list_env"], "audience": audience})

    return {
        "workspace_id": CLICKUP_WORKSPACE_ID,
        "lists_found": len(lists),
        "matched": matched,
        "unmatched_types": unmatched,
        # Client-facing types ONLY. `discover` used to emit all 8 types in one
        # block, so the natural "paste this into Render" step silently turned on
        # dispo_cancel and new_business — the lists that govern cancelling a
        # property and sales intake. Those are split out deliberately: pasting
        # them is a separate, conscious act.
        "env_block": "\n".join(env_lines),
        "env_block_internal": "\n".join(internal_env_lines),
    }
