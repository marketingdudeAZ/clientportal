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
    PORTAL_TICKET_FORMS,
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


class ManifestDrift(Exception):
    """A manifest field does not resolve against the list's live ClickUp fields.

    Raised only for a REQUIRED field. Rendering the form without it would let a
    requester submit a task missing something the services team needs, which is
    the failure this whole manifest exists to end — so the type goes unavailable
    instead, with the same "use the form" fallback as a ClickUp outage.
    """


def _manifest_for(type_key: str) -> dict | None:
    return PORTAL_TICKET_FORMS.get(type_key)


def _defs_by_name(defs: list[dict]) -> dict[str, list[dict]]:
    """{lowercased field name: [every def with that name]}.

    A LIST, not a single def, because duplicate names are the normal case here:
    each of the 8 paid channels exists twice on the same list — a `currency`
    variant for new-build budgets and a `drop_down` variant for add/remove/change
    — and `general` carries FOUR fields called "Market" with different regional
    option sets. A dict keyed by name silently keeps whichever came last, which
    is a wrong-field-id write that nothing downstream can detect.
    """
    out: dict[str, list[dict]] = {}
    for d in defs:
        out.setdefault((d.get("name") or "").strip().lower(), []).append(d)
    return out


def _pick_def(entry: dict, candidates: list[dict]) -> dict | None:
    """Choose the one field def a manifest entry means, or None if ambiguous.

    `field_id` pins an exact field for the case where duplicates are
    indistinguishable by anything else; `clickup_type` separates the
    currency/drop_down channel twins. Both are last resorts — a unique name is
    always better, and "Market*" exists precisely because someone needed one.
    """
    if len(candidates) == 1:
        return candidates[0]
    fid = entry.get("field_id")
    if fid:
        return next((d for d in candidates if str(d.get("id")) == str(fid)), None)
    want = entry.get("clickup_type")
    if want:
        narrowed = [d for d in candidates if d.get("type") == want]
        if len(narrowed) == 1:
            return narrowed[0]
    return None


def _resolve_manifest_field(entry: dict, by_name: dict, type_key: str) -> dict | None:
    """One manifest entry + the list's live field defs → a rendered form field.

    The manifest owns what is asked and how it reads; ClickUp owns the field id,
    its type and its dropdown options. Returns None when the entry resolves but
    is not an input (tier known/file), and raises ManifestDrift when a required
    entry cannot be resolved to exactly one live field.
    """
    name = (entry.get("name") or "").strip()
    candidates = by_name.get(name.lower()) or []
    d = _pick_def(entry, candidates)
    if d is None:
        why = (f"{len(candidates)} fields share that name and the manifest does "
               f"not say which" if len(candidates) > 1 else "not on this list")
        if entry.get("required"):
            raise ManifestDrift(f"{type_key}: required field {name!r} — {why}")
        logger.warning("portal ticket manifest drift: %s names %r — %s; skipped",
                       type_key, name, why)
        return None
    if entry.get("tier", "ask") != "ask":
        return None
    field = _shape_field(d)
    # The manifest, not ClickUp, decides requiredness: ClickUp's list-level
    # field defs report required=False for every field, because requiredness
    # lives on the form view the API will not show us.
    field["required"] = bool(entry.get("required"))
    for k in ("help", "maxlength", "validate"):
        if entry.get(k):
            field[k] = entry[k]
    return field


def _manifest_schema(type_key: str, defs: list[dict]) -> dict[str, Any]:
    """Build {fields, sections} for a manifest-backed type, in manifest order."""
    manifest = PORTAL_TICKET_FORMS[type_key]
    by_name = _defs_by_name(defs)
    fields: list[dict[str, Any]] = []
    sections: list[dict[str, Any]] = []
    for sec in manifest.get("sections") or []:
        rendered = []
        for entry in sec.get("fields") or []:
            f = _resolve_manifest_field(entry, by_name, type_key)
            if f is not None:
                rendered.append(f)
        fields.extend(rendered)
        # A section whose fields are all `known`/`file` renders nothing today,
        # so it is dropped rather than shown as an empty heading.
        if rendered:
            sections.append({"title": sec.get("title") or "",
                             "help": sec.get("help") or "",
                             "fields": rendered})
    return {"fields": fields, "sections": sections}


def form_schema(type_key: str, list_id: str,
                prefill: dict | None = None) -> "list[dict[str, Any]] | None":
    """Client-facing form fields for a ticket type, in the order they're asked.

    With a manifest (`PORTAL_TICKET_FORMS`), this repo decides which fields
    appear and ClickUp is consulted only to resolve each name to its id, type
    and options — per list, never globally: several channel fields share a
    display name across lists with different types, so a space-wide lookup
    returns the wrong field id.

    Without one, the previous behaviour stands: every field ClickUp says is
    available to the list, minus the ones we can actually pre-fill. That path is
    what renders ~105 fields on a type, and it is kept only so the five types
    that have no manifest yet do not regress.

    `prefill` is optional: with none supplied, no field is hidden, which is the
    safe direction (the requester sees a field we would have filled, rather than
    a field nobody fills). It does not apply to manifest types — there, the
    `known` tier already decides what is never asked.

    None means ClickUp would not tell us the schema. That must propagate all the
    way to `available: false`; collapsing it into an empty form lets a requester
    submit into a task missing every required field.
    """
    defs = clickup_client.get_list_fields(list_id)
    if defs is None:
        return None
    if _manifest_for(type_key):
        return _manifest_schema(type_key, defs)["fields"]
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
        manifest = _manifest_for(t["key"]) or {}
        entry = {
            "key": t["key"], "label": t["label"], "audience": audience,
            "order": idx, "available": False, "reason": None,
            "reason_code": None, "form_url": _form_url(t), "fields": [],
            # The framing around the form. Empty for a type with no manifest,
            # so the renderer's checks are uniform rather than per-type.
            "sections": [], "sla": manifest.get("sla") or "",
            "prereqs": list(manifest.get("prereqs") or []),
            "notes": list(manifest.get("notes") or []),
            "name_pattern": manifest.get("name_pattern") or "",
        }
        list_id = _list_id_for(t)
        if not list_id:
            entry["reason_code"] = "not_configured"
            entry["reason"] = _REASON_NOT_CONFIGURED
            out.append(entry)
            continue
        defs = clickup_client.get_list_fields(list_id)
        if defs is None:
            entry["reason_code"] = "schema_unavailable"
            entry["reason"] = _REASON_SCHEMA_DOWN
            out.append(entry)
            continue
        if manifest:
            try:
                built = _manifest_schema(t["key"], defs)
            except ManifestDrift as e:
                # Someone renamed or deleted a field in ClickUp that this form
                # requires. Rendering the rest would collect an incomplete
                # request that looks complete to the requester, so the type goes
                # offline with the same fallback as an outage — and loudly,
                # because only a deploy fixes it.
                logger.error("portal ticket manifest drift on %s: %s", t["key"], e)
                entry["reason_code"] = "manifest_drift"
                entry["reason"] = _REASON_SCHEMA_DOWN
                out.append(entry)
                continue
            entry["fields"] = built["fields"]
            entry["sections"] = built["sections"]
        else:
            entry["fields"] = form_schema(t["key"], list_id, prefill) or []
        entry["available"] = True
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


# ── field-level validation (manifest rules) ──────────────────────────────────

class TicketValidation(Exception):
    """A submitted value breaks a manifest rule. Carries requester-facing text."""

    def __init__(self, field: str, message: str):
        super().__init__(f"{field}: {message}")
        self.field = field
        self.message = message


# Punctuation the ad platforms accept in this copy. Periods only — the brief's
# rule verbatim, and the reason the field exists in this shape at all.
_ALLOWED_PUNCT = set(".$ ")


def _no_caps_no_punct_except_period(value: str) -> str | None:
    """The special-copy rule. Returns a requester-facing complaint, or None.

    Enforced here as well as in the browser because the browser is advice: the
    API is reachable directly, and a rejected special is cheaper than an ad
    account rejecting the copy days later.
    """
    if any(c.isupper() for c in value):
        return ("Please use no capital letters — write it the way it should "
                "appear in the ad.")
    bad = sorted({c for c in value
                  if not c.isalnum() and not c.isspace() and c not in _ALLOWED_PUNCT})
    if bad:
        return ("Please remove " + " ".join(bad) +
                " — periods are the only punctuation allowed.")
    return None


_VALIDATORS = {"no_caps_no_punct_except_period": _no_caps_no_punct_except_period}


def _manifest_rules(type_key: str) -> dict[str, dict]:
    """{lowercased ClickUp field name: {maxlength, validate}} for a type."""
    manifest = _manifest_for(type_key) or {}
    rules: dict[str, dict] = {}
    for sec in manifest.get("sections") or []:
        for entry in sec.get("fields") or []:
            rule = {k: entry[k] for k in ("maxlength", "validate") if entry.get(k)}
            if rule:
                rules[(entry.get("name") or "").strip().lower()] = rule
    return rules


def _check_rule(field_name: str, value: Any, rule: dict | None) -> None:
    """Raise TicketValidation if `value` breaks the manifest rule for a field."""
    if not rule or value in (None, ""):
        return
    text = str(value)
    maxlength = rule.get("maxlength")
    if maxlength and len(text) > int(maxlength):
        raise TicketValidation(
            field_name,
            f"Please shorten this to {int(maxlength)} characters "
            f"(currently {len(text)}).")
    check = _VALIDATORS.get(rule.get("validate") or "")
    if check:
        complaint = check(text)
        if complaint:
            raise TicketValidation(field_name, complaint)


# ── custom-field payload building ────────────────────────────────────────────

def _coerce(field_def: dict, value: Any, rule: dict | None = None) -> Any:
    """Coerce a form value into the shape ClickUp's API expects for the field.

    drop_down/labels resolve option *labels* back to option ids; number/currency
    become floats; checkbox becomes bool. Returns None to skip the field.

    `rule` is the manifest's constraint for this field, checked BEFORE coercion
    so the complaint quotes what the requester actually typed.
    """
    if value in (None, ""):
        return None
    _check_rule((field_def.get("name") or "").strip(), value, rule)
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
    list_id: str, inputs: dict | None, prefill: dict | None,
    rules: dict | None = None,
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
    # Ambiguous names resolve to NOTHING rather than to whichever def happened
    # to come last. The form posts ids so it is unaffected; prefill posts names,
    # and "Market" matches four different fields on the General list with
    # different regional option sets. Writing to a coin-flip field is worse than
    # leaving it blank, because nothing downstream can tell it happened.
    _named = _defs_by_name(defs)
    by_name = {n: ds[0] for n, ds in _named.items() if len(ds) == 1}
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
        # Rules are keyed by field NAME, but the form posts field IDS — so the
        # lookup happens after `d` resolves, or every rule would silently miss.
        cu_val = _coerce(d, value, (rules or {}).get((d.get("name") or "").strip().lower()))
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
    try:
        built = _build_custom_fields(list_id, fields, prefill,
                                     _manifest_rules(type_key))
    except TicketValidation as e:
        # 400, not 422: the browser enforces the same rule, so reaching here
        # means either a stale form or a direct API call. Name the field so the
        # response is usable without the form's own error handling.
        return {"ok": False, "error": e.message, "field": e.field}, 400
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
    return {"ok": True, "ticket": _shape_task(task, type_key, submitted_by)}, 201


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
    # submitted_by comes from OUR mapping row, not from ClickUp — the requester
    # is a portal user, who may not exist as a ClickUp member. The placeholder
    # carries it too: an unresolvable task is exactly when "who filed this" is
    # the only thing we can still tell the requester.
    out = [
        _shape_task(tasks[str(r["task_id"])], r.get("ticket_type"), r.get("submitted_by"))
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
        "submitted_by": ref.get("submitted_by") or "",
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


def _shape_task(task: dict, type_key: str | None = None,
                submitted_by: str | None = None) -> dict[str, Any]:
    status = ((task.get("status") or {}).get("status")) or ""
    created = task.get("date_created")
    t = _type_by_key(type_key) if type_key else None
    return {
        "id": task.get("id"),
        "type": type_key or "",
        "type_label": (t or {}).get("label", ""),
        "subject": task.get("name"),
        "submitted_by": submitted_by or "",
        "status": client_status(status),
        "raw_status": status,
        "created_ts": int(created) if created else None,
        "age_days": _age_days(created),
        "url": task.get("url"),
        "unresolved": False,
    }


# ── mapping store (BigQuery, append-only) ────────────────────────────────────

# Mirrors migrations/0012_portal_tickets_table.py. Kept here as well because a
# migration that was never run is indistinguishable, at runtime, from a table
# that never existed — and that is exactly what happened: tickets filed into
# ClickUp correctly for weeks while every mapping write was swallowed, so the
# portal showed "No open requests" forever and no recap ever matched. A schema
# this small should not depend on someone remembering to run a script.
# tests/test_portal_tickets.py asserts the two definitions stay identical.
_MAPPING_DDL = """
CREATE TABLE IF NOT EXISTS `{project}.{dataset}.portal_tickets` (
  task_id        STRING NOT NULL,
  company_id     STRING,
  property_uuid  STRING,
  ticket_type    STRING,
  submitted_by   STRING,
  created_at     TIMESTAMP
)
"""

# Set when the mapping store is unusable, so "we can't show your requests right
# now" can be said out loud instead of rendering an empty list that reads as
# "your request was never filed".
_TRACKING_DEGRADED = False
_TABLE_ENSURED = False


def tracking_degraded() -> bool:
    """True when tickets are filing but we cannot record or read the mapping."""
    return _TRACKING_DEGRADED


def _ensure_mapping_table() -> bool:
    """CREATE TABLE IF NOT EXISTS for the mapping table. Once per process.

    Returns True if the statement ran. Idempotent by construction, and cheap:
    it fires at most once, and only after a write has already failed.
    """
    global _TABLE_ENSURED
    if _TABLE_ENSURED:
        return False
    _TABLE_ENSURED = True
    try:
        import bigquery_client
        from config import BIGQUERY_PROJECT_ID
        bigquery_client.query(_MAPPING_DDL.format(
            project=BIGQUERY_PROJECT_ID, dataset=bigquery_client._dataset()))
        logger.warning("portal_tickets mapping table was missing — created it")
        return True
    except Exception as e:  # noqa: BLE001 — the app may lack DDL rights
        logger.error("portal ticket mapping table could not be created: %s. "
                     "Run migrations/0012_portal_tickets_table.py against the "
                     "prod dataset.", e)
        return False


def _record_mapping(task_id, company_id, property_uuid, type_key, submitted_by) -> None:
    """Record task_id ↔ company_id. Best-effort, but never SILENTLY best-effort.

    A failure here does not fail the ticket — the work is already filed in
    ClickUp and losing it would be worse. But it does flip `_TRACKING_DEGRADED`,
    because the alternative is what shipped before: the requester sees an empty
    "what's open" list and concludes their request vanished.
    """
    global _TRACKING_DEGRADED
    if not task_id:
        return
    row = {
        "task_id": str(task_id),
        "company_id": str(company_id or ""),
        "property_uuid": str(property_uuid or ""),
        "ticket_type": type_key or "",
        "submitted_by": submitted_by or "",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        import bigquery_client
        if not bigquery_client.is_bigquery_configured():
            _TRACKING_DEGRADED = True
            logger.error("portal ticket %s filed but BigQuery is not configured "
                         "— it will not appear in the requester's list", task_id)
            return
        try:
            bigquery_client.insert_rows(_MAPPING_TABLE, [row])
        except Exception as first:  # noqa: BLE001
            # Most likely the table does not exist. Create it and retry once —
            # any other cause simply fails again and is reported below.
            logger.warning("portal ticket mapping write failed for %s (%s); "
                           "ensuring the table exists and retrying", task_id, first)
            if not _ensure_mapping_table():
                raise
            bigquery_client.insert_rows(_MAPPING_TABLE, [row])
        _TRACKING_DEGRADED = False
    except Exception as e:  # noqa: BLE001 — a mapping-write failure must not fail the ticket
        _TRACKING_DEGRADED = True
        logger.error("portal ticket %s filed but its mapping row was NOT "
                     "recorded: %s. The requester will not see it in their "
                     "list, and no recap will match it.", task_id, e)


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
    global _TRACKING_DEGRADED
    try:
        import bigquery_client
        if not bigquery_client.is_bigquery_configured():
            _TRACKING_DEGRADED = True
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
        rows = bigquery_client.query(sql, params)
        _TRACKING_DEGRADED = False
        return rows
    except Exception as e:  # noqa: BLE001 — tracking is best-effort
        # An empty list and a failed read look identical to the requester, and
        # one of them means "your request is gone". Say which.
        _TRACKING_DEGRADED = True
        logger.error("portal ticket mapping read failed for %s: %s", company_id, e)
        return []


# ── admin: discover the ClickUp list ids ─────────────────────────────────────

_LIST_PARENT_TYPE = 6  # ClickUp view.parent.type for "list"


def _list_by_form(form_slug: str) -> tuple[str, str] | tuple[None, str]:
    """Resolve `form_slug` → (list_id, reason) via the form view's parent.

    Authoritative: the id in a public ClickUp form URL is a view id, and that
    view names the list it files into. Returns (None, reason) when the form is
    gone, unreachable, or parented to something other than a list.
    """
    if not form_slug:
        return None, "no form_slug in the registry"
    view = clickup_client.get_view(form_slug)
    if not view:
        return None, f"form {form_slug} not reachable (deleted, or ClickUp failed)"
    if (view.get("type") or "") != "form":
        return None, f"view {form_slug} is a {view.get('type')!r} view, not a form"
    parent = view.get("parent") or {}
    pid = str(parent.get("id") or "")
    if not pid or parent.get("type") != _LIST_PARENT_TYPE:
        return None, f"form {form_slug} is not parented to a list (parent={parent!r})"
    return pid, f"form {form_slug} ({view.get('name')})"


def _list_by_name(t: dict, by_name: dict) -> dict | None:
    """The legacy name/alias match, kept only to corroborate the form match."""
    candidates = [t["label"], *(t.get("aliases") or [])]
    for c in candidates:
        hit = by_name.get(c.strip().lower())
        if hit:
            return hit
    for name, lst in by_name.items():  # loose contains-match
        if any(c.strip().lower() in name or name in c.strip().lower() for c in candidates):
            return lst
    return None


def discover_list_ids() -> dict[str, Any]:
    """Resolve each ticket type's ClickUp list id, form first, name second.

    Returns a paste-ready `env_block` plus per-type match detail, so the real
    numeric list ids can be pulled without hand-decoding ClickUp URLs (which
    carry *view* ids, not list ids).

    Name matching alone is not safe here and was demonstrably wrong: the loose
    contains-match resolved `creative_ad_copy` — whose label is "Ad Updates:
    Photos & New Specials", alias "Creative" — to a list literally named
    "Creative" in a *different space's* documentation folder, while the real
    intake list is "Creative + Ad Copy Updates". Every ad-update ticket in the
    pilot would have filed into a guidance folder nobody triages, and the
    portal would have looked like it worked. So the form view's `parent` is the
    resolver and the name match is only a second opinion; where the two
    disagree, `conflicts` says so rather than silently picking one.

    Archived lists never make it into an env block. `campaign_review`'s
    registered form_slug still points at "[OLD] - Campaign Performance Review",
    which is archived — an archived list accepts tasks that nobody sees.
    """
    lists = clickup_client.discover_workspace_lists(CLICKUP_WORKSPACE_ID)
    by_name = {(l.get("name") or "").strip().lower(): l for l in lists if l.get("id")}
    by_id = {str(l["id"]): l for l in lists if l.get("id")}

    matched: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    warnings: list[str] = []
    env_lines: list[str] = []
    internal_env_lines: list[str] = []

    for t in PORTAL_TICKET_TYPES:
        audience = t.get("audience", "client")
        form_id, form_reason = _list_by_form(t.get("form_slug", ""))
        named = _list_by_name(t, by_name)
        named_id = str(named["id"]) if named else None

        list_id = form_id or named_id
        source = "form" if form_id else ("name" if named_id else None)

        if form_id and named_id and form_id != named_id:
            conflicts.append({
                "key": t["key"],
                "form_list_id": form_id, "form_list_name": _name_of(form_id, by_id),
                "name_list_id": named_id, "name_list_name": named.get("name"),
                "using": form_id,
                "note": "form view wins; the name match points somewhere else",
            })

        if not list_id:
            unmatched.append({"key": t["key"], "label": t["label"],
                              "env": t["list_env"], "audience": audience,
                              "reason": form_reason})
            continue

        meta = by_id.get(list_id) or {}
        # Archived lists are absent from the workspace walk, so a form-resolved
        # id that isn't in `by_id` has to be fetched before it can be trusted.
        if not meta:
            fetched = clickup_client.get_list(list_id) or {}
            meta = {"name": fetched.get("name"), "archived": fetched.get("archived"),
                    "space": (fetched.get("space") or {}).get("name"),
                    "folder": (fetched.get("folder") or {}).get("name")}
        archived = bool(meta.get("archived"))

        entry = {"key": t["key"], "label": t["label"],
                 "list_id": list_id, "list_name": meta.get("name"),
                 "space": meta.get("space"), "folder": meta.get("folder"),
                 "env": t["list_env"], "audience": audience,
                 "source": source, "resolved_via": form_reason if form_id else "name/alias match",
                 "archived": archived}
        matched.append(entry)

        if archived:
            warnings.append(
                f'{t["key"]}: {t["list_env"]} NOT emitted — list {list_id} '
                f'("{meta.get("name")}") is archived. Point form_slug at the live '
                f'list before switching this type on.')
            continue

        line = f'{t["list_env"]}={list_id}'
        (env_lines if audience == "client" else internal_env_lines).append(line)

    return {
        "workspace_id": CLICKUP_WORKSPACE_ID,
        "lists_found": len(lists),
        "matched": matched,
        "unmatched_types": unmatched,
        "conflicts": conflicts,
        "warnings": warnings,
        # Client-facing types ONLY. `discover` used to emit all 8 types in one
        # block, so the natural "paste this into Render" step silently turned on
        # dispo_cancel and new_business — the lists that govern cancelling a
        # property and sales intake. Those are split out deliberately: pasting
        # them is a separate, conscious act.
        "env_block": "\n".join(env_lines),
        "env_block_internal": "\n".join(internal_env_lines),
    }


def _name_of(list_id: str, by_id: dict) -> str | None:
    hit = by_id.get(str(list_id))
    if hit:
        return hit.get("name")
    fetched = clickup_client.get_list(list_id) or {}
    return fetched.get("name")
