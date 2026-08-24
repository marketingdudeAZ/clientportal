"""What needs your attention — one queue across every system that holds work.

The portal used to answer "what is outstanding?" in four places that disagreed:
`/api/needs-you` (onboarding + dispositions + aging triage rows), the ticket
page (ClickUp tasks THIS property filed through the portal), the Service Hub
list (HubSpot tickets), and ClickUp itself (everything filed straight into a
list, invisible to all three). Somebody had to open ClickUp to know the answer,
which is the thing this page exists to stop.

So this module is an aggregator, not a new source of truth. It composes:

  onboarding.list_onboarding()      properties coming online + what's missing
  disposition.list_dispositioning() retain-or-turn-off review
  triage.get_portfolio_triage()     the ranked ticket_aging / ticket_open rows
  clickup_client.get_tasks()        OPEN tasks in the creative / branding /
                                    digital lists (config.ATTENTION_TICKET_LISTS)
  portal_tickets.list_tickets()     ClickUp tasks filed through the portal
  ticket_manager.list_tickets()     HubSpot Service Hub tickets

Health-score triage rows are deliberately NOT here. Properties and the
Portfolio Dashboard already show them; repeating them turns an action inbox
into a second dashboard, which is what the triage list replaced.

Two rules the shape of this file follows from:

1. **Every source is independently try/excepted.** One dead integration must
   degrade one section, never blank the page. What is new is that a failure is
   NAMED in `degraded` rather than only logged: "no open creative tickets" and
   "we could not reach ClickUp" are opposite answers and used to render
   identically.

2. **Unattributed work is shown portfolio-wide and dropped when scoped.** A
   ClickUp task whose property we cannot determine is still real work, so the
   team sees it on the whole-portfolio queue. But it is never shown to a
   single-property caller, because "we don't know whose this is" must not
   resolve to "probably yours". See `_matches_scope`.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any

import clickup_client
import portal_tickets
from config import ATTENTION_TICKET_LISTS

logger = logging.getLogger(__name__)

# Matches triage.py's 10 minutes on purpose: the two feed the same screen, and
# a queue whose halves expire on different clocks shows a ticket in one section
# and not the other for minutes at a time.
_TTL = float(os.getenv("ATTENTION_CACHE_TTL", "600"))

# ClickUp pages list tasks 100 at a time. Three pages per list is 300 open
# tasks — well past what any of these lists actually carries — and bounds the
# worst case at 7 lists × 3 calls rather than "however many pages exist".
_MAX_PAGES = int(os.getenv("ATTENTION_CLICKUP_MAX_PAGES", "3"))

# Name resolution on the portfolio view costs one HubSpot read per distinct
# uuid the resolver hasn't cached. Bounded the same way onboarding bounds its
# asset checks: past the cap rows keep their uuid and lose only the display
# name, which degrades the row instead of the request.
_MAX_IDENTITY_LOOKUPS = int(os.getenv("ATTENTION_MAX_IDENTITY_LOOKUPS", "60"))

# ClickUp status *types* that mean "this is finished". `type` is the stable
# signal — every list renames its statuses, none of them renames the type.
_CLOSED_TYPES = {"closed", "done"}

_CATEGORY_ORDER = ("creative", "branding", "digital")

_cache: dict = {}
_lock = threading.Lock()


class ScopeUnresolved(LookupError):
    """A property scope was requested and could not be identified.

    An exception, not a None return, for the reason property_resolver states:
    absence and failure must not look alike. Here the stakes are higher than
    reporting — a caller that reads "unresolved" as "no filter" serves the
    whole portfolio to someone who asked for one property.
    """


def invalidate_cache() -> None:
    """Drop the cached ClickUp scan. Mirrors triage.invalidate_cache()."""
    with _lock:
        _cache.clear()


# ── configured sources ───────────────────────────────────────────────────────

def _list_id_for(src: dict) -> str:
    """The ClickUp list id for one attention source, env first."""
    env_name = src.get("list_env") or ""
    return ((os.getenv(env_name, "") or "").strip()
            or (src.get("list_id_default") or "").strip())


def sources() -> list:
    """Configured attention sources, in registry order.

    A source with no resolvable list id is omitted rather than erroring, so the
    queue lights up list-by-list as ids are filled in — same posture as
    `portal_tickets.configured_types`.
    """
    out = []
    for src in ATTENTION_TICKET_LISTS:
        list_id = _list_id_for(src)
        if not list_id:
            continue
        out.append({
            "key": src["key"],
            "category": src.get("category", "digital"),
            "label": src.get("label", src["key"]),
            "list_id": list_id,
        })
    return out


# ── ClickUp list scan ────────────────────────────────────────────────────────

def _is_open(task: dict) -> bool:
    """True when a ClickUp task is still outstanding.

    Belt and braces: ClickUp's `include_closed=false` already filters most of
    it, but lists carry custom "done-ish" statuses (Complete, Closed) whose
    type is `custom`, not `closed`. `client_status` is the same mapping the
    ticket page shows the client, so the queue and the ticket card can never
    disagree about whether something is finished.
    """
    status = task.get("status") or {}
    if (status.get("type") or "").strip().lower() in _CLOSED_TYPES:
        return False
    return portal_tickets.client_status(status.get("status") or "") != "Done"


def _task_uuid(task: dict) -> str:
    """The property uuid stamped on a ClickUp task, or "".

    Read off the TASK, never off `GET /list/{id}/field`. ClickUp custom fields
    are SPACE-level, so a list's field endpoint returns the union for the whole
    space — the presence of a `uuid` definition there says nothing about
    whether THIS list carries it, and matching a field id resolved that way can
    land on a same-named field belonging to a different list. A task's own
    `custom_fields` array is list-scoped by construction, so it is the only
    honest answer. (portal_tickets.form_schema resolves per list for the same
    reason.)
    """
    for name in ("uuid", "UUID"):
        try:
            value = clickup_client.custom_field_value(task, name)
        except Exception:  # noqa: BLE001 — a malformed task is not a 500
            value = None
        if value:
            return str(value).strip()
    return ""


def _age_days(created_ms: Any) -> "int | None":
    try:
        created = datetime.fromtimestamp(int(created_ms) / 1000, tz=timezone.utc)
    except (TypeError, ValueError):
        return None
    return max(0, (datetime.now(timezone.utc) - created).days)


def _row_from_task(task: dict, src: dict, mapping: "dict | None") -> dict:
    """One ClickUp task → a unified queue row."""
    mapping = mapping or {}
    raw_status = ((task.get("status") or {}).get("status")) or ""
    created = task.get("date_created")
    assignees = [a.get("username") or a.get("email") or ""
                 for a in (task.get("assignees") or [])]
    return {
        "system": "clickup",
        "id": str(task.get("id") or ""),
        "source": src["key"],
        "source_label": src["label"],
        "category": src["category"],
        "subject": task.get("name") or "",
        "status": portal_tickets.client_status(raw_status),
        "raw_status": raw_status,
        "priority": ((task.get("priority") or {}) or {}).get("priority") or "",
        "assignees": [a for a in assignees if a],
        "submitted_by": mapping.get("submitted_by") or "",
        "company_id": str(mapping.get("company_id") or ""),
        "uuid": str(mapping.get("property_uuid") or "") or _task_uuid(task),
        "property_name": "",
        "market": "",
        "created_ts": int(created) if created else None,
        "age_days": _age_days(created),
        "url": task.get("url"),
        "unresolved": False,
    }


def _scan_list(src: dict) -> list:
    """Every OPEN task in one attention list, paginated under a page cap."""
    tasks: list = []
    for page in range(_MAX_PAGES):
        batch = clickup_client.get_tasks(src["list_id"], params={
            "page": page,
            "subtasks": "false",
            "include_closed": "false",
            "order_by": "created",
            "reverse": "true",
        })
        if not batch:
            break
        tasks.extend(batch)
        if len(batch) < 100:      # ClickUp's page size; a short page is the last
            break
    else:
        logger.warning("attention: hit the %d-page cap on list %s (%s) — "
                       "older open tasks are not on the queue",
                       _MAX_PAGES, src["list_id"], src["key"])
    return [t for t in tasks if _is_open(t)]


def scan_open_tickets(force: bool = False) -> dict:
    """{"rows": [...], "degraded": [source keys]} — open work across all lists.

    Cached portfolio-wide and filtered per request, not cached per scope: the
    expensive half (ClickUp + the mapping read) does not vary by who is asking,
    and a per-scope cache would multiply that cost by the number of viewers.
    """
    configured = sources()
    if not configured:
        return {"rows": [], "degraded": [], "sources": []}

    now = time.monotonic()
    with _lock:
        hit = _cache.get("scan")
        if hit and not force and (now - hit[0]) < _TTL:
            return hit[1]

    rows: list = []
    degraded: list = []
    for src in configured:
        try:
            tasks = _scan_list(src)
        except Exception as e:  # noqa: BLE001 — one dead list ≠ a blank queue
            logger.warning("attention: list scan failed for %s (%s): %s",
                           src["key"], src["list_id"], e)
            degraded.append(src["key"])
            continue
        rows.append((src, tasks))

    # One BigQuery read for every task on the queue, not one per task.
    flat = [(src, t) for src, tasks in rows for t in tasks]
    mappings = {}
    try:
        mappings = portal_tickets.mappings_for_tasks([str(t.get("id") or "")
                                                      for _, t in flat])
    except Exception as e:  # noqa: BLE001 — attribution is best-effort
        logger.warning("attention: ticket→property mapping unavailable: %s", e)
        degraded.append("ticket_mapping")

    out = [_row_from_task(t, src, mappings.get(str(t.get("id") or "")))
           for src, t in flat]
    out.sort(key=lambda r: r.get("created_ts") or 0, reverse=True)
    payload = {
        "rows": out,
        "degraded": degraded,
        "sources": [{"key": s["key"], "category": s["category"], "label": s["label"]}
                    for s in configured],
    }
    with _lock:
        _cache["scan"] = (now, payload)
    return payload


# ── identity ─────────────────────────────────────────────────────────────────

def resolve_scope(identifier: str) -> "dict | None":
    """`company_id` or `uuid` → {company_id, uuid, name, market}, or None.

    Goes through the Layer 2 Property Resolver rather than adding a twelfth
    inline lookup (see skills/property_resolver.py's own docstring for the list
    it is retiring). R1-safe: read only, `uuid` is never written.

    None means "we could not identify this property". Callers must treat that
    as a hard stop when scoping, never as "show everything".
    """
    ident = str(identifier or "").strip()
    if not ident:
        return None
    try:
        from skills import property_resolver
        identity = property_resolver.resolve(ident)
    except Exception as e:  # noqa: BLE001 — includes PropertyNotFound
        logger.info("attention: could not resolve scope %r: %s", ident, e)
        return None
    return {
        "company_id": identity.company_id or "",
        "uuid": identity.uuid or "",
        "name": identity.name or "",
        "market": identity.market or "",
    }


def _enrich_names(rows: list) -> None:
    """Fill property_name/market on rows that carry a uuid. Best-effort, capped."""
    unknown = []
    for r in rows:
        if r.get("uuid") and not r.get("property_name") and r["uuid"] not in unknown:
            unknown.append(r["uuid"])
    if not unknown:
        return
    identities = {}
    try:
        from skills import property_resolver
        identities = property_resolver.resolve_many(unknown[:_MAX_IDENTITY_LOOKUPS],
                                                    kind="uuid")
    except Exception as e:  # noqa: BLE001 — a nameless row still shows the work
        logger.warning("attention: name enrichment unavailable: %s", e)
        return
    for r in rows:
        ident = identities.get(r.get("uuid") or "")
        if not ident:
            continue
        r["property_name"] = ident.name or ""
        r["market"] = ident.market or ""
        if not r.get("company_id"):
            r["company_id"] = ident.company_id or ""


def _matches_scope(row: dict, scope: dict) -> bool:
    """True only when the row is PROVABLY this property's.

    A row with neither a company_id nor a uuid is unattributed work. It belongs
    on the portfolio queue and nowhere else: defaulting an unknown owner into
    the caller's own bucket is precisely the cross-property leak this endpoint
    must not have.
    """
    cid, uuid = scope.get("company_id") or "", scope.get("uuid") or ""
    if cid and str(row.get("company_id") or "") == cid:
        return True
    if uuid and str(row.get("uuid") or "") == uuid:
        return True
    return False


# ── unified ticket view (HubSpot Service Hub + ClickUp) ──────────────────────

def _row_from_hubspot(ticket: dict) -> dict:
    """One HubSpot Service Hub ticket → the same unified row shape.

    The stage label goes through `portal_tickets.client_status` so both systems
    speak ONE status vocabulary. Without it the queue shows "New" next to
    "Open" and "Closed" next to "Done" for states that are the same state, and
    the reader has to know which system a row came from to read it.
    """
    created = ticket.get("created_at") or ""
    created_ms = None
    if created:
        try:
            dt = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            created_ms = int(dt.timestamp() * 1000)
        except (TypeError, ValueError):
            created_ms = None
    return {
        "system": "hubspot",
        "id": str(ticket.get("id") or ""),
        "source": "service_hub",
        "source_label": "Service Hub",
        "category": "digital",
        "subject": ticket.get("subject") or "",
        "status": portal_tickets.client_status(ticket.get("stage_label") or ""),
        "raw_status": ticket.get("stage_label") or "",
        "priority": ticket.get("priority") or "",
        "assignees": [ticket["owner_name"]] if ticket.get("owner_name") else [],
        "submitted_by": ticket.get("submitter_email") or "",
        "company_id": "",
        "uuid": "",
        "property_name": "",
        "market": "",
        "created_ts": created_ms,
        "age_days": _age_days(created_ms),
        "url": None,
        "unresolved": False,
    }


def _row_from_portal_ticket(ticket: dict) -> dict:
    """One `portal_tickets.list_tickets` row → the unified row shape."""
    key = ticket.get("type") or ""
    category = next((s["category"] for s in ATTENTION_TICKET_LISTS
                     if s["key"] == key), "digital")
    return {
        "system": "clickup",
        "id": str(ticket.get("id") or ""),
        "source": key or "portal_ticket",
        "source_label": ticket.get("type_label") or "Request",
        "category": category,
        "subject": ticket.get("subject") or "",
        "status": ticket.get("status") or "",
        "raw_status": ticket.get("raw_status") or "",
        "priority": "",
        "assignees": [],
        "submitted_by": ticket.get("submitted_by") or "",
        "company_id": "",
        "uuid": "",
        "property_name": "",
        "market": "",
        "created_ts": ticket.get("created_ts"),
        "age_days": ticket.get("age_days"),
        "url": ticket.get("url"),
        # Carried through, not dropped: a request we know exists but whose live
        # state ClickUp would not give us must render as unknown, never as absent.
        "unresolved": bool(ticket.get("unresolved")),
    }


def _merge(base: list, extra: list) -> list:
    """Union two row lists on (system, id), filling blanks rather than replacing.

    The same ClickUp task legitimately arrives twice — once from scanning its
    list, once from the portal's own mapping table — and each side knows
    something the other does not (the scan has assignees and category; the
    mapping has who filed it and which property). Dropping either duplicate
    loses real information, so they are merged field by field.
    """
    index = {}
    out = []
    for row in list(base) + list(extra):
        key = (row.get("system"), row.get("id"))
        seen = index.get(key)
        if seen is None:
            index[key] = row
            out.append(row)
            continue
        for field, value in row.items():
            if value in (None, "", [], False) or field == "unresolved":
                continue
            if seen.get(field) in (None, "", []):
                seen[field] = value
        # Unresolved is sticky only while NOTHING resolved it: a live scan row
        # is proof the task is readable, so it clears the placeholder flag.
        if not row.get("unresolved"):
            seen["unresolved"] = False
    return out


def property_tickets(company_id: str = "", property_uuid: str = "",
                     include_closed: bool = False, limit: int = 50) -> dict:
    """Every open ticket for ONE property, whichever system holds it.

    HubSpot Service Hub and ClickUp are both queried; the caller gets one list
    and does not need to know which system a row came from. Both sources are
    independently try/excepted — a HubSpot outage must not hide the ClickUp
    work, and vice versa.
    """
    scope = {"company_id": str(company_id or ""), "uuid": str(property_uuid or "")}
    rows: list = []
    degraded: list = []

    if scope["company_id"]:
        try:
            import ticket_manager
            rows += [_row_from_hubspot(t) for t in
                     ticket_manager.list_tickets(scope["company_id"],
                                                 include_closed=include_closed)]
        except Exception as e:  # noqa: BLE001
            logger.warning("attention: Service Hub tickets failed for %s: %s",
                           scope["company_id"], e)
            degraded.append("service_hub")

    # The Service Hub rows have no property columns of their own — they were
    # fetched BY company, so the association is the attribution.
    for r in rows:
        r["company_id"] = r["company_id"] or scope["company_id"]
        r["uuid"] = r["uuid"] or scope["uuid"]

    portal_rows: list = []
    try:
        portal_rows = [_row_from_portal_ticket(t) for t in portal_tickets.list_tickets(
            scope["company_id"], property_uuid=scope["uuid"], limit=limit)]
        for r in portal_rows:
            r["company_id"] = scope["company_id"]
            r["uuid"] = scope["uuid"]
        if not include_closed:
            portal_rows = [r for r in portal_rows if r["status"] != "Done"]
    except Exception as e:  # noqa: BLE001
        logger.warning("attention: portal tickets failed for %s: %s",
                       scope["company_id"], e)
        degraded.append("portal_tickets")

    # Anything in the watched lists that belongs to this property but was filed
    # straight into ClickUp — the work that was invisible to every other view.
    scanned: list = []
    try:
        scan = scan_open_tickets()
        degraded += [d for d in scan["degraded"] if d not in degraded]
        # Copied, not referenced: `_merge` fills blanks in place and these rows
        # live in the module cache, so mutating them would leak one caller's
        # enrichment into the next caller's queue.
        scanned = [dict(r) for r in scan["rows"] if _matches_scope(r, scope)]
    except Exception as e:  # noqa: BLE001
        logger.warning("attention: list scan failed for %s: %s",
                       scope["company_id"], e)
        degraded.append("clickup_lists")

    merged = _merge(rows + scanned, portal_rows)
    merged.sort(key=lambda r: r.get("created_ts") or 0, reverse=True)
    return {"rows": merged, "degraded": degraded, "count": len(merged)}


# ── the aggregate ────────────────────────────────────────────────────────────

def _by_category(rows: list) -> dict:
    counts = {c: 0 for c in _CATEGORY_ORDER}
    for r in rows:
        counts[r.get("category", "digital")] = counts.get(r.get("category", "digital"), 0) + 1
    return counts


def build(scope_identifier: str = "", force: bool = False) -> dict:
    """The attention payload: onboarding + dispositions + aging + open work.

    `scope_identifier` is a company_id or a uuid. Supplied, every section is
    filtered to that property and unattributed work is dropped. Omitted, the
    payload is portfolio-wide — which is the whole team's view of the queue and
    is exactly what `/api/needs-you` has always returned.

    Note what scoping is NOT: it is attribution, not authorization. Nothing
    here verifies the caller may see the property they named. That gate is
    `require_company_access()` (Workstream A) and belongs at the route.
    """
    scope = resolve_scope(scope_identifier) if scope_identifier else None
    if scope_identifier and not scope:
        # A scope was ASKED FOR and could not be resolved. Falling through to
        # the portfolio-wide branch here would hand one property's caller the
        # entire portfolio — a resolver hiccup must never widen access.
        raise ScopeUnresolved(scope_identifier)
    onboarding_rows: list = []
    dispo_rows: list = []
    aging_rows: list = []
    degraded: list = []

    try:
        import onboarding
        onboarding_rows = onboarding.list_onboarding() or []
    except Exception as e:  # noqa: BLE001
        logger.warning("attention: onboarding failed: %s", e)
        degraded.append("onboarding")

    try:
        import disposition
        dispo_rows = disposition.list_dispositioning() or []
    except Exception as e:  # noqa: BLE001
        logger.warning("attention: dispositions failed: %s", e)
        degraded.append("dispositions")

    try:
        import triage as _tri
        rows = (_tri.get_portfolio_triage(force=force) or {}).get("rows", [])
        # Health-score rows are dropped ON PURPOSE. Properties and the
        # Portfolio Dashboard already surface them, and an action inbox that
        # repeats the dashboard stops being an action inbox.
        aging_rows = [r for r in rows
                      if r.get("reason_kind") in ("ticket_aging", "ticket_open")]
    except Exception as e:  # noqa: BLE001
        logger.warning("attention: triage failed: %s", e)
        degraded.append("triage")

    work_rows: list = []
    work_sources: list = []
    try:
        scan = scan_open_tickets(force=force)
        # Copies — see the note in property_tickets; these rows are cached and
        # both the scope branch and _enrich_names write to them.
        work_rows = [dict(r) for r in scan["rows"]]
        work_sources = scan["sources"]
        degraded += [d for d in scan["degraded"] if d not in degraded]
    except Exception as e:  # noqa: BLE001
        logger.warning("attention: open ticket scan failed: %s", e)
        degraded.append("clickup_lists")

    if scope:
        cid, uuid = scope["company_id"], scope["uuid"]
        onboarding_rows = [r for r in onboarding_rows
                           if str(r.get("company_id") or "") == cid
                           or str(r.get("uuid") or "") == uuid]
        dispo_rows = [r for r in dispo_rows
                      if str(r.get("company_id") or "") == cid
                      or str(r.get("uuid") or "") == uuid]
        aging_rows = [r for r in aging_rows
                      if str(r.get("property_id") or "") == cid
                      or str(r.get("uuid") or "") == uuid]
        work_rows = [r for r in work_rows if _matches_scope(r, scope)]
        try:
            hub = property_tickets(company_id=cid, property_uuid=uuid)
            work_rows = _merge(work_rows, hub["rows"])
            degraded += [d for d in hub["degraded"] if d not in degraded]
        except Exception as e:  # noqa: BLE001
            logger.warning("attention: unified ticket view failed for %s: %s", cid, e)
            degraded.append("unified_tickets")
        for r in work_rows:
            r["property_name"] = r.get("property_name") or scope["name"]
            r["market"] = r.get("market") or scope["market"]
        work_rows.sort(key=lambda r: r.get("created_ts") or 0, reverse=True)
    else:
        _enrich_names(work_rows)

    return {
        # The three keys /api/needs-you has always returned, unchanged. The
        # portal template reads them by name (client-portal.html loadTriage).
        "onboarding": onboarding_rows,
        "dispositions": dispo_rows,
        "attention": aging_rows,
        "work": {
            "rows": work_rows,
            "total": len(work_rows),
            "by_category": _by_category(work_rows),
            "sources": work_sources,
        },
        "summary": {
            "onboarding": len(onboarding_rows),
            "dispositions": len(dispo_rows),
            "aging_tickets": len(aging_rows),
            "open_work": len(work_rows),
        },
        # Named, not just logged: an empty section because a source is down and
        # an empty section because there is nothing to do are opposite answers.
        "degraded": degraded,
        "scope": scope,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
