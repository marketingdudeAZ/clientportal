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
    """Companies with plestatus RPM Managed + an seo_budget > 0 or SEO_Package line item.

    If SEO_PROPERTY_UUID_ALLOWLIST env var is set (comma-separated list of UUIDs),
    only companies whose uuid matches are returned. Used during testing to cap
    DataForSEO spend — e.g. SEO_PROPERTY_UUID_ALLOWLIST=10559996814 scopes to
    only Muse at Winter Garden.
    """
    url = "https://api.hubapi.com/crm/v3/objects/companies/search"
    body = {
        "filterGroups": [{
            "filters": [
                {"propertyName": "plestatus", "operator": "IN", "values": ["RPM Managed", "Onboarding"]},
                {"propertyName": "seo_budget", "operator": "HAS_PROPERTY"},
            ],
        }],
        # Pull `uuid` (the HubSpot property name) — the RPM property UUID is stored there.
        "properties": ["name", "domain", "city", "state", "seo_budget", "uuid"],
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

    # Optional allowlist — only run the cron against these property UUIDs.
    # Useful for staged rollout / cost control during testing.
    allowlist_raw = os.getenv("SEO_PROPERTY_UUID_ALLOWLIST", "").strip()
    if allowlist_raw:
        allowed = {u.strip() for u in allowlist_raw.split(",") if u.strip()}
        before = len(out)
        out = [c for c in out if _find_property_uuid(c) in allowed]
        logger.info("SEO_PROPERTY_UUID_ALLOWLIST active: %d -> %d companies", before, len(out))

    return out


def _find_property_uuid(company: dict) -> str | None:
    # HubSpot stores the RPM UUID in the `uuid` company property. Fall back to
    # the older `property_uuid` name in case any environments still use it.
    props = company.get("properties", {})
    return props.get("uuid") or props.get("property_uuid")


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
            # Don't silently swallow — callers need to know the BQ write failed.
            # Common cause: seo_ranks_daily table doesn't exist. See BIGQUERY_SETUP.md §3.
            logger.error("BigQuery rank insert failed for %s: %s", property_uuid, e)
            raise RuntimeError(f"BigQuery write failed: {e}") from e
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


def _meets_tier(tier: str, min_tier: str) -> bool:
    """True if `tier` meets or exceeds `min_tier` in SEO_TIER_ORDER."""
    from config import SEO_TIER_ORDER
    if not tier or tier not in SEO_TIER_ORDER:
        return False
    try:
        return SEO_TIER_ORDER.index(tier) >= SEO_TIER_ORDER.index(min_tier)
    except ValueError:
        return False


def _refresh_content_planning(uuid: str, domain: str) -> None:
    """Phase 2 — rebuild cluster cache + populate decay queue for one property.

    Called from run_weekly for Standard+ properties. Errors are logged and
    swallowed so one bad property doesn't stop the rest of the cron.
    """
    from datetime import datetime as _dt

    try:
        from content_planner import cluster_keywords
        clusters = cluster_keywords(uuid)
        logger.info("cluster rebuild for %s: %d clusters", uuid, len(clusters))
    except Exception as e:
        logger.error("cluster rebuild failed for %s: %s", uuid, e)

    try:
        from content_planner import detect_decay
        from config import (
            HUBDB_CONTENT_DECAY_TABLE_ID,
            CONTENT_DECAY_RANK_THRESHOLD,
            CONTENT_DECAY_MIN_KEYWORDS,
            CONTENT_REFRESH_LOOKBACK_DAYS,
        )
        decay_rows = detect_decay(
            uuid,
            threshold=CONTENT_DECAY_RANK_THRESHOLD,
            min_affected=CONTENT_DECAY_MIN_KEYWORDS,
            lookback_days=CONTENT_REFRESH_LOOKBACK_DAYS,
        )
        logger.info("decay detection for %s: %d URLs flagged", uuid, len(decay_rows))

        # Persist to HubDB so the /api/content/decay endpoint can read it instantly.
        if HUBDB_CONTENT_DECAY_TABLE_ID and decay_rows:
            import json as _json
            from hubdb_helpers import insert_row, publish
            now_iso = _dt.utcnow().isoformat() + "Z"
            for row in decay_rows:
                try:
                    insert_row(HUBDB_CONTENT_DECAY_TABLE_ID, {
                        "property_uuid":           uuid,
                        "url":                     row["url"],
                        "avg_rank_drop":           row["avg_drop"],
                        "affected_keywords_count": row["affected_keywords_count"],
                        "affected_keywords_json":  _json.dumps(row["affected_keywords"]),
                        "priority":                row["priority"],
                        "detected_at":             now_iso,
                        "status":                  "open",
                    })
                except Exception as e:
                    logger.warning("decay row insert failed for %s: %s", row["url"], e)
            publish(HUBDB_CONTENT_DECAY_TABLE_ID)
    except Exception as e:
        logger.error("decay detection failed for %s: %s", uuid, e)


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
        # Phase 2 — Standard+ gets cluster rebuild + decay detection
        if _meets_tier(tier, "Standard"):
            _refresh_content_planning(uuid, domain)


if __name__ == "__main__":
    mode = os.getenv("CRON_MODE", "daily")
    logging.basicConfig(level=logging.INFO)
    if mode == "weekly":
        run_weekly()
    else:
        run_daily()
