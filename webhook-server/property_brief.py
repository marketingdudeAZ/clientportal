"""Property Brief Automation — orchestrator.

Two parallel paths, both triggered by a ClickUp ticket creation event:

  Path A (commercial):
    parse → match/create HubSpot company → create deal + line items →
    create quote → email RM → comment back to ClickUp + status update.

  Path B (brief):
    Once Path A's deal exists, run the LLM, persist the brief with an
    unguessable token, post the approval URL into the ClickUp ticket
    tagging the submitter. On approval, write to HubSpot company,
    update the spend sheet, and confirm in ClickUp. On needs-edits,
    re-run with feedback up to PROPERTY_BRIEF_MAX_REVISIONS times.

The HubSpot quote-signed webhook is a separate trigger handled here too.

This module is the coordinator only — the heavy lifting (HubSpot company
match, deal/line-item creation, quote generation, LLM call, ClickUp
comment) lives in dedicated single-purpose modules.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urljoin

import clickup_client
import property_brief_store as store
from config import (
    CLICKUP_BRIEF_STATUSES,
    PROPERTY_BRIEF_FAILURE_CHANNEL,
    PROPERTY_BRIEF_MAX_REVISIONS,
    PROPERTY_BRIEF_PUBLIC_URL,
    PROPERTY_BRIEF_REFIRE_FIELD,
)

logger = logging.getLogger(__name__)


# ── Ticket parsing ─────────────────────────────────────────────────────────

class TicketParseError(Exception):
    """Raised when the ClickUp ticket is missing required fields."""


REQUIRED_FIELDS = ("property_name", "rm_email", "submitter_email", "selections")


def parse_ticket(task: dict[str, Any]) -> dict[str, Any]:
    """Pull the structured payload out of a ClickUp task.

    The portal asks for these fields explicitly when the ticket is filed:

      Property Name        — company display name
      Property Domain      — used for HubSpot company match (optional)
      Submitter Email      — who filed the ticket; gets tagged in approval URL
      RM Email             — who the quote goes to
      Selections (JSON)    — { channel: { tier, monthly, setup } } per Path A
      Notes                — free text, optional, fed to the LLM as context

    Returning a dict (not raising) for missing optional fields keeps the
    parser composable; required-field gaps raise so the webhook handler
    can comment in ClickUp asking the submitter to fix them.
    """
    if not task:
        raise TicketParseError("Empty ClickUp task payload")

    cf = clickup_client.custom_field_value
    selections_raw = cf(task, "Selections") or cf(task, "selections")
    selections = _coerce_selections(selections_raw)

    parsed = {
        "ticket_id":       str(task.get("id") or ""),
        "ticket_url":      task.get("url") or "",
        "property_name":   _str(cf(task, "Property Name")) or _str(task.get("name")),
        "property_domain": _str(cf(task, "Property Domain") or cf(task, "Domain")),
        "submitter_email": _str(cf(task, "Submitter Email") or cf(task, "Submitter")),
        "submitter_id":    _str(cf(task, "Submitter ClickUp ID")),
        "rm_email":        _str(cf(task, "RM Email") or cf(task, "Relationship Manager")),
        "rm_id":           _str(cf(task, "RM ClickUp ID")),
        "notes":           _str(task.get("description") or cf(task, "Notes")),
        "selections":      selections,
        "totals":          _totals_from_selections(selections),
    }

    missing = [k for k in REQUIRED_FIELDS if not parsed.get(k)]
    if missing:
        raise TicketParseError(f"Missing required ClickUp fields: {', '.join(missing)}")

    return parsed


def _str(value: Any) -> str:
    return str(value).strip() if value not in (None, "") else ""


def _coerce_selections(value: Any) -> dict[str, dict]:
    """Selections may arrive as a JSON string, dict, or already-parsed list."""
    if not value:
        return {}
    if isinstance(value, str):
        import json
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return {}
    if isinstance(value, list):
        # List form: [{channel, tier, monthly, setup}, ...]
        out: dict[str, dict] = {}
        for entry in value:
            if not isinstance(entry, dict):
                continue
            channel = _str(entry.get("channel"))
            if not channel:
                continue
            out[channel] = {
                "tier":    _str(entry.get("tier")),
                "monthly": _num(entry.get("monthly")),
                "setup":   _num(entry.get("setup")),
            }
        return out
    if isinstance(value, dict):
        out = {}
        for channel, entry in value.items():
            if not isinstance(entry, dict):
                continue
            out[_str(channel)] = {
                "tier":    _str(entry.get("tier")),
                "monthly": _num(entry.get("monthly")),
                "setup":   _num(entry.get("setup")),
            }
        return out
    return {}


def _num(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(str(value).replace(",", "").replace("$", ""))
    except (TypeError, ValueError):
        return 0.0


def _totals_from_selections(selections: dict[str, dict]) -> dict[str, float]:
    monthly = sum(s.get("monthly", 0) for s in selections.values())
    setup = sum(s.get("setup", 0) for s in selections.values())
    return {"monthly": monthly, "setup": setup}


# ── Trigger gating ─────────────────────────────────────────────────────────

def should_fire(event: dict[str, Any], task: dict[str, Any]) -> bool:
    """Return True if this ClickUp event should trigger the workflow.

    Rules:
      - Always fire on `taskCreated`.
      - On `taskUpdated`, fire only when the configured re-process flag flips
        to a truthy value. Anything else is a no-op so editing a description
        doesn't re-bill the LLM and re-create deals.
    """
    event_type = (event.get("event") or "").lower()
    if event_type in ("taskcreated", "task_created"):
        return True
    if event_type in ("taskupdated", "task_updated"):
        flag = clickup_client.custom_field_value(task, PROPERTY_BRIEF_REFIRE_FIELD)
        return _truthy(flag)
    return False


def _truthy(value: Any) -> bool:
    if value is None or value == "":
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in ("true", "yes", "y", "1", "on")


# ── Path A: Commercial ─────────────────────────────────────────────────────

class CompanyMatchAmbiguous(Exception):
    """Raised when more than one HubSpot company matches the ticket details."""


def run_commercial_path(parsed: dict[str, Any]) -> dict[str, Any]:
    """Execute the HubSpot deal/quote workflow for a parsed ClickUp ticket.

    Returns a dict describing what landed: company_id, deal_id, quote_id,
    quote_url. Raises on hard failures (ambiguous match, deal creation
    failure after retries) so the caller can comment back to ClickUp with
    a meaningful error.
    """
    company = match_or_create_company(parsed)
    deal_creator = _import("deal_creator")
    quote_generator = _import("quote_generator")

    deal_id = deal_creator.create_deal_with_line_items(
        company_id=company["id"],
        selections=parsed["selections"],
        totals=parsed["totals"],
    )
    quote_id = quote_generator.generate_and_send_quote(
        deal_id=deal_id,
        company_id=company["id"],
    )

    portal_id = _hs_portal_id()
    quote_url = (
        f"https://app.hubspot.com/contacts/{portal_id}/quote/{quote_id}"
        if portal_id else ""
    )
    deal_url = (
        f"https://app.hubspot.com/contacts/{portal_id}/deal/{deal_id}"
        if portal_id else ""
    )

    return {
        "company_id": company["id"],
        "company_name": company.get("name") or parsed["property_name"],
        "deal_id":    deal_id,
        "deal_url":   deal_url,
        "quote_id":   quote_id,
        "quote_url":  quote_url,
    }


def match_or_create_company(parsed: dict[str, Any]) -> dict[str, Any]:
    """Look up a HubSpot company; create one if no match exists.

    Match order: exact-domain → name + market → name only. If the name-only
    search returns more than one company we refuse to auto-pick — the
    caller stops the workflow and flags for human review.
    """
    drafter = _import("brief_ai_drafter")

    domain = drafter.normalize_domain(parsed.get("property_domain") or "")
    if domain:
        match = drafter.resolve_company_by_domain(domain)
        if match:
            return match

    name = parsed["property_name"]
    candidates = _search_companies_by_name(name)
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise CompanyMatchAmbiguous(
            f"{len(candidates)} HubSpot companies match '{name}' — needs human review"
        )

    return _create_company(name=name, domain=domain)


def _search_companies_by_name(name: str) -> list[dict]:
    """HubSpot company search by exact name. Empty list if nothing matches."""
    if not name:
        return []
    import requests
    from config import HUBSPOT_API_KEY
    if not HUBSPOT_API_KEY:
        return []
    body = {
        "filterGroups": [{
            "filters": [{"propertyName": "name", "operator": "EQ", "value": name}],
        }],
        "properties": ["name", "domain", "website", "uuid", "rpmmarket"],
        "limit": 10,
    }
    try:
        r = requests.post(
            "https://api.hubapi.com/crm/v3/objects/companies/search",
            headers={"Authorization": f"Bearer {HUBSPOT_API_KEY}", "Content-Type": "application/json"},
            json=body,
            timeout=10,
        )
        r.raise_for_status()
    except requests.RequestException as e:
        logger.warning("Company name search failed: %s", e)
        return []
    out = []
    for rec in r.json().get("results") or []:
        p = rec.get("properties") or {}
        out.append({
            "id":     rec.get("id"),
            "name":   p.get("name"),
            "domain": p.get("domain"),
        })
    return out


def _create_company(*, name: str, domain: str = "") -> dict:
    import requests
    from config import HUBSPOT_API_KEY
    if not HUBSPOT_API_KEY:
        raise RuntimeError("HUBSPOT_API_KEY not configured")
    properties = {"name": name}
    if domain:
        properties["domain"] = domain
    r = requests.post(
        "https://api.hubapi.com/crm/v3/objects/companies",
        headers={"Authorization": f"Bearer {HUBSPOT_API_KEY}", "Content-Type": "application/json"},
        json={"properties": properties},
        timeout=10,
    )
    r.raise_for_status()
    body = r.json()
    return {"id": body["id"], "name": name, "domain": domain}


def comment_commercial_result(parsed: dict[str, Any], result: dict[str, Any]) -> None:
    """Post the deal/quote details into the ClickUp ticket and move status."""
    lines = [
        f"HubSpot deal created: {result.get('deal_url') or result.get('deal_id')}",
        f"Quote: {result.get('quote_url') or result.get('quote_id')} — sent to {parsed.get('rm_email') or 'RM'}",
        f"Monthly: ${parsed['totals']['monthly']:,.0f} · Setup: ${parsed['totals']['setup']:,.0f}",
    ]
    clickup_client.post_comment(parsed["ticket_id"], "\n".join(lines))
    _set_status(parsed["ticket_id"], "deal_created")


# ── Path B: Brief ──────────────────────────────────────────────────────────

def run_brief_path(parsed: dict[str, Any], commercial: dict[str, Any]) -> dict[str, Any]:
    """Generate the brief, persist it with a token, post approval URL.

    Returns the persisted record so the caller can log the token + URL.
    """
    brief = generate_brief(parsed=parsed, company_id=commercial["company_id"])
    record = store.create(
        ticket_id=parsed["ticket_id"],
        company_id=commercial["company_id"],
        deal_id=commercial.get("deal_id"),
        submitter_email=parsed["submitter_email"],
        rm_email=parsed["rm_email"],
        brief_markdown=brief,
    )
    post_approval_url(parsed=parsed, record=record)
    return record


def generate_brief(*, parsed: dict[str, Any], company_id: str, prior_feedback: list[str] | None = None) -> str:
    """Run the LLM and return the rendered brief markdown.

    Reuses the existing brief_ai_drafter scrape + Anthropic plumbing so the
    same Sonnet model and ILS-research grounding power both flows.
    """
    drafter = _import("brief_ai_drafter")
    domain = drafter.normalize_domain(parsed.get("property_domain") or "")
    site_text = drafter.scrape_site_text(domain) if domain else ""

    prompt = _build_brief_prompt(parsed=parsed, prior_feedback=prior_feedback or [])
    return _call_llm_for_brief(
        prompt=prompt,
        site_text=site_text,
        domain=domain,
    )


def _build_brief_prompt(*, parsed: dict[str, Any], prior_feedback: list[str]) -> str:
    """Assemble the user-side prompt body for the brief LLM call."""
    lines = [
        f"Property: {parsed['property_name']}",
    ]
    if parsed.get("property_domain"):
        lines.append(f"Domain: {parsed['property_domain']}")
    if parsed.get("notes"):
        lines.append("")
        lines.append("Submitter notes:")
        lines.append(parsed["notes"])
    if parsed.get("selections"):
        lines.append("")
        lines.append("Approved channel selections:")
        for channel, sel in parsed["selections"].items():
            tier = sel.get("tier") or ""
            monthly = sel.get("monthly") or 0
            lines.append(f"- {channel}: {tier} (${monthly:.0f}/mo)")
    if prior_feedback:
        lines.append("")
        lines.append("Submitter feedback from prior revisions (apply ALL of these):")
        for i, fb in enumerate(prior_feedback, 1):
            lines.append(f"{i}. {fb}")
    return "\n".join(lines)


def _call_llm_for_brief(*, prompt: str, site_text: str, domain: str) -> str:
    """Single LLM round-trip producing the brief markdown.

    Kept narrow on purpose: callers shouldn't reach into Anthropic plumbing
    and this function is the only natural mock point in tests.
    """
    import anthropic
    from config import ANTHROPIC_API_KEY, CLAUDE_AGENT_MODEL

    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")

    system = (
        "You are drafting a property marketing brief for an apartment community.\n"
        "Produce a concise markdown document with these sections:\n"
        "  1. Property Overview\n"
        "  2. Target Audience\n"
        "  3. Voice & Tone\n"
        "  4. Differentiators\n"
        "  5. Channel Strategy (one paragraph per selected channel)\n"
        "  6. Success Metrics\n"
        "Ground every claim in the source material. Do not invent statistics, "
        "phone numbers, or addresses. If a section can't be supported by the "
        "source material, say 'TBD — needs submitter input' instead of guessing."
    )

    user_content: list[dict] = []
    if site_text:
        user_content.append({
            "type": "text",
            "text": f"WEBSITE CONTENT from https://{domain} (trimmed):\n\n{site_text}",
            "cache_control": {"type": "ephemeral"},
        })
    user_content.append({"type": "text", "text": prompt})
    user_content.append({"type": "text", "text": "Write the full brief now in markdown."})

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message = client.messages.create(
        model=CLAUDE_AGENT_MODEL,
        max_tokens=2500,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_content}],
    )
    return next((b.text for b in message.content if b.type == "text"), "").strip()


def approval_url(token: str) -> str:
    """Return the public URL the submitter clicks to render the brief preview."""
    base = (PROPERTY_BRIEF_PUBLIC_URL or "").rstrip("/") + "/"
    return urljoin(base, f"property-brief/approve/{token}")


def post_approval_url(*, parsed: dict[str, Any], record: dict[str, Any]) -> None:
    """Drop the approval URL into ClickUp, tag submitter, update status."""
    url = approval_url(record["token"])
    text = (
        f"Brief draft ready for review.\n"
        f"Approve or request edits: {url}\n"
        f"Revision: {record['revision_count']}"
    )
    if parsed.get("submitter_id"):
        clickup_client.tag_user_in_comment(parsed["ticket_id"], parsed["submitter_id"], text)
    else:
        clickup_client.post_comment(parsed["ticket_id"], text)
    _set_status(parsed["ticket_id"], "awaiting_approval")


# ── Decision handlers ──────────────────────────────────────────────────────

def handle_approval(record: dict[str, Any]) -> dict[str, Any]:
    """Run the on-approved branch.

    1. Log approver to HubSpot company.
    2. Write the brief markdown to the company record.
    3. Generate the final brief doc and link it.
    4. Update the spend-sheet row with the brief URL.
    5. Comment in ClickUp + advance status.
    """
    company_id = record["company_id"]
    approver = record.get("decided_by") or record.get("submitter_email") or ""
    decided_at_ms = int(record.get("decided_at_ms") or 0)
    decided_iso = _iso_from_ms(decided_at_ms)

    _hs_update_company(company_id, {
        "rpm_brief_approved_by":       approver,
        "rpm_brief_approved_at":       decided_iso,
        "rpm_brief_content":           record.get("brief_markdown") or "",
        "rpm_brief_revision_count":    str(record.get("revision_count") or 0),
    })

    brief_url = generate_brief_doc(record)
    if brief_url:
        _hs_update_company(company_id, {"rpm_brief_url": brief_url})

    update_spend_sheet_row(company_id=company_id, brief_url=brief_url)

    text_lines = [
        f"Brief approved by {approver}.",
    ]
    if brief_url:
        text_lines.append(f"Final brief: {brief_url}")
    clickup_client.post_comment(record["ticket_id"], "\n".join(text_lines))
    _set_status(record["ticket_id"], "approved")

    return {"brief_url": brief_url, "approver": approver}


def handle_needs_edits(record: dict[str, Any]) -> dict[str, Any]:
    """Run the needs-edits branch.

    Re-runs the LLM with prior feedback, persists a new token-keyed record,
    posts a fresh approval URL. After PROPERTY_BRIEF_MAX_REVISIONS, escalates
    to the ops queue rather than re-prompting the LLM in a loop.
    """
    next_revision = int(record.get("revision_count") or 0) + 1
    if next_revision > PROPERTY_BRIEF_MAX_REVISIONS:
        return escalate_to_ops(record, reason="max_revisions_reached")

    parsed = _rebuild_parsed_for_revision(record)
    brief = generate_brief(
        parsed=parsed,
        company_id=record["company_id"],
        prior_feedback=record.get("feedback_history") or [],
    )

    feedback = ""
    history = record.get("feedback_history") or []
    if history:
        feedback = history[-1]

    new_record = store.attach_revision(
        previous=record,
        brief_markdown=brief,
        feedback="",  # already in record's history; don't double-append
    )
    # attach_revision rebuilds history off `previous`, so the latest feedback
    # is preserved without duplication.
    _ = feedback

    post_approval_url(parsed=parsed, record=new_record)
    _set_status(record["ticket_id"], "needs_edits")
    return {"new_token": new_record["token"], "revision_count": new_record["revision_count"]}


def escalate_to_ops(record: dict[str, Any], *, reason: str) -> dict[str, Any]:
    """Move the brief into the manual ops queue and notify the failure channel."""
    record["status"] = store.STATUS_ESCALATED
    store._backend().put(record)  # explicit override of normal lifecycle

    msg = (
        f"Property brief escalated for manual handling. Reason: {reason}. "
        f"Revision count: {record.get('revision_count')}."
    )
    if PROPERTY_BRIEF_FAILURE_CHANNEL == "clickup":
        clickup_client.post_comment(record["ticket_id"], msg)
    else:
        logger.error("Brief escalation (channel=%s): %s", PROPERTY_BRIEF_FAILURE_CHANNEL, msg)
    _set_status(record["ticket_id"], "escalated")
    return {"escalated": True, "reason": reason}


def _rebuild_parsed_for_revision(record: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct just enough of `parsed` to re-render the brief + comment.

    The original ClickUp ticket may have been edited since the first run, but
    we honour what the submitter approved at quote time — so re-fetch the
    ticket for current submitter/RM identity, but keep the deal/company
    linkage from the persisted record.
    """
    task = clickup_client.get_task(record["ticket_id"]) or {}
    try:
        return parse_ticket(task)
    except TicketParseError:
        # Ticket details became invalid mid-flight — fall back to what we
        # already know so we can still post the new approval URL.
        return {
            "ticket_id":       record["ticket_id"],
            "ticket_url":      "",
            "property_name":   "",
            "property_domain": "",
            "submitter_email": record.get("submitter_email", ""),
            "submitter_id":    "",
            "rm_email":        record.get("rm_email", ""),
            "rm_id":           "",
            "notes":           "",
            "selections":      {},
            "totals":          {"monthly": 0, "setup": 0},
        }


# ── Final logging ──────────────────────────────────────────────────────────

def generate_brief_doc(record: dict[str, Any]) -> str:
    """Render the brief to a hosted doc and return its URL.

    Uses the existing kb_writer Google Drive helper so the brief lands in a
    shareable Doc next to the rest of the property's collateral. If Drive
    isn't configured, returns an empty string and the workflow continues —
    the brief content is already stored on the company record.
    """
    try:
        from kb_writer import create_brief_doc  # type: ignore
    except ImportError:
        try:
            from kb_writer import create_kb_draft as _legacy  # type: ignore
        except ImportError:
            logger.info("kb_writer unavailable; skipping brief doc generation")
            return ""

        try:
            result = _legacy(
                ticket_id=record.get("ticket_id") or "",
                title=f"Property Brief — {record.get('company_id', '')}",
                description=record.get("brief_markdown", ""),
                thread_messages=[],
                category="Property Brief",
                source="Portal",
                ticket_url="",
            )
            return (result or {}).get("doc_url") or ""
        except Exception as e:
            logger.warning("Brief doc generation via kb_writer failed: %s", e)
            return ""

    try:
        result = create_brief_doc(  # type: ignore[misc]
            company_id=record["company_id"],
            title=f"Property Brief — {record.get('company_id', '')}",
            markdown=record.get("brief_markdown") or "",
        )
        return (result or {}).get("doc_url") or ""
    except Exception as e:
        logger.warning("Brief doc generation failed: %s", e)
        return ""


def update_spend_sheet_row(*, company_id: str, brief_url: str) -> None:
    """Write the brief URL into the spend-sheet row for the company.

    The spend-sheet module owns the column schema; we only nudge the cache
    so a UI refresh shows the new link. The real write target is a HubSpot
    company property the spend-sheet builder reads from.
    """
    if not brief_url:
        return
    try:
        _hs_update_company(company_id, {"rpm_brief_url": brief_url})
    finally:
        try:
            from spend_sheet import invalidate_cache
            invalidate_cache()
        except ImportError:
            pass


def handle_quote_signed(deal_id: str) -> dict[str, Any]:
    """HubSpot quote-signed webhook handler.

    Find the brief record linked to this deal, post the signed details into
    the originating ClickUp ticket, and advance status.
    """
    if not deal_id:
        return {"status": "ignored", "reason": "missing_deal_id"}

    record = _find_record_by_deal(deal_id)
    if not record:
        logger.info("No brief record found for deal %s; cannot post-back to ClickUp", deal_id)
        return {"status": "ignored", "reason": "no_brief_record"}

    text = f"Quote signed for HubSpot deal {deal_id}. Onboarding can begin."
    clickup_client.post_comment(record["ticket_id"], text)
    _set_status(record["ticket_id"], "quote_signed")
    return {"status": "ok", "ticket_id": record["ticket_id"]}


def _find_record_by_deal(deal_id: str) -> dict[str, Any] | None:
    """Best-effort lookup: scan known briefs for a matching deal_id.

    HubDB doesn't surface a deal_id index for this table; for a more efficient
    lookup we'd add one. For now scan the in-memory backend and fall back to
    None on HubDB.
    """
    backend = store._backend()
    if isinstance(backend, store._MemoryBackend):
        for row in backend._rows.values():  # noqa: SLF001 — internal scan
            if row.get("deal_id") == deal_id:
                return dict(row)
        return None
    try:
        import requests
        from config import HUBDB_PROPERTY_BRIEFS_TABLE_ID, HUBSPOT_API_KEY
        r = requests.get(
            f"https://api.hubapi.com/cms/v3/hubdb/tables/{HUBDB_PROPERTY_BRIEFS_TABLE_ID}/rows",
            headers={"Authorization": f"Bearer {HUBSPOT_API_KEY}", "Content-Type": "application/json"},
            params={"deal_id__eq": deal_id, "limit": 1},
            timeout=10,
        )
        r.raise_for_status()
        results = r.json().get("results") or []
        if not results:
            return None
        return backend._from_row(results[0])  # noqa: SLF001
    except Exception as e:
        logger.warning("HubDB deal_id lookup failed: %s", e)
        return None


# ── Helpers ────────────────────────────────────────────────────────────────

def _set_status(ticket_id: str, key: str) -> None:
    status = CLICKUP_BRIEF_STATUSES.get(key)
    if not status:
        return
    clickup_client.update_status(ticket_id, status)


def _hs_update_company(company_id: str, properties: dict[str, str]) -> None:
    if not company_id or not properties:
        return
    import requests
    from config import HUBSPOT_API_KEY
    if not HUBSPOT_API_KEY:
        return
    try:
        requests.patch(
            f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}",
            headers={"Authorization": f"Bearer {HUBSPOT_API_KEY}", "Content-Type": "application/json"},
            json={"properties": properties},
            timeout=10,
        ).raise_for_status()
    except Exception as e:
        logger.warning("HubSpot company update failed for %s: %s", company_id, e)


def _hs_portal_id() -> str:
    import os
    return os.getenv("HUBSPOT_PORTAL_ID", "")


def _iso_from_ms(ms: int) -> str:
    if not ms:
        return ""
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _import(name: str):
    """Local import wrapper so tests can patch the module attribute on this one."""
    import importlib
    return importlib.import_module(name)
