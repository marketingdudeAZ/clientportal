"""Reconcile the Fluency budget sheet against HubSpot's closed-won deals.

WHAT THIS IS
    An independent check that the Fluency budget sheet says what HubSpot says
    it should. It trusts the budget-sync pipeline not at all — it recomputes
    the expected budgets from source and diffs them against what is actually
    in the sheet.

WHY IT EXISTS
    The sync pipeline cannot report the failure of a run that never started.
    On 2026-08-01 ~46 budget updates never enrolled in the HubSpot workflow,
    so nothing ran, nothing errored, and nobody knew until Monday. Inline
    verification inside the writer is structurally incapable of catching that
    class of failure. Only an out-of-band comparison can.
    See docs/budget-sync-plan.md §"The one that cannot be fixed inline".

READ-ONLY — TWICE
    This module never writes to the budget sheet. That is enforced in two
    independent places, because "the code is careful" is not a guarantee:

      1. The Google credential requests `spreadsheets.readonly` scope, so a
         write is rejected by Google regardless of what this code does.
      2. There is no write call in this file.

    Grant the service account VIEWER on the budget sheet, not Editor. If a
    future change needs to write, that is a separate module with a separate
    credential and its own review.

THE RULE (confirmed with Kyle, 2026-08-12)
    Each property has a launch date. On the launch date the deal is moved to
    Closed Won with that date as its close date. So close date == launch date
    == the date the budget takes effect; there is no future-dating. Therefore:

        the sheet should reflect the MOST RECENTLY CLOSED-WON deal per
        property, effective immediately.

    Reconciliation uses exactly that rule. If the writer ever adopts a
    different one, this module is wrong and will emit false drift.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────

BUDGET_SHEET_ID = os.getenv("FLUENCY_BUDGET_SHEET_ID", "")
BUDGET_TAB_NAME = os.getenv("FLUENCY_BUDGET_TAB", "DO NOT RENAME - Fluency Budgets")

# Column layout of the budget tab: A=uuid, B=account name, C=budget group, D=value.
_COL_UUID, _COL_ACCOUNT, _COL_GROUP, _COL_VALUE = 0, 1, 2, 3

# Sentinel the writer uses for "this property does not buy this channel".
# DISTINCT from "$0.00", which means the line item exists and is zeroed. The
# HubSpot action draws that distinction (nameBudgetMap defaults to '$ -' and is
# overwritten with '$0.00' when a line item is present with a zero amount), so
# reconciliation must draw it too or every property reports drift.
NOT_PURCHASED = "$ -"

# Channel → (HubSpot product id, exact sheet label).
#
# MATCHED BY PRODUCT ID, NOT NAME. The HubSpot workflow action matches line
# items by display name against a hardcoded list with trailing asterisks
# ('Paid Search Ads*'), which means renaming a product in HubSpot silently
# drops that channel to '$ -' and zeroes a live budget. Product ids are stable
# across renames. Ids come from product_catalog.CHANNEL_PRODUCT_MAP.
#
# The LABEL is what Fluency joins on (uuid + budget name), so these strings
# must stay byte-identical to what the sheet holds today: the action writes
# `name.replace(/\*/g, '').trim()`, i.e. the catalog name minus its asterisk.
BUDGET_CHANNELS: list[tuple[str, str]] = [
    ("1828410484",  "Paid Search Ads"),
    ("1992302863",  "Google Ads Performance Max"),
    ("1828407304",  "Paid Meta Ads"),
    ("2950596276",  "Paid TikTok Ads"),
    ("2837370149",  "Google Display Ads"),
    ("20381236570", "Display Retargeting Campaign"),
    ("2837636253",  "Programmatic Display Ads"),
    ("20971413775", "YouTube Reach Campaign"),
    ("1828397328",  "Geofence"),
    ("25711575176", "Demand Gen"),
]

# CTV/OTT (product 42010615327) is in the product catalog and is quoted on every
# IO, but is NOT in the workflow action's BUDGET_NAMES — so it is sold and never
# synced. Whether that is deliberate (Fluency may not execute CTV) is an open
# question in docs/budget-sync-plan.md §4c. Until it is answered, reconciliation
# must NOT expect a CTV row, or every property reports a false missing channel.
# Flip this on only together with the writer.
if os.getenv("FLUENCY_BUDGET_INCLUDE_CTV", "").lower() == "true":
    BUDGET_CHANNELS.append(("42010615327", "CTV/OTT"))

EXPECTED_ROWS_PER_PROPERTY = len(BUDGET_CHANNELS)

# Deal stages that count as closed-won. `dealstage` is a pipeline-specific id,
# so match on the internal 'closedwon' value and on any stage label containing
# "won" — the same tolerance spend_sheet._deal_sort_key uses.
_WON_HINTS = ("closedwon", "won")


# ── Drift records ────────────────────────────────────────────────────────────

# Drift kinds, most-actionable first. The reporter groups on these.
KIND_MISSING_PROPERTY = "missing_property"    # HubSpot has a budget, sheet has no rows
KIND_VALUE_MISMATCH = "value_mismatch"        # a channel's number disagrees
KIND_MISSING_CHANNEL = "missing_channel"      # property present, channel row absent
KIND_ROW_COUNT = "row_count"                  # not exactly EXPECTED_ROWS_PER_PROPERTY
KIND_DUPLICATE_ROW = "duplicate_row"          # same (uuid, channel) more than once
KIND_ORPHAN_PROPERTY = "orphan_property"      # sheet has rows, HubSpot has no basis
KIND_UNKNOWN_CHANNEL = "unknown_channel"      # a label we do not recognise


def _drift(kind: str, uuid: str, name: str, **extra: Any) -> dict:
    return {"kind": kind, "uuid": uuid, "account_name": name, **extra}


# ── Money formatting — must match the writer byte for byte ───────────────────

def format_money(amount: Any) -> str:
    """Format an amount the way the HubSpot action writes it.

    The action does `$${parseFloat(amount).toFixed(2)}` — no thousands
    separator, no space after the sign — and falls back to '$0.00' when the
    amount is zero, null, or the string '0'. Reproduced exactly, because a
    formatting difference here would report every property as drifted.
    """
    if amount is None or amount == "":
        return "$0.00"
    try:
        val = float(amount)
    except (TypeError, ValueError):
        return "$0.00"
    return f"${val:.2f}"


def normalize_sheet_value(raw: Any) -> str:
    """Normalise a cell for comparison.

    Only whitespace is stripped. Deliberately NOT normalising '$ -' vs '$-' vs
    '' — those differences are themselves drift worth seeing, and collapsing
    them would hide a writer that started emitting a different sentinel.
    """
    return str(raw or "").strip()


# ── HubSpot side: what the sheet SHOULD say ──────────────────────────────────

def _is_won(deal: dict) -> bool:
    stage = (deal.get("properties", {}).get("dealstage") or "").lower()
    return any(h in stage for h in _WON_HINTS)


def _close_sort_key(deal: dict) -> str:
    """Most-recent-close-first ordering key.

    Uses closedate, falling back to createdate. Both are ISO-8601 from HubSpot,
    so lexicographic ordering is chronological.
    """
    props = deal.get("properties", {})
    return props.get("closedate") or props.get("createdate") or ""


def _line_items_by_product(deal_ids: list[str]) -> dict[str, dict[str, float]]:
    """{deal_id: {product_id: amount}}.

    Separate from spend_sheet._get_line_items_for_deals because that function
    aggregates into spend COLUMNS via SKU name matching and never fetches
    `hs_product_id` — which is the whole point here. Same batch-read shape.
    """
    import requests
    from spend_sheet import HS_BASE, HS_HDRS

    deal_li: dict[str, list[str]] = {}
    for i in range(0, len(deal_ids), 100):
        chunk = deal_ids[i:i + 100]
        try:
            r = requests.post(
                f"{HS_BASE}/crm/v4/associations/deals/line_items/batch/read",
                headers=HS_HDRS,
                json={"inputs": [{"id": d} for d in chunk]},
                timeout=20,
            )
            if r.status_code in (200, 207):
                for item in r.json().get("results", []):
                    did = str(item.get("from", {}).get("id", ""))
                    ids = [str(a.get("toObjectId")) for a in item.get("to", [])]
                    if ids:
                        deal_li[did] = ids
        except Exception as e:  # noqa: BLE001 — a chunk failure must not abort the sweep
            logger.error("deal→line_item assoc failed (chunk %d): %s", i, e)

    all_ids = list({lid for ids in deal_li.values() for lid in ids})
    props: dict[str, dict] = {}
    for i in range(0, len(all_ids), 100):
        chunk = all_ids[i:i + 100]
        try:
            r = requests.post(
                f"{HS_BASE}/crm/v3/objects/line_items/batch/read",
                headers=HS_HDRS,
                json={"inputs": [{"id": lid} for lid in chunk],
                      "properties": ["hs_product_id", "name", "amount", "price"]},
                timeout=20,
            )
            r.raise_for_status()
            for li in r.json().get("results", []):
                props[str(li["id"])] = li.get("properties", {})
        except Exception as e:  # noqa: BLE001
            logger.error("line item batch read failed (chunk %d): %s", i, e)

    out: dict[str, dict[str, float]] = {}
    for did, ids in deal_li.items():
        agg: dict[str, float] = {}
        for lid in ids:
            p = props.get(lid, {})
            pid = str(p.get("hs_product_id") or "").strip()
            if not pid:
                continue
            raw = p.get("amount")
            if raw in (None, ""):
                raw = p.get("price")
            try:
                amt = float(raw) if raw not in (None, "") else 0.0
            except (TypeError, ValueError):
                amt = 0.0
            # Sum rather than overwrite: a deal may legitimately carry two line
            # items for the same product (e.g. a correction line).
            agg[pid] = agg.get(pid, 0.0) + amt
        out[did] = agg
    return out


def _uuids_for(company_ids: list[str]) -> dict[str, str]:
    """{company_id: uuid}, batch-read.

    spend_sheet._get_managed_companies() does NOT request `uuid` — its property
    list is built for the spend table (name, market, manager, plestatus…). Its
    filtering logic is battle-tested and worth reusing, so rather than forking
    it we reuse it for the company set and fetch uuid separately.

    R1: uuid is READ here and never written. Only the HubSpot workflow
    "Trigger enrollment for companies" writes it. See IMMUTABLE_RULES.md.
    """
    import requests
    from spend_sheet import HS_BASE, HS_HDRS

    out: dict[str, str] = {}
    for i in range(0, len(company_ids), 100):
        chunk = company_ids[i:i + 100]
        try:
            r = requests.post(
                f"{HS_BASE}/crm/v3/objects/companies/batch/read",
                headers=HS_HDRS,
                json={"inputs": [{"id": c} for c in chunk],
                      "properties": ["uuid", "name"]},
                timeout=20,
            )
            r.raise_for_status()
            for c in r.json().get("results", []):
                props = c.get("properties", {})
                uuid = str(props.get("uuid") or "").strip()
                if uuid:
                    out[str(c["id"])] = uuid
        except Exception as e:  # noqa: BLE001
            logger.error("company uuid batch read failed (chunk %d): %s", i, e)
    return out


def _company_names(company_ids: list[str]) -> dict[str, str]:
    """{company_id: name}, from the same batch read as uuid. Split out so the
    two concerns read clearly at the call site."""
    import requests
    from spend_sheet import HS_BASE, HS_HDRS

    out: dict[str, str] = {}
    for i in range(0, len(company_ids), 100):
        chunk = company_ids[i:i + 100]
        try:
            r = requests.post(
                f"{HS_BASE}/crm/v3/objects/companies/batch/read",
                headers=HS_HDRS,
                json={"inputs": [{"id": c} for c in chunk],
                      "properties": ["name"]},
                timeout=20,
            )
            r.raise_for_status()
            for c in r.json().get("results", []):
                out[str(c["id"])] = (c.get("properties", {}).get("name") or "").strip()
        except Exception as e:  # noqa: BLE001
            logger.error("company name batch read failed (chunk %d): %s", i, e)
    return out


def expected_budgets() -> dict[str, dict]:
    """{uuid: {"account_name", "deal_id", "deal_name", "closedate", "budgets"}}.

    `budgets` is {sheet_label: formatted_value} for every channel, using
    NOT_PURCHASED for channels with no line item on the winning deal.
    """
    from spend_sheet import (_batch_read_deals, _get_deal_associations,
                             _get_managed_companies)

    companies = _get_managed_companies()
    by_id = {str(c["id"]): c for c in companies}
    logger.info("reconcile: %d managed companies", len(by_id))

    deal_to_company = _get_deal_associations(list(by_id.keys()))
    deals = _batch_read_deals(list(deal_to_company.keys()))

    # Winning deal per company: most recently closed CLOSED-WON deal.
    winner: dict[str, dict] = {}
    for did, deal in deals.items():
        if not _is_won(deal):
            continue
        cid = deal_to_company.get(did)
        if not cid:
            continue
        cur = winner.get(cid)
        if cur is None or _close_sort_key(deal) > _close_sort_key(cur):
            winner[cid] = deal

    li = _line_items_by_product([str(d["id"]) for d in winner.values()])
    uuids = _uuids_for(list(winner.keys()))
    names = _company_names(list(winner.keys()))
    logger.info("reconcile: %d companies with a closed-won deal, %d have a uuid",
                len(winner), len(uuids))

    out: dict[str, dict] = {}
    for cid, deal in winner.items():
        uuid = uuids.get(cid, "")
        if not uuid:
            # R1: a property with no uuid is not addressable by Fluency, so it
            # cannot appear in the sheet. Not drift — an enrollment gap owned by
            # the HubSpot workflow that assigns uuid. Logged, never written.
            logger.info("reconcile: company %s has no uuid — skipped", cid)
            continue
        amounts = li.get(str(deal["id"]), {})
        out[uuid] = {
            "account_name": names.get(cid, ""),
            "deal_id": str(deal["id"]),
            "deal_name": (deal.get("properties", {}).get("dealname") or "").strip(),
            "closedate": deal.get("properties", {}).get("closedate") or "",
            "budgets": {
                label: (format_money(amounts[pid]) if pid in amounts else NOT_PURCHASED)
                for pid, label in BUDGET_CHANNELS
            },
        }
    return out


# ── Sheet side: what the sheet ACTUALLY says ─────────────────────────────────

def _readonly_client():
    """gspread client with READ-ONLY scope.

    Mirrors fluency_feed._gc's credential handling (raw JSON in env OR a path)
    but requests `spreadsheets.readonly`. The narrower scope is the point: a
    write from this module is refused by Google, not merely absent from the
    code.
    """
    import gspread
    from google.oauth2.service_account import Credentials

    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not raw:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON not set")
    info = json.loads(raw) if raw.strip().startswith("{") else json.load(open(raw))
    creds = Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    return gspread.authorize(creds)


def read_sheet_rows(tab: str | None = None) -> list[list[str]]:
    """Raw rows of a budget tab, header included. One API call.

    `tab` defaults to the live tab. It is a parameter rather than the module
    global it used to be because the parallel run (docs/budget-sync-plan.md
    §4e) reads the live tab and the shadow tab in the same process, and a
    global would make "which tab did this report describe?" depend on
    execution order.
    """
    if not BUDGET_SHEET_ID:
        raise RuntimeError("FLUENCY_BUDGET_SHEET_ID not set")
    sh = _readonly_client().open_by_key(BUDGET_SHEET_ID)
    return sh.worksheet(tab or BUDGET_TAB_NAME).get_all_values()


def parse_sheet(rows: list[list[str]]) -> tuple[dict[str, dict], list[dict]]:
    """Rows → ({uuid: {label: value}}, [structural drift]).

    Structural problems (duplicate rows, unknown channel labels) are reported
    here rather than swallowed: they are the fingerprint of the delete/append
    race described in docs/budget-sync-plan.md §3 #2, and they are invisible if
    parsing simply takes the last value it sees.
    """
    known = {label for _, label in BUDGET_CHANNELS}
    actual: dict[str, dict[str, str]] = {}
    names: dict[str, str] = {}
    seen: dict[tuple[str, str], int] = {}
    drift: list[dict] = []

    for row in rows[1:]:  # header
        if len(row) <= _COL_VALUE:
            row = list(row) + [""] * (_COL_VALUE + 1 - len(row))
        uuid = normalize_sheet_value(row[_COL_UUID])
        if not uuid:
            continue
        label = normalize_sheet_value(row[_COL_GROUP])
        value = normalize_sheet_value(row[_COL_VALUE])
        names.setdefault(uuid, normalize_sheet_value(row[_COL_ACCOUNT]))

        if label not in known:
            drift.append(_drift(KIND_UNKNOWN_CHANNEL, uuid, names[uuid],
                                channel=label, sheet_value=value))
            continue

        key = (uuid, label)
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            drift.append(_drift(KIND_DUPLICATE_ROW, uuid, names[uuid],
                                channel=label, sheet_value=value,
                                occurrences=seen[key]))
        actual.setdefault(uuid, {})[label] = value

    for uuid, chans in actual.items():
        if len(chans) != EXPECTED_ROWS_PER_PROPERTY:
            drift.append(_drift(KIND_ROW_COUNT, uuid, names.get(uuid, ""),
                                found=len(chans),
                                expected=EXPECTED_ROWS_PER_PROPERTY))
    return actual, drift


# ── The diff ─────────────────────────────────────────────────────────────────

def diff(expected: dict[str, dict], actual: dict[str, dict]) -> list[dict]:
    """Compare expected vs actual. Pure — no I/O, so it is fully testable."""
    out: list[dict] = []

    for uuid, exp in expected.items():
        name = exp["account_name"]
        rows = actual.get(uuid)
        if rows is None:
            out.append(_drift(KIND_MISSING_PROPERTY, uuid, name,
                              deal_id=exp["deal_id"], deal_name=exp["deal_name"],
                              closedate=exp["closedate"],
                              expected_budgets=exp["budgets"]))
            continue
        for label, want in exp["budgets"].items():
            if label not in rows:
                out.append(_drift(KIND_MISSING_CHANNEL, uuid, name,
                                  channel=label, expected_value=want,
                                  deal_id=exp["deal_id"]))
            elif rows[label] != want:
                out.append(_drift(KIND_VALUE_MISMATCH, uuid, name,
                                  channel=label, sheet_value=rows[label],
                                  expected_value=want,
                                  deal_id=exp["deal_id"],
                                  deal_name=exp["deal_name"],
                                  closedate=exp["closedate"]))

    for uuid, rows in actual.items():
        if uuid not in expected:
            # Not necessarily wrong: a property that left management keeps its
            # rows until someone removes them, and Fluency may still reference
            # them. Reported so a human decides, never auto-corrected.
            out.append(_drift(KIND_ORPHAN_PROPERTY, uuid,
                              "", channels=len(rows)))
    return out


# ── Entry point ──────────────────────────────────────────────────────────────

def reconcile(tab: str | None = None) -> dict:
    """Run a full reconciliation. Returns a report; writes nothing anywhere.

    The caller decides what to do with the report — render it, post it to a
    channel, append it to a tracking sheet. Keeping delivery out of here means
    this function is safe to run by hand at any time, including in prod.

    `tab` defaults to the live tab. Pass the shadow tab to reconcile that one
    instead; budget_compare does exactly that, twice.
    """
    expected = expected_budgets()
    rows = read_sheet_rows(tab)
    actual, structural = parse_sheet(rows)
    drift = structural + diff(expected, actual)

    by_kind: dict[str, int] = {}
    for d in drift:
        by_kind[d["kind"]] = by_kind.get(d["kind"], 0) + 1

    return {
        "ok": not drift,
        "tab": tab or BUDGET_TAB_NAME,
        "properties_expected": len(expected),
        "properties_in_sheet": len(actual),
        "sheet_rows": max(0, len(rows) - 1),
        "channels_per_property": EXPECTED_ROWS_PER_PROPERTY,
        "drift_count": len(drift),
        "drift_by_kind": by_kind,
        "drift": drift,
    }
