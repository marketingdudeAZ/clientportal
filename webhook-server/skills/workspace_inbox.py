"""Workspace inbox — every approval source, normalized into one Item shape.

The contract's Item shape is in docs/handoffs/PORTAL_WORKSPACE_BUILD_PLAN.md.
This module is an adapter layer, not a new store: each source keeps its own
state, and each adapter reads it through the module the legacy portal already
uses.

Sources, and where each one is read:

| source          | reader                                              | lens    |
|-----------------|-----------------------------------------------------|---------|
| hubdb_rec       | hubdb_helpers.read_rows(rpm_recommendations)        | evolve  |
| loop_rec        | forecasting.get_latest_forecast()                   | evolve  |
| call_prep       | company `callprep_data_json` (hubspot_client)       | tailor  |
| content_brief   | hubdb_helpers.read_rows(rpm_content_briefs)         | tailor  |
| video_variant   | company `video_variants_json` (hubspot_client)      | amplify |
| ticket_profile  | ticket_profile_sync.list_proposals()                | express |
| onboarding_gap  | onboarding.list_onboarding()                        | express |
| portal_ticket   | portal_tickets.list_tickets()                       | amplify |
| service_ticket  | ticket_manager.list_tickets()                       | amplify |

Lens rationale: express = the property's context (profile, onboarding);
tailor = context shaped for one channel (call-prep channel recs, SEO briefs);
amplify = approve and publish (creative, requests the team executes);
evolve = results feeding back (Red Light findings, forecast recommendations).

Item ids are `"<source>:<source_id>"`. Every source has a stable id of its own
except `loop_rec`: forecast recommendations are regenerated on every forecast
run and carry no id (see routes/loop.py). Their id is a deterministic hash of
the forecast_id plus the recommendation's content, so it is stable for the life
of one forecast run and CHANGES when the next run lands, even if the
recommendation is identical. A decision recorded against an old id stays in the
trail of that run only.

Rules this file follows:

* `found` / `expect` / `if_skip` / `steps` / `receipts` come from fields the
  source actually carries, or are null/empty. LLM-written prose that contains a
  digit is withheld (`workspace_common.llm_text`).
* `steps` describe what the existing handler does on approval, read from its
  code path, never an invented plan.
* Every source is read independently. One dead source adds a gap; it never
  blanks the queue.
* Status for sources whose store does not record a workspace decision (loop
  recs, content briefs, "not now" on videos) is overlaid from the
  `workspace_decision` loop events this API writes.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable

from skills import workspace_common as wc

logger = logging.getLogger(__name__)

SOURCES = (
    "hubdb_rec", "loop_rec", "call_prep", "content_brief", "video_variant",
    "ticket_profile", "onboarding_gap", "portal_ticket", "service_ticket",
)

LENS = {
    "hubdb_rec": "evolve",
    "loop_rec": "evolve",
    "call_prep": "tailor",
    "content_brief": "tailor",
    "video_variant": "amplify",
    "ticket_profile": "express",
    "onboarding_gap": "express",
    "portal_ticket": "amplify",
    "service_ticket": "amplify",
}

# Existing loop stages a decision on each source is written under (ADR 0010).
STAGE = {
    "hubdb_rec": "optimize",
    "loop_rec": "optimize",
    "call_prep": "optimize",
    "content_brief": "engage",
    "video_variant": "attract",
    "ticket_profile": "engage",
    "onboarding_gap": "ops",
    "portal_ticket": "ops",
    "service_ticket": "ops",
}

# Sources with an existing approval handler (see workspace_decisions).
DECIDABLE = frozenset({
    "hubdb_rec", "loop_rec", "call_prep", "content_brief", "video_variant",
    "ticket_profile",
})

# Shown on the client view.
CLIENT_VISIBLE = frozenset({
    "hubdb_rec", "content_brief", "video_variant", "portal_ticket", "service_ticket",
})
# Never shown to a client-role caller at all.
INTERNAL_ONLY = frozenset({"loop_rec", "call_prep", "onboarding_gap"})

STATUSES = ("to_do", "in_motion", "done")

REASONS = {
    "wrong_data": "The data is wrong",
    "already_handled": "Already handled",
    "not_priority": "Not a priority",
    "discuss_on_call": "Discuss on our call",
}

# One read of the company serves the inbox and the property views (the
# hubspot_client cache is keyed by the property list, so one list = one read).
PROPERTY_FIELDS = (
    "uuid", "name", "plestatus", "seo_tier",
    "address", "city", "state", "zip", "domain", "website", "managementstart",
    "totalunits", "target_occupancy",
    "aptiq_property_id", "aptiq_market_id", "hyly_property_id",
    "ga4_property_id", "google_ads_customer_id",
    "marketing_manager", "marketing_manager_email", "marketing_director_email",
    "marketing_rvp_email", "hubspot_owner_id",
    "fluency_romance",
    "callprep_data_json", "callprep_cycle_month",
    "video_variants_json", "video_cycle_month",
)


class PropertyNotFound(LookupError):
    """The company id does not exist in HubSpot."""


@dataclass
class PropertyContext:
    company_id: str
    uuid: str
    name: str
    props: dict = field(default_factory=dict)


def load_context(company_id: str) -> PropertyContext:
    """One HubSpot read for everything the workspace needs about a property.

    R1: reads `uuid`, never writes it.
    """
    import hubspot_client

    cid = str(company_id).strip()
    try:
        props = hubspot_client.get_company(cid, list(PROPERTY_FIELDS))
    except hubspot_client.HubSpotAuthError:
        raise
    except hubspot_client.HubSpotError as exc:
        if str(exc).startswith("404"):
            raise PropertyNotFound(cid) from exc
        raise
    if not props:
        raise PropertyNotFound(cid)
    return PropertyContext(
        company_id=cid,
        uuid=str(props.get("uuid") or "").strip(),
        name=str(props.get("name") or ""),
        props=props,
    )


# ── ids ──────────────────────────────────────────────────────────────────────

def item_id(source: str, source_id: Any) -> str:
    return f"{source}:{source_id}"


def parse_item_id(value: str) -> tuple[str, str]:
    """"hubdb_rec:991" → ("hubdb_rec", "991"). ValueError if not a known source."""
    source, sep, source_id = str(value or "").partition(":")
    if not sep or source not in SOURCES or not source_id:
        raise ValueError(f"not a workspace item id: {value!r}")
    return source, source_id


def loop_rec_hash(forecast_id: Any, recommendation: dict) -> str:
    """Deterministic id for a forecast recommendation (they carry none).

    Stable within one forecast run; a new run yields new ids.
    """
    canon = json.dumps(
        {"forecast_id": str(forecast_id or ""), "rec": recommendation},
        sort_keys=True, default=str, separators=(",", ":"),
    )
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


# ── item construction ────────────────────────────────────────────────────────

def _new_item(source: str, source_id: Any, title: str, **fields: Any) -> dict:
    item = {
        "id": item_id(source, source_id),
        "source": source,
        "source_id": str(source_id),
        "title": title,
        "found": None,
        "expect": None,
        "if_skip": None,
        "receipts": [],
        "channels": [],
        "lens": LENS[source],
        "start_by": None,
        "due": None,
        "status": "to_do",
        "needs_approval": False,
        "client_visible": source in CLIENT_VISIBLE,
        "internal_only": source in INTERNAL_ONLY,
        "owner": None,
        "comments_count": None,
        "cost_note": None,
        "steps": [],
        "trail": [],
        "actions": {"approve": False, "not_now": False},
        # internal bookkeeping, stripped by workspace_common.public()
        "_created": None,
        "_closed": None,
        "_closed_as": None,
        "_raw": {},
    }
    item.update(fields)
    return item


def _receipt(label: str, source: str, as_of: Any) -> dict:
    return {"label": label, "source": source, "as_of": wc.to_iso_ts(as_of)}


def _step(label: str, kind: str, channel: str | None = None) -> dict:
    return {"when": None, "label": label, "channel": channel, "kind": kind, "status": "pending"}


def _withheld_gap(gaps: list, source: str, count: int) -> None:
    if count:
        gaps.append(wc.gap(
            f"source:{source}",
            f"LLM-written text containing numbers was withheld on {count} item(s); "
            "those numbers have no receipt.",
            source=source,
        ))


# ── adapters ─────────────────────────────────────────────────────────────────

_REC_STATUS = {"pending": "to_do", "approved": "in_motion", "dismissed": "done"}
_REC_TITLES = {
    "budget_change": "Budget change recommendation",
    "strategy_change": "Strategy change recommendation",
    "package_upgrade": "Package upgrade recommendation",
}
# What approval_agent.route_approval does per rec_type.
_REC_STEPS = {
    "budget_change": [
        _step("Draft deal created in HubSpot for signature", "auto"),
        _step("Paid media task opened in ClickUp", "queued"),
        _step("Account manager gets the deal signed before any spend changes", "person"),
    ],
    "strategy_change": [
        _step("Task opened in the matching ClickUp list", "queued"),
        _step("Account manager follow-up task in HubSpot", "person"),
    ],
    "package_upgrade": [
        _step("Draft deal created in HubSpot for signature", "auto"),
        _step("Account manager gets the upgrade signed", "person"),
    ],
}


def _hubdb_recs(ctx: PropertyContext, gaps: list, today: date) -> list:
    from config import HUBDB_RECOMMENDATIONS_TABLE_ID
    if not HUBDB_RECOMMENDATIONS_TABLE_ID:
        gaps.append(wc.gap("source:hubdb_rec", "HUBDB_RECOMMENDATIONS_TABLE_ID is not configured",
                           source="hubdb_rec"))
        return []
    if not ctx.uuid:
        gaps.append(wc.gap("source:hubdb_rec",
                           "Property has no uuid; recommendation cards are keyed by uuid",
                           source="hubdb_rec"))
        return []
    from hubdb_helpers import read_rows
    rows = read_rows(HUBDB_RECOMMENDATIONS_TABLE_ID, filters={"property_uuid": ctx.uuid})

    items, withheld = [], 0
    for row in rows:
        rec_id = str(row.get("rec_id") or "").strip()
        if not rec_id:
            continue
        rec_type = str(row.get("rec_type") or "").strip()
        raw_title = str(row.get("title") or "")
        raw_body = str(row.get("body") or "")
        title = wc.llm_text(raw_title)
        found = wc.llm_text(raw_body)
        if (raw_title and not title) or (raw_body and not found):
            withheld += 1
        status_raw = str(row.get("status") or "").strip().lower()
        status = _REC_STATUS.get(status_raw, "in_motion")
        created = row.get("created_date")
        origin = str(row.get("source") or "red_light")
        origin_label = "Red Light report" if origin == "red_light" else origin.replace("_", " ")
        item = _new_item(
            "hubdb_rec", rec_id,
            title or _REC_TITLES.get(rec_type, "Recommendation"),
            found=found,
            receipts=[_receipt(f"{origin_label} finding", origin, created)],
            status=status,
            needs_approval=status_raw == "pending",
            steps=[dict(s) for s in _REC_STEPS.get(rec_type, [])],
            trail=([{"at": wc.to_iso_ts(created), "actor": origin_label, "text": "Opened"}]
                   if created else []),
            _created=created,
            _closed=row.get("approved_date") if status_raw == "approved" else None,
            _closed_as="dismissed" if status_raw == "dismissed" else None,
            _raw={"rec_id": rec_id, "rec_type": rec_type, "title": raw_title, "body": raw_body},
        )
        items.append(item)
    _withheld_gap(gaps, "hubdb_rec", withheld)
    return items


_NOOP_LOOP_ACTIONS = ("hold", "collect_more_data", "expand_inputs")


def _loop_recs(ctx: PropertyContext, gaps: list, today: date) -> list:
    if not ctx.uuid:
        return []
    import forecasting
    forecast = forecasting.get_latest_forecast(ctx.uuid) or {}
    recs = forecast.get("recommendations") or []
    if isinstance(recs, str):
        try:
            recs = json.loads(recs)
        except ValueError:
            recs = []
    forecast_id = forecast.get("forecast_id")
    run_at = forecast.get("run_at")
    horizon = forecast.get("horizon_days")

    items = []
    for rec in recs:
        if not isinstance(rec, dict) or rec.get("action") in _NOOP_LOOP_ACTIONS:
            continue
        action = str(rec.get("action") or "")
        frm, to = rec.get("from_channel"), rec.get("to_channel")
        amount = wc.to_float(rec.get("amount"))
        if action == "shift_budget" and frm and to and amount is not None:
            title = f"Shift ${amount:,.0f} from {frm} to {to}"
        else:
            title = action.replace("_", " ").capitalize() or "Forecast recommendation"
        impact = wc.to_float(rec.get("forecast_impact"))
        expect = None
        if impact is not None:
            window = f"{int(horizon)} days" if wc.to_float(horizon) else "the forecast horizon"
            expect = f"Forecast: {impact:+g} leases over {window}"
        items.append(_new_item(
            "loop_rec", loop_rec_hash(forecast_id, rec), title,
            # `reason` is built by forecasting.generate_recommendations from the
            # channel CPLs it computed, not by an LLM.
            found=str(rec.get("reason") or "") or None,
            expect=expect,
            receipts=[_receipt(f"Forecast run {forecast_id}", "forecast_runs", run_at)],
            channels=[c for c in (frm, to) if c],
            needs_approval=True,
            steps=[
                _step("Approval recorded on the optimize loop", "auto"),
                _step("No budget moves until a deal is drafted and signed", "person"),
            ],
            trail=[{"at": wc.to_iso_ts(run_at), "actor": "forecasting", "text": "Proposed"}]
            if run_at else [],
            _created=run_at,
            _raw={"forecast_id": forecast_id, "recommendation": rec},
        ))
    return items


_CALLPREP_STATUS = {"pending": "to_do", "approved": "in_motion", "dismissed": "done"}


def _call_prep(ctx: PropertyContext, gaps: list, today: date) -> list:
    raw = ctx.props.get("callprep_data_json") or ""
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except ValueError:
        gaps.append(wc.gap("source:call_prep", "callprep_data_json is not valid JSON",
                           source="call_prep"))
        return []
    if not isinstance(payload, dict):
        return []
    month = str(ctx.props.get("callprep_cycle_month") or payload.get("cycle_month") or "").strip()
    if month != today.strftime("%Y-%m"):
        # The legacy GET regenerates on a new month and overwrites the old
        # items, so last month's are not actionable.
        gaps.append(wc.gap("source:call_prep",
                           f"Call prep on record is for {month or 'an unknown month'}; "
                           "this month's has not been generated yet",
                           source="call_prep"))
        return []
    generated_at = payload.get("generated_at")
    due = wc.month_end(month)

    items, withheld = [], 0
    for rec in payload.get("recommendations") or []:
        if not isinstance(rec, dict) or not rec.get("rec_id"):
            continue
        raw_title, raw_body = str(rec.get("title") or ""), str(rec.get("body") or "")
        title, found = wc.llm_text(raw_title), wc.llm_text(raw_body)
        if (raw_title and not title) or (raw_body and not found):
            withheld += 1
        channel = str(rec.get("channel") or "").strip()
        status_raw = str(rec.get("status") or "pending").lower()
        status = _CALLPREP_STATUS.get(status_raw, "in_motion")
        trail = []
        if generated_at:
            trail.append({"at": wc.to_iso_ts(generated_at), "actor": "call prep", "text": "Generated"})
        if rec.get("actioned_at"):
            trail.append({"at": wc.to_iso_ts(rec.get("actioned_at")),
                          "actor": rec.get("actioned_by") or None,
                          "text": status_raw.capitalize()})
        items.append(_new_item(
            "call_prep", rec["rec_id"],
            title or (f"Call prep: {channel} recommendation" if channel else "Call prep recommendation"),
            found=found,
            receipts=[_receipt(f"Call prep for {month}", "call_prep", generated_at)],
            channels=[channel] if channel else [],
            due=due,
            status=status,
            needs_approval=status_raw == "pending",
            steps=[
                _step("Marked approved on the call prep record", "auto"),
                _step("Task opened in ClickUp for the team", "queued"),
            ],
            trail=trail,
            _created=generated_at,
            _closed=rec.get("actioned_at") if status != "to_do" else None,
            _closed_as="dismissed" if status_raw == "dismissed" else None,
            _raw={"rec_id": rec["rec_id"]},
        ))
    _withheld_gap(gaps, "call_prep", withheld)
    return items


def _content_briefs(ctx: PropertyContext, gaps: list, today: date) -> list:
    from config import HUBDB_CONTENT_BRIEFS_TABLE_ID
    if not HUBDB_CONTENT_BRIEFS_TABLE_ID or not ctx.uuid:
        return []
    # Same entitlement the legacy /api/content/* routes enforce.
    from seo_entitlement import has_feature
    if not has_feature(ctx.props.get("seo_tier") or None, "content_briefs"):
        return []
    from hubdb_helpers import read_rows
    rows = read_rows(HUBDB_CONTENT_BRIEFS_TABLE_ID, filters={"property_uuid": ctx.uuid})
    items = []
    for row in rows:
        brief_id = str(row.get("brief_id") or "").strip()
        if not brief_id:
            continue
        keyword = str(row.get("hub_keyword") or "").strip()
        h1 = wc.llm_text(row.get("h1"))
        status_raw = str(row.get("status") or "").strip().lower()
        if status_raw == "generated":
            status = "to_do"
        elif status_raw in ("published", "complete", "completed", "done"):
            status = "done"
        else:
            status = "in_motion"
        generated_at = row.get("generated_at")
        items.append(_new_item(
            "content_brief", brief_id,
            f"Content brief: {h1 or keyword or brief_id}",
            found=f"Written for the search “{keyword}”." if keyword else None,
            receipts=[_receipt(f"Brief generated for “{keyword}”" if keyword else "Brief generated",
                               "content_briefs", generated_at)],
            channels=["seo"],
            status=status,
            needs_approval=status_raw == "generated",
            steps=[
                _step("SEO content task opened in ClickUp", "queued", "seo"),
                _step("Account manager task logged in HubSpot", "person"),
            ],
            trail=[{"at": wc.to_iso_ts(generated_at), "actor": "content planner", "text": "Generated"}]
            if generated_at else [],
            _created=generated_at,
            _raw={"brief_id": brief_id, "hub_keyword": keyword, "h1": row.get("h1") or ""},
        ))
    return items


def _video_variants(ctx: PropertyContext, gaps: list, today: date) -> list:
    raw = ctx.props.get("video_variants_json") or ""
    if not raw:
        return []
    try:
        variants = json.loads(raw)
    except ValueError:
        gaps.append(wc.gap("source:video_variant", "video_variants_json is not valid JSON",
                           source="video_variant"))
        return []
    cycle = str(ctx.props.get("video_cycle_month") or "").strip()
    items, withheld = [], 0
    for v in variants if isinstance(variants, list) else []:
        if not isinstance(v, dict) or not v.get("variant_id"):
            continue
        status_raw = str(v.get("status") or "").lower()
        failed = status_raw in ("failed", "error")
        if status_raw == "pending_review":
            status = "to_do"
        elif status_raw == "approved":
            status = "done"
        elif failed:
            status = "to_do"
        else:
            status = "in_motion"
        raw_title, raw_why = str(v.get("title") or ""), str(v.get("rationale") or "")
        title, found = wc.llm_text(raw_title), wc.llm_text(raw_why)
        if (raw_title and not title) or (raw_why and not found):
            withheld += 1
        created = v.get("created_at") or v.get("generated_at")
        items.append(_new_item(
            "video_variant", v["variant_id"],
            title or (f"Video variant for {cycle}" if cycle else "Video variant"),
            found=found,
            receipts=[_receipt(f"Video cycle {cycle}" if cycle else "Video variant",
                               "video_variants", created)],
            channels=["video"],
            status=status,
            needs_approval=status_raw == "pending_review",
            # A failed render needs a person to regenerate it; it is not a
            # client decision.
            client_visible=not failed,
            internal_only=failed,
            steps=[
                _step("Variant marked approved on the property record", "auto", "video"),
                _step("Added to the property asset library", "auto", "video"),
            ],
            _created=created,
            _closed=v.get("approved_at") if status_raw == "approved" else None,
            _closed_as="failed" if failed else None,
            _raw={"variant_id": v["variant_id"]},
        ))
    _withheld_gap(gaps, "video_variant", withheld)
    return items


def _ticket_profile(ctx: PropertyContext, gaps: list, today: date) -> list:
    import ticket_profile_sync
    if not ticket_profile_sync.enabled():
        return []
    rows = ticket_profile_sync.list_proposals(ctx.company_id, ctx.uuid, status=None, limit=100)
    items, withheld = [], 0
    for row in rows:
        pid = str(row.get("proposal_id") or "").strip()
        if not pid:
            continue
        status_raw = str(row.get("status") or "").lower()
        status = "to_do" if status_raw == "proposed" else "done"
        label = row.get("field_label") or row.get("field_key") or "a field"
        proposed = row.get("proposed_value")
        if row.get("extractor") == "ai":
            clean = wc.llm_text(proposed)
            if proposed and not clean:
                withheld += 1
            proposed = clean
        current = row.get("current_value")
        receipts = [_receipt(f"ClickUp ticket {row.get('task_id')}", "clickup", row.get("created_at"))]
        if row.get("conflicts_with_override"):
            receipts.append(_receipt("This field already has a human edit", "community_brief", None))
        items.append(_new_item(
            "ticket_profile", pid,
            f"Update “{label}” on the property profile",
            found=f"A completed ticket suggests: {wc.truncate(proposed)}" if proposed else None,
            if_skip=f"The profile keeps: {wc.truncate(current)}" if current else None,
            receipts=receipts,
            status=status,
            needs_approval=status_raw == "proposed",
            owner=row.get("actor") or None,
            steps=[
                _step("Written to the profile override (the human edit wins)", "auto"),
                _step("Reaches Fluency on the next feed sync", "queued"),
            ],
            trail=[{"at": wc.to_iso_ts(row.get("created_at")), "actor": "ticket loop", "text": "Proposed"}]
            if row.get("created_at") else [],
            _created=row.get("created_at"),
            _closed=row.get("created_at") if status == "done" else None,
            _closed_as="rejected" if status_raw == "rejected" else None,
            _raw={"proposal_id": pid},
        ))
    _withheld_gap(gaps, "ticket_profile", withheld)
    return items


_ONBOARDING_TITLES = {
    "brief": "Finish the property brief",
    "budget": "Set the marketing budget",
    "creative": "Upload photos or creative",
    "gbp": "Connect the Google Business Profile",
}
_ONBOARDING_TTL = 600.0
_onboarding_cache: dict = {}
_onboarding_lock = threading.Lock()


def _onboarding_rows() -> tuple[list, str]:
    """onboarding.list_onboarding() is a portfolio-wide scan; cache it."""
    with _onboarding_lock:
        hit = _onboarding_cache.get("rows")
        if hit and (time.monotonic() - hit[0]) < _ONBOARDING_TTL:
            return hit[1], hit[2]
    import onboarding
    rows = onboarding.list_onboarding() or []
    fetched = wc.now_iso()
    with _onboarding_lock:
        _onboarding_cache["rows"] = (time.monotonic(), rows, fetched)
    return rows, fetched


def _onboarding_gaps(ctx: PropertyContext, gaps: list, today: date) -> list:
    if str(ctx.props.get("plestatus") or "").strip() != "Onboarding":
        return []
    rows, fetched = _onboarding_rows()
    row = next((r for r in rows if str(r.get("company_id")) == ctx.company_id), None)
    if not row:
        return []
    done, total = row.get("done"), row.get("total")
    items = []
    for key, ok in (row.get("checklist") or {}).items():
        if ok:
            continue
        items.append(_new_item(
            "onboarding_gap", f"{ctx.company_id}-{key}",
            _ONBOARDING_TITLES.get(key, f"Onboarding: {key}"),
            found=f"Onboarding checklist: {done} of {total} complete.",
            receipts=[_receipt(f"{done} of {total} onboarding checks complete", "hubspot_company", fetched)],
        ))
    return items


def _portal_status(label: str) -> str:
    s = (label or "").strip().lower()
    if s == "done":
        return "done"
    if s == "needs your approval":
        return "to_do"
    return "in_motion"


def _portal_tickets(ctx: PropertyContext, gaps: list, today: date) -> list:
    import portal_tickets
    rows = portal_tickets.list_tickets(ctx.company_id, property_uuid=ctx.uuid, limit=50)
    items, unresolved = [], 0
    for r in rows:
        tid = str(r.get("id") or "").strip()
        if not tid:
            continue
        if r.get("unresolved"):
            unresolved += 1
        created = r.get("created_ts")
        who = r.get("submitted_by") or ""
        status = _portal_status(r.get("status"))
        items.append(_new_item(
            "portal_ticket", tid,
            r.get("subject") or "Request",
            receipts=[_receipt(f"Filed through the portal{' by ' + who if who else ''}",
                               "clickup", created)],
            status=status,
            trail=[{"at": wc.to_iso_ts(created), "actor": who or "portal", "text": "Filed"}]
            if created else [],
            _created=created,
            _raw={"status": r.get("status"), "url": r.get("url")},
        ))
    if portal_tickets.tracking_degraded():
        # list_tickets returns [] both for "no requests" and for "the mapping
        # store could not be read"; only the second is a gap.
        gaps.append(wc.gap("source:portal_ticket",
                           "The portal ticket store could not be read, so requests filed "
                           "through the portal may be missing",
                           source="portal_ticket"))
    if unresolved:
        gaps.append(wc.gap("source:portal_ticket",
                           f"ClickUp did not return live status for {unresolved} request(s)",
                           source="portal_ticket"))
    return items


def _service_tickets(ctx: PropertyContext, gaps: list, today: date) -> list:
    import ticket_manager
    rows = ticket_manager.list_tickets(ctx.company_id, include_closed=True)
    closed = ticket_manager.STAGES.get("closed")
    items = []
    for t in rows:
        tid = str(t.get("id") or "").strip()
        if not tid:
            continue
        is_closed = str(t.get("stage_id")) == str(closed)
        owner = t.get("owner_name") or None
        if owner == "Your AM":          # ticket_manager's placeholder, not a name
            owner = None
        created, updated = t.get("created_at"), t.get("updated_at")
        items.append(_new_item(
            "service_ticket", tid,
            t.get("subject") or "Service ticket",
            receipts=[_receipt(f"HubSpot ticket, {t.get('stage_label') or 'open'}",
                               "hubspot_tickets", updated or created)],
            status="done" if is_closed else "in_motion",
            owner=owner,
            trail=[{"at": wc.to_iso_ts(created), "actor": t.get("submitter_email") or "HubSpot",
                    "text": "Opened"}] if created else [],
            _created=created,
            _closed=updated if is_closed else None,
        ))
    return items


ADAPTERS: dict[str, Callable[[PropertyContext, list, date], list]] = {
    "hubdb_rec": _hubdb_recs,
    "loop_rec": _loop_recs,
    "call_prep": _call_prep,
    "content_brief": _content_briefs,
    "video_variant": _video_variants,
    "ticket_profile": _ticket_profile,
    "onboarding_gap": _onboarding_gaps,
    "portal_ticket": _portal_tickets,
    "service_ticket": _service_tickets,
}


# ── decision history overlay ─────────────────────────────────────────────────

def decision_history(ctx: PropertyContext, gaps: list) -> dict:
    """item_id → [decision, …] oldest first, from `workspace_decision` events."""
    if not ctx.uuid:
        return {}
    import loop_writer
    if loop_writer._bq() is None:
        gaps.append(wc.gap("trail", "Decision history is unavailable: BigQuery is not configured"))
        return {}
    out: dict = {}
    for ev in loop_writer.query_recent(ctx.uuid, limit=500):
        if ev.get("event_type") != "workspace_decision":
            continue
        payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
        iid = payload.get("item_id")
        if not iid:
            continue
        out.setdefault(iid, []).append({
            "at": ev.get("occurred_at"),
            "action": payload.get("action"),
            "reason": payload.get("reason"),
            "actor": payload.get("actor"),
            "outcome": payload.get("outcome", "ok"),
        })
    for decisions in out.values():
        decisions.sort(key=lambda d: str(d.get("at") or ""))
    return out


def decision_text(action: str, reason: str | None) -> str:
    if action == "approve":
        return "Approved"
    return f"Not now: {REASONS.get(reason or '', reason or 'no reason')}"


def apply_decisions(items: list, history: dict) -> None:
    for item in items:
        decisions = history.get(item["id"]) or []
        for d in decisions:
            item["trail"].append({"at": wc.to_iso_ts(d.get("at")), "actor": d.get("actor"),
                                  "text": decision_text(d.get("action"), d.get("reason"))})
        ok = [d for d in decisions if d.get("outcome", "ok") in ("ok", "partial")]
        if ok and item["status"] == "to_do":
            last = ok[-1]
            if last.get("action") == "approve":
                item["status"] = "in_motion"
            elif last.get("action") == "not_now":
                item["status"] = "done"
                item["_closed_as"] = "not_now"
            item["_closed"] = last.get("at")


def finalize(item: dict) -> dict:
    """Derive the fields that depend on final status."""
    actionable = item["status"] == "to_do" and item["needs_approval"] and item["source"] in DECIDABLE
    item["needs_approval"] = bool(item["needs_approval"] and item["status"] == "to_do")
    item["actions"] = {"approve": actionable, "not_now": actionable}
    return item


def _fair_housing_pass(items: list, gaps: list) -> None:
    for item in items:
        flags = wc.fair_housing_flags(item.get("title"), item.get("found"),
                                      item.get("expect"), item.get("if_skip"))
        if flags:
            item["client_visible"] = False
            item["internal_only"] = True
            gaps.append(wc.gap(f"item:{item['id']}",
                               f"Held from clients for Fair Housing review: {', '.join(flags)}",
                               source=item["source"]))


# ── collection ───────────────────────────────────────────────────────────────

def collect(ctx: PropertyContext, *, sources: tuple | list | None = None,
            today: date | None = None, internal: bool = True,
            with_history: bool = True) -> tuple[list, list]:
    """Read every requested source for one property. Returns (items, gaps).

    `internal=False` drops internal-only items and the gaps that describe them.
    """
    today = today or date.today()
    wanted = [s for s in (sources or SOURCES) if s in ADAPTERS]
    per_source: dict = {}

    def _run(src: str):
        local_gaps: list = []
        try:
            return src, ADAPTERS[src](ctx, local_gaps, today), local_gaps
        except Exception as exc:  # noqa: BLE001 — one dead source never blanks the queue
            logger.warning("workspace inbox: %s failed for %s: %s", src, ctx.company_id, exc)
            local_gaps.append(wc.gap(f"source:{src}", f"Could not be read ({type(exc).__name__})",
                                     source=src))
            return src, [], local_gaps

    if len(wanted) == 1:
        results = [_run(wanted[0])]
    else:
        with ThreadPoolExecutor(max_workers=min(len(wanted), 9)) as pool:
            results = list(pool.map(_run, wanted))
    for src, items, local_gaps in results:
        per_source[src] = (items, local_gaps)

    items: list = []
    gaps: list = []
    for src in wanted:
        items += per_source[src][0]
        gaps += per_source[src][1]

    if with_history:
        try:
            apply_decisions(items, decision_history(ctx, gaps))
        except Exception as exc:  # noqa: BLE001
            logger.warning("workspace inbox: decision history failed for %s: %s", ctx.company_id, exc)
            gaps.append(wc.gap("trail", f"Decision history could not be read ({type(exc).__name__})"))

    for item in items:
        finalize(item)
    _fair_housing_pass(items, gaps)

    if not internal:
        hidden_sources = set(INTERNAL_ONLY)
        hidden_ids = {i["id"] for i in items if i["internal_only"]}
        items = [i for i in items if not i["internal_only"]]
        gaps = [g for g in gaps
                if g.get("_source") not in hidden_sources
                and not (g["field"].startswith("item:") and g["field"][5:] in hidden_ids)]
    return items, gaps


def find_item(ctx: PropertyContext, value: str, *, today: date | None = None,
              internal: bool = True) -> tuple[dict | None, list]:
    """One item by id, reading only its source. (None, gaps) if absent."""
    source, _ = parse_item_id(value)
    items, gaps = collect(ctx, sources=[source], today=today, internal=internal)
    return next((i for i in items if i["id"] == value), None), gaps


# ── views over a collection ──────────────────────────────────────────────────

def item_date(item: dict) -> date | None:
    return wc.to_date(item.get("start_by") or item.get("due"))


def _sort_key(item: dict):
    d = item_date(item)
    return (d is None, d or date.max, str(item.get("title") or ""))


def build_work(items: list, gaps: list, *, status: str = "to_do",
               today: date | None = None) -> dict:
    """The `GET /work` payload.

    Grouping: an open item dated before today is late; dated more than six days
    out is later; everything else, including every undated item, is this week.
    No source carries a start-by date today, so undated is the common case.
    """
    today = today or date.today()
    horizon = today + timedelta(days=6)
    counts = {s: sum(1 for i in items if i["status"] == s) for s in STATUSES}
    open_items = [i for i in items if i["status"] in ("to_do", "in_motion")]
    late_open = [i for i in open_items if item_date(i) and item_date(i) < today]
    upcoming = sorted(d for d in (item_date(i) for i in open_items) if d and d >= today)

    selected = items if status == "all" else [i for i in items if i["status"] == status]
    late, this_week, later = [], [], []
    for i in sorted(selected, key=_sort_key):
        d = item_date(i)
        if d and d < today and i["status"] != "done":
            late.append(i)
        elif d and d > horizon:
            later.append(i)
        else:
            this_week.append(i)
    return {
        "summary": {
            "open": len(open_items),
            "late": len(late_open),
            "next_deadline": upcoming[0].isoformat() if upcoming else None,
        },
        "counts": counts,
        "groups": {
            "late": [wc.public(i) for i in late],
            "this_week": [wc.public(i) for i in this_week],
            "later": {"count": len(later), "titles": [i["title"] for i in later[:5]]},
        },
        "hidden_count": len(items) - len(selected),
        "gaps": wc.public(gaps),
    }


_NOT_COMPLETED = ("dismissed", "rejected", "failed", "not_now")


def build_client_view(items: list, gaps: list, *, today: date | None = None) -> dict:
    """The `GET /client-view` payload: committed work and completed work only."""
    today = today or date.today()
    q_start = wc.quarter_start(today)
    visible = [i for i in items if i["client_visible"] and not i["internal_only"]]

    changing = []
    for i in sorted((i for i in visible if i["status"] == "in_motion"), key=_sort_key):
        changing.append({
            "date": i.get("due") or i.get("start_by") or wc.to_iso_date(i.get("_closed") or i.get("_created")),
            "title": i["title"],
            "note": i.get("found"),
            "status": "in_production",
        })

    done, undated = [], 0
    for i in visible:
        if i["status"] != "done" or i.get("_closed_as") in _NOT_COMPLETED:
            continue
        d = wc.to_date(i.get("_closed") or i.get("_created"))
        if d is None:
            undated += 1
            continue
        if d >= q_start:
            done.append((d, i))
    done.sort(key=lambda p: p[0], reverse=True)
    out_gaps = [g for g in gaps if not g.get("_source") or g["_source"] in CLIENT_VISIBLE]
    if undated:
        out_gaps.append(wc.gap("done_this_quarter",
                               f"{undated} completed item(s) carry no completion date and are not listed"))
    return {
        "changing": changing,
        "done_this_quarter": [
            {"date": d.isoformat(), "title": i["title"], "note": i.get("found"), "status": "complete"}
            for d, i in done[:20]
        ],
        "done_count": len(done),
        "hidden_open_count": sum(1 for i in items
                                 if i["status"] in ("to_do", "in_motion")
                                 and not (i["client_visible"] and not i["internal_only"])),
        "gaps": wc.public(out_gaps),
    }
