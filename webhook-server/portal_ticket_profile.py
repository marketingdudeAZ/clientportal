"""Closed loop — a submitted ticket refreshes the property profile.

When a ticket carries fields that *describe the property* (amenities, hotspots,
competitors, voice, target audience, lifecycle, CMS…), those values flow back
into the Community Brief (the property profile) so it stays solidified. Mapping
is declared per field in portal_ticket_specs.py via `profile_key`.

Conflict policy (Kyle's call): never silently overwrite a curated value.
  - profile field EMPTY  → fill it in.
  - profile field SAME   → no-op.
  - profile field DIFFERS → return a conflict for a human to resolve; write nothing.

All writes go through community_brief.write_field, so they land on the same
`fluency_*_override` properties the /accounts editor uses and are audited. R1:
`uuid` is never in the map, so it can never be written here.
"""

from __future__ import annotations

import logging

from portal_ticket_specs import PROFILE_VALUE_MAP

logger = logging.getLogger(__name__)


def _transform(profile_key: str, raw) -> str:
    """Normalize a ticket value into the profile field's expected form."""
    val = (raw if isinstance(raw, str) else "; ".join(raw) if isinstance(raw, list) else str(raw)).strip()
    if not val:
        return ""
    vmap = PROFILE_VALUE_MAP.get(profile_key)
    if vmap:
        return vmap.get(val.strip().lower(), val)
    return val


def apply_updates(company_id: str, profile_values: dict, *,
                  task_id: str | None = None, edited_by: str = "") -> dict:
    """Write non-conflicting profile updates; return applied + conflicts.

    profile_values: {community_brief field key: ticket value}.
    """
    import community_brief as cb

    entries, props_needed = [], set()
    for key, raw in (profile_values or {}).items():
        field = cb.FIELDS.get(key)
        if not field or not field.hs_override:
            continue
        val = _transform(key, raw)
        if not val:
            continue
        entries.append((key, field, val))
        if field.hs_resolved:
            props_needed.add(field.hs_resolved)
        props_needed.add(field.hs_override)

    if not entries:
        return {"applied": [], "conflicts": []}

    props = {}
    try:
        import hubspot_client
        props = hubspot_client.get_company(company_id, sorted(props_needed)) or {}
    except Exception as e:  # noqa: BLE001 — degrade to "treat as empty" rather than fail
        logger.warning("closed-loop profile read failed for %s: %s", company_id, e)

    applied, conflicts = [], []
    for key, field, val in entries:
        current = cb.resolve_value(props, field.hs_resolved, field.hs_override)
        if not current:
            ok, msg = cb.write_field(company_id, key, val, edited_by=edited_by)
            if ok:
                applied.append({"key": key, "label": field.label, "value": val})
            else:
                logger.info("closed-loop skip %s for %s: %s", key, company_id, msg)
        elif current.strip() == val.strip():
            continue
        else:
            conflicts.append({"key": key, "label": field.label,
                              "ticket_value": val, "current_value": current})
    if applied or conflicts:
        logger.info("closed-loop %s task=%s: %d applied, %d conflicts",
                    company_id, task_id, len(applied), len(conflicts))
    return {"applied": applied, "conflicts": conflicts}


def resolve_conflict(company_id: str, key: str, value: str, *, edited_by: str = "") -> tuple[bool, str]:
    """Apply a human's conflict decision — write the chosen value to the profile.

    The UI sends this only when the requester chose to overwrite with the ticket
    value; choosing "keep current" needs no write and never reaches here.
    """
    import community_brief as cb
    if not (value or "").strip():
        return False, "empty value"
    return cb.write_field(company_id, key, value, edited_by=edited_by)
