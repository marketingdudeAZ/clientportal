"""Workspace v3 Media Plan — `GET /api/workspace/media-plan?company_id=`.

The plan is the property's contracted monthly line items (spend sheet, via
`workspace_cache`), one row per channel, laid across the fiscal year (July to
June). Each channel has a `mode` (Round 4, Kyle's definition):

* always_on — runs all year: SEO / content, website, reputation, social posting,
  email, management; (Google Business Profile and base ILS listings are always-on
  too, but are not deal line items, so they are a gap). Monthly values are flat.
* flighted — follows exposure: paid search, PMax, display, retargeting, Meta and
  other paid social/video, geofence; (ILS upgrades are flighted but not line
  items). Monthly values follow the AptIQ exposure forecast, which covers three
  months; months outside it are null with a gap.

* `objective` — `plan_stages.determine_mode` over AptIQ occupancy, target and
  exposure (the same posture the legacy plan builder uses).
* `months[].units_to_lease` — AptIQ exposure windows for the next three months;
  later months are null with a gap. `forecasting.py` forecasts leases over a
  30-day horizon and has no monthly unit forecast.
* `cpl_target` — null: no CPL target is stored per channel.

Notes (Round 4, bug 3) are generated from the channel mix and the property's
pending recommendations, and never contradict them: a channel with a
recommendation waiting on a decision gets a "holds until you decide" note, and
`guard_notes` drops any note that would tell the reader to keep, grow or continue
a channel a pending recommendation wants to change.

`POST /api/workspace/media-plan/regenerate` never changes a budget. It files a
work item asking a person to draft a refreshed plan, through the existing portal
ticket path, behind verified identity (money rule).
"""

from __future__ import annotations

import logging
import re
from datetime import date

from skills import workspace_common as wc

logger = logging.getLogger(__name__)

REGENERATE_TICKET_TYPE = "campaign_review"

# spend sheet SKU column → (row key, label, mode, CHANNEL_TERMS key used to match recommendations)
SKU_CHANNELS = {
    "search": ("paid_search", "Paid search", "flighted", "paid_search"),
    "pmax": ("pmax", "PMax", "flighted", "paid_search"),
    "display": ("display", "Display", "flighted", "paid_search"),
    "retargeting": ("retargeting", "Retargeting", "flighted", "paid_search"),
    "paid_social": ("meta", "Meta", "flighted", "paid_social"),
    "tiktok": ("tiktok", "TikTok", "flighted", "paid_social"),
    "youtube": ("youtube", "YouTube", "flighted", "paid_social"),
    "ctv": ("ctv", "CTV", "flighted", "paid_social"),
    "demand_gen": ("demand_gen", "Demand Gen", "flighted", "paid_social"),
    "geofence": ("geofence", "Geofence", "flighted", "paid_social"),
    "seo": ("seo", "SEO and content", "always_on", "seo"),
    "reputation": ("reputation", "Reputation", "always_on", "reputation"),
    "social_posting": ("social_posting", "Social posting", "always_on", "creative"),
    "eblast": ("email", "Email", "always_on", "creative"),
    "email_drip": ("email", "Email", "always_on", "creative"),
    "website_hosting": ("website", "Website", "always_on", "website"),
    "mgmt_fee": ("management", "Management fee", "always_on", "fees"),
}
MODES = ("always_on", "flighted")

# Words that tie text to a channel. Keys are spend_sheet_to_channels buckets plus
# the ILS, GBP and website channels Kyle's always-on list names.
CHANNEL_TERMS = {
    "paid_search": ("paid search", "search ads", "ppc", "google ads", "pmax", "performance max", "display",
                    "retargeting"),
    "paid_social": ("paid social", "meta", "facebook", "instagram", "tiktok", "social ads", "youtube", "ctv",
                    "demand gen", "geofence"),
    "seo": ("seo", "organic search", "content"),
    "reputation": ("reputation", "reviews"),
    "creative": ("social posting", "email", "eblast", "creative"),
    "ils": ("apartments.com", "costar", "zillow", "ils", "listing"),
    "gbp": ("google business profile", "gbp"),
    "website": ("website",),
    "fees": ("management fee", "hosting"),
}
CHANNEL_OF_SOURCE_FIELD = {"paid_search": "paid_search", "paid_social": "paid_social", "seo": "seo",
                           "reputation": "reputation", "social": "creative", "video_creative": "paid_social"}
_CONTINUE = re.compile(r"\b(keep|keeps|continue|continues|maintain|maintains|increase|increases|grow|expand|"
                       r"add|running all year|stays? on|hold steady)\b", re.IGNORECASE)
PENDING_SOURCES = ("hubdb_rec", "loop_rec", "call_prep", "content_brief")


def _mentions(text: str, key: str) -> bool:
    t = (text or "").lower()
    return any(re.search(r"(?<![a-z])" + re.escape(term) + r"(?![a-z])", t) for term in CHANNEL_TERMS.get(key, ()))


def item_channels(item: dict) -> set:
    """Channel keys a work item touches, from its own fields."""
    keys = set()
    for c in item.get("channels") or []:
        keys.add(CHANNEL_OF_SOURCE_FIELD.get(c, c))
    raw = item.get("_raw") or {}
    rec = raw.get("recommendation") or {}
    for c in (rec.get("from_channel"), rec.get("to_channel")):
        if c:
            keys.add(c)
    text = " ".join(str(x or "") for x in (item.get("title"), item.get("found"), raw.get("title"), raw.get("body")))
    keys |= {k for k in CHANNEL_TERMS if _mentions(text, k)}
    return keys


def contradicts(note: str, pending: list) -> bool:
    """True when a note would keep, grow or continue a channel that a pending
    recommendation for the same property wants to change."""
    if not _CONTINUE.search(note or ""):
        return False
    touched = set()
    for item in pending:
        touched |= item_channels(item)
    return any(_mentions(note, k) for k in touched)


def guard_notes(notes: list, pending: list) -> list:
    return [n for n in notes if n and not contradicts(n, pending)]


def plan_notes(channel_keys: list, labels: dict, pending: list, objective_reason: str | None,
               *, always_on: set | None = None) -> list:
    """Notes from the mix and the pending recommendations, never contradicting them."""
    notes = []
    if objective_reason:
        notes.append(objective_reason)
    for key in channel_keys:
        label = labels.get(key, key)
        waiting = [i for i in pending if key in item_channels(i)]
        if waiting:
            title = waiting[0].get("title") or "A recommendation"
            notes.append(f"{label}: “{title}” is waiting on a decision, so the plan holds {label} "
                         "as contracted until it is decided.")
        elif always_on and key in always_on:
            notes.append(f"Keep {label} running all year.")
    return guard_notes(notes, pending)


def channel_rows(by_sku: dict) -> list:
    """Line items grouped into channel rows: [{key, label, mode, match, amount}]."""
    rows: dict = {}
    for sku, amt in (by_sku or {}).items():
        key, label, mode, match = SKU_CHANNELS.get(sku, (sku, sku.replace("_", " ").capitalize(), "always_on", sku))
        row = rows.setdefault(key, {"key": key, "label": label, "mode": mode, "match": match, "amount": 0.0})
        row["amount"] += wc.to_float(amt) or 0.0
    order = [v[0] for v in SKU_CHANNELS.values()]
    return sorted(rows.values(),
                  key=lambda r: (MODES.index(r["mode"]), order.index(r["key"]) if r["key"] in order else 99))


def flighted_monthly(amount: float, months: list, exposure: dict) -> list:
    """Monthly values that follow exposure: the contracted amount weighted by each
    known month's units to lease; months without a forecast are null."""
    known = [exposure[m] for m in months if m in exposure]
    mean = (sum(known) / len(known)) if known else 0
    if not mean:
        return [None] * len(months)
    return [round(amount * exposure[m] / mean, 2) if m in exposure else None for m in months]


def mix_notes(rows: list, pending: list, objective_reason: str | None, exposure: dict) -> list:
    """Notes from the always-on / flighted mix and pending recommendations, guarded."""
    notes = [objective_reason] if objective_reason else []
    peak = max(exposure, key=exposure.get) if exposure else None
    for r in rows:
        waiting = [i for i in pending if r["match"] in item_channels(i)]
        if waiting:
            title = waiting[0].get("title") or "A recommendation"
            notes.append(f"{r['label']}: “{title}” is waiting on a decision, so the plan holds {r['label']} "
                         "as contracted until it is decided.")
        elif r["mode"] == "always_on":
            notes.append(f"Keep {r['label']} running all year.")
        elif peak:
            notes.append(f"{r['label']} follows the exposure forecast, heaviest in {peak}.")
    return guard_notes(notes, pending)


def pending_recommendations(ctx, today: date, gaps: list) -> list:
    from skills import workspace_inbox as wi
    items, item_gaps = wi.collect(ctx, sources=PENDING_SOURCES, today=today, with_history=False)
    gaps.extend(g for g in item_gaps if g.get("source"))
    return [i for i in items if i["status"] == "to_do" and i["needs_approval"]]


def fiscal_year(today: date) -> tuple[str, list]:
    start = today.year if today.month >= 7 else today.year - 1
    months = [f"{start + (1 if m < 7 else 0):04d}-{m:02d}" for m in list(range(7, 13)) + list(range(1, 7))]
    return f"FY {start}-{str(start + 1)[2:]}", months


def build_media_plan(ctx, *, internal: bool = True, today: date | None = None) -> dict:
    from skills import workspace_property_overview as wpo
    from skills import workspace_views as wv

    today = today or date.today()
    gaps: list = []
    label, months = fiscal_year(today)
    row, as_of = wv.spend(ctx.company_id, gaps, field="channels")
    aptiq, aptiq_as_of = wv.aptiq_snapshot(ctx, gaps)

    exposure = wpo.exposure_months(ctx, aptiq, aptiq_as_of, today)
    by_month = {m["month"]: m["units_to_lease"] for m in (exposure or {}).get("months", [])}
    month_rows = []
    for m in months:
        value = by_month.get(m)
        entry = {"month": m, "units_to_lease": value}
        if value is not None:
            entry["source"] = "aptiq"
        month_rows.append(entry)
    gaps.append(wc.gap("months.units_to_lease",
                       "Units to lease come from AptIQ exposure for the next three months only"))

    channels, total = [], None
    rows = channel_rows((row or {}).get("by_sku") or {}) if row else []
    if row:
        total = row.get("total") or 0.0
        for r in rows:
            amt = round(r["amount"], 2)
            monthly = [amt] * 12 if r["mode"] == "always_on" else flighted_monthly(amt, months, by_month)
            channels.append({
                "channel": r["label"],
                "mode": r["mode"],
                "monthly": monthly,
                "monthly_avg": amt,
                "annual": round(amt * 12, 2),
                "share": round(amt / total, 4) if total else None,
                "cpl_target": None,
                "source": wv.SPEND_SOURCE,
            })
        if any(r["mode"] == "flighted" for r in rows):
            gaps.append(wc.gap("channels.monthly", "Flighted months follow the AptIQ exposure forecast, which covers "
                                                   "three months; other months are not planned yet"))
        gaps.append(wc.gap("channels.cpl_target", "No CPL target is stored per channel"))
        gaps.append(wc.gap("channels", "Google Business Profile and ILS listings are not deal line items, so they "
                                       "are not in the plan"))

    objective, reason = wpo.objective(ctx, aptiq)
    pending = pending_recommendations(ctx, today, gaps)
    notes = mix_notes(rows, pending, reason, by_month)
    envelope = wc.metric(round(total * 12, 2), wv.SPEND_SOURCE, as_of, period="annual") if total else None
    return {
        "fiscal_year": label,
        "envelope": envelope,
        "objective": objective,
        "generated_at": as_of,
        "months": month_rows,
        "channels": channels,
        "allocated": envelope,
        "notes": notes,
        "gaps": wc.gaps_for(gaps, internal),
    }


def regenerate(ctx, actor: str, *, ticket_internal: bool) -> dict:
    """File a work item for a person to draft a refreshed plan. No budget changes."""
    import loop_writer
    import portal_tickets

    details = "\n".join([
        f"Draft a refreshed media plan for {ctx.name or ctx.company_id}.",
        "Requested from the Workspace Media Plan. Nothing changes in live budgets until a "
        "revised deal is drafted and signed.",
    ])
    body, status = portal_tickets.create_ticket(
        ctx.company_id, REGENERATE_TICKET_TYPE, subject=f"Draft a refreshed media plan: {ctx.name or 'property'}",
        fields={"Details": details}, submitted_by=actor, property_uuid=ctx.uuid, internal=ticket_internal,
    )
    task_id = str((body.get("ticket") or {}).get("id") or "") if isinstance(body, dict) else ""
    if status != 201 or not task_id:
        raise wc.WorkspaceError(status if status >= 400 else 502, (body or {}).get("error") or "Could not file")
    loop_writer.record(
        "ops", "workspace_request_filed",
        property_uuid=ctx.uuid or None, company_id=ctx.company_id,
        source="workspace", source_id=task_id, trigger="client_action",
        payload={"origin": "media_plan_regenerate", "actor": actor, "clickup_task_id": task_id},
    )
    return {"work_item_id": f"portal_ticket:{task_id}", "clickup_task_id": task_id, "status": "draft_requested"}
