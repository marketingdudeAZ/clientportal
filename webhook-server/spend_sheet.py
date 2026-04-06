"""Spend Sheet builder — HubSpot deals + line items for all managed properties.

Pipeline:
1. CRM Search — all companies with plestatus IN (RPM Managed, Onboarding, Dispositioning)
2. Batch associations (v4) companies → deals
3. Batch read deals, pick most-recent per company
4. Batch associations deals → line_items
5. Batch read line items, aggregate by SKU column key
6. Batch associations deals → quotes
7. Batch read quotes, pick most-recent per deal

Results are cached for 30 minutes.
"""

import logging
import time
import requests

from config import HUBSPOT_API_KEY

logger = logging.getLogger(__name__)

HS_BASE = "https://api.hubapi.com"
HS_HDRS = {
    "Authorization": f"Bearer {HUBSPOT_API_KEY}",
    "Content-Type": "application/json",
}

PLE_STATUSES = ["RPM Managed", "Dispositioning", "Onboarding"]

# HubSpot SKU → column key returned in each row
SKU_COLUMN_MAP = {
    "SEO_Package":                "seo",
    "Paid_Search_Ads":            "search",
    "Google_Ads_Performance_Max": "pmax",
    "Paid_Social_Ads":            "paid_social",
    "Paid_TikTok_Ads":            "tiktok",
    "Geofence":                   "geofence",
    "Management_Fee":             "mgmt_fee",
    "Social_Posting":             "social_posting",
    "Reputation_Management":      "reputation",
    "Google_Display_Ads":         "display",
    "YouTube_Reach_Campaign":     "youtube",
    "CTV_OTT":                    "ctv",
    "Demand_Gen":                 "demand_gen",
    "Retargeting":                "retargeting",
    "Website_Hosting":            "website_hosting",
    # Common name-based fallbacks (if hs_sku is empty)
    "SEO Package":                "seo",
    "Management Fee":             "mgmt_fee",
    "Paid Search Ads":            "search",
    "Paid Social Ads":            "paid_social",
    "Performance Max":            "pmax",
}

_cache: dict = {}
CACHE_TTL = 1800  # 30 minutes


# ── Public API ─────────────────────────────────────────────────────────────────

def get_spend_sheet_data(force: bool = False) -> list[dict]:
    """Return spend rows for all managed properties (30-min cache)."""
    now = time.time()
    cached = _cache.get("data")
    if not force and cached and (now - cached[0]) < CACHE_TTL:
        logger.debug("Spend sheet cache hit — %d rows", len(cached[1]))
        return cached[1]

    data = _build_spend_sheet()
    _cache["data"] = (now, data)
    logger.info("Spend sheet built — %d rows", len(data))
    return data


def invalidate_cache():
    _cache.clear()


# ── Build pipeline ──────────────────────────────────────────────────────────────

def _build_spend_sheet() -> list[dict]:
    companies = _get_managed_companies()
    if not companies:
        logger.info("No managed companies found")
        return []

    company_ids = [c["id"] for c in companies]
    company_map = {c["id"]: c for c in companies}

    # companies → deals
    deal_to_company = _get_deal_associations(company_ids)
    all_deal_ids = list(set(deal_to_company.keys()))

    if not all_deal_ids:
        return _rows_no_deal(companies)

    # Batch read all deal properties
    deals_by_id = _batch_read_deals(all_deal_ids)

    # Pick most-recent deal per company
    company_best_deal: dict[str, dict] = {}
    for deal_id, deal in deals_by_id.items():
        cid = deal_to_company.get(deal_id)
        if not cid:
            continue
        existing = company_best_deal.get(cid)
        if not existing or _deal_sort_key(deal) > _deal_sort_key(existing):
            company_best_deal[cid] = deal

    selected_deals = list(company_best_deal.values())
    selected_deal_ids = [d["id"] for d in selected_deals]

    # Line items for selected deals
    deal_li_map = _get_line_items_for_deals(selected_deal_ids)

    # Most-recent quote per deal
    deal_quote_map = _get_quotes_for_deals(selected_deal_ids)

    # Assemble rows
    rows = []
    for company in companies:
        cid = company["id"]
        deal = company_best_deal.get(cid)
        row = _base_row(company)

        if deal:
            dp = deal.get("properties", {})
            row["deal_id"]    = deal["id"]
            row["deal_name"]  = dp.get("dealname", "")
            row["deal_stage"] = dp.get("dealstage", "")
            row["close_date"] = dp.get("closedate", "")
            row["deal_amount"] = _f(dp.get("amount"))

            # Spread line-item columns
            row.update(deal_li_map.get(deal["id"], {}))

            # Quote
            q = deal_quote_map.get(deal["id"])
            row["quote_status"] = q.get("status") if q else None
            row["quote_title"]  = q.get("title") if q else None
        else:
            row.update({"deal_id": None, "deal_name": None, "deal_stage": None,
                        "close_date": None, "deal_amount": None,
                        "quote_status": None, "quote_title": None})

        rows.append(row)

    rows.sort(key=lambda r: r.get("property_name", "").lower())
    return rows


def _base_row(company: dict) -> dict:
    return {
        "company_id":        company["id"],
        "property_name":     company.get("name", ""),
        "market":            company.get("market", ""),
        "marketing_manager": company.get("manager", ""),
        "ple_status":        company.get("ple_status", ""),
        # line-item columns default to None
        "seo": None, "search": None, "pmax": None, "paid_social": None,
        "tiktok": None, "geofence": None, "mgmt_fee": None,
        "social_posting": None, "reputation": None, "display": None,
        "youtube": None, "ctv": None, "demand_gen": None,
        "retargeting": None, "website_hosting": None,
    }


def _rows_no_deal(companies: list) -> list[dict]:
    rows = [_base_row(c) for c in companies]
    for r in rows:
        r.update({"deal_id": None, "deal_name": None, "deal_stage": None,
                  "close_date": None, "deal_amount": None,
                  "quote_status": None, "quote_title": None})
    return rows


# ── HubSpot fetchers ────────────────────────────────────────────────────────────

def _get_managed_companies() -> list[dict]:
    """CRM Search for all companies with the target PLE statuses."""
    filter_groups = [{"filters": [
        {"propertyName": "plestatus", "operator": "IN", "values": PLE_STATUSES}
    ]}]
    companies: list[dict] = []
    after = None

    for _ in range(20):  # safety: max 2,000 companies
        body = {
            "filterGroups": filter_groups,
            "properties": ["name", "rpmmarket", "marketing_manager_email", "plestatus"],
            "limit": 100,
            "sorts": [{"propertyName": "name", "direction": "ASCENDING"}],
        }
        if after:
            body["after"] = after

        try:
            r = requests.post(
                f"{HS_BASE}/crm/v3/objects/companies/search",
                headers=HS_HDRS, json=body, timeout=20,
            )
            r.raise_for_status()
        except Exception as e:
            logger.error("Company search failed: %s", e)
            break

        data = r.json()
        for c in data.get("results", []):
            props = c.get("properties", {})
            companies.append({
                "id":         c["id"],
                "name":       props.get("name", ""),
                "market":     props.get("rpmmarket", ""),
                "manager":    props.get("marketing_manager_email", ""),
                "ple_status": props.get("plestatus", ""),
            })

        after = data.get("paging", {}).get("next", {}).get("after")
        if not after:
            break

    return companies


def _get_deal_associations(company_ids: list) -> dict:
    """Return {deal_id: company_id} for all company→deal associations."""
    deal_to_company: dict[str, str] = {}

    for i in range(0, len(company_ids), 100):
        chunk = company_ids[i:i + 100]
        if not chunk:
            continue

        # Try CRM v4 batch (returns 200 or 207)
        try:
            r = requests.post(
                f"{HS_BASE}/crm/v4/associations/companies/deals/batch/read",
                headers=HS_HDRS,
                json={"inputs": [{"id": cid} for cid in chunk]},
                timeout=20,
            )
            if r.status_code in (200, 207):
                for item in r.json().get("results", []):
                    cid = str(item.get("from", {}).get("id", ""))
                    for assoc in item.get("to", []):
                        did = str(assoc.get("toObjectId", ""))
                        if did:
                            deal_to_company[did] = cid
                continue
        except Exception as e:
            logger.warning("v4 batch assoc failed, falling back per-company: %s", e)

        # Fallback: per-company (slower)
        for cid in chunk:
            try:
                r = requests.get(
                    f"{HS_BASE}/crm/v3/objects/companies/{cid}/associations/deals",
                    headers=HS_HDRS, timeout=10,
                )
                if r.status_code == 200:
                    for assoc in r.json().get("results", []):
                        deal_to_company[str(assoc["id"])] = str(cid)
            except Exception as e:
                logger.warning("Per-company deal assoc failed for %s: %s", cid, e)

    return deal_to_company


def _batch_read_deals(deal_ids: list) -> dict:
    """Return {deal_id: deal_object}."""
    deals: dict[str, dict] = {}

    for i in range(0, len(deal_ids), 100):
        chunk = deal_ids[i:i + 100]
        try:
            r = requests.post(
                f"{HS_BASE}/crm/v3/objects/deals/batch/read",
                headers=HS_HDRS,
                json={
                    "inputs": [{"id": did} for did in chunk],
                    "properties": ["dealname", "amount", "closedate",
                                   "dealstage", "pipeline", "createdate"],
                },
                timeout=20,
            )
            r.raise_for_status()
            for deal in r.json().get("results", []):
                deals[str(deal["id"])] = deal
        except Exception as e:
            logger.error("Deal batch read failed (chunk %d): %s", i, e)

    return deals


def _get_line_items_for_deals(deal_ids: list) -> dict:
    """Return {deal_id: {seo: 1300, search: 3500, ...}}."""
    # Step 1 — deal→line_item associations
    deal_li_ids: dict[str, list[str]] = {}

    for i in range(0, len(deal_ids), 100):
        chunk = deal_ids[i:i + 100]
        try:
            r = requests.post(
                f"{HS_BASE}/crm/v4/associations/deals/line_items/batch/read",
                headers=HS_HDRS,
                json={"inputs": [{"id": did} for did in chunk]},
                timeout=20,
            )
            if r.status_code in (200, 207):
                for item in r.json().get("results", []):
                    did = str(item.get("from", {}).get("id", ""))
                    ids = [str(a.get("toObjectId")) for a in item.get("to", [])]
                    if ids:
                        deal_li_ids[did] = ids
                continue
        except Exception as e:
            logger.warning("v4 deal→li assoc failed: %s", e)

        # Fallback per-deal
        for did in chunk:
            try:
                r = requests.get(
                    f"{HS_BASE}/crm/v3/objects/deals/{did}/associations/line_items",
                    headers=HS_HDRS, timeout=10,
                )
                if r.status_code == 200:
                    deal_li_ids[str(did)] = [str(a["id"]) for a in r.json().get("results", [])]
            except Exception:
                pass

    all_li_ids = list({lid for lids in deal_li_ids.values() for lid in lids})
    if not all_li_ids:
        return {}

    # Step 2 — batch read line item properties
    li_props: dict[str, dict] = {}
    for i in range(0, len(all_li_ids), 100):
        chunk = all_li_ids[i:i + 100]
        try:
            r = requests.post(
                f"{HS_BASE}/crm/v3/objects/line_items/batch/read",
                headers=HS_HDRS,
                json={
                    "inputs": [{"id": lid} for lid in chunk],
                    "properties": ["hs_sku", "name", "amount", "price", "quantity"],
                },
                timeout=20,
            )
            r.raise_for_status()
            for li in r.json().get("results", []):
                li_props[str(li["id"])] = li.get("properties", {})
        except Exception as e:
            logger.error("Line item batch read failed: %s", e)

    # Step 3 — aggregate per deal
    result: dict[str, dict] = {}
    for did, li_ids in deal_li_ids.items():
        agg: dict[str, float] = {}
        for lid in li_ids:
            props = li_props.get(lid, {})
            sku  = (props.get("hs_sku") or props.get("name") or "").strip()
            raw  = props.get("amount") or props.get("price") or "0"
            amt  = _f(raw) or 0.0
            col  = SKU_COLUMN_MAP.get(sku)
            if col:
                agg[col] = (agg.get(col) or 0) + amt
        result[did] = agg

    return result


def _get_quotes_for_deals(deal_ids: list) -> dict:
    """Return {deal_id: {status, title, amount}} for most-recent quote per deal."""
    if not deal_ids:
        return {}

    deal_quote_ids: dict[str, list[str]] = {}

    for i in range(0, len(deal_ids), 100):
        chunk = deal_ids[i:i + 100]
        try:
            r = requests.post(
                f"{HS_BASE}/crm/v4/associations/deals/quotes/batch/read",
                headers=HS_HDRS,
                json={"inputs": [{"id": did} for did in chunk]},
                timeout=20,
            )
            if r.status_code in (200, 207):
                for item in r.json().get("results", []):
                    did = str(item.get("from", {}).get("id", ""))
                    ids = [str(a.get("toObjectId")) for a in item.get("to", [])]
                    if ids:
                        deal_quote_ids[did] = ids
        except Exception as e:
            logger.warning("Deal→quote associations failed: %s", e)

    all_qids = list({qid for qids in deal_quote_ids.values() for qid in qids})
    if not all_qids:
        return {}

    # Batch read quotes
    q_props: dict[str, dict] = {}
    for i in range(0, len(all_qids), 100):
        chunk = all_qids[i:i + 100]
        try:
            r = requests.post(
                f"{HS_BASE}/crm/v3/objects/quotes/batch/read",
                headers=HS_HDRS,
                json={
                    "inputs": [{"id": qid} for qid in chunk],
                    "properties": ["hs_quote_status", "hs_quote_amount",
                                   "hs_createdate", "hs_title"],
                },
                timeout=20,
            )
            r.raise_for_status()
            for q in r.json().get("results", []):
                q_props[str(q["id"])] = q.get("properties", {})
        except Exception as e:
            logger.error("Quote batch read failed: %s", e)

    # Pick most-recently created quote per deal
    result: dict[str, dict] = {}
    for did, qids in deal_quote_ids.items():
        best_qp: dict | None = None
        best_date = ""
        for qid in qids:
            qp = q_props.get(qid, {})
            cd = qp.get("hs_createdate", "")
            if cd > best_date:
                best_date = cd
                best_qp = qp
        if best_qp:
            result[did] = {
                "status": (best_qp.get("hs_quote_status") or "").upper(),
                "amount": _f(best_qp.get("hs_quote_amount")),
                "title":  best_qp.get("hs_title", ""),
            }

    return result


# ── Helpers ─────────────────────────────────────────────────────────────────────

def _deal_sort_key(deal: dict) -> tuple:
    props = deal.get("properties", {})
    # Prefer closed-won, then by most-recent date
    stage = (props.get("dealstage") or "").lower()
    is_won = 1 if ("won" in stage or stage == "closedwon") else 0
    date = props.get("closedate") or props.get("createdate") or "0"
    return (is_won, date)


def _f(val, default=None):
    """Safely parse a float."""
    if val is None or val == "":
        return default
    try:
        return float(str(val).replace(",", ""))
    except (ValueError, TypeError):
        return default
