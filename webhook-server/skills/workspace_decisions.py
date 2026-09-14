"""Workspace decisions — `POST /api/workspace/work/<id>/decision`.

One decision, dispatched to the source's EXISTING handler as a module call
(never an HTTP self-call), then written to the loop as a `workspace_decision`
event under the source's existing stage.

| source         | approve                                   | not now                                 |
|----------------|-------------------------------------------|-----------------------------------------|
| hubdb_rec      | approval_agent.route_approval             | approval_agent._update_rec_status +     |
|                |                                           | _log_hubspot_activity (= /api/dismiss)  |
| loop_rec       | routes.loop.record_recommendation_approved| routes.loop.record_recommendation_rejected |
| call_prep      | approval_actions.callprep_approve         | approval_actions.callprep_dismiss       |
| content_brief  | routes.seo.approve_content_brief          | loop event only (no dismissed state)    |
| video_variant  | approval_actions.approve_video_variants   | loop event only (no dismissed state)    |
| ticket_profile | ticket_profile_sync.accept                | ticket_profile_sync.reject              |

Money: nothing here moves spend or writes to Google Ads, Fluency or a rent roll.
The only handler that touches budget is `route_approval` for `budget_change` /
`package_upgrade`, which drafts a HubSpot deal for a human signature (its
existing behavior). A loop recommendation approval records an event and
nothing more: no consumer on main turns `recommendation_approved` into a spend
change (loop_autopilot's hook is a stub that logs).
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from skills import workspace_common as wc
from skills import workspace_inbox as wi

logger = logging.getLogger(__name__)

ACTIONS = ("approve", "not_now")

# What an approval leaves the item as, when the handler finishes the job itself.
_APPROVED_STATUS = {"video_variant": "done", "ticket_profile": "done"}

_REC_TYPES = ("budget_change", "strategy_change", "package_upgrade")


class DecisionError(Exception):
    def __init__(self, status: int, message: str, detail: str | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.detail = detail

    def body(self) -> dict:
        out = {"error": self.message}
        if self.detail:
            out["detail"] = self.detail
        return out


def validate(action: Any, reason: Any) -> None:
    if action not in ACTIONS:
        raise DecisionError(400, "Invalid action", "approve|not_now")
    if action == "not_now" and not reason:
        raise DecisionError(400, "A reason is required for not now", "|".join(wi.REASONS))
    if reason is not None and reason not in wi.REASONS:
        raise DecisionError(400, "Invalid reason", "|".join(wi.REASONS))


def _motion(label: str, kind: str, status: str, detail: str | None = None) -> dict:
    return {"label": label, "kind": kind, "status": status, "detail": detail}


def _from_route_approval(result: dict) -> tuple[list, str]:
    motion = [_motion(a, "auto", "done") for a in result.get("actions_taken") or []]
    motion += [_motion(e, "auto", "failed") for e in result.get("errors") or []]
    outcome = "ok" if result.get("status") == "ok" else "partial"
    return motion, outcome


# ── handlers: (ctx, item, reason, actor) -> result ───────────────────────────

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
    if raw["rec_type"] in ("budget_change", "package_upgrade"):
        motion.append(_motion("The deal waits for a signature before anything is billed or spent",
                              "person", "waiting"))
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
                _motion("No budget moves until a person drafts the change as a deal and it is signed",
                        "person", "waiting"),
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
                _motion("Task opened in ClickUp for the team", "queued", status),
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


# ── the decision ─────────────────────────────────────────────────────────────

def record_event(ctx, item: dict, action: str, reason: str | None, actor: str, *,
                 outcome: str, detail: str | None = None) -> str:
    """The loop event every decision writes, whether the handler succeeded or not."""
    import loop_writer
    return loop_writer.record(
        wi.STAGE[item["source"]], "workspace_decision",
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
        },
    )


def decide(ctx, value: str, action: str, reason: str | None, actor: str, *,
           internal: bool, today=None) -> dict:
    """Run one decision. Raises DecisionError with the HTTP status to return."""
    validate(action, reason)
    try:
        source, _ = wi.parse_item_id(value)
    except ValueError:
        raise DecisionError(404, "Item not found")
    item, _ = wi.find_item(ctx, value, today=today, internal=internal)
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
    decided_at = wc.now_iso()

    item = dict(item)
    item["trail"] = list(item["trail"]) + [
        {"at": decided_at, "actor": actor, "text": wi.decision_text(action, reason)}]
    if action == "approve":
        item["status"] = _APPROVED_STATUS.get(source, "in_motion")
    else:
        item["status"] = "done"
    wi.finalize(item)
    return {
        "item": wc.public(item),
        "decided_by": actor,
        "decided_at": decided_at,
        "in_motion": result["in_motion"],
        "written_down": result["written_down"],
        # No source on main carries a re-check date; see the contract changes.
        "check_back": None,
    }
