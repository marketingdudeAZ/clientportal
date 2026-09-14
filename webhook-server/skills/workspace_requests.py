"""Workspace new requests — plain words in, portal tickets out.

    POST /api/workspace/requests/draft   text → proposed tickets (nothing filed)
    POST /api/workspace/requests         file the (possibly edited) drafts
    GET  /api/workspace/requests         recent requests for the property

Drafting goes through `skills/llm_gateway.py`. The model returns JSON only, and
`parse_draft` validates it as structured output: a bad shape, bad JSON or an
unknown category is refused or coerced, never passed through. What the model
may decide is limited to the title, category, a needed-by date with its reason,
and warnings. Everything else is deterministic:

* numbers: a sentence in any model-written field is kept only if every number in
  it appears in the requester's own text (or the property name); a needed-by
  date is kept only when its reason survives that check;
* team: the label of the existing portal ticket type the category maps to;
* attached_context: which prefill fields the property record actually has;
* Fair Housing: the requester's text and every drafted ticket are checked. A
  high-severity match refuses the draft and blocks filing.

Filing goes through `portal_tickets.create_ticket`, the existing create path,
and writes a `workspace_request_filed` loop event per ticket.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta

from skills import workspace_common as wc
from skills import workspace_inbox as wi

logger = logging.getLogger(__name__)

CATEGORIES = ("creative", "web", "paid", "seo", "listing", "reputation", "other")
# Existing, client-audience portal ticket types (config.PORTAL_TICKET_TYPES).
CATEGORY_TYPE = {
    "creative": "creative_ad_copy",
    "listing": "creative_ad_copy",
    "paid": "campaign_review",
    "seo": "campaign_review",
    "web": "general",
    "reputation": "general",
    "other": "general",
}
CATEGORY_TITLES = {
    "creative": "Creative request", "web": "Website request", "paid": "Paid media request",
    "seo": "SEO request", "listing": "Listing update", "reputation": "Reputation request",
    "other": "Request",
}
CATEGORY_WARNINGS = {
    "listing": "A wrong detail in a listing is usually also wrong in the paid ads and ILS "
               "feeds. We'll flag those too.",
}
MAX_TEXT = 4000
MAX_TICKETS = 5
MAX_FILE = 10

SYSTEM_PROMPT = """You turn a property team's plain-words request into proposed marketing tickets \
for RPM Living's services team.

Return ONLY a JSON object, no prose, no code fences:
{"tickets": [{"title": str, "category": one of ["creative","web","paid","seo","listing","reputation","other"], \
"needed_by": "YYYY-MM-DD" or null, "needed_by_reason": str or null, "warnings": [str]}]}

Rules:
- One ticket per distinct piece of work, at most 5.
- Titles are short and specific, written in plain words.
- Never invent numbers, prices, unit counts, dates or deadlines. Use only numbers and dates that \
appear in the request. If the request states no deadline, needed_by and needed_by_reason are null.
- Never describe or target people by race, color, religion, sex, national origin, familial status, \
disability, age or source of income.
- warnings are for real conflicts the team should know about; otherwise an empty list."""


def _prompt(ctx, text: str, today: date) -> str:
    return (f"Today is {today.isoformat()}.\nProperty: {ctx.name or 'this property'}.\n\n"
            f"Request:\n{text}")


def _extract_json(raw: str) -> dict:
    s = (raw or "").strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
    try:
        data = json.loads(s)
    except ValueError:
        m = re.search(r"\{.*\}", s, re.S)
        if not m:
            raise ValueError("no JSON object in the model output")
        data = json.loads(m.group(0))
    if not isinstance(data, dict):
        raise ValueError("model output is not a JSON object")
    return data


def attached_context(ctx) -> list:
    p = ctx.props
    out = []
    if p.get("address") or p.get("city"):
        out.append("Property address")
    if p.get("rpmmarket"):
        out.append("Market")
    if p.get("marketing_manager_email"):
        out.append("PM contact")
    if p.get("hubspot_owner_id"):
        out.append("Account manager")
    if p.get("domain") or p.get("website"):
        out.append("Property website")
    return out


def _team(category: str) -> str | None:
    import portal_tickets
    t = portal_tickets._type_by_key(CATEGORY_TYPE[category])
    return (t or {}).get("label")


def parse_draft(raw: str, user_text: str, ctx, today: date) -> tuple[list, list]:
    """Validate model output into draft tickets. Raises WorkspaceError(502)."""
    try:
        data = _extract_json(raw)
    except (ValueError, TypeError) as exc:
        raise wc.WorkspaceError(502, "draft_failed", f"The draft was not valid JSON ({exc})")
    tickets_in = data.get("tickets")
    if not isinstance(tickets_in, list) or not tickets_in:
        raise wc.WorkspaceError(502, "draft_failed", "The draft had no tickets list")

    allowed = wc.number_forms([user_text, ctx.name])
    gaps: list = []
    out = []
    for n, t in enumerate(tickets_in[:MAX_TICKETS], start=1):
        if not isinstance(t, dict) or not isinstance(t.get("title"), str) or not t["title"].strip():
            raise wc.WorkspaceError(502, "draft_failed", f"Ticket {n} has no title")
        category = t.get("category") if t.get("category") in CATEGORIES else "other"
        if t.get("category") not in CATEGORIES:
            gaps.append(wc.gap("category", f"Ticket {n}: unknown category {t.get('category')!r}, filed as other"))
        title, removed = wc.verified_text(wc.truncate(t["title"], 140), allowed)
        if not title:
            title = CATEGORY_TITLES[category]
            gaps.append(wc.gap("title", f"Ticket {n}: the drafted title quoted numbers not in the request"))
        reason, _ = wc.verified_text(t.get("needed_by_reason"), allowed) if t.get("needed_by_reason") else (None, 0)
        needed_by = None
        if t.get("needed_by"):
            d = wc.to_date(t.get("needed_by"))
            if d and reason and today <= d <= today + timedelta(days=366):
                needed_by = d.isoformat()
            else:
                reason = None
                gaps.append(wc.gap("needed_by", f"Ticket {n}: the needed-by date was dropped because "
                                                "the request does not state it"))
        warnings = []
        for w in t.get("warnings") or []:
            text, _ = wc.verified_text(w, allowed) if isinstance(w, str) else (None, 0)
            if text:
                warnings.append(text)
        if CATEGORY_WARNINGS.get(category) and CATEGORY_WARNINGS[category] not in warnings:
            warnings.append(CATEGORY_WARNINGS[category])
        review = wc.fair_housing_review(title, reason, *warnings)
        if review and review["severity"] == "high":
            warnings.append("This ticket includes language Fair Housing rules don't allow in housing "
                            f"marketing ({', '.join(review['terms'])}). It can't be filed as written.")
        out.append({
            "draft_id": f"d{n}",
            "title": title,
            "category": category,
            "team": _team(category),
            "needed_by": needed_by,
            "needed_by_reason": reason if needed_by else None,
            "attached_context": attached_context(ctx),
            "warnings": warnings,
        })
    return out, gaps


def draft(ctx, text: str, *, today: date | None = None) -> dict:
    today = today or date.today()
    text = (text or "").strip()
    if not text:
        raise wc.WorkspaceError(400, "text is required")
    if len(text) > MAX_TEXT:
        raise wc.WorkspaceError(400, "text is too long", f"at most {MAX_TEXT} characters")
    review = wc.fair_housing_review(text)
    if review and review["severity"] == "high":
        raise wc.WorkspaceError(400, "fair_housing",
                                reason="The request includes language Fair Housing rules don't allow in "
                                       f"housing marketing: {', '.join(review['terms'])}.")
    if review:
        logger.info("workspace request draft fair housing review (low): %s", review["terms"])

    from skills import llm_gateway
    try:
        resp = llm_gateway.complete(_prompt(ctx, text, today), system=SYSTEM_PROMPT,
                                    max_tokens=1500, purpose="workspace_request_draft")
    except llm_gateway.LLMNotConfigured:
        raise wc.WorkspaceError(503, "draft_unavailable", "The drafting model is not configured")
    except llm_gateway.LLMError as exc:
        raise wc.WorkspaceError(502, "draft_failed", type(exc).__name__)
    tickets, gaps = parse_draft(resp.text, text, ctx, today)
    return {"tickets": tickets, "gaps": wc.gaps_for(gaps, internal=True)}


def file_requests(ctx, tickets: list, actor: str, *, ticket_internal: bool) -> dict:
    """File each draft through the portal ticket create path."""
    import loop_writer
    import portal_tickets

    if not isinstance(tickets, list) or not tickets:
        raise wc.WorkspaceError(400, "tickets is required")
    if len(tickets) > MAX_FILE:
        raise wc.WorkspaceError(400, "Too many tickets", f"at most {MAX_FILE}")
    created, failed = [], []
    for n, t in enumerate(tickets, start=1):
        draft_id = str((t or {}).get("draft_id") or f"d{n}") if isinstance(t, dict) else f"d{n}"
        if not isinstance(t, dict) or not str(t.get("title") or "").strip():
            failed.append({"draft_id": draft_id, "reason": "A title is required"})
            continue
        category = t.get("category")
        if category not in CATEGORIES:
            failed.append({"draft_id": draft_id, "reason": "Unknown category"})
            continue
        title = wc.truncate(t["title"], 140)
        reason = str(t.get("needed_by_reason") or "").strip() or None
        needed_by = wc.to_iso_date(t.get("needed_by")) if t.get("needed_by") else None
        review = wc.fair_housing_review(title, reason)
        if review and review["severity"] == "high":
            failed.append({"draft_id": draft_id, "reason": "fair_housing"})
            continue
        details = "\n".join(x for x in (
            f"Needed by: {needed_by}" + (f" ({reason})" if reason else "") if needed_by else "",
            f"Category: {category}",
            "Filed from Workspace New request.",
        ) if x)
        body, status = portal_tickets.create_ticket(
            ctx.company_id, CATEGORY_TYPE[category], subject=title, fields={"Details": details},
            submitted_by=actor, property_uuid=ctx.uuid, internal=ticket_internal,
        )
        task_id = str((body.get("ticket") or {}).get("id") or "") if isinstance(body, dict) else ""
        if status != 201 or not task_id:
            failed.append({"draft_id": draft_id, "reason": (body or {}).get("error") or "Could not file"})
            continue
        created.append({"draft_id": draft_id, "work_item_id": wi.item_id("portal_ticket", task_id),
                        "clickup_task_id": task_id})
        loop_writer.record(
            "ops", "workspace_request_filed",
            property_uuid=ctx.uuid or None, company_id=ctx.company_id,
            source="workspace", source_id=task_id, trigger="client_action",
            payload={"origin": "new_request", "draft_id": draft_id, "category": category,
                     "actor": actor, "clickup_task_id": task_id},
        )
    return {"created": created, "failed": failed}


_RECENT_STATUS = {"done": "done", "open": "new"}


def recent(ctx, limit: int = 10) -> dict:
    import portal_tickets
    gaps: list = []
    rows = portal_tickets.list_tickets(ctx.company_id, property_uuid=ctx.uuid, limit=limit)
    if portal_tickets.tracking_degraded():
        gaps.append(wc.gap("recent", "The portal ticket store could not be read, so recent requests may be missing",
                           source="portal_ticket"))
    out = []
    for r in rows[:limit]:
        tid = str(r.get("id") or "")
        if not tid:
            continue
        label = str(r.get("status") or "").strip().lower()
        out.append({
            "title": r.get("subject") or "Request",
            "status": _RECENT_STATUS.get(label, "in_progress"),
            "status_date": wc.to_iso_ts(r.get("created_ts")),
            "work_item_id": wi.item_id("portal_ticket", tid),
        })
    if out:
        gaps.append(wc.gap("status_date", "ClickUp status change dates are not tracked; this is the filing date"))
    return {"recent": out, "gaps": wc.gaps_for(gaps, internal=True)}
