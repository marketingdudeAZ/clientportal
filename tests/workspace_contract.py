"""The Workspace API contract as checkable shapes.

Mirrors the JSON in docs/handoffs/PORTAL_WORKSPACE_BUILD_PLAN.md: the original
"API contract", the "Phase 2 contract" with its amendments, and the recorded
"Contract changes (API)". The API tests check live responses against these
shapes; after the UI branch merges, the same shapes check
`tests/fixtures/workspace/*.json`:

    import workspace_contract as contract
    contract.assert_shape(payload, "work")
    assert contract.numbers_without_source(payload) == []

Spec language, deliberately tiny so fixtures and responses read the same way:
    STR, INT, NUM, BOOL, ANY           leaf types (bool is never a number)
    opt(spec)                          may be null
    enum("a", "b")                     one of these strings
    [spec]                             list of spec
    {"key": spec}                      dict with these REQUIRED keys; extra keys
                                       are allowed, so additive changes pass
    absent("key")                      in a dict spec: the key must NOT be present
"""

from __future__ import annotations

from typing import Any

STR, INT, NUM, BOOL, ANY = "str", "int", "num", "bool", "any"


class opt:  # noqa: N801 — reads as a type in the specs below
    def __init__(self, spec: Any):
        self.spec = spec


class enum:  # noqa: N801
    def __init__(self, *values: str):
        self.values = values


class absent:  # noqa: N801
    def __init__(self, key: str):
        self.key = key


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def check(value: Any, spec: Any, path: str = "$") -> list[str]:
    """Every way `value` departs from `spec`, as "path: problem" strings."""
    if isinstance(spec, opt):
        return [] if value is None else check(value, spec.spec, path)
    if value is None:
        return [f"{path}: is null"]
    if isinstance(spec, enum):
        return [] if value in spec.values else [f"{path}: {value!r} not in {spec.values}"]
    if spec == ANY:
        return []
    if spec == STR:
        return [] if isinstance(value, str) else [f"{path}: expected string, got {type(value).__name__}"]
    if spec == BOOL:
        return [] if isinstance(value, bool) else [f"{path}: expected bool, got {type(value).__name__}"]
    if spec == INT:
        ok = isinstance(value, int) and not isinstance(value, bool)
        return [] if ok else [f"{path}: expected int, got {type(value).__name__}"]
    if spec == NUM:
        return [] if _is_num(value) else [f"{path}: expected number, got {type(value).__name__}"]
    if isinstance(spec, list):
        if not isinstance(value, list):
            return [f"{path}: expected list, got {type(value).__name__}"]
        errs: list[str] = []
        for i, v in enumerate(value):
            errs += check(v, spec[0], f"{path}[{i}]")
        return errs
    if isinstance(spec, dict):
        if not isinstance(value, dict):
            return [f"{path}: expected object, got {type(value).__name__}"]
        errs = []
        for key, sub in spec.items():
            if isinstance(sub, absent):
                if sub.key in value:
                    errs.append(f"{path}.{sub.key}: must not be present")
                continue
            if key not in value:
                errs.append(f"{path}.{key}: missing")
            else:
                errs += check(value[key], sub, f"{path}.{key}")
        return errs
    raise TypeError(f"bad spec at {path}: {spec!r}")


# ── shared pieces ────────────────────────────────────────────────────────────

SOURCES = enum("hubdb_rec", "loop_rec", "call_prep", "content_brief", "video_variant",
               "ticket_profile", "onboarding_gap", "portal_ticket", "service_ticket")
LENSES = enum("express", "tailor", "amplify", "evolve")
STATUSES = enum("to_do", "in_motion", "done")
KINDS = enum("auto", "queued", "person")
REASONS = enum("wrong_data", "already_handled", "not_priority", "discuss_on_call")
VISIBILITY = enum("client", "internal")

# Phase 2 amendment 1: {message, field?, source?}.
GAP = {"message": STR, "field": absent("reason")}
METRIC = {"value": NUM, "source": STR, "as_of": opt(STR)}
RECEIPT = {"label": STR, "source": STR, "as_of": opt(STR)}
STEP = {"when": opt(STR), "label": STR, "channel": opt(STR), "kind": KINDS, "status": STR}
TRAIL = {"at": opt(STR), "actor": opt(STR), "text": STR, "visibility": VISIBILITY}
NOTE = {"at": opt(STR), "actor": opt(STR), "text": STR, "visibility": VISIBILITY}
EVIDENCE = {"columns": [STR], "rows": [[ANY]], "more_count": INT}
SPARKLINE = {"label": STR, "points": [{"x": ANY, "y": NUM}], "highlight_index": opt(INT)}
ACTION_LINK = {"label": STR, "href": STR}

ITEM = {
    "id": STR, "source": SOURCES, "source_id": STR, "title": STR,
    "found": opt(STR), "expect": opt(STR), "if_skip": opt(STR),
    "receipts": [RECEIPT], "evidence": opt(EVIDENCE), "sparkline": opt(SPARKLINE),
    "channels": [STR], "lens": LENSES,
    "start_by": opt(STR), "due": opt(STR), "status": STATUSES,
    "needs_approval": BOOL, "client_visible": BOOL,
    "internal_only": absent("internal_only"),
    "owner": opt(STR), "comments_count": INT, "cost_note": opt(STR),
    "steps": opt([STEP]), "trail": [TRAIL], "notes": [NOTE],
    "actions": {"approve": BOOL, "not_now": BOOL},
}

ME = {
    "email": STR, "role": enum("internal", "client"), "verified": BOOL, "can_decide": BOOL,
    "companies": [{"company_id": STR, "uuid": opt(STR), "name": opt(STR),
                   "city": opt(STR), "state": opt(STR), "units": opt(INT)}],
}

PORTFOLIO = {
    "as_of": STR, "view": enum("needs_me", "all"), "scope_label": STR,
    "property_count": INT, "item_count": INT, "starting_this_week": INT,
    "properties": [{
        "company_id": STR, "name": opt(STR), "city": opt(STR), "state": opt(STR),
        "units": opt(INT), "occupancy": opt(METRIC),
        "top_item": opt({"id": STR, "title": STR, "needs_approval": BOOL}), "more_items": INT,
        "start_by": opt(STR), "overdue": BOOL, "units_at_risk": opt(METRIC),
    }],
    "quiet_count": INT, "gaps": [GAP],
}

WORK = {
    "summary": {"open": INT, "late": INT, "needs_approval": INT, "next_deadline": opt(STR)},
    "counts": {"to_do": INT, "in_motion": INT, "done": INT},
    "groups": {"late": [ITEM], "this_week": [ITEM], "later": {"count": INT, "titles": [STR]}},
    "hidden_count": INT, "gaps": [GAP],
}

DECISION_REQUEST = {"company_id": STR, "action": enum("approve", "not_now"), "reason": opt(REASONS)}

UNDO = {"available": BOOL, "until": opt(STR), "reason": opt(STR)}
RECORD = {"label": STR, "approved_unedited": INT, "total": INT, "threshold": opt(NUM), "pct": NUM}

DECISION = {
    "item": ITEM, "decided_by": STR, "decided_at": STR,
    "in_motion": [{"label": STR, "kind": KINDS, "status": STR, "detail": opt(STR),
                   "note": opt(STR), "action": opt(ACTION_LINK)}],
    "written_down": STR,
    "record": opt(RECORD),
    "undo": UNDO,
    "check_back": opt({"date": STR, "text": STR}),
}

UNDO_RESULT = {"item": ITEM, "undone": BOOL}
NOT_UNDOABLE = {"error": enum("not_undoable"), "reason": STR}

PROPERTY = {
    "name": opt(STR), "address": opt(STR), "domain": opt(STR), "managed_since": opt(STR),
    "hubspot_url": opt(STR), "brief_edit_url": opt(STR),
    "brief": {"text": opt(STR), "curated": BOOL, "edited_by": opt(STR), "edited_at": opt(STR)},
    "floorplans": [{"code": opt(STR), "beds": opt(NUM), "sqft": opt(NUM), "available": opt(NUM)}],
    "people": [{"name": STR, "role": STR}],
    "connections": [{"name": STR, "status": STR, "synced_at": opt(STR)}],
    "gaps": [GAP],
}

PERFORMANCE = {
    "occupied": opt({"value": NUM, "units": opt(INT), "total": opt(INT), "target": opt(NUM),
                     "source": STR, "as_of": opt(STR)}),
    "available_now": opt({"value": NUM, "stale_90_plus": opt(INT), "source": STR, "as_of": opt(STR)}),
    "coming_open_90d": opt({"value": NUM, "one_bed": opt(INT), "source": STR, "as_of": opt(STR)}),
    "coming_by_week": [{"week_start": STR, "one_bed": INT, "two_bed": INT, "other": INT}],
    "monthly_plan": opt({"value": NUM, "by_channel": ANY, "source": STR, "as_of": opt(STR)}),
    "note": opt(STR), "gaps": [GAP],
}

PLAN = {
    "monthly_total": opt(NUM), "channel_count": INT, "pending_changes": opt(INT),
    "channels": [{"channel": STR, "monthly": NUM, "share": opt(NUM), "cost_per_lease": opt(NUM),
                  "pointed_at": opt(STR), "status": enum("running", "pending", "ended", "paused"),
                  "status_note": opt(STR)}],
    "caveat": STR, "gaps": [GAP],
}

_CLIENT_ROW = {"date": opt(STR), "title": STR, "note": opt(STR), "status": STR}
CLIENT_VIEW = {
    "changing": [_CLIENT_ROW], "done_this_quarter": [_CLIENT_ROW],
    "done_count": INT, "hidden_open_count": absent("hidden_open_count"), "gaps": [GAP],
}

SIGNAL = {
    "id": STR, "company_id": STR, "property_name": opt(STR),
    "kind": enum("occupancy_drop", "stale_inventory", "lease_wave", "lead_drop", "spend_pacing",
                 "reputation_drop", "tracking_break", "data_stale"),
    "severity": enum("high", "medium", "low"), "title": STR, "detail": STR,
    "metric": opt(METRIC), "change": opt({"from": NUM, "to": NUM, "window_days": opt(INT)}),
    "detected_at": STR, "work_item_id": opt(STR),
}
SIGNALS = {"as_of": STR, "counts": {"high": INT, "medium": INT, "low": INT},
           "signals": [SIGNAL], "gaps": [GAP]}
START_WORK = {"work_item_id": STR}

DRAFT_TICKET = {
    "draft_id": STR, "title": STR,
    "category": enum("creative", "web", "paid", "seo", "listing", "reputation", "other"),
    "team": opt(STR), "needed_by": opt(STR), "needed_by_reason": opt(STR),
    "attached_context": [STR], "warnings": [STR],
}
DRAFT = {"tickets": [DRAFT_TICKET], "gaps": [GAP]}
FILED = {"created": [{"draft_id": STR, "work_item_id": STR, "clickup_task_id": STR}],
         "failed": [{"draft_id": STR, "reason": STR}]}
RECENT = {"recent": [{"title": STR, "status": enum("done", "in_progress", "new"),
                      "status_date": opt(STR), "work_item_id": STR}]}

SEARCH = {"results": [{"type": enum("property", "work_item", "report", "question"), "id": STR,
                       "title": STR, "subtitle": opt(STR), "company_id": opt(STR), "href": STR}]}

ERROR = {"error": STR}

SHAPES = {
    "me": ME, "portfolio": PORTFOLIO, "work": WORK, "item": ITEM, "decision": DECISION,
    "decision_request": DECISION_REQUEST, "undo_result": UNDO_RESULT, "not_undoable": NOT_UNDOABLE,
    "property": PROPERTY, "performance": PERFORMANCE, "plan": PLAN, "client_view": CLIENT_VIEW,
    "signals": SIGNALS, "start_work": START_WORK, "draft": DRAFT, "filed": FILED, "recent": RECENT,
    "search": SEARCH, "error": ERROR,
}


def assert_shape(value: Any, name: str) -> None:
    errs = check(value, SHAPES[name])
    assert not errs, f"{name} breaks the workspace contract:\n  " + "\n  ".join(errs)


# ── "every number carries a receipt" ─────────────────────────────────────────

# Numbers the contract carries bare: tallies of workspace items, paging and
# screen parameters, decision-record tallies, not measurements of a property.
COUNT_KEYS = frozenset({
    "open", "late", "to_do", "in_motion", "done", "count", "hidden_count", "needs_approval",
    "property_count", "item_count", "starting_this_week", "more_items", "quiet_count",
    "channel_count", "pending_changes", "done_count", "comments_count", "range",
    "page", "page_size", "next_page", "high", "medium", "low", "more_count",
    "approved_unedited", "total", "pct", "threshold", "highlight_index",
})


def numbers_without_source(obj: Any, path: str = "$", sourced: bool = False) -> list[str]:
    """Paths of numbers with no receipt.

    A number is sourced when it, or any object above it, sits beside a `source`
    key, or when its own object carries `<key>_source`. Tallies in COUNT_KEYS are
    exempt. Evidence tables and sparklines sit inside items whose receipts name
    their sources, so their cells are exempt too.
    """
    out: list[str] = []
    if isinstance(obj, dict):
        here = sourced or "source" in obj
        for k, v in obj.items():
            p = f"{path}.{k}"
            if k in ("evidence", "sparkline"):
                continue
            if _is_num(v):
                if not (here or k in COUNT_KEYS or f"{k}_source" in obj):
                    out.append(p)
            else:
                out += numbers_without_source(v, p, here)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if _is_num(v) and not sourced:
                out.append(f"{path}[{i}]")
            else:
                out += numbers_without_source(v, f"{path}[{i}]", sourced)
    return out
