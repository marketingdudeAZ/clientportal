"""DataForSEO API client.

HTTP Basic auth with persistent session. One function per endpoint we consume —
callers pass plain Python args, get back parsed result dicts (or lists).

Endpoints map to the DataForSEO v3 REST API:
  https://docs.dataforseo.com/v3/
"""

import logging
from typing import Any

import requests

from config import (
    DATAFORSEO_BASE_URL,
    DATAFORSEO_DEFAULT_LANGUAGE,
    DATAFORSEO_DEFAULT_LOCATION,
    DATAFORSEO_LOGIN,
    DATAFORSEO_PASSWORD,
)

logger = logging.getLogger(__name__)

_TIMEOUT = 30

_SESSION = requests.Session()
if DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD:
    _SESSION.auth = (DATAFORSEO_LOGIN, DATAFORSEO_PASSWORD)
_SESSION.headers.update({"Content-Type": "application/json"})


class DataForSEOError(Exception):
    """Raised when a DataForSEO call returns a non-success status."""


def _post(path: str, payload: list[dict] | dict) -> dict:
    """POST to DataForSEO. `payload` is wrapped in a list if not already one."""
    if isinstance(payload, dict):
        payload = [payload]
    url = f"{DATAFORSEO_BASE_URL}{path}"
    try:
        r = _SESSION.post(url, json=payload, timeout=_TIMEOUT)
        r.raise_for_status()
    except requests.RequestException as e:
        logger.error("DataForSEO POST %s failed: %s", path, e)
        raise DataForSEOError(str(e)) from e
    body = r.json()
    if body.get("status_code", 0) >= 40000:
        raise DataForSEOError(f"{body.get('status_code')}: {body.get('status_message')}")
    return body


def _get(path: str) -> dict:
    url = f"{DATAFORSEO_BASE_URL}{path}"
    try:
        r = _SESSION.get(url, timeout=_TIMEOUT)
        r.raise_for_status()
    except requests.RequestException as e:
        logger.error("DataForSEO GET %s failed: %s", path, e)
        raise DataForSEOError(str(e)) from e
    return r.json()


def _first_result(body: dict) -> dict:
    """Unwrap `{tasks: [{result: [<first>]}]}` nesting. Returns {} if empty."""
    tasks = body.get("tasks") or []
    if not tasks:
        return {}
    results = tasks[0].get("result") or []
    if not results:
        return {}
    return results[0]


def _all_results(body: dict) -> list[dict]:
    tasks = body.get("tasks") or []
    if not tasks:
        return []
    return tasks[0].get("result") or []


# ─── SERP ───────────────────────────────────────────────────────────────────

def serp_organic_advanced(
    keyword: str,
    location_code: int | None = None,
    language_code: str | None = None,
    depth: int = 100,
) -> dict:
    """Live Google organic SERP with AI Overview + PAA + related searches."""
    payload = {
        "keyword": keyword,
        "location_code": location_code or DATAFORSEO_DEFAULT_LOCATION,
        "language_code": language_code or DATAFORSEO_DEFAULT_LANGUAGE,
        "depth": depth,
        "calculate_rectangles": False,
    }
    return _first_result(_post("/v3/serp/google/organic/live/advanced", payload))


def serp_ai_mode(keyword: str, location_code: int | None = None) -> dict:
    """Google AI Mode (AI Overview) SERP snapshot."""
    payload = {
        "keyword": keyword,
        "location_code": location_code or DATAFORSEO_DEFAULT_LOCATION,
        "language_code": DATAFORSEO_DEFAULT_LANGUAGE,
    }
    return _first_result(_post("/v3/serp/google/ai_mode/live/advanced", payload))


# ─── On-Page ────────────────────────────────────────────────────────────────

def onpage_task_post(target: str, max_crawl_pages: int = 100, enable_javascript: bool = True) -> str:
    """Kick off an on-page crawl. Returns task_id to poll later."""
    payload = {
        "target": target,
        "max_crawl_pages": max_crawl_pages,
        "load_resources": False,
        "enable_javascript": enable_javascript,
        "respect_sitemap": True,
    }
    body = _post("/v3/on_page/task_post", payload)
    tasks = body.get("tasks") or []
    if not tasks:
        raise DataForSEOError("on_page task_post returned no tasks")
    return tasks[0].get("id")


def onpage_summary(task_id: str) -> dict:
    return _first_result(_get(f"/v3/on_page/summary/{task_id}"))


def onpage_content_parsing(url: str) -> dict:
    """Extract entities, headings, word count for a single URL — synchronous."""
    payload = {"url": url}
    return _first_result(_post("/v3/on_page/content_parsing/live", payload))


# ─── Backlinks ──────────────────────────────────────────────────────────────

def backlinks_summary(target: str) -> dict:
    payload = {"target": target, "internal_list_limit": 10, "backlinks_status_type": "live"}
    return _first_result(_post("/v3/backlinks/summary/live", payload))


def backlinks_referring_domains(target: str, limit: int = 100) -> list[dict]:
    payload = {"target": target, "limit": limit, "order_by": ["backlinks_count,desc"]}
    result = _first_result(_post("/v3/backlinks/referring_domains/live", payload))
    return result.get("items", [])


# ─── Labs (Keyword research + ranks) ────────────────────────────────────────

def ranked_keywords(target: str, location_code: int | None = None, limit: int = 500) -> list[dict]:
    payload = {
        "target": target,
        "location_code": location_code or DATAFORSEO_DEFAULT_LOCATION,
        "language_code": DATAFORSEO_DEFAULT_LANGUAGE,
        "limit": limit,
        "order_by": ["ranked_serp_element.serp_item.rank_absolute,asc"],
    }
    result = _first_result(_post("/v3/dataforseo_labs/google/ranked_keywords/live", payload))
    return result.get("items", [])


def keyword_ideas(seed_keywords: list[str], location_code: int | None = None, limit: int = 200) -> list[dict]:
    payload = {
        "keywords": seed_keywords,
        "location_code": location_code or DATAFORSEO_DEFAULT_LOCATION,
        "language_code": DATAFORSEO_DEFAULT_LANGUAGE,
        "limit": limit,
        "include_serp_info": True,
    }
    result = _first_result(_post("/v3/dataforseo_labs/google/keyword_ideas/live", payload))
    return result.get("items", [])


def bulk_keyword_difficulty(keywords: list[str], location_code: int | None = None) -> list[dict]:
    payload = {
        "keywords": keywords,
        "location_code": location_code or DATAFORSEO_DEFAULT_LOCATION,
        "language_code": DATAFORSEO_DEFAULT_LANGUAGE,
    }
    result = _first_result(_post("/v3/dataforseo_labs/google/bulk_keyword_difficulty/live", payload))
    return result.get("items", [])


def keyword_suggestions(seed: str, location_code: int | None = None, limit: int = 200) -> list[dict]:
    """Long-tail variations of a single seed keyword (modifiers prefixed/suffixed).

    Distinct from keyword_ideas which returns semantically-related terms — this
    returns n-gram variations (e.g. seed='apartments winter garden' yields
    'apartments winter garden fl', 'luxury apartments winter garden', etc.).
    """
    payload = {
        "keyword": seed,
        "location_code": location_code or DATAFORSEO_DEFAULT_LOCATION,
        "language_code": DATAFORSEO_DEFAULT_LANGUAGE,
        "limit": limit,
        "include_serp_info": True,
    }
    result = _first_result(_post("/v3/dataforseo_labs/google/keyword_suggestions/live", payload))
    return result.get("items", [])


def domain_intersection(target1: str, target2: str, exclude_top_domain: bool = True, limit: int = 200) -> list[dict]:
    """Keywords target2 ranks for but target1 doesn't (competitor gap)."""
    payload = {
        "target1": target1,
        "target2": target2,
        "location_code": DATAFORSEO_DEFAULT_LOCATION,
        "language_code": DATAFORSEO_DEFAULT_LANGUAGE,
        "exclude_top_domain": exclude_top_domain,
        "intersections": True,
        "limit": limit,
    }
    result = _first_result(_post("/v3/dataforseo_labs/google/domain_intersection/live", payload))
    return result.get("items", [])


# ─── AI Optimization (LLM mention tracking) ─────────────────────────────────

def llm_response_chatgpt(prompt: str, model: str = "gpt-4o") -> dict:
    payload = {"user_prompt": prompt, "model_name": model}
    return _first_result(_post("/v3/ai_optimization/chat_gpt/llm_responses/live", payload))


def llm_response_perplexity(prompt: str, model: str = "sonar-pro") -> dict:
    payload = {"user_prompt": prompt, "model_name": model}
    return _first_result(_post("/v3/ai_optimization/perplexity/llm_responses/live", payload))


def llm_response_gemini(prompt: str, model: str = "gemini-2.0-flash") -> dict:
    payload = {"user_prompt": prompt, "model_name": model}
    return _first_result(_post("/v3/ai_optimization/gemini/llm_responses/live", payload))


# ─── Trends (Phase 3) ───────────────────────────────────────────────────────

def trends_explore(keywords: list[str], timeframe: str = "past_12_months") -> dict:
    payload = {"keywords": keywords, "time_range": timeframe, "category_code": 0}
    return _first_result(_post("/v3/keywords_data/google_trends/explore/live", payload))


def is_configured() -> bool:
    return bool(DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD)
