"""Layer 1+2 — read each property's own website, and store what the pages say.

WHY THIS EXISTS
    Four of the SEO rules in `skills/reco_seo.py` are about the property's own
    site: a floor plan with no page, availability no engine can read, landing
    pages with no title, and pages nothing links to. Every one of them reads
    `data["pages"]`, and until now nothing in the repo produced that list. The
    vendor's site-health feed covers one website; the roster has 98. So the
    four rules were skipped on 97 sites and reported as "not measured" —
    honest, and useless.

    This module is the missing read. It crawls one site at a time through
    DataForSEO's On-Page API, keeps only the facts those rules need, and
    appends them to `BIGQUERY_SEO_AUDIT_TABLE` (`seo_onpage_audit`) — a table
    that has been declared in config and written by nothing.

WHAT IT MEASURES, AND WHAT IT REFUSES TO
    Per page: `title`, `meta_description`, `h1` (and every h1, so a page with
    two of them is visible), the JSON-LD `@type`s, inbound internal link count,
    word count, canonical and status code.

    Three refusals, because each one is a way to manufacture a finding:

    * A crawl that stopped at the page cap has an incomplete link graph. A page
      can then look unlinked only because the page that links to it was never
      reached. `internal_links_in` is stored NULL for a truncated crawl and the
      reader omits the key, so `rule_orphan_page` stays quiet rather than
      reporting invented orphans. `crawl_complete` on every row records which
      it was.
    * JSON-LD needs the page's raw HTML, one fetch per page, so it is capped
      separately. A page whose HTML we never read carries no `jsonld_types` at
      all — absent, not "no markup found". `jsonld_read` records the difference,
      because an empty repeated field and a NULL one are the same value coming
      back out of BigQuery.
    * A crawl that never finished is not stored. Partial inventories are the
      one input that would make all four rules lie at once.

    A page we DID read and that genuinely has no title is stored as "" — that
    is a measurement, and it is the finding. Only unmeasured is NULL. This is
    the same distinction `reco_seo._pages_from_site_health` draws, for the same
    reason.

IDEMPOTENCY AND COST
    One row set per (site, crawl date). A second run on the same day finds the
    site already stored and skips it without spending a call; `force=True` is
    the deliberate override. Every DataForSEO response reports what it charged
    and that number is summed and logged per site — nothing here estimates a
    price we did not observe.

ONE SITE FAILING
    `crawl_sites()` catches everything per site. A site that times out, 404s or
    returns nonsense becomes an entry in `failures` and the run continues. A
    batch over 98 sites that stops on the first bad one is not a batch.

READ ONLY OUTSIDE ITS OWN TABLE
    Nothing here writes to HubSpot, so R1 cannot be violated through this path.
    HubSpot is read through `skills/property_resolver` and `skills/rpmi_roster`
    (CLAUDE.md, Layer 2), never directly.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from config import (
    BIGQUERY_SEO_AUDIT_TABLE,
    SEO_CRAWL_ENABLE_JAVASCRIPT,
    SEO_CRAWL_JSONLD_PAGES,
    SEO_CRAWL_LOOKBACK_DAYS,
    SEO_CRAWL_MAX_PAGES,
    SEO_CRAWL_POLL_SECONDS,
    SEO_CRAWL_POLL_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)

# The word every receipt in reco_seo already uses for this source.
SOURCE = "site_crawl"

# Sites that are not a property's own site. Mirrors rpmi_roster.SHARED_SITES:
# crawling the corporate domain once per property that points at it would bill
# 98 crawls of the same pages and file the same findings under every name.
SHARED_SITES = frozenset({"rpmliving.com"})

# Only HTML documents become rows. A PDF or an image has no title to be missing.
HTML_RESOURCE_TYPES = frozenset({"html", ""})

# Paths a renter lands on with intent — the order the JSON-LD budget is spent
# in. Same vocabulary as reco_seo.MONEY_PAGE_TYPES, stated here because that
# module reads pages and this one produces them; neither imports the other.
_MONEY_PATH_HINTS: Tuple[str, ...] = (
    "floorplan", "floor-plan", "floor_plan", "availability", "pricing", "rates",
    "amenities", "neighborhood", "contact", "tour", "apply", "gallery",
)

_LD_BLOCK = re.compile(
    r"<script[^>]*?type\s*=\s*[\"']application/ld\+json[\"'][^>]*?>(.*?)</script\s*>",
    re.IGNORECASE | re.DOTALL,
)


# ── small helpers ────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _gap(field: str, source: str, message: str) -> Dict[str, str]:
    """The shape reco_engine, mcp_context and reco_seo all use for a gap."""
    return {"field": field, "source": source, "message": message}


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    out = str(value).strip()
    return out


def _int(value: Any) -> Optional[int]:
    try:
        if value in (None, ""):
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_site(*candidates: Any) -> Optional[str]:
    """First usable candidate reduced to a bare host: 'x.com'.

    HubSpot stores the same site three ways across these records
    ("https://www.x.com/", "www.x.com", "x.com"). Same reduction as
    `rpmi_roster._host`, so a crawl keys off exactly the host the roster
    de-duplicated on — otherwise the same site is crawled twice under two
    spellings and neither run is idempotent against the other.
    """
    for candidate in candidates:
        text = _text(candidate)
        if not text:
            continue
        text = re.sub(r"^[a-z]+://", "", text.lower(), flags=re.I)
        text = re.sub(r"^www\.", "", text)
        text = text.split("/")[0].split("?")[0].strip().rstrip(".")
        if text and "." in text:
            return text
    return None


def _path_of(url: Any) -> str:
    raw = str(url or "").lower()
    stripped = raw.split("//", 1)[-1]
    if "/" not in stripped:
        return "/"
    return stripped[stripped.find("/"):]


def _money_rank(url: Any) -> Tuple[int, str]:
    """Sort key putting the pages a renter lands on first.

    Homepage, then the intent paths, then everything else. Used only to decide
    which pages the JSON-LD budget is spent on.
    """
    path = _path_of(url)
    if path in ("/", ""):
        return (0, path)
    for index, hint in enumerate(_MONEY_PATH_HINTS):
        if hint in path:
            return (1 + index, path)
    return (1 + len(_MONEY_PATH_HINTS), path)


# ── extraction ───────────────────────────────────────────────────────────────

def jsonld_types(html: Any) -> List[str]:
    """Every `@type` in every ld+json block on the page, de-duplicated.

    Walks nested objects and `@graph`, because the schema these sites emit
    almost always arrives as one graph rather than one object per block. A
    block that does not parse is skipped — a malformed script is not a type,
    and guessing at one is how a rule ends up asserting markup that is not
    there.
    """
    text = str(html or "")
    if not text:
        return []
    found: List[str] = []
    seen = set()

    def note(value: Any) -> None:
        for item in (value if isinstance(value, list) else [value]):
            name = _text(item)
            if name and name not in seen:
                seen.add(name)
                found.append(name)

    def walk(node: Any, depth: int = 0) -> None:
        if depth > 12:
            return
        if isinstance(node, dict):
            if "@type" in node:
                note(node["@type"])
            for value in node.values():
                if isinstance(value, (dict, list)):
                    walk(value, depth + 1)
        elif isinstance(node, list):
            for value in node:
                walk(value, depth + 1)

    for block in _LD_BLOCK.findall(text):
        body = block.strip()
        # Some templates wrap the JSON in a CDATA or HTML comment guard.
        body = re.sub(r"^<!\[CDATA\[|\]\]>$", "", body).strip()
        body = re.sub(r"^<!--|-->$", "", body).strip()
        if not body:
            continue
        try:
            walk(json.loads(body))
        except ValueError:
            logger.debug("seo_crawl: unparseable ld+json block skipped")
    return found


def inbound_counts(link_items: Iterable[Any], site: Optional[str] = None) -> Dict[str, int]:
    """Internal inbound links per destination URL, from the crawl's link graph.

    The fallback for when DataForSEO's per-page `inbound_links_count` is absent.
    External links and a page linking to itself are not links in, and counting
    either turns every page on the site into a well-linked one.
    """
    counts: Dict[str, int] = {}
    for item in link_items or []:
        if not isinstance(item, dict):
            continue
        if item.get("is_external") is True:
            continue
        source = _text(item.get("page_from") or item.get("link_from") or
                       item.get("url_from"))
        target = _text(item.get("page_to") or item.get("link_to") or
                       item.get("url_to"))
        if not target or target == source:
            continue
        if site:
            host = normalize_site(target)
            if host and host != site and not host.endswith("." + site):
                continue
        counts[target] = counts.get(target, 0) + 1
    return counts


def _first_h1(htags: Any) -> Tuple[Optional[str], Optional[List[str]]]:
    """(first h1, every h1). ('' , []) when the page carries none.

    A page with two h1s is a real finding, so all of them are kept; reco_seo
    reads them back as `headings` and says "more than one top heading".
    """
    if not isinstance(htags, dict):
        return None, None
    raw = htags.get("h1")
    if raw is None:
        return None, None
    values = raw if isinstance(raw, list) else [raw]
    texts = [_text(v) for v in values]
    texts = [t for t in texts if t is not None]
    return (texts[0] if texts else ""), texts


def extract_page(item: Any) -> Optional[Dict[str, Any]]:
    """One DataForSEO on-page item → the facts the SEO rules read, or None.

    None when the item is not an HTML document we actually read: a redirect, a
    PDF, an image, a page the crawler could not fetch. Those carry no title to
    be missing, and a row for one would be a finding about a file.
    """
    if not isinstance(item, dict):
        return None
    url = _text(item.get("url"))
    if not url:
        return None
    resource = (_text(item.get("resource_type")) or "").lower()
    if resource not in HTML_RESOURCE_TYPES:
        return None
    meta = item.get("meta")
    if not isinstance(meta, dict):
        return None
    status = _int(item.get("status_code"))
    if status is not None and not (200 <= status < 300):
        return None

    h1, h1_all = _first_h1(meta.get("htags"))
    content = meta.get("content") if isinstance(meta.get("content"), dict) else {}
    inbound = _int(meta.get("inbound_links_count"))
    if inbound is None:
        inbound = _int(meta.get("internal_links_in"))

    return {
        "url": url,
        "status_code": status,
        # "" is a measurement — the page has no title. None never reaches here
        # because a non-HTML item returned above.
        "title": _text(meta.get("title")) or "",
        "meta_description": (_text(meta.get("description")) or
                             _text(meta.get("meta_description")) or ""),
        "h1": h1 if h1 is not None else "",
        "h1_all": h1_all if h1_all is not None else [],
        "internal_links_in": inbound,
        "internal_links_out": _int(meta.get("internal_links_count")),
        "word_count": _int(content.get("plain_text_word_count") or
                           meta.get("plain_text_word_count")),
        "canonical": _text(meta.get("canonical")),
        "onpage_score": _float(item.get("onpage_score")),
    }


def extract_pages(items: Iterable[Any],
                  link_graph: Optional[Dict[str, int]] = None) -> List[Dict[str, Any]]:
    """Every readable HTML page in a crawl inventory, link counts filled in.

    `link_graph` is used only where the per-page count is missing; a page with
    no inbound entry in a graph we do hold has zero links in, which is the
    orphan finding.
    """
    pages: List[Dict[str, Any]] = []
    for item in items or []:
        page = extract_page(item)
        if page is None:
            continue
        if page["internal_links_in"] is None and link_graph is not None:
            page["internal_links_in"] = int(link_graph.get(page["url"], 0))
            page["inbound_source"] = "link_graph"
        else:
            page["inbound_source"] = "meta" if page["internal_links_in"] is not None else None
        pages.append(page)
    pages.sort(key=lambda p: _money_rank(p["url"]))
    return pages


# ── storage ──────────────────────────────────────────────────────────────────

def _bq():
    import bigquery_client
    return bigquery_client


def _rows_for(site: str, pages: Sequence[Dict[str, Any]], *,
              crawl_date: str, target: str, task_id: Optional[str],
              company_id: Optional[str], property_uuid: Optional[str],
              property_name: Optional[str], crawl_complete: bool,
              pages_available: Optional[int]) -> List[Dict[str, Any]]:
    """Pages → BigQuery rows. This is where the honesty rules are applied."""
    stamp = _now_iso()
    rows: List[Dict[str, Any]] = []
    for page in pages:
        jsonld_read = "jsonld_types" in page
        rows.append({
            "crawl_date": crawl_date,
            "site": site,
            "target": target,
            "company_id": company_id,
            "property_uuid": property_uuid,
            "property_name": property_name,
            "url": page["url"],
            "status_code": page.get("status_code"),
            "title": page.get("title"),
            "meta_description": page.get("meta_description"),
            "h1": page.get("h1"),
            "h1_all": list(page.get("h1_all") or []),
            "jsonld_read": jsonld_read,
            "jsonld_types": list(page.get("jsonld_types") or []),
            # A truncated crawl has a partial link graph, so the count it
            # produced is not a measurement of the site. NULL, not 0.
            "internal_links_in": page.get("internal_links_in") if crawl_complete else None,
            "internal_links_out": page.get("internal_links_out"),
            "inbound_source": page.get("inbound_source") if crawl_complete else None,
            "word_count": page.get("word_count"),
            "canonical": page.get("canonical"),
            "onpage_score": page.get("onpage_score"),
            "crawl_complete": bool(crawl_complete),
            "pages_available": pages_available,
            "task_id": task_id,
            "crawled_at": stamp,
        })
    return rows


def store_rows(rows: Sequence[Dict[str, Any]]) -> int:
    """Append rows to the audit table. Returns how many landed."""
    if not rows:
        return 0
    _bq().insert_rows(BIGQUERY_SEO_AUDIT_TABLE, list(rows))
    logger.info("seo_crawl stored %d rows in %s", len(rows), BIGQUERY_SEO_AUDIT_TABLE)
    return len(rows)


def stored_sites(crawl_date: Optional[str] = None) -> Optional[set]:
    """Hosts already stored for a crawl date, or None when we cannot tell.

    None is not "nothing stored" — it is "BigQuery could not answer". The
    caller must treat the two differently: skipping on None would silently stop
    crawling, and re-crawling on a wrong empty set would bill the run twice.
    """
    day = crawl_date or date.today().isoformat()
    bq = _bq()
    if not bq.is_bigquery_configured():
        return None
    try:
        from google.cloud import bigquery as bq_types
        sql = (
            "SELECT DISTINCT site FROM `%s.%s.%s` WHERE crawl_date = @day"
            % (_project(), bq._dataset(), BIGQUERY_SEO_AUDIT_TABLE)
        )
        rows = bq.query(sql, [bq_types.ScalarQueryParameter("day", "DATE", day)])
    except Exception as exc:  # noqa: BLE001 — an unanswerable table is not a crash
        logger.warning("seo_crawl: could not read stored sites for %s (%s)", day, exc)
        return None
    return {r.get("site") for r in rows if r.get("site")}


def _project() -> str:
    from config import BIGQUERY_PROJECT_ID
    return BIGQUERY_PROJECT_ID


# ── the crawl ────────────────────────────────────────────────────────────────

def _client(client: Any = None):
    if client is not None:
        return client
    import dataforseo_client
    return dataforseo_client


def _poll(client: Any, task_id: str, *, poll_seconds: int, poll_timeout: int,
          sleep: Callable[[float], None], clock: Callable[[], float]) -> Dict[str, Any]:
    """Wait for the crawl to finish. Returns the last summary read.

    The summary carries `crawl_progress`; anything other than "finished" when
    the clock runs out means the caller must not store what it fetched.
    """
    deadline = clock() + max(poll_timeout, 0)
    summary: Dict[str, Any] = {}
    while True:
        summary = client.onpage_summary(task_id) or {}
        if (_text(summary.get("crawl_progress")) or "").lower() == "finished":
            return summary
        if clock() >= deadline:
            return summary
        sleep(max(poll_seconds, 1))


def crawl_site(site: Any, *, company_id: Optional[str] = None,
               property_uuid: Optional[str] = None,
               property_name: Optional[str] = None,
               max_pages: Optional[int] = None,
               jsonld_pages: Optional[int] = None,
               enable_javascript: Optional[bool] = None,
               crawl_date: Optional[str] = None,
               store: bool = True, force: bool = False,
               already: Optional[set] = None,
               poll_seconds: Optional[int] = None,
               poll_timeout: Optional[int] = None,
               client: Any = None,
               sleep: Optional[Callable[[float], None]] = None,
               clock: Optional[Callable[[], float]] = None) -> Dict[str, Any]:
    """Crawl one site and store its pages. Never raises for a site problem.

    Returns a result dict whose `status` is one of:
      stored              — rows written (or `store=False`: rows built only)
      already_crawled     — this site already has rows for this crawl date
      unreadable_site     — no usable host, or the shared corporate domain
      crawl_unfinished    — the crawl did not finish inside the timeout
      no_pages            — the crawl finished and read no HTML page
      failed              — DataForSEO or BigQuery refused; see `errors`
    """
    day = crawl_date or date.today().isoformat()
    host = normalize_site(site)
    result: Dict[str, Any] = {
        "site": host, "input": _text(site), "company_id": company_id,
        "property_uuid": property_uuid, "property_name": property_name,
        "crawl_date": day, "status": None, "pages": 0, "rows": [],
        "stored": 0, "cost_usd": 0.0, "task_id": None,
        "crawl_complete": None, "errors": [], "gaps": [],
    }

    if not host:
        result["status"] = "unreadable_site"
        result["gaps"].append(_gap("pages", SOURCE,
                                   "This property has no usable website recorded, so "
                                   "its site cannot be read at all."))
        return result
    if host in SHARED_SITES:
        result["status"] = "unreadable_site"
        result["gaps"].append(_gap("pages", SOURCE,
                                   "%s is the corporate site, not this property's own "
                                   "site; crawling it would file findings about "
                                   "someone else's pages." % host))
        return result

    if not force:
        seen = already if already is not None else stored_sites(day)
        if seen is not None and host in seen:
            result["status"] = "already_crawled"
            logger.info("seo_crawl: %s already stored for %s — skipped", host, day)
            return result

    cap = int(max_pages if max_pages is not None else SEO_CRAWL_MAX_PAGES)
    ld_cap = int(jsonld_pages if jsonld_pages is not None else SEO_CRAWL_JSONLD_PAGES)
    js = SEO_CRAWL_ENABLE_JAVASCRIPT if enable_javascript is None else bool(enable_javascript)
    api = _client(client)
    sleep = sleep or time.sleep
    clock = clock or time.time

    try:
        created = api.onpage_task_create(
            host, max_crawl_pages=cap, enable_javascript=js,
            store_raw_html=ld_cap > 0) or {}
        task_id = created.get("task_id")
        result["task_id"] = task_id
        result["cost_usd"] += float(created.get("cost") or 0.0)
        if not task_id:
            raise RuntimeError("no task id returned for %s" % host)

        summary = _poll(
            api, task_id,
            poll_seconds=int(poll_seconds if poll_seconds is not None
                             else SEO_CRAWL_POLL_SECONDS),
            poll_timeout=int(poll_timeout if poll_timeout is not None
                             else SEO_CRAWL_POLL_TIMEOUT_SECONDS),
            sleep=sleep, clock=clock)
        progress = (_text(summary.get("crawl_progress")) or "").lower()
        if progress != "finished":
            result["status"] = "crawl_unfinished"
            result["errors"].append("crawl still %s after the timeout" % (progress or "unstarted"))
            result["gaps"].append(_gap("pages", SOURCE,
                                       "The crawl of %s did not finish, so nothing was "
                                       "stored — a partial read of a site would make "
                                       "every page-level finding unreliable." % host))
            logger.warning("seo_crawl: %s unfinished (%s) — nothing stored", host, progress)
            return result

        inventory = api.onpage_pages(task_id, limit=cap) or {}
        result["cost_usd"] += float(inventory.get("cost") or 0.0)
        items = inventory.get("items") or []
        crawl_status = inventory.get("crawl_status") or summary.get("crawl_status") or {}
        available = _int(inventory.get("total")) or _int(crawl_status.get("pages_crawled"))
        complete = _crawl_is_complete(crawl_status, available, cap, len(items))
        result["crawl_complete"] = complete

        pages = extract_pages(items)
        if any(p.get("internal_links_in") is None for p in pages) and complete:
            graph, cost = _link_graph(api, task_id, host)
            result["cost_usd"] += cost
            if graph is not None:
                pages = extract_pages(items, link_graph=graph)

        if not pages:
            result["status"] = "no_pages"
            result["gaps"].append(_gap("pages", SOURCE,
                                       "The crawl of %s finished and found no readable "
                                       "HTML page." % host))
            logger.warning("seo_crawl: %s finished with no readable pages", host)
            return result

        if ld_cap > 0:
            result["cost_usd"] += _attach_jsonld(api, task_id, pages, ld_cap)

        if not complete:
            result["gaps"].append(_gap(
                "internal_links_in", SOURCE,
                "The crawl of %s stopped at the %d-page cap, so the link graph is "
                "partial and inbound link counts were not stored for it." % (host, cap)))

        rows = _rows_for(host, pages, crawl_date=day, target=host, task_id=task_id,
                         company_id=company_id, property_uuid=property_uuid,
                         property_name=property_name, crawl_complete=complete,
                         pages_available=available)
        result["pages"] = len(pages)
        result["rows"] = rows
        if store:
            result["stored"] = store_rows(rows)
        result["status"] = "stored"
        logger.info("seo_crawl %s: %d pages, complete=%s, cost $%.4f",
                    host, len(pages), complete, result["cost_usd"])
        return result
    except Exception as exc:  # noqa: BLE001 — one site must never sink the run
        result["status"] = "failed"
        result["errors"].append(str(exc))
        result["gaps"].append(_gap("pages", SOURCE,
                                   "The site read for %s failed, so its page-level "
                                   "findings are unmeasured rather than clean." % host))
        logger.error("seo_crawl: %s failed: %s", host, exc, exc_info=True)
        return result


def _crawl_is_complete(crawl_status: Dict[str, Any], available: Optional[int],
                       cap: int, fetched: int) -> bool:
    """Did the whole site fit inside the cap?

    Complete means nothing was left in the queue and the crawler did not stop
    because it ran out of allowance. Anything less and the link graph is a
    sample, which `_rows_for` refuses to store as a count.
    """
    queued = _int(crawl_status.get("pages_in_queue"))
    crawled = _int(crawl_status.get("pages_crawled"))
    if queued:
        return False
    if crawled is not None and cap and crawled >= cap:
        return False
    if available is not None and available > fetched:
        return False
    return True


def _link_graph(api: Any, task_id: str,
                site: str) -> Tuple[Optional[Dict[str, int]], float]:
    """The crawl's link graph, or (None, cost) when it cannot be read."""
    reader = getattr(api, "onpage_links", None)
    if not callable(reader):
        return None, 0.0
    try:
        payload = reader(task_id) or {}
    except Exception as exc:  # noqa: BLE001 — a missing graph is a gap, not a crash
        logger.info("seo_crawl: link graph unavailable for %s (%s)", site, exc)
        return None, 0.0
    return inbound_counts(payload.get("items") or [], site), float(payload.get("cost") or 0.0)


def _attach_jsonld(api: Any, task_id: str, pages: List[Dict[str, Any]],
                   limit: int) -> float:
    """Read raw HTML for up to `limit` pages and attach their JSON-LD types.

    `pages` is already ordered with the pages a renter lands on first, so the
    budget lands where availability and pricing markup belongs. A page whose
    HTML could not be read keeps no `jsonld_types` key at all.
    """
    reader = getattr(api, "onpage_raw_html", None)
    if not callable(reader):
        return 0.0
    cost = 0.0
    for page in pages[:max(limit, 0)]:
        try:
            payload = reader(task_id, page["url"]) or {}
        except Exception as exc:  # noqa: BLE001 — per-page, so one bad page is one gap
            logger.info("seo_crawl: raw html unavailable for %s (%s)", page["url"], exc)
            continue
        cost += float(payload.get("cost") or 0.0)
        html = payload.get("html")
        if html is None:
            continue
        page["jsonld_types"] = jsonld_types(html)
    return cost


# ── batches ──────────────────────────────────────────────────────────────────

def plan(targets: Sequence[Dict[str, Any]], *, max_pages: Optional[int] = None,
         crawl_date: Optional[str] = None,
         check_stored: bool = True) -> Dict[str, Any]:
    """What a run would crawl, without calling DataForSEO. Drives --dry-run.

    No cost is predicted: DataForSEO reports what it charged in the response,
    and a number invented here would be the first fabricated figure in the
    pipeline. The page cap is reported instead, which is the thing that
    actually bounds the spend.
    """
    day = crawl_date or date.today().isoformat()
    cap = int(max_pages if max_pages is not None else SEO_CRAWL_MAX_PAGES)
    seen = stored_sites(day) if check_stored else None

    rows: List[Dict[str, Any]] = []
    hosts: Dict[str, int] = {}
    for target in targets or []:
        host = normalize_site(target.get("site") or target.get("website") or
                              target.get("domain"))
        if not host:
            state = "unreadable_site"
        elif host in SHARED_SITES:
            state = "unreadable_site"
        elif host in hosts:
            state = "duplicate_of_earlier_target"
        elif seen is not None and host in seen:
            state = "already_crawled"
        else:
            state = "would_crawl"
        if host and state == "would_crawl":
            hosts[host] = 1
        rows.append({"site": host, "input": target.get("site") or target.get("website"),
                     "company_id": target.get("company_id"),
                     "property_name": target.get("name") or target.get("property_name"),
                     "state": state})

    return {
        "crawl_date": day,
        "max_pages_per_site": cap,
        "targets": len(rows),
        "would_crawl": sum(1 for r in rows if r["state"] == "would_crawl"),
        "already_crawled": sum(1 for r in rows if r["state"] == "already_crawled"),
        "unreadable": sum(1 for r in rows if r["state"] == "unreadable_site"),
        "duplicates": sum(1 for r in rows if r["state"] == "duplicate_of_earlier_target"),
        "stored_sites_known": seen is not None,
        "max_pages_total": cap * sum(1 for r in rows if r["state"] == "would_crawl"),
        "rows": rows,
    }


def crawl_sites(targets: Sequence[Dict[str, Any]], *, crawl_date: Optional[str] = None,
                force: bool = False, **kwargs: Any) -> Dict[str, Any]:
    """Crawl a list of targets. One failure never stops the rest.

    A target is `{site|website|domain, company_id?, uuid?, name?}`. The
    already-stored set is read once for the whole run rather than per site.
    """
    day = crawl_date or date.today().isoformat()
    already = None if force else stored_sites(day)

    results: List[Dict[str, Any]] = []
    done: Dict[str, str] = {}
    for target in targets or []:
        host = normalize_site(target.get("site") or target.get("website") or
                              target.get("domain"))
        if host and host in done:
            results.append({"site": host, "company_id": target.get("company_id"),
                            "crawl_date": day, "status": "already_crawled",
                            "pages": 0, "rows": [], "stored": 0, "cost_usd": 0.0,
                            "task_id": None, "crawl_complete": None,
                            "errors": [], "gaps": []})
            continue
        try:
            outcome = crawl_site(
                target.get("site") or target.get("website") or target.get("domain"),
                company_id=target.get("company_id"),
                property_uuid=target.get("uuid") or target.get("property_uuid"),
                property_name=target.get("name") or target.get("property_name"),
                crawl_date=day, force=force, already=already, **kwargs)
        except Exception as exc:  # noqa: BLE001 — belt and braces around crawl_site
            logger.error("seo_crawl: target %s raised: %s", target, exc, exc_info=True)
            outcome = {"site": host, "company_id": target.get("company_id"),
                       "crawl_date": day, "status": "failed", "pages": 0, "rows": [],
                       "stored": 0, "cost_usd": 0.0, "task_id": None,
                       "crawl_complete": None, "errors": [str(exc)], "gaps": []}
        if host and outcome.get("status") == "stored":
            done[host] = "stored"
        results.append(outcome)

    stored = [r for r in results if r.get("status") == "stored"]
    failures = [{"site": r.get("site"), "company_id": r.get("company_id"),
                 "status": r.get("status"), "errors": r.get("errors") or []}
                for r in results if r.get("status") in ("failed", "crawl_unfinished",
                                                        "no_pages", "unreadable_site")]
    cost = round(sum(float(r.get("cost_usd") or 0.0) for r in results), 6)
    pages = sum(int(r.get("pages") or 0) for r in results)
    summary = {
        "crawl_date": day,
        "sites": len(results),
        "crawled": len(stored),
        "skipped_already_crawled": sum(1 for r in results
                                       if r.get("status") == "already_crawled"),
        "incomplete_link_graphs": sum(1 for r in stored if r.get("crawl_complete") is False),
        "pages": pages,
        "rows_stored": sum(int(r.get("stored") or 0) for r in results),
        "cost_usd": cost,
        "cost_per_site_usd": round(cost / len(stored), 6) if stored else None,
        "failures": failures,
        "results": results,
    }
    logger.info("seo_crawl run %s: %d/%d sites, %d pages, %d failures, $%.4f",
                day, len(stored), len(results), pages, len(failures), cost)
    return summary


def crawl_property(company_id: Any, **kwargs: Any) -> Dict[str, Any]:
    """Crawl the one site belonging to a HubSpot company. Read-only on HubSpot."""
    try:
        from skills import property_resolver as pr
        identity = pr.resolve(str(company_id)).to_dict()
    except Exception as exc:  # noqa: BLE001
        logger.error("seo_crawl: could not resolve %s (%s)", company_id, exc)
        return {"site": None, "company_id": str(company_id), "status": "failed",
                "pages": 0, "rows": [], "stored": 0, "cost_usd": 0.0,
                "task_id": None, "crawl_complete": None, "errors": [str(exc)],
                "gaps": [_gap("pages", "property_resolver",
                              "This property could not be resolved, so there is no "
                              "website to read.")]}
    return crawl_site(identity.get("domain") or identity.get("website"),
                      company_id=str(company_id), property_uuid=identity.get("uuid"),
                      property_name=identity.get("name"), **kwargs)


def rpmi_targets(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """The RPMI roster as crawl targets — already one row per site."""
    from skills import rpmi_roster
    rows = rpmi_roster.get_roster()
    targets = [{"site": r.get("domain") or r.get("website"),
                "company_id": r.get("company_id"), "uuid": r.get("uuid"),
                "name": r.get("name")} for r in rows]
    if limit is not None:
        targets = targets[:int(limit)]
    return targets


def crawl_rpmi(limit: Optional[int] = None, **kwargs: Any) -> Dict[str, Any]:
    """Crawl the RPMI roster, one crawl per site."""
    return crawl_sites(rpmi_targets(limit), **kwargs)


# ── the reader the SEO rules call ────────────────────────────────────────────

_PAGE_STRINGS = ("title", "meta_description", "h1")


def _row_to_page(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """One stored row → one page the way `reco_seo`'s rules read pages.

    Absent means absent. A NULL column is left off the dict entirely, exactly
    as `reco_seo._pages_from_site_health` does: copying a NULL through as
    `title: None` would make the metadata rule report a page it never read as a
    page with no title.
    """
    url = _text(row.get("url"))
    if not url:
        return None
    page: Dict[str, Any] = {"url": url}
    for key in _PAGE_STRINGS:
        if row.get(key) is not None:
            page[key] = str(row[key])
    # An empty repeated field and a NULL one are indistinguishable coming out of
    # BigQuery, so the boolean is what says whether the markup was ever read.
    if row.get("jsonld_read"):
        page["jsonld_types"] = [str(t) for t in (row.get("jsonld_types") or [])]
    if row.get("internal_links_in") is not None:
        page["internal_links_in"] = int(row["internal_links_in"])
    h1_all = row.get("h1_all")
    if h1_all is not None:
        page["headings"] = [{"level": "1", "text": str(t)} for t in h1_all]
    if row.get("word_count") is not None:
        page["word_count"] = int(row["word_count"])
    if row.get("status_code") is not None:
        page["status_code"] = int(row["status_code"])
    return page


def _iso_day(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if len(text) >= 10:
        try:
            return date.fromisoformat(text[:10]).isoformat()
        except ValueError:
            return None
    return None


def _one_site(rows: Optional[Sequence[Dict[str, Any]]], *,
              company_id: Optional[str], host: Optional[str]) -> List[Dict[str, Any]]:
    """Narrow a mixed read to a single site's rows.

    The lookup matches on company_id OR site, because a roster crawl is stored
    under the primary record of a site that several records share. That OR is
    also the one way a property could be handed a neighbour's pages, so the
    rows are collapsed to one site here: the site the company_id was stored
    under, else the site that was asked for, else the site with the most rows.
    """
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows or []:
        groups.setdefault(_text(row.get("site")) or "", []).append(row)
    if len(groups) <= 1:
        return list(rows or [])
    if company_id:
        wanted = str(company_id)
        for name, group in groups.items():
            if any(str(r.get("company_id") or "") == wanted for r in group):
                return group
    if host and host in groups:
        return groups[host]
    return groups[max(groups, key=lambda name: len(groups[name]))]


def stored_pages(*, company_id: Optional[str] = None, site: Optional[str] = None,
                 property_uuid: Optional[str] = None,
                 within_days: Optional[int] = None) -> Dict[str, Any]:
    """The newest stored crawl for one property, shaped for the SEO rules.

    Returns `{"pages", "source", "as_of", "crawl_date", "site", "complete",
    "gaps"}`. `pages` is empty when nothing is stored, when BigQuery is not
    configured, or when the table cannot be read — and only the last of those
    adds a gap, because the first two are already covered by the caller's own
    "no read of this site is stored" gap and saying it twice tells a reader
    less, not more.
    """
    days = int(within_days if within_days is not None else SEO_CRAWL_LOOKBACK_DAYS)
    host = normalize_site(site)
    empty: Dict[str, Any] = {"pages": [], "source": SOURCE, "as_of": None,
                             "crawl_date": None, "site": host, "complete": None,
                             "gaps": []}

    where: List[str] = []
    params: List[Any] = []
    bq = _bq()
    if not bq.is_bigquery_configured():
        logger.debug("seo_crawl: BigQuery not configured; no stored crawl to read")
        return empty
    try:
        from google.cloud import bigquery as bq_types
    except Exception as exc:  # noqa: BLE001 — library absent is not a crash
        logger.info("seo_crawl: BigQuery library unavailable (%s)", exc)
        return empty

    if company_id:
        where.append("company_id = @company_id")
        params.append(bq_types.ScalarQueryParameter("company_id", "STRING", str(company_id)))
    if host:
        where.append("site = @site")
        params.append(bq_types.ScalarQueryParameter("site", "STRING", host))
    if property_uuid and not where:
        where.append("property_uuid = @property_uuid")
        params.append(bq_types.ScalarQueryParameter("property_uuid", "STRING",
                                                    str(property_uuid)))
    if not where:
        return empty
    # Either key identifies the property; requiring both drops every row a
    # roster crawl stored under a site whose company_id moved.
    clause = "(%s)" % " OR ".join(where)
    params.append(bq_types.ScalarQueryParameter("days", "INT64", days))

    table = "`%s.%s.%s`" % (_project(), bq._dataset(), BIGQUERY_SEO_AUDIT_TABLE)
    sql = """
        WITH scoped AS (
            SELECT * FROM {table}
            WHERE {clause}
              AND crawl_date >= DATE_SUB(CURRENT_DATE(), INTERVAL @days DAY)
        ), newest AS (
            SELECT MAX(crawl_date) AS crawl_date FROM scoped
        )
        SELECT s.* FROM scoped AS s
        JOIN newest AS n USING (crawl_date)
        ORDER BY s.url
    """.format(table=table, clause=clause)

    try:
        rows = bq.query(sql, params)
    except Exception as exc:  # noqa: BLE001 — a table we cannot read is a gap
        logger.warning("seo_crawl: stored crawl unreadable for %s (%s)",
                       company_id or host, exc)
        out = dict(empty)
        out["gaps"] = [_gap("pages", BIGQUERY_SEO_AUDIT_TABLE,
                            "The stored site crawl could not be read, so the "
                            "page-level rules are unmeasured rather than clean.")]
        return out

    rows = _one_site(rows, company_id=company_id, host=host)
    pages: List[Dict[str, Any]] = []
    crawl_date = None
    complete = None
    stored_site = host
    for row in rows or []:
        page = _row_to_page(row)
        if page is None:
            continue
        pages.append(page)
        crawl_date = crawl_date or _iso_day(row.get("crawl_date"))
        stored_site = stored_site or _text(row.get("site"))
        if row.get("crawl_complete") is not None:
            complete = bool(row["crawl_complete"]) if complete is None else \
                complete and bool(row["crawl_complete"])

    gaps: List[Dict[str, str]] = []
    if pages and complete is False:
        gaps.append(_gap("internal_links_in", SOURCE,
                         "The stored crawl of this site stopped at its page cap, so "
                         "inbound link counts were not measured for it."))
    return {"pages": pages, "source": SOURCE, "as_of": crawl_date,
            "crawl_date": crawl_date, "site": stored_site, "complete": complete,
            "gaps": gaps}


def pages_for_property(company_id: Optional[str] = None, *, site: Optional[str] = None,
                       property_uuid: Optional[str] = None,
                       within_days: Optional[int] = None) -> Dict[str, Any]:
    """What `reco_seo.gather()` calls. Same contract as `stored_pages`."""
    return stored_pages(company_id=company_id, site=site,
                        property_uuid=property_uuid, within_days=within_days)
