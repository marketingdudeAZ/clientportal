"""Creative Transition task creator — PLE Status → RPM Managed → ClickUp.

When a HubSpot company's `plestatus` changes to "RPM Managed", create a
task on the Creative Marketing Services "2026 Property Transitions
(Creative Setup)" list so the creative team starts transition collateral.

Trigger: the company-property-change HubSpot webhook receiver
(routes/webhooks/hubspot.py) calls `handle_plestatus_change` in a
background thread.

Dedup — one task per company, EVER (Kyle 2026-06-12): the ClickUp task id
is stamped on the company (`creative_transition_task_id`); a company that
already carries a stamp is skipped, so a property that bounces out of and
back into RPM Managed doesn't get a second Creative Setup task. A
short-TTL in-process claim guards the window between task creation and
the HubSpot PATCH landing (webhook retries / multi-worker delivery).

Field mapping (ClickUp ← HubSpot company):
    [PM POC] (users)                    ← marketing_manager_email → ClickUp member
    What state is this asset in?        ← state  (2-letter codes expanded)
    [Market] (drop down)                ← rpmmarket
    SF Property Code (short text)       ← salesforceaccountid
    Property Type (drop down)           ← occupancy_status
    [Property Address] (text)           ← address, city, state, zip
    [Website] (url)                     ← domain

Dropdown values that don't match a ClickUp option, and PM POC emails with
no ClickUp member, are NOT errors: the value lands in the task description
instead so the team can set it by hand.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Optional

import requests

from config import CLICKUP_API_KEY, HUBSPOT_API_KEY

logger = logging.getLogger(__name__)

CU_BASE = "https://api.clickup.com/api/v2"
HS_BASE = "https://api.hubapi.com"
_TIMEOUT = 15

TASK_ID_PROP = "creative_transition_task_id"
TASK_URL_PROP = "creative_transition_task_url"

TASK_NAME_SUFFIX = " - Transition Collateral"
TASK_STATUS = os.getenv("CLICKUP_CREATIVE_TRANSITION_STATUS", "incoming")

COMPANY_PROPS = [
    "name", "marketing_manager", "marketing_manager_email", "state",
    "rpmmarket", "salesforceaccountid", "occupancy_status",
    "address", "city", "zip", "domain", "plestatus",
    TASK_ID_PROP,
]

_STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut",
    "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine",
    "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri",
    "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota",
    "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
}

# In-process claim: company_id -> claim time. Guards webhook retries that
# arrive before the HubSpot task-id stamp is readable. Same pattern as
# routes/property_brief._recent.
_recent: dict[str, float] = {}
_recent_lock = threading.Lock()
_CLAIM_TTL = 600  # seconds

# List schema cache: (fetched_at, {normalized_name: field_dict})
_field_cache: dict[str, tuple] = {}
_FIELD_CACHE_TTL = 1800


def _list_id() -> str:
    return os.getenv("CLICKUP_LIST_CREATIVE_TRANSITIONS", "")


def _cu_headers() -> dict:
    return {"Authorization": CLICKUP_API_KEY or "", "Content-Type": "application/json"}


def _hs_headers() -> dict:
    return {"Authorization": f"Bearer {HUBSPOT_API_KEY}", "Content-Type": "application/json"}


# ── Entry point ──────────────────────────────────────────────────────────────

def handle_plestatus_change(company_id: str, new_value: str) -> dict:
    """Create the Creative Transition ClickUp task for a company that just
    became RPM Managed. Returns a result dict (for logs/tests)."""
    company_id = str(company_id or "").strip()
    if not company_id:
        return {"skipped": "no company_id"}
    if (new_value or "").strip() != "RPM Managed":
        return {"skipped": f"plestatus={new_value!r} (not RPM Managed)"}

    list_id = _list_id()
    if not list_id:
        logger.warning("creative_transition: CLICKUP_LIST_CREATIVE_TRANSITIONS unset — skipping company %s", company_id)
        return {"skipped": "list env unset"}
    if not CLICKUP_API_KEY:
        logger.warning("creative_transition: CLICKUP_API_KEY unset — skipping company %s", company_id)
        return {"skipped": "clickup token unset"}

    # In-process claim (webhook retries / double delivery)
    now = time.time()
    with _recent_lock:
        ts = _recent.get(company_id)
        if ts and (now - ts) < _CLAIM_TTL:
            logger.info("creative_transition: claim hit for company %s — skipping", company_id)
            return {"skipped": "claimed in-process"}
        _recent[company_id] = now
        # opportunistic GC
        for k in [k for k, v in _recent.items() if (now - v) > _CLAIM_TTL]:
            _recent.pop(k, None)

    company = _read_company(company_id)
    if not company:
        return {"error": "company read failed"}
    props = company.get("properties", {})

    # Durable dedup: one task per company, ever.
    if (props.get(TASK_ID_PROP) or "").strip():
        logger.info("creative_transition: company %s already has task %s — skipping",
                    company_id, props.get(TASK_ID_PROP))
        return {"skipped": "task already exists", "task_id": props.get(TASK_ID_PROP)}

    name = (props.get("name") or "").strip() or f"Company {company_id}"
    task = _create_task(list_id, name, props, company_id)
    if not task:
        # Release the claim so a retry can succeed once the cause is fixed.
        with _recent_lock:
            _recent.pop(company_id, None)
        return {"error": "task create failed"}

    task_id = str(task.get("id") or "")
    task_url = task.get("url") or f"https://app.clickup.com/t/{task_id}"
    _stamp_company(company_id, task_id, task_url)
    logger.info("creative_transition: created task %s for company %s (%s)",
                task_id, company_id, name)
    return {"task_id": task_id, "task_url": task_url}


def handle_async(company_id: str, new_value: str) -> None:
    """Fire-and-forget wrapper for the webhook receiver."""
    def _run():
        try:
            handle_plestatus_change(company_id, new_value)
        except Exception:
            logger.exception("creative_transition: unhandled error for company %s", company_id)
    threading.Thread(target=_run, daemon=True).start()


# ── HubSpot ──────────────────────────────────────────────────────────────────

def _read_company(company_id: str) -> Optional[dict]:
    try:
        r = requests.get(
            f"{HS_BASE}/crm/v3/objects/companies/{company_id}",
            headers=_hs_headers(),
            params={"properties": ",".join(COMPANY_PROPS)},
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.warning("creative_transition: company %s read failed: %s", company_id, e)
        return None


def _stamp_company(company_id: str, task_id: str, task_url: str) -> None:
    try:
        r = requests.patch(
            f"{HS_BASE}/crm/v3/objects/companies/{company_id}",
            headers=_hs_headers(),
            json={"properties": {TASK_ID_PROP: task_id, TASK_URL_PROP: task_url}},
            timeout=_TIMEOUT,
        )
        if r.status_code >= 400:
            logger.warning("creative_transition: stamp failed for company %s: %s %s",
                           company_id, r.status_code, r.text[:200])
    except Exception as e:
        logger.warning("creative_transition: stamp error for company %s: %s", company_id, e)


# ── ClickUp list schema ──────────────────────────────────────────────────────

def _norm(name: str) -> str:
    return " ".join((name or "").lower().split())


def _list_fields(list_id: str) -> dict[str, dict]:
    """{normalized_field_name: field_dict}, cached 30 min."""
    now = time.time()
    cached = _field_cache.get(list_id)
    if cached and (now - cached[0]) < _FIELD_CACHE_TTL:
        return cached[1]
    try:
        r = requests.get(f"{CU_BASE}/list/{list_id}/field", headers=_cu_headers(), timeout=_TIMEOUT)
        r.raise_for_status()
        fields = {_norm(f.get("name")): f for f in r.json().get("fields", [])}
        _field_cache[list_id] = (now, fields)
        return fields
    except Exception as e:
        logger.warning("creative_transition: field fetch failed for list %s: %s", list_id, e)
        return cached[1] if cached else {}


def _dropdown_option_id(field: dict, value: str) -> Optional[str]:
    """Match a dropdown option by name, case-insensitive. None if no match."""
    want = _norm(value)
    if not want:
        return None
    for opt in ((field.get("type_config") or {}).get("options")) or []:
        if _norm(opt.get("name") or opt.get("label") or "") == want:
            return opt.get("id")
    return None


def _member_id_by_email(list_id: str, email: str) -> Optional[int]:
    email = (email or "").strip().lower()
    if not email:
        return None
    try:
        r = requests.get(f"{CU_BASE}/list/{list_id}/member", headers=_cu_headers(), timeout=_TIMEOUT)
        r.raise_for_status()
        for m in r.json().get("members", []):
            if (m.get("email") or "").strip().lower() == email:
                return m.get("id")
    except Exception as e:
        logger.warning("creative_transition: member lookup failed: %s", e)
    return None


# ── Task assembly ────────────────────────────────────────────────────────────

def _full_state(value: str) -> str:
    v = (value or "").strip()
    return _STATE_NAMES.get(v.upper(), v) if len(v) == 2 else v


def _compose_address(props: dict) -> str:
    street = (props.get("address") or "").strip()
    city = (props.get("city") or "").strip()
    state = _full_state(props.get("state") or "")
    zipc = (props.get("zip") or "").strip()
    tail = " ".join(x for x in (state, zipc) if x)
    return ", ".join(x for x in (street, city, tail) if x)


def _create_task(list_id: str, company_name: str, props: dict, company_id: str) -> Optional[dict]:
    fields = _list_fields(list_id)
    custom_fields: list[dict] = []
    manual_notes: list[str] = []

    def field(name: str) -> Optional[dict]:
        f = fields.get(_norm(name))
        if not f:
            manual_notes.append(f"(field {name!r} not found on the list)")
        return f

    def add_dropdown(name: str, value: str):
        value = (value or "").strip()
        if not value:
            return
        f = field(name)
        if not f:
            return
        oid = _dropdown_option_id(f, value)
        if oid:
            custom_fields.append({"id": f["id"], "value": oid})
        else:
            manual_notes.append(f"{name}: {value} (no matching dropdown option — set manually)")

    def add_text(name: str, value: str):
        value = (value or "").strip()
        if not value:
            return
        f = field(name)
        if f:
            custom_fields.append({"id": f["id"], "value": value})

    add_dropdown("What state is this asset in?", _full_state(props.get("state") or ""))
    add_dropdown("[Market]", props.get("rpmmarket") or "")
    add_dropdown("Property Type", props.get("occupancy_status") or "")
    add_text("SF Property Code", props.get("salesforceaccountid") or "")
    add_text("[Property Address]", _compose_address(props))

    domain = (props.get("domain") or "").strip()
    if domain:
        url = domain if domain.startswith("http") else f"https://{domain}"
        add_text("[Website]", url)

    # PM POC (users field) — set after create via the set-field endpoint,
    # which is the documented path for people fields.
    mm_email = (props.get("marketing_manager_email") or "").strip()
    mm_name = (props.get("marketing_manager") or "").strip()
    pm_poc_member = _member_id_by_email(list_id, mm_email) if mm_email else None
    if (mm_email or mm_name) and not pm_poc_member:
        manual_notes.append(
            f"PM POC: {mm_name or '?'} ({mm_email or 'no email'}) — not a ClickUp member, assign manually"
        )

    desc_lines = [
        f"Auto-created: PLE Status changed to RPM Managed in HubSpot.",
        f"HubSpot company: https://app.hubspot.com/contacts/19843861/company/{company_id}",
        f"Marketing Manager: {mm_name or '—'}" + (f" ({mm_email})" if mm_email else ""),
    ]
    if manual_notes:
        desc_lines += ["", "Needs manual attention:"] + [f"- {n}" for n in manual_notes]

    payload = {
        "name": f"{company_name}{TASK_NAME_SUFFIX}",
        "description": "\n".join(desc_lines),
        "status": TASK_STATUS,
        "custom_fields": custom_fields,
    }

    task = _post_task(list_id, payload)
    if task is None and TASK_STATUS:
        # Status name mismatch shouldn't kill the task — retry with the
        # list default status (first column, i.e. Incoming).
        payload.pop("status", None)
        task = _post_task(list_id, payload)
    if task and pm_poc_member:
        _set_users_field(task["id"], fields, "[PM POC]", [pm_poc_member])
    return task


def _post_task(list_id: str, payload: dict) -> Optional[dict]:
    try:
        r = requests.post(f"{CU_BASE}/list/{list_id}/task", headers=_cu_headers(),
                          json=payload, timeout=_TIMEOUT)
    except requests.RequestException as e:
        logger.warning("creative_transition: create_task network error: %s", e)
        return None
    if r.status_code >= 400:
        logger.warning("creative_transition: create_task -> %s %s", r.status_code, r.text[:300])
        return None
    return r.json()


def _set_users_field(task_id: str, fields: dict, field_name: str, user_ids: list) -> None:
    f = fields.get(_norm(field_name))
    if not f:
        logger.warning("creative_transition: users field %r not on list", field_name)
        return
    try:
        r = requests.post(
            f"{CU_BASE}/task/{task_id}/field/{f['id']}",
            headers=_cu_headers(),
            json={"value": {"add": user_ids}},
            timeout=_TIMEOUT,
        )
        if r.status_code >= 400:
            logger.warning("creative_transition: set users field -> %s %s",
                           r.status_code, r.text[:200])
    except Exception as e:
        logger.warning("creative_transition: set users field error: %s", e)
