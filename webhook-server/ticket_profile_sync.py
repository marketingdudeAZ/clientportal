"""Ticket → property-profile loop (Layer 2 skill).

Closes the loop that ended at the client-facing recap note: when a ClickUp
ticket completes and its content implies a change to the property's profile
(a rebrand's new advertised name, a budget update's new figure, an onboarding
build's amenity list), that change becomes a **proposed** profile update.

    completed ticket ──► proposals ──► human accepts ──► community_brief.write_field()

It never writes the profile itself. `CLAUDE.md` rule 4 — override wins for
human-curated fields — is load-bearing here: a ticket-driven write would
silently stomp a human's edit. So a proposal whose target field already holds
a human override is stored with `conflicts_with_override=True` and surfaced
with a warning; accepting it is a deliberate act by a person.

Two properties this module never violates:

  * **One resolution rule.** The current value of a field is always read via
    `community_brief.resolve_value()`. That rule has drifted before when it
    was reimplemented; it is not reimplemented here.
  * **One write path.** Accepting a proposal calls
    `community_brief.write_field()` — the same call the portal's inline
    editor makes — so the value lands on the `fluency_*_override` property,
    is audit-logged with the accepting user's email, and reaches Fluency on
    the next daily sync.

Two extractors feed proposals:

  * **field map** (always on) — a ClickUp custom-field name → profile field
    key map (`config.TICKET_PROFILE_FIELD_MAP`), extended without a deploy
    via `TICKET_PROFILE_FIELD_MAP_JSON`.
  * **ai** (`TICKET_PROFILE_AI_EXTRACT=true`, off by default) — reads the
    ticket thread and may only propose fields already allow-listed for that
    ticket type.

Storage is the append-only BQ table `ticket_profile_proposals` (migration
0013): one row per state transition, latest per `proposal_id` wins.
Everything degrades — no BigQuery, no LLM key, no ClickUp: callers get empty
results or a clear error, never a 500.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

import community_brief
import ticket_recap

logger = logging.getLogger(__name__)

_TABLE = "ticket_profile_proposals"

STATUS_PENDING = "proposed"
STATUS_ACCEPTED = "accepted"
STATUS_REJECTED = "rejected"

EXTRACTOR_FIELD_MAP = "field_map"
EXTRACTOR_AI = "ai"

# Structured JSON editors (floorplan / tracking / documents). A ticket never
# proposes one — they are edited in the full brief, and a malformed JSON
# proposal would be un-acceptable anyway.
_UNPROPOSABLE_TYPES = frozenset(community_brief.TABLE_TYPES) | {"readonly"}

# ClickUp custom fields that carry matching/provenance data rather than
# property facts. Never a profile proposal, whatever the field map says.
_IDENTITY_FIELDS = frozenset({
    "property url", "property domain", "website", "property code", "uuid",
    "market", "account manager", "submitter email",
})

_MAX_VALUE_LEN = 4000


def enabled() -> bool:
    """Master flag. Off → no proposals are generated and the routes 404."""
    return os.environ.get("TICKET_PROFILE_LOOP_ENABLED", "").strip().lower() == "true"


def _ai_enabled() -> bool:
    return os.environ.get("TICKET_PROFILE_AI_EXTRACT", "").strip().lower() == "true"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def proposal_id(task_id: Any, field_key: str) -> str:
    """Deterministic id: a re-fired webhook re-proposes, never duplicates."""
    raw = f"{task_id}:{field_key}".encode()
    return hashlib.sha1(raw).hexdigest()[:20]


# ── policy ───────────────────────────────────────────────────────────────────


def allowed_fields(ticket_type: str) -> tuple:
    """Profile fields this ticket type may propose. Unknown type → nothing.

    Types opt IN. dispo_cancel is absent by construction and additionally
    hard-excluded below, matching ticket_recap.EXCLUDED_TYPES.
    """
    if ticket_type in ticket_recap.EXCLUDED_TYPES:
        return ()
    from config import TICKET_PROFILE_TYPE_FIELDS
    return tuple(TICKET_PROFILE_TYPE_FIELDS.get(ticket_type, ()))


def _proposable(field_key: str, ticket_type: str) -> "community_brief.BriefField | None":
    """The BriefField this key may be proposed for, or None."""
    if field_key not in allowed_fields(ticket_type):
        return None
    field = community_brief.FIELDS.get(field_key)
    if not field:
        return None
    if not field.hs_override:          # nowhere to write → not proposable
        return None
    if field.type in _UNPROPOSABLE_TYPES:
        return None
    return field


def _normalize(field: "community_brief.BriefField", value: Any) -> str:
    """Coerce a raw extracted value into what write_field will accept.

    Returns "" when the value can't be stored for this field — an option-typed
    field whose value isn't in its option list produces no proposal rather than
    a card that fails the moment someone clicks Accept.
    """
    if value in (None, "", []):
        return ""
    if isinstance(value, (list, tuple)):
        value = "\n".join(str(v).strip() for v in value if str(v).strip())
    text = str(value).strip()
    if not text:
        return ""
    if len(text) > _MAX_VALUE_LEN:
        text = text[:_MAX_VALUE_LEN].rstrip()
    if field.options:
        by_lower = {o.lower(): o for o in field.options}
        picked = []
        for part in re.split(r"[;,]", text):
            hit = by_lower.get(part.strip().lower())
            if hit and hit not in picked:
                picked.append(hit)
        if not picked:
            return ""
        if field.type == "multiselect":
            return ";".join(picked)
        return picked[0]
    return text


# ── extractors ───────────────────────────────────────────────────────────────


def _extract_from_fields(task: dict, ticket_type: str) -> list[tuple]:
    """(field_key, value, EXTRACTOR_FIELD_MAP) from the task's custom fields."""
    from config import TICKET_PROFILE_FIELD_MAP
    try:
        from clickup_client import _resolve_field_value
    except ImportError:  # pragma: no cover — clickup_client is always present
        return []

    out = []
    for f in (task.get("custom_fields") or []):
        name = (f.get("name") or "").strip().lower()
        if not name or name in _IDENTITY_FIELDS:
            continue
        key = TICKET_PROFILE_FIELD_MAP.get(name)
        if not key:
            continue
        try:
            value = _resolve_field_value(f)
        except Exception:  # noqa: BLE001 — one bad field never kills extraction
            continue
        if f.get("type") == "currency" and value not in (None, "", []):
            try:
                value = "$" + format(int(round(float(value))), ",")
            except (TypeError, ValueError):
                pass
        out.append((key, value, EXTRACTOR_FIELD_MAP))
    return out


_AI_SYSTEM = (
    "You read a COMPLETED internal support ticket for a multifamily "
    "digital-marketing agency and decide whether the work it describes changed "
    "any FACT about the property that its marketing profile should now record.\n\n"
    "RULES:\n"
    "- Only propose a value you can point to in the ticket text. Never infer, "
    "estimate, or invent a name, number, amount, or list item.\n"
    "- Propose ONLY fields from the allowed list given below. Skip everything else.\n"
    "- A field the ticket merely discusses is not a change. Propose only what the "
    "completed work actually established.\n"
    "- Values are stored verbatim on the property profile. Write the VALUE, not a "
    "sentence about it (e.g. 'The Maple at Foxwood', not 'the name was changed to "
    "The Maple at Foxwood').\n"
    "- Multi-item fields (amenities, competitors, taglines) use one item per line.\n"
    "- FAIR HOUSING: never propose a value referencing age, family status, race, "
    "religion, national origin, disability, schools, or school districts.\n"
    "- If nothing in the ticket changed a property fact, return an empty list. That "
    "is the expected answer for most tickets.\n\n"
    'Return ONLY JSON: {"proposals":[{"field_key":"…","value":"…"}]}'
)


def _extract_with_ai(task: dict, comments: list, ticket_type: str) -> list[tuple]:
    """(field_key, value, EXTRACTOR_AI) read from the ticket thread.

    Off unless TICKET_PROFILE_AI_EXTRACT=true. Bounded by the same per-type
    allowlist as the field map, and every proposal still goes to a human.
    """
    allow = allowed_fields(ticket_type)
    if not allow:
        return []
    try:
        from config import ANTHROPIC_API_KEY, CLAUDE_DIGEST_MODEL
        if not ANTHROPIC_API_KEY:
            logger.info("ticket_profile_sync: AI extract on but no ANTHROPIC_API_KEY")
            return []
        import anthropic

        narrative = ticket_recap._internal_narrative(task, comments)
        if not narrative.strip():
            return []
        catalog = []
        for key in allow:
            field = community_brief.FIELDS.get(key)
            if not field or not field.hs_override:
                continue
            line = f"- {key} ({field.label})"
            if field.options:
                line += " — one of: " + ", ".join(field.options)
            catalog.append(line)
        if not catalog:
            return []

        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model=CLAUDE_DIGEST_MODEL, max_tokens=800, temperature=0,
            system=_AI_SYSTEM,
            messages=[{"role": "user", "content":
                       "ALLOWED FIELDS:\n" + "\n".join(catalog) +
                       "\n\nTICKET:\n" + narrative}],
        )
        raw = "".join(b.text for b in msg.content
                      if getattr(b, "type", "") == "text").strip()
        m = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(m.group(0)) if m else {}
    except Exception as e:  # noqa: BLE001 — extraction is never load-bearing
        logger.warning("ticket_profile_sync: AI extraction failed: %s", e)
        return []

    out = []
    for row in (data.get("proposals") or []):
        if not isinstance(row, dict):
            continue
        key = str(row.get("field_key") or "").strip()
        if key in allow:
            out.append((key, row.get("value"), EXTRACTOR_AI))
    return out


# ── proposal building (pure) ─────────────────────────────────────────────────


def build_proposals(task: dict, comments: list, company_id: str,
                    property_uuid: str, ticket_type: str,
                    props: dict | None = None) -> list[dict]:
    """Candidate profile updates for one completed ticket. Writes nothing.

    Drops: excluded ticket types, fields not allow-listed for the type,
    non-editable / structured fields, values that don't validate, and
    proposals that match what the profile already shows (no-ops).
    """
    if ticket_type in ticket_recap.EXCLUDED_TYPES:
        return []
    if not allowed_fields(ticket_type):
        return []

    candidates = _extract_from_fields(task, ticket_type)
    if _ai_enabled():
        candidates += _extract_with_ai(task, comments or [], ticket_type)
    if not candidates:
        return []

    if props is None:
        props = community_brief.load_company_state(company_id) or {}

    task_id = str(task.get("id") or "")
    seen: set = set()
    out: list[dict] = []
    for field_key, raw_value, extractor in candidates:
        field = _proposable(field_key, ticket_type)
        if not field:
            continue
        value = _normalize(field, raw_value)
        if not value:
            continue

        # The one resolution rule — override > resolved > empty.
        current = community_brief.resolve_value(props, field.hs_resolved,
                                                field.hs_override)
        if value.strip() == (current or "").strip():
            continue  # no-op: the profile already shows this

        has_override = community_brief._nonblank(props.get(field.hs_override))
        has_resolved = bool(field.hs_resolved) and community_brief._nonblank(
            props.get(field.hs_resolved))
        source = "override" if has_override else ("pipeline" if has_resolved else "empty")

        # First extractor to reach a field wins; the deterministic map runs
        # first, so a mapped ClickUp field beats the model on the same field.
        if field_key in seen:
            continue
        seen.add(field_key)

        out.append({
            "proposal_id": proposal_id(task_id, field_key),
            "task_id": task_id,
            "company_id": str(company_id or ""),
            "property_uuid": str(property_uuid or ""),
            "ticket_type": ticket_type,
            "field_key": field_key,
            "field_label": field.label,
            "proposed_value": value,
            "current_value": current or "",
            "current_source": source,
            # Accepting this replaces a human's edit — the portal warns, and
            # nothing applies it without someone clicking Accept.
            "conflicts_with_override": source == "override",
            "extractor": extractor,
            "status": STATUS_PENDING,
            "actor": "",
            "note": "",
            "created_at": _now(),
        })
    return out


def propose_from_task(task: dict, company_id: str, property_uuid: str,
                      ticket_type: str, *, comments: list | None = None,
                      props: dict | None = None,
                      persist: bool = True) -> list[dict]:
    """Build (and by default store) the proposals for a completed ticket.

    Called from clickup_recap after a ticket matched EXACTLY ONE uuid-bearing
    company. A no-op unless TICKET_PROFILE_LOOP_ENABLED=true.
    """
    if not enabled():
        return []
    proposals = build_proposals(task, comments or [], company_id,
                                property_uuid, ticket_type, props=props)
    if proposals and persist:
        _append(proposals)
    return proposals


# ── store (BigQuery, append-only state log) ──────────────────────────────────


def _bq():
    """The BigQuery client module, or None when BigQuery isn't configured."""
    try:
        import bigquery_client
        if not bigquery_client.is_bigquery_configured():
            return None
        return bigquery_client
    except Exception as e:  # noqa: BLE001
        logger.warning("ticket_profile_sync: BigQuery unavailable: %s", e)
        return None


def _append(rows: list[dict]) -> bool:
    bq = _bq()
    if not bq:
        logger.info("ticket_profile_sync: no BigQuery — %d proposal(s) not stored",
                    len(rows))
        return False
    try:
        bq.insert_rows(_TABLE, rows)
        return True
    except Exception as e:  # noqa: BLE001 — storage must not break the recap path
        logger.warning("ticket_profile_sync: proposal write failed: %s", e)
        return False


_LATEST_SQL = """
    SELECT * EXCEPT(_rn) FROM (
      SELECT *, ROW_NUMBER() OVER (
          PARTITION BY proposal_id ORDER BY created_at DESC) AS _rn
      FROM `{project}.{dataset}.{table}`
      WHERE {where}
    )
    WHERE _rn = 1
    ORDER BY created_at DESC
    LIMIT @lim
"""


def _query_latest(where: str, strings: dict, limit: int) -> list[dict]:
    """Latest state row per proposal_id. `strings` are named STRING params.

    Everything — including building the query parameters — sits inside the
    try, so a missing BigQuery client degrades to an empty list rather than
    raising out of a page render.
    """
    bq = _bq()
    if not bq:
        return []
    try:
        from google.cloud import bigquery
        from config import BIGQUERY_PROJECT_ID
        sql = _LATEST_SQL.format(project=BIGQUERY_PROJECT_ID,
                                 dataset=bq._dataset(), table=_TABLE, where=where)
        params = [bigquery.ScalarQueryParameter(k, "STRING", str(v or ""))
                  for k, v in strings.items()]
        params.append(bigquery.ScalarQueryParameter("lim", "INT64", int(limit)))
        return bq.query(sql, params)
    except Exception as e:  # noqa: BLE001 — reads are best-effort
        logger.warning("ticket_profile_sync: proposal read failed: %s", e)
        return []


def _shape(row: dict) -> dict:
    created = row.get("created_at")
    return {
        "proposal_id": row.get("proposal_id"),
        "task_id": row.get("task_id"),
        "company_id": row.get("company_id"),
        "property_uuid": row.get("property_uuid") or "",
        "ticket_type": row.get("ticket_type"),
        "field_key": row.get("field_key"),
        "field_label": row.get("field_label"),
        "proposed_value": row.get("proposed_value"),
        "current_value": row.get("current_value"),
        "current_source": row.get("current_source"),
        "conflicts_with_override": bool(row.get("conflicts_with_override")),
        "extractor": row.get("extractor"),
        "status": row.get("status"),
        "actor": row.get("actor") or "",
        "created_at": created.isoformat() if hasattr(created, "isoformat") else created,
    }


def list_proposals(company_id: str, property_uuid: str = "",
                   status: str | None = STATUS_PENDING,
                   limit: int = 100) -> list[dict]:
    """Latest state of this property's proposals, newest first.

    `status=None` returns every proposal regardless of state (used by the
    ticket-history panel); the default returns only the pending ones.
    """
    if not (company_id or property_uuid):
        return []
    rows = _query_latest(
        "company_id = @cid OR (@uuid != '' AND property_uuid = @uuid)",
        {"cid": company_id, "uuid": property_uuid},
        limit,
    )
    out = [_shape(r) for r in rows]
    if status:
        out = [r for r in out if r.get("status") == status]
    return out


def get_proposal(proposal_id_value: str) -> dict | None:
    """The latest state row for one proposal, or None."""
    if not proposal_id_value:
        return None
    rows = _query_latest("proposal_id = @pid", {"pid": proposal_id_value}, 1)
    return _shape(rows[0]) if rows else None


class ProposalError(Exception):
    """A proposal couldn't be actioned. `status` is the HTTP status to return."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _transition(row: dict, status: str, actor: str, note: str = "") -> None:
    _append([{
        "proposal_id": row.get("proposal_id"),
        "task_id": row.get("task_id"),
        "company_id": row.get("company_id"),
        "property_uuid": row.get("property_uuid") or "",
        "ticket_type": row.get("ticket_type"),
        "field_key": row.get("field_key"),
        "field_label": row.get("field_label"),
        "proposed_value": row.get("proposed_value"),
        "current_value": row.get("current_value"),
        "current_source": row.get("current_source"),
        "conflicts_with_override": bool(row.get("conflicts_with_override")),
        "extractor": row.get("extractor"),
        "status": status,
        "actor": actor or "",
        "note": note or "",
        "created_at": _now(),
    }])


def accept(proposal_id_value: str, actor: str) -> dict:
    """Apply a proposal to the property profile, then record the acceptance.

    The write goes through `community_brief.write_field()` — the same path the
    portal's inline editor uses — so it lands on the field's override property,
    is audit-logged against `actor`, and reaches Fluency on the next sync.
    The status row is appended only after the write succeeds.
    """
    if not _bq():
        raise ProposalError(503, "proposal store unavailable")
    row = get_proposal(proposal_id_value)
    if not row:
        raise ProposalError(404, "proposal not found")
    if row.get("status") != STATUS_PENDING:
        raise ProposalError(409, f"proposal already {row.get('status')}")

    ok, message = community_brief.write_field(
        row["company_id"], row["field_key"], row["proposed_value"] or "",
        edited_by=actor,
    )
    if not ok:
        raise ProposalError(400, message)

    _transition(row, STATUS_ACCEPTED, actor)
    _emit_loop_event("profile_update_accepted", row, actor)
    return {**row, "status": STATUS_ACCEPTED, "actor": actor, "value": message}


def reject(proposal_id_value: str, actor: str, note: str = "") -> dict:
    """Dismiss a proposal. The profile is untouched."""
    if not _bq():
        raise ProposalError(503, "proposal store unavailable")
    row = get_proposal(proposal_id_value)
    if not row:
        raise ProposalError(404, "proposal not found")
    if row.get("status") != STATUS_PENDING:
        raise ProposalError(409, f"proposal already {row.get('status')}")
    _transition(row, STATUS_REJECTED, actor, note)
    _emit_loop_event("profile_update_rejected", row, actor)
    return {**row, "status": STATUS_REJECTED, "actor": actor}


def _emit_loop_event(event_type: str, row: dict, actor: str) -> None:
    """Best-effort Loop event. Never raises — analytics can't block a write."""
    try:
        import loop_writer
        loop_writer.record(
            "engage", event_type,
            property_uuid=row.get("property_uuid") or None,
            company_id=row.get("company_id") or None,
            source="ticket_profile_sync",
            source_id=row.get("task_id") or None,
            trigger="portal",
            payload={"field_key": row.get("field_key"),
                     "ticket_type": row.get("ticket_type"),
                     "extractor": row.get("extractor"),
                     "conflicts_with_override": bool(row.get("conflicts_with_override")),
                     "actor": actor},
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("ticket_profile_sync: loop event %s skipped: %s", event_type, e)


# ── profile → ticket history ─────────────────────────────────────────────────


def ticket_history(company_id: str, property_uuid: str = "",
                   limit: int = 25) -> list[dict]:
    """Tickets that touched this property, and what each changed on the profile.

    Uses the stored `task_id ↔ company_id` mapping (portal_tickets) — an exact
    lookup, never a fuzzy match. Each ticket carries the profile fields whose
    proposals were accepted from it, so the profile can show the request that
    produced a value.
    """
    import portal_tickets
    try:
        tickets = portal_tickets.list_tickets(company_id,
                                              property_uuid=property_uuid,
                                              limit=limit)
    except Exception as e:  # noqa: BLE001 — history is never load-bearing
        logger.warning("ticket_profile_sync: ticket history read failed for %s: %s",
                       company_id, e)
        tickets = []

    by_task: dict[str, list[dict]] = {}
    for p in list_proposals(company_id, property_uuid, status=None, limit=500):
        by_task.setdefault(str(p.get("task_id") or ""), []).append(p)

    out = []
    for t in tickets:
        related = by_task.get(str(t.get("id") or ""), [])
        out.append({
            **t,
            "profile_changes": [
                {"field_key": p["field_key"], "field_label": p["field_label"],
                 "value": p["proposed_value"], "actor": p["actor"]}
                for p in related if p.get("status") == STATUS_ACCEPTED
            ],
            "pending_proposals": sum(1 for p in related
                                     if p.get("status") == STATUS_PENDING),
        })
    return out
