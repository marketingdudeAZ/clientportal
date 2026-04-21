"""Daily / weekly SEO refresh driver.

Invoked by Railway cron (or ad-hoc by POST /api/admin/seo-refresh) to:
  - Pull rank snapshots for all tracked keywords of every SEO-tier property
    and append to the BigQuery rank table.
  - Weekly: run AI-mentions scan, persist snapshot to HubDB.
  - Weekly: refresh on-page audit score, write to HubSpot company property.

Kept here rather than in server.py so the request path stays slim.
"""

import logging
import os
from datetime import datetime, timezone

import requests

from ai_mentions import persist_snapshot, scan_mentions
from config import HUBSPOT_API_KEY, HUBDB_SEO_KEYWORDS_TABLE_ID
from dataforseo_client import (
    backlinks_summary,
    is_configured,
    onpage_summary,
    onpage_task_post,
    serp_organic_advanced,
)
from hubdb_helpers import read_rows
from seo_entitlement import get_seo_tier

logger = logging.getLogger(__name__)

_HS_HDRS = {"Authorization": f"Bearer {HUBSPOT_API_KEY}", "Content-Type": "application/json"}


def _list_seo_companies() -> list[dict]:
    """Companies with plestatus RPM Managed + an seo_budget > 0 or SEO_Package line item."""
    url = "https://api.hubapi.com/crm/v3/objects/companies/search"
    body = {
        "filterGroups": [{
            "filters": [
                {"propertyName": "plestatus", "operator": "IN", "values": ["RPM Managed", "Onboarding"]},
                {"propertyName": "seo_budget", "operator": "HAS_PROPERTY"},
            ],
        }],
        "properties": ["name", "domain", "city", "state", "seo_budget", "property_uuid"],
        "limit": 100,
    }
    out: list[dict] = []
    after = None
    while True:
        if after:
            body["after"] = after
        r = requests.post(url, headers=_HS_HDRS, json=body, timeout=30)
        if r.status_code != 200:
            logger.error("CRM search failed: %s %s", r.status_code, r.text[:200])
            break
        data = r.json()
        out.extend(data.get("results", []))
        paging = data.get("paging", {}).get("next", {}).get("after")
        if not paging:
            break
        after = paging
    return out


def _find_property_uuid(company: dict) -> str | None:
    return company.get("properties", {}).get("property_uuid")


def refresh_ranks(property_uuid: str, domain: str, location_code: int | None = None) -> int:
    """Fetch current SERP position for each tracked keyword; write to BigQuery."""
    keywords = read_rows(HUBDB_SEO_KEYWORDS_TABLE_ID, filters={"property_uuid": property_uuid})
    if not keywords:
        return 0

    rows_to_insert = []
    now_iso = datetime.now(timezone.utc).isoformat()
    for kw in keywords:
        keyword = kw.get("keyword")
        if not keyword:
            continue
        try:
            serp = serp_organic_advanced(keyword, location_code=location_code)
        except Exception as e:
            logger.warning("SERP fetch failed for %r: %s", keyword, e)
            continue
        position = None
        ranked_url = None
        for item in serp.get("items") or []:
            url = (item.get("url") or "").lower()
            if domain and domain in url:
                position = item.get("rank_absolute")
                ranked_url = item.get("url")
                break
        rows_to_insert.append({
            "keyword": keyword,
            "position": position,
            "url": ranked_url,
            "volume": kw.get("volume"),
            "difficulty": kw.get("difficulty"),
            "fetched_at": now_iso,
        })

    if rows_to_insert:
        try:
            from bigquery_client import write_seo_rank_snapshot

            write_seo_rank_snapshot(property_uuid, rows_to_insert)
        except Exception as e:
            logger.error("BigQuery rank insert failed for %s: %s", property_uuid, e)
    return len(rows_to_insert)


def refresh_onpage(company_id: str, domain: str) -> int | None:
    """Kick off + poll on-page crawl; write audit score to HubSpot company property."""
    try:
        task_id = onpage_task_post(domain, max_crawl_pages=50)
    except Exception as e:
        logger.warning("on_page task_post failed for %s: %s", domain, e)
        return None

    summary = {}
    for _ in range(12):
        import time as _t
        _t.sleep(10)
        try:
            summary = onpage_summary(task_id)
        except Exception:
            continue
        if summary and summary.get("crawl_progress") == "finished":
            break

    score = None
    crawl = summary.get("domain_info") or {}
    if "page_count" in summary:
        total = summary.get("page_count") or 1
        broken = (crawl.get("checks") or {}).get("broken_links", 0)
        score = max(0, int(100 - (broken / total) * 100))

    if score is not None:
        url = f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}"
        requests.patch(
            url,
            headers=_HS_HDRS,
            json={"properties": {
                "seo_last_audit_score": score,
                "seo_last_crawl_at": datetime.now(timezone.utc).isoformat(),
            }},
            timeout=15,
        )
    return score


def refresh_ai_mentions(property_uuid: str, property_name: str, domain: str, city: str):
    scan = scan_mentions(property_name, domain, city)
    persist_snapshot(property_uuid, scan)
    return scan


def run_daily():
    if not is_configured():
        logger.error("DataForSEO not configured — aborting daily refresh")
        return
    companies = _list_seo_companies()
    logger.info("Daily refresh: %d SEO companies", len(companies))
    for c in companies:
        props = c.get("properties", {})
        tier = get_seo_tier(c["id"])
        if not tier:
            continue
        uuid = _find_property_uuid(c)
        domain = props.get("domain")
        if not (uuid and domain):
            continue
        refresh_ranks(uuid, domain)


def run_weekly():
    if not is_configured():
        logger.error("DataForSEO not configured — aborting weekly refresh")
        return
    companies = _list_seo_companies()
    logger.info("Weekly refresh: %d SEO companies", len(companies))
    for c in companies:
        props = c.get("properties", {})
        tier = get_seo_tier(c["id"])
        if not tier:
            continue
        uuid = _find_property_uuid(c)
        domain = props.get("domain")
        city = props.get("city") or ""
        name = props.get("name") or ""
        if not (uuid and domain):
            continue
        try:
            refresh_ai_mentions(uuid, name, domain, city)
        except Exception as e:
            logger.error("AI mentions refresh failed for %s: %s", uuid, e)
        try:
            refresh_onpage(c["id"], domain)
        except Exception as e:
            logger.error("On-page refresh failed for %s: %s", uuid, e)


if __name__ == "__main__":
    mode = os.getenv("CRON_MODE", "daily")
    logging.basicConfig(level=logging.INFO)
    if mode == "weekly":
        run_weekly()
    else:
        run_daily()
