"""Workspace decisions and undo.

`POST /api/workspace/work/<id>/decision` dispatches one decision to the source's
EXISTING handler as a module call (never an HTTP self-call), then writes a
`workspace_decision` loop event under the source's existing stage.

| source         | approve                                    | not now                                  |
|----------------|--------------------------------------------|------------------------------------------|
| hubdb_rec      | approval_agent.route_approval              | approval_agent._update_rec_status +      |
|                |                                            | _log_hubspot_activity (= /api/dismiss)   |
| loop_rec       | routes.loop.record_recommendation_approved | routes.loop.record_recommendation_rejected |
| call_prep      | approval_actions.callprep_approve          | approval_actions.callprep_dismiss        |
| content_brief  | routes.seo.approve_content_brief           | loop event only (no dismissed state)     |
| video_variant  | approval_actions.approve_video_variants    | loop event only (no dismissed state)     |
| ticket_profile | ticket_profile_sync.accept                 | ticket_profile_sync.reject               |

`POST /api/workspace/work/<id>/undo` reverses a decision for 10 minutes, only
for the person who made it, only where the source has a safe reverse:

| source         | undo "approve"                              | undo "not now"                          |
|----------------|---------------------------------------------|-----------------------------------------|
| hubdb_rec      | 409: a draft deal and ClickUp tasks exist   | card status back to pending             |
| loop_rec       | `recommendation_undone` loop event          | `recommendation_undone` loop event      |
| call_prep      | 409: a ClickUp task exists                  | rec status back to pending              |
| content_brief  | 409: the content team has a ClickUp task    | nothing to reverse (brief stays a draft)|
| video_variant  | 409: written to the asset library           | nothing to reverse                      |
| ticket_profile | 409: the profile was written                | proposal back to proposed               |

Money: nothing here moves spend or writes to Google Ads, Fluency or a rent roll.
The only handler that touches budget is `route_approval` for `budget_change` /
`package_upgrade`, which drafts a HubSpot deal for a human signature (its
existing behavior). Budget and spend decisions carry `requires_signature: true`
in the loop event. A loop recommendation approval records an event and nothing
more: no consumer on main turns `recommendation_approved` into a spend change
(loop_autopilot's hook is a stub that logs).

Undo state: the decider and time are held in-process for the undo window, with
the `workspace_decision` loop events as the fallback after a restart when
BigQuery is available.
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from skills import workspace_common as wc
from skills import workspace_inbox as wi

logger = logging.getLogger(__name__)

ACTIONS = ("approve", "not_now")
UNDO_WINDOW = timedelta(minutes=10)

# What an approval leaves the item as, when the handler finishes the job itself.
_APPROVED_STATUS = {"video_variant": "done", "ticket_profile": "done"}

_REC_TYPES = ("budget_change", "strategy_change", "package_upgrade")


class DecisionError(Exception):
    def __init__(self, status: int, message: str, detail: str | None = None,
                 reason: str | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.detail = detail
        self.reason = reason

    def body(self) -> dict:
        out = {"error": self.message}
        if self.detail:
            out["detail"] = self.detail
        if self.reason:
            out["reason"] = self.reason
        return out


def validate(action: Any, reason: Any) -> None:
    if action not in ACTIONS:
        raise DecisionError(400, "Invalid action", "approve|not_now")
    if action == "not_now" and not reason:
        raise DecisionError(400, "A reason is required for not now", "|".join(wi.REASONS))
    if reason is not None and reason not in wi.REASONS:
        raise DecisionError(400, "Invalid reason", "|".join(wi.REASONS))


# ── in_motion rows ───────────────────────────────────────────────────────────

_DEAL = re.compile(r"HubSpot Deal created: (\w+)")
_CLICKUP = re.compile(r"ClickUp .*task created: (\w+)")


def _motion(label: str, kind: str, status: str, detail: str | None = None,
            note: str | None = None, action: dict | None = None) -> dict:
    return {"label": label, "kind": kind, "status": status, "detail": detail,
            "note": note, "action": action}


def _link_for(text: str) -> dict | None:
    from config import HUBSPOT_PORTAL_ID
    m = _DEAL.search(text or "")
    if m and HUBSPOT_PORTAL_ID:
        return {"label": "Open the draft deal",
                "href": f"https://app.hubspot.com/contacts/{HUBSPOT_PORTAL_ID}/record/0-3/{m.group(1)}"}
    m = _CLICKUP.search(text or "")
    if m:
        return {"label": "Open the ClickUp task", "href": f"https://app.clickup.com/t/{m.group(1)}"}
    return None


def _from_route_approval(result: dict) -> tuple[list, str]:
    motion = [_motion(a, "auto", "done", action=_link_for(a)) for a in result.get("actions_taken") or []]
    motion += [_motion(e, "auto", "failed") for e in result.get("errors") or []]
    outcome = "ok" if result.get("status") == "ok" else "partial"
    return motion, outcome


# ── decision handlers: (ctx, item, reason, actor) -> result ──────────────────

def _hubdb_approve(ctx, item, reason, actor):
    raw = item["_raw"]
    if raw.get("rec_type") not in _REC_TYPES:
        raise DecisionError(400, "This recommendation type has no approval route", raw.get("rec_type"))
    from approval_agent import route_approval
    result = route_approval(
        rec_id=raw["rec_id"], rec_type=raw["rec_type"],
        property_uuid=ctx.uuid, company_id=ctx.company_id, property_name=ctx.name,
        rec_title=raw.get("title", ""), rec_body=raw.get("body", ""),
    )
    motion, outcome = _from_route_approval(result)
    if raw["rec_type"] in wi.SIGNATURE_REC_TYPES:
        motion.append(_motion("Waiting on a signature", "person", "waiting",
                              note="Nothing is billed or spent until the deal is signed."))
    return {"in_motion": motion, "outcome": outcome,
            "written_down": "Approved unedited. The recommendation card is marked approved "
                            "and the approval is logged on the company record."}


def _hubdb_not_now(ctx, item, reason, actor):
    from approval_agent import _log_hubspot_activity, _update_rec_status
    rec_id = item["_raw"]["rec_id"]
    _update_rec_status(rec_id, "dismissed")
    _log_hubspot_activity(ctx.company_id, f"Portal: Client dismissed recommendation (rec_id={rec_id})")
    return {"in_motion": [_motion("Recommendation card marked dismissed", "auto", "done")],
            "outcome": "ok",
            "written_down": f"Not now: {wi.REASONS[reason]}. The card is dismissed and the reason is logged."}


def _loop_approve(ctx, item, reason, actor):
    from routes.loop import record_recommendation_approved
    raw = item["_raw"]
    event_id = record_recommendation_approved(
        ctx.uuid, raw["recommendation"], forecast_id=raw.get("forecast_id"), approver_email=actor,
    )
    return {"in_motion": [
                _motion("Approval recorded on the optimize loop", "auto", "done", event_id),
                _motion("A person drafts the change as a deal", "person", "waiting",
                        note="No budget moves until that deal is signed."),
            ],
            "outcome": "ok",
            "written_down": "Approved unedited. Counts toward this forecast's recommendation record."}


def _loop_not_now(ctx, item, reason, actor):
    from routes.loop import record_recommendation_rejected
    raw = item["_raw"]
    event_id = record_recommendation_rejected(
        ctx.uuid, raw["recommendation"], reason=reason, forecast_id=raw.get("forecast_id"),
        rejecter_email=actor,
    )
    return {"in_motion": [_motion("Rejection recorded on the optimize loop", "auto", "done", event_id)],
            "outcome": "ok",
            "written_down": f"Not now: {wi.REASONS[reason]}. Counts toward this forecast's recommendation record."}


def _callprep_approve(ctx, item, reason, actor):
    from approval_actions import callprep_approve
    result, clickup = callprep_approve(ctx.company_id, item["_raw"]["rec_id"], actor)
    if not result:
        raise DecisionError(404, "Recommendation not found or update failed")
    status = {"created": "done", "failed": "failed"}.get(clickup, "skipped")
    return {"in_motion": [
                _motion("Marked approved on the call prep record", "auto", "done"),
                _motion("Task opened in ClickUp for the team", "queued", status,
                        note=None if clickup == "created" else "ClickUp is not configured for this list"
                        if clickup == "skipped" else "ClickUp did not accept the task"),
            ],
            "outcome": "ok" if clickup != "failed" else "partial",
            "written_down": "Approved unedited on this month's call prep."}


def _callprep_not_now(ctx, item, reason, actor):
    from approval_actions import callprep_dismiss
    if not callprep_dismiss(ctx.company_id, item["_raw"]["rec_id"], actor):
        raise DecisionError(404, "Recommendation not found or update failed")
    return {"in_motion": [_motion("Marked dismissed on the call prep record", "auto", "done")],
            "outcome": "ok",
            "written_down": f"Not now: {wi.REASONS[reason]}."}


def _brief_approve(ctx, item, reason, actor):
    from seo_entitlement import has_feature
    if not has_feature(ctx.props.get("seo_tier") or None, "content_briefs"):
        raise DecisionError(403, "Content briefs are not on this property's SEO tier")
    from routes.seo import approve_content_brief
    body, status = approve_content_brief(
        item["_raw"]["brief_id"], company_id=ctx.company_id, property_uuid=ctx.uuid,
        property_name=ctx.name,
    )
    if status != 200:
        raise DecisionError(404 if status == 404 else 502, body.get("error") or "Approval failed")
    motion, outcome = _from_route_approval(body)
    return {"in_motion": motion, "outcome": outcome,
            "written_down": "Approved unedited. The content team has the brief."}


def _record_only_not_now(ctx, item, reason, actor):
    return {"in_motion": [],
            "outcome": "ok",
            "written_down": f"Not now: {wi.REASONS[reason]}. This source has no dismissed state, "
                            "so the decision is kept in the workspace decision log."}


def _video_approve(ctx, item, reason, actor):
    from approval_actions import approve_video_variants
    body, status = approve_video_variants(ctx.company_id, [item["_raw"]["variant_id"]])
    if status != 200:
        raise DecisionError(502, body.get("error") or "Approval failed")
    rows = body.get("asset_rows") or 0
    return {"in_motion": [
                _motion("Variant marked approved on the property record", "auto", "done"),
                _motion("Added to the property asset library", "auto",
                        "done" if rows else "failed", f"{rows} asset row(s) written"),
            ],
            "outcome": "ok" if rows else "partial",
            "written_down": "Approved unedited."}


def _profile_approve(ctx, item, reason, actor):
    import ticket_profile_sync
    if not ticket_profile_sync.enabled():
        raise DecisionError(404, "Item not found")
    try:
        ticket_profile_sync.accept(item["_raw"]["proposal_id"], actor)
    except ticket_profile_sync.ProposalError as exc:
        raise DecisionError(exc.status, exc.message) from exc
    return {"in_motion": [
                _motion("Written to the profile override", "auto", "done"),
                _motion("Reaches Fluency on the next feed sync", "queued", "pending"),
            ],
            "outcome": "ok",
            "written_down": "Accepted unedited. The edit is in the property profile's audit log."}


def _profile_not_now(ctx, item, reason, actor):
    import ticket_profile_sync
    if not ticket_profile_sync.enabled():
        raise DecisionError(404, "Item not found")
    try:
        ticket_profile_sync.reject(item["_raw"]["proposal_id"], actor, wi.REASONS[reason])
    except ticket_profile_sync.ProposalError as exc:
        raise DecisionError(exc.status, exc.message) from exc
    return {"in_motion": [_motion("Proposal marked rejected", "auto", "done")],
            "outcome": "ok",
            "written_down": f"Not now: {wi.REASONS[reason]}. The profile is unchanged."}


HANDLERS: dict[tuple[str, str], Callable] = {
    ("hubdb_rec", "approve"): _hubdb_approve,
    ("hubdb_rec", "not_now"): _hubdb_not_now,
    ("loop_rec", "approve"): _loop_approve,
    ("loop_rec", "not_now"): _loop_not_now,
    ("call_prep", "approve"): _callprep_approve,
    ("call_prep", "not_now"): _callprep_not_now,
    ("content_brief", "approve"): _brief_approve,
    ("content_brief", "not_now"): _record_only_not_now,
    ("video_variant", "approve"): _video_approve,
    ("video_variant", "not_now"): _record_only_not_now,
    ("ticket_profile", "approve"): _profile_approve,
    ("ticket_profile", "not_now"): _profile_not_now,
}


# ── undo handlers: (ctx, raw, actor) -> None ─────────────────────────────────

def _undo_hubdb_not_now(ctx, raw, actor):
    from approval_agent import _log_hubspot_activity, _update_rec_status
    _update_rec_status(raw["rec_id"], "pending")
    _log_hubspot_activity(ctx.company_id,
                          f"Portal: dismissal undone, recommendation back to pending (rec_id={raw['rec_id']})")


def _undo_loop(ctx, raw, actor, action):
    import loop_writer
    loop_writer.record(
        "optimize", "recommendation_undone",
        property_uuid=ctx.uuid or None, company_id=ctx.company_id,
        source="client_action", trigger="client_action",
        payload={"recommendation": raw.get("recommendation"), "undone_action": action, "actor": actor},
        parent_event_id=raw.get("forecast_id") or None,
    )


def _undo_callprep_not_now(ctx, raw, actor):
    from approval_actions import callprep_update_rec
    if not callprep_update_rec(ctx.company_id, raw["rec_id"], "pending", actor):
        raise DecisionError(502, "The call prep record could not be updated")


def _undo_nothing(ctx, raw, actor):
    return None


def _undo_profile_not_now(ctx, raw, actor):
    import ticket_profile_sync
    row = ticket_profile_sync.get_proposal(raw["proposal_id"])
    if not row:
        raise DecisionError(404, "Item not found")
    if row.get("status") != ticket_profile_sync.STATUS_REJECTED:
        raise DecisionError(409, "not_undoable", reason=f"The proposal is now {row.get('status')}")
    ticket_profile_sync._transition(row, ticket_profile_sync.STATUS_PENDING, actor, "undo")


UNDO: dict[tuple[str, str], Callable | str] = {
    ("hubdb_rec", "approve"): "Approval already created a draft deal or ClickUp tasks, which are not reversed automatically.",
    ("hubdb_rec", "not_now"): _undo_hubdb_not_now,
    ("loop_rec", "approve"): lambda ctx, raw, actor: _undo_loop(ctx, raw, actor, "approve"),
    ("loop_rec", "not_now"): lambda ctx, raw, actor: _undo_loop(ctx, raw, actor, "not_now"),
    ("call_prep", "approve"): "Approval opened a ClickUp task for the team.",
    ("call_prep", "not_now"): _undo_callprep_not_now,
    ("content_brief", "approve"): "Approval opened a ClickUp task for the content team.",
    ("content_brief", "not_now"): _undo_nothing,
    ("video_variant", "approve"): "Approval wrote the variant to the asset library.",
    ("video_variant", "not_now"): _undo_nothing,
    ("ticket_profile", "approve"): "Accepting wrote the change to the property profile.",
    ("ticket_profile", "not_now"): _undo_profile_not_now,
}

_recent: dict = {}
_recent_lock = threading.Lock()


def _remember(ctx, item: dict, action: str, actor: str, at: datetime) -> None:
    with _recent_lock:
        _recent[(ctx.company_id, item["id"])] = {
            "actor": actor, "action": action, "at": at, "source": item["source"],
            "raw": dict(item.get("_raw") or {}),
        }
        cutoff = at - UNDO_WINDOW * 3
        for key in [k for k, v in _recent.items() if v["at"] < cutoff]:
            _recent.pop(key, None)


def undo_info(source: str, action: str, at: datetime, outcome: str = "ok") -> dict:
    rule = UNDO.get((source, action))
    if outcome not in ("ok", "partial"):
        return {"available": False, "until": None, "reason": "The decision did not complete."}
    if not callable(rule):
        return {"available": False, "until": None, "reason": rule or "This decision cannot be undone."}
    return {"available": True, "until": wc.to_iso_ts(at + UNDO_WINDOW), "reason": None}


# ── the decision ─────────────────────────────────────────────────────────────

def record_event(ctx, item: dict, action: str, reason: str | None, actor: str, *,
                 outcome: str, detail: str | None = None) -> str:
    """The loop event every decision writes, whether the handler succeeded or not."""
    import loop_writer
    return loop_writer.record(
        wi.STAGE[item["source"]], wi.DECISION_EVENT,
        property_uuid=ctx.uuid or None,
        company_id=ctx.company_id,
        source="workspace",
        source_id=item["id"],
        trigger="client_action",
        status="failed" if outcome == "failed" else "completed",
        payload={
            "item_id": item["id"],
            "source": item["source"],
            "source_id": item["source_id"],
            "lens": item["lens"],
            "action": action,
            "reason": reason,
            "actor": actor,
            "outcome": outcome,
            "detail": detail,
            "requires_signature": bool(item.get("_requires_signature")),
        },
    )


def _record(ctx, source: str, action: str) -> dict | None:
    """This property's decision record on the same kind of item, including the
    decision just made. Null when there is no earlier decision to count."""
    history = wi.decision_history(ctx, [])
    if not history:
        return None
    prior = [d for iid, ds in history.items() if iid.startswith(source + ":")
             for d in ds if d.get("outcome", "ok") in ("ok", "partial") and not d.get("undone")]
    if not prior:
        return None
    approvals = sum(1 for d in prior if d.get("action") == "approve") + (action == "approve")
    total = len(prior) + 1
    return {"label": f"Decisions on {wi.SOURCE_LABELS[source]} at this property",
            "approved_unedited": approvals, "total": total, "threshold": None,
            "pct": round(approvals / total, 4)}


def decide(ctx, value: str, action: str, reason: str | None, actor: str, *,
           internal: bool, today=None) -> dict:
    """Run one decision. Raises DecisionError with the HTTP status to return."""
    validate(action, reason)
    try:
        source, _ = wi.parse_item_id(value)
    except ValueError:
        raise DecisionError(404, "Item not found")
    item, _ = wi.find_item(ctx, value, today=today)
    if item is None:
        raise DecisionError(404, "Item not found")
    handler = HANDLERS.get((source, action))
    if handler is None:
        raise DecisionError(400, "This item has no approval step")
    if not item["actions"].get(action):
        raise DecisionError(400, "This item is not waiting on a decision")

    try:
        result = handler(ctx, item, reason, actor)
    except DecisionError as exc:
        record_event(ctx, item, action, reason, actor, outcome="failed", detail=exc.message)
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("workspace decision %s on %s failed: %s", action, value, exc, exc_info=True)
        record_event(ctx, item, action, reason, actor, outcome="failed", detail=type(exc).__name__)
        raise DecisionError(502, "The decision could not be completed", type(exc).__name__)

    outcome = result.get("outcome", "ok")
    record_event(ctx, item, action, reason, actor, outcome=outcome)
    now = wc.utc_now().replace(microsecond=0)
    _remember(ctx, item, action, actor, now)
    try:
        record = _record(ctx, source, action)
    except Exception as exc:  # noqa: BLE001
        logger.debug("workspace decision record unavailable: %s", exc)
        record = None

    item = dict(item)
    item["trail"] = list(item["trail"]) + [wi.trail(now, actor, wi.decision_text(action, reason))]
    item["status"] = _APPROVED_STATUS.get(source, "in_motion") if action == "approve" else "done"
    wi.finalize(item)
    return {
        "item": wi.view_item(item, internal),
        "decided_by": actor,
        "decided_at": wc.to_iso_ts(now),
        "in_motion": result["in_motion"],
        "written_down": result["written_down"],
        "record": record,
        "undo": undo_info(source, action, now, outcome),
        # No source on main carries a re-check date; see the contract changes.
        "check_back": None,
    }


def _last_decision(ctx, value: str) -> dict | None:
    with _recent_lock:
        hit = _recent.get((ctx.company_id, value))
    if hit:
        return hit
    history = wi.decision_history(ctx, []) or {}
    effective = [d for d in history.get(value, [])
                 if d.get("outcome", "ok") in ("ok", "partial") and not d.get("undone")]
    if not effective:
        return None
    last = effective[-1]
    at = wc.to_datetime(last.get("at"))
    if at is None:
        return None
    source, _ = wi.parse_item_id(value)
    return {"actor": last.get("actor"), "action": last.get("action"), "at": at,
            "source": source, "raw": None}


def undo(ctx, value: str, actor: str, *, internal: bool, now: datetime | None = None,
         today=None) -> dict:
    """Reverse the caller's own recent decision. Raises DecisionError."""
    try:
        source, _ = wi.parse_item_id(value)
    except ValueError:
        raise DecisionError(404, "Item not found")
    now = now or wc.utc_now()
    last = _last_decision(ctx, value)
    if not last:
        raise DecisionError(409, "not_undoable", reason="There is no decision on this item to undo.")
    if (last.get("actor") or "").lower() != (actor or "").lower():
        raise DecisionError(403, "Only the person who made this decision can undo it")
    at = last["at"] if last["at"].tzinfo else last["at"].replace(tzinfo=timezone.utc)
    if now - at > UNDO_WINDOW:
        raise DecisionError(409, "not_undoable", reason="The 10-minute undo window has passed.")
    rule = UNDO.get((source, last["action"]))
    if not callable(rule):
        raise DecisionError(409, "not_undoable", reason=rule or "This decision cannot be undone.")

    item, _ = wi.find_item(ctx, value, today=today)
    if item is None:
        raise DecisionError(404, "Item not found")
    raw = last.get("raw") or item.get("_raw") or {}
    try:
        rule(ctx, raw, actor)
    except DecisionError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("workspace undo on %s failed: %s", value, exc, exc_info=True)
        raise DecisionError(502, "The undo could not be completed", type(exc).__name__)

    import loop_writer
    loop_writer.record(
        wi.STAGE[source], wi.UNDO_EVENT,
        property_uuid=ctx.uuid or None, company_id=ctx.company_id,
        source="workspace", source_id=value, trigger="client_action", status="completed",
        payload={"item_id": value, "source": source, "lens": wi.LENS[source],
                 "undone_action": last["action"], "actor": actor,
                 "decided_at": wc.to_iso_ts(at)},
    )
    with _recent_lock:
        _recent.pop((ctx.company_id, value), None)

    item = dict(item)
    item["trail"] = [e for e in item["trail"]] + [wi.trail(now, actor, "Decision undone", "internal")]
    item["status"] = "to_do"
    item["needs_approval"] = True
    item["_closed_as"] = None
    wi.finalize(item)
    return {"item": wi.view_item(item, internal), "undone": True}
