"""Community Brief auto-capture + AptIQ match tracking.

The new capture flow (2026-05-27):

  PLE status == "RPM Managed"
    → run the AI capture (website scrape + LLM) for what we can get
    → persist a brief record + token
    → log the approval link ON the company record (rpm_brief_approval_url)
    → status = pending_approval (waits for a human to approve)
    → on approval the brief publishes to the HubSpot /accounts/property side

Alongside capture, this module tracks the AptIQ exact-match retry: a property
can become RPM Managed before its aptiq_property_id resolves / appears in the
daily CSV, so we retry the match for ~30 days and record match status +
attempt count on the company. After the window we mark it "failed" and stop.

Design: the decision logic is pure + unit-tested (needs_brief_capture,
compute_aptiq_tracking). The orchestrator (run_scan / process_company) takes
injectable dependencies so it can be exercised without HubSpot/Anthropic.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)

RPM_MANAGED_STATUS = "RPM Managed"
APTIQ_RETRY_WINDOW_DAYS = 30
_DAY_MS = 86_400_000

# Brief lifecycle states stored on rpm_brief_status.
BRIEF_NOT_STARTED = "not_started"
BRIEF_PENDING_APPROVAL = "pending_approval"
BRIEF_APPROVED = "approved"
BRIEF_NEEDS_EDITS = "needs_edits"


def _now_ms() -> int:
    return int(time.time() * 1000)


# ── Pure decision logic (unit-tested) ───────────────────────────────────────


def needs_brief_capture(props: dict) -> bool:
    """True iff this property has no brief yet and should be auto-captured.

    Only fires when rpm_brief_status is unset / not_started. Once a brief is
    captured (pending_approval), approved, or in needs_edits, we never
    re-capture automatically — humans own it from there.
    """
    status = (props.get("rpm_brief_status") or "").strip()
    return status in ("", BRIEF_NOT_STARTED)


def _as_int(v: Any) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def compute_aptiq_tracking(props: dict, matched: bool, now_ms: int) -> dict:
    """Return the HubSpot property updates for AptIQ match tracking.

    Datetime props are epoch-ms ints (HubSpot DATETIME convention).
    - matched   → status=matched, stamp first (if unset) + last attempt.
    - unmatched → bump attempts, set first (if unset) + last; status flips to
      "failed" once we're past the 30-day window, else "pending".
    """
    if matched:
        updates: dict[str, Any] = {
            "aptiq_match_status": "matched",
            "aptiq_last_attempt_at": now_ms,
        }
        if not _as_int(props.get("aptiq_first_attempt_at")):
            updates["aptiq_first_attempt_at"] = now_ms
        return updates

    attempts = _as_int(props.get("aptiq_match_attempts")) + 1
    first = _as_int(props.get("aptiq_first_attempt_at")) or now_ms
    age_days = (now_ms - first) / _DAY_MS
    return {
        "aptiq_match_attempts": attempts,
        "aptiq_first_attempt_at": first,
        "aptiq_last_attempt_at": now_ms,
        "aptiq_match_status": "failed" if age_days >= APTIQ_RETRY_WINDOW_DAYS else "pending",
    }


# ── Orchestration ────────────────────────────────────────────────────────────


class CaptureDeps:
    """Injectable dependencies — defaults wire to the real implementations."""

    def __init__(
        self,
        *,
        read_property: Callable[[dict], dict] | None = None,
        generate_brief: Callable[..., str] | None = None,
        store_create: Callable[..., dict] | None = None,
        find_by_ticket: Callable[[str], list] | None = None,
        approval_url: Callable[[str], str] | None = None,
        hs_update: Callable[[str, dict], None] | None = None,
        notify_email: str = "",
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        self.read_property = read_property
        self.generate_brief = generate_brief
        self.store_create = store_create
        self.find_by_ticket = find_by_ticket
        self.approval_url = approval_url
        self.hs_update = hs_update
        self.notify_email = notify_email
        self.now_ms = now_ms or _now_ms

    def resolve(self) -> "CaptureDeps":
        """Lazily bind any unset dependency to its production implementation."""
        if self.read_property is None:
            from services.fluency_ingestion import apt_iq_reader
            self.read_property = apt_iq_reader.read_property
        if self.generate_brief is None or self.store_create is None or \
           self.find_by_ticket is None or self.approval_url is None or self.hs_update is None:
            import property_brief
            import property_brief_store as store
            self.generate_brief = self.generate_brief or property_brief.generate_brief
            self.store_create = self.store_create or store.create
            self.find_by_ticket = self.find_by_ticket or store.find_by_ticket
            self.approval_url = self.approval_url or property_brief.approval_url
            self.hs_update = self.hs_update or property_brief._hs_update_company  # noqa: SLF001
        return self


def _auto_ticket_id(company_id: str) -> str:
    """Stable pseudo-ticket id so store idempotency works for auto-captures."""
    return f"auto:{company_id}"


def process_company(company: dict, *, deps: CaptureDeps, dry_run: bool = False) -> dict:
    """Process one RPM-Managed company: AptIQ match tracking + brief capture.

    `company` carries at least: id, name, domain, and the property dict under
    "props" (HubSpot company properties already read by the caller).
    Returns an action summary dict.
    """
    company_id = str(company.get("id") or "")
    props = company.get("props") or {}
    now = deps.now_ms()
    action: dict[str, Any] = {"company_id": company_id, "name": company.get("name", "")}
    updates: dict[str, Any] = {}

    # 1) AptIQ match tracking (best-effort; never blocks capture).
    try:
        apt = deps.read_property({
            "aptiq_property_id": props.get("aptiq_property_id") or "",
            "aptiq_market_id": props.get("aptiq_market_id") or "",
        }) or {}
        matched = bool(apt.get("matched"))
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("aptiq read failed for %s: %s", company_id, e)
        matched = False
    updates.update(compute_aptiq_tracking(props, matched, now))
    action["aptiq_matched"] = matched
    action["aptiq_status"] = updates.get("aptiq_match_status")

    # 2) Brief capture, if this property has no brief yet.
    if needs_brief_capture(props):
        ticket_id = _auto_ticket_id(company_id)
        existing = deps.find_by_ticket(ticket_id) if deps.find_by_ticket else None
        if existing:
            action["brief"] = "exists"
        elif dry_run:
            action["brief"] = "would_capture"
        else:
            try:
                parsed = {
                    "ticket_id": ticket_id,
                    "property_name": company.get("name", ""),
                    "property_domain": company.get("domain") or props.get("domain") or "",
                    "submitter_email": deps.notify_email,
                    "rm_email": deps.notify_email,
                    "submitter_id": "",
                }
                brief_md = deps.generate_brief(parsed=parsed, company_id=company_id)
                record = deps.store_create(
                    ticket_id=ticket_id,
                    company_id=company_id,
                    deal_id=None,
                    submitter_email=deps.notify_email,
                    rm_email=deps.notify_email,
                    brief_markdown=brief_md,
                )
                updates["rpm_brief_approval_url"] = deps.approval_url(record["token"])
                updates["rpm_brief_status"] = BRIEF_PENDING_APPROVAL
                updates["rpm_brief_captured_at"] = now
                updates["rpm_brief_source"] = "auto_ple"
                action["brief"] = "captured"
                action["token"] = record["token"]
            except Exception as e:
                logger.exception("brief capture failed for %s", company_id)
                action["brief"] = f"error: {e}"
    else:
        action["brief"] = "skip:" + (props.get("rpm_brief_status") or "")

    # 3) Persist the property updates (single PATCH).
    if updates and not dry_run:
        try:
            deps.hs_update(company_id, {k: str(v) for k, v in updates.items()})
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("hs_update failed for %s: %s", company_id, e)
            action["write_error"] = str(e)
    action["updates"] = updates
    return action


def fetch_rpm_managed_companies() -> list[dict]:
    """Fetch every RPM-Managed company with the props the capture flow needs.

    Scope is plestatus == "RPM Managed" ONLY (Kyle's trigger), and we do NOT
    require aptiq_property_id — the whole point is to capture + start the
    AptIQ retry clock even before the ID resolves. Returns
    [{id, name, domain, props}] where props is the raw HubSpot property dict.
    """
    import requests
    from config import HUBSPOT_API_KEY
    if not HUBSPOT_API_KEY:
        return []
    headers = {"Authorization": f"Bearer {HUBSPOT_API_KEY}", "Content-Type": "application/json"}
    url = "https://api.hubapi.com/crm/v3/objects/companies/search"
    props = [
        "name", "domain", "plestatus", "aptiq_property_id", "aptiq_market_id",
        "rpm_brief_status", "rpm_brief_approval_url",
        "aptiq_match_status", "aptiq_first_attempt_at",
        "aptiq_last_attempt_at", "aptiq_match_attempts",
    ]
    out: list[dict] = []
    after = None
    while True:
        payload = {
            "filterGroups": [{"filters": [
                {"propertyName": "plestatus", "operator": "EQ", "value": RPM_MANAGED_STATUS},
            ]}],
            "properties": props,
            "limit": 100,
        }
        if after:
            payload["after"] = after
        r = requests.post(url, headers=headers, json=payload, timeout=30)
        r.raise_for_status()
        d = r.json()
        for raw in d.get("results", []):
            p = raw.get("properties") or {}
            out.append({
                "id": raw.get("id"),
                "name": p.get("name") or "",
                "domain": p.get("domain") or "",
                "props": p,
            })
        after = (d.get("paging") or {}).get("next", {}).get("after")
        if not after:
            break
        time.sleep(0.15)
    return out


def run_scan(companies: list[dict], *, deps: CaptureDeps | None = None,
             dry_run: bool = False, limit: int | None = None) -> dict:
    """Scan RPM-Managed companies, capturing briefs + tracking AptIQ matches.

    `companies` items: {id, name, domain, props}. Caller is responsible for
    pre-filtering to RPM Managed (or pass all and we filter on plestatus prop).
    """
    deps = (deps or CaptureDeps()).resolve()
    targets = [c for c in companies
               if (c.get("props", {}).get("plestatus") or c.get("plestatus") or "").strip()
               == RPM_MANAGED_STATUS or "plestatus" not in (c.get("props") or {})]
    # If callers already filtered, the filter above is a no-op safety net.
    if limit:
        targets = targets[:limit]
    actions = [process_company(c, deps=deps, dry_run=dry_run) for c in targets]
    return {
        "scanned": len(targets),
        "captured": sum(1 for a in actions if a.get("brief") == "captured"),
        "would_capture": sum(1 for a in actions if a.get("brief") == "would_capture"),
        "aptiq_matched": sum(1 for a in actions if a.get("aptiq_matched")),
        "aptiq_failed": sum(1 for a in actions if a.get("aptiq_status") == "failed"),
        "dry_run": dry_run,
        "actions": actions[:50],
    }
