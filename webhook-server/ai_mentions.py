"""AI-Mentions tracker — does the property get cited by ChatGPT / Perplexity /
Gemini / Google AI Overviews when prospects ask common renter questions?

Per-property prompt fan-out:
  - "best apartments in {city}"
  - "luxury apartments near {neighborhood}"
  - "{property_name} reviews"
  - "apartments in {city} under $X"  (skipped if rent data unavailable)
  - "pet friendly apartments in {city}"

Per engine, for each prompt, we ask the LLM and scan the response text for
the property's domain. Result is persisted weekly to the rpm_ai_mentions HubDB
table so the dashboard reads are cheap.

Composite index = % of prompts cited, averaged across engines, 0–100.
"""

import logging
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

from config import HUBDB_AI_MENTIONS_TABLE_ID
from dataforseo_client import (
    llm_response_chatgpt,
    llm_response_gemini,
    llm_response_perplexity,
    serp_ai_mode,
)
from hubdb_helpers import insert_row, publish, read_rows

logger = logging.getLogger(__name__)

_cache: dict = {}
CACHE_TTL = 1800


def default_prompts(property_name: str, city: str, neighborhood: str | None = None) -> list[str]:
    prompts = [
        f"best apartments in {city}",
        f"luxury apartments in {city}",
        f"{property_name} reviews",
        f"pet friendly apartments in {city}",
    ]
    if neighborhood:
        prompts.append(f"apartments near {neighborhood}")
    return prompts


def _domain_root(url_or_domain: str) -> str:
    """Normalize to 'example.com' — strip scheme, www, path."""
    if not url_or_domain:
        return ""
    if "://" in url_or_domain:
        host = urlparse(url_or_domain).netloc
    else:
        host = url_or_domain.split("/")[0]
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _is_cited(response_text: str, citations: list[dict] | None, domain: str) -> bool:
    if not domain:
        return False
    d = domain.lower()
    if response_text and d in response_text.lower():
        return True
    for c in citations or []:
        href = c.get("url") or c.get("link") or ""
        if d in href.lower():
            return True
    return False


def _run_engine(engine_fn, prompt: str) -> dict:
    """Call one LLM endpoint, return {'text':..., 'citations':[...]}.

    Swallows errors so one engine failing doesn't kill the whole run.
    """
    try:
        res = engine_fn(prompt)
    except Exception as e:
        logger.warning("LLM engine failed for prompt %r: %s", prompt, e)
        return {"text": "", "citations": []}
    items = res.get("items") or []
    if items:
        text = items[0].get("text") or items[0].get("message") or ""
        citations = items[0].get("annotations") or items[0].get("citations") or []
    else:
        text = res.get("text") or ""
        citations = res.get("citations") or []
    return {"text": text, "citations": citations}


def _aio_cited(prompt: str, domain: str) -> dict:
    """Google AI Overview citation check via SERP ai_mode endpoint."""
    try:
        res = serp_ai_mode(prompt)
    except Exception as e:
        logger.warning("AI Overview SERP failed for %r: %s", prompt, e)
        return {"cited": False, "rank": None}
    items = res.get("items") or []
    for i, item in enumerate(items, start=1):
        for src in item.get("references") or item.get("links") or []:
            href = src.get("url") or src.get("link") or ""
            if domain and domain in href.lower():
                return {"cited": True, "rank": i}
    return {"cited": False, "rank": None}


def scan_mentions(property_name: str, domain: str, city: str, neighborhood: str | None = None) -> dict:
    """Run one full fan-out. Returns composite index + per-engine detail.

    Does not persist — caller does.
    """
    domain = _domain_root(domain)
    prompts = default_prompts(property_name, city, neighborhood)

    engines = {
        "chatgpt":    llm_response_chatgpt,
        "perplexity": llm_response_perplexity,
        "gemini":     llm_response_gemini,
    }
    by_engine: dict[str, dict] = {}
    for engine_name, fn in engines.items():
        cited_count = 0
        prompt_results = []
        for p in prompts:
            resp = _run_engine(fn, p)
            cited = _is_cited(resp["text"], resp["citations"], domain)
            if cited:
                cited_count += 1
            prompt_results.append({"prompt": p, "cited": cited})
        by_engine[engine_name] = {
            "cited_rate": round(cited_count / len(prompts), 2) if prompts else 0,
            "cited_count": cited_count,
            "total": len(prompts),
            "prompts": prompt_results,
        }

    aio_results = []
    aio_cited = 0
    for p in prompts:
        r = _aio_cited(p, domain)
        aio_results.append({"prompt": p, **r})
        if r["cited"]:
            aio_cited += 1
    by_engine["google_aio"] = {
        "cited_rate": round(aio_cited / len(prompts), 2) if prompts else 0,
        "cited_count": aio_cited,
        "total": len(prompts),
        "prompts": aio_results,
    }

    composite = int(round(
        100 * sum(e["cited_rate"] for e in by_engine.values()) / len(by_engine)
    )) if by_engine else 0

    return {
        "composite_index": composite,
        "by_engine": by_engine,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
    }


def persist_snapshot(property_uuid: str, scan: dict) -> str | None:
    if not HUBDB_AI_MENTIONS_TABLE_ID:
        return None
    row_id = insert_row(
        HUBDB_AI_MENTIONS_TABLE_ID,
        {
            "property_uuid": property_uuid,
            "scanned_at": scan["scanned_at"],
            "composite_index": scan["composite_index"],
            "chatgpt_rate": scan["by_engine"]["chatgpt"]["cited_rate"],
            "perplexity_rate": scan["by_engine"]["perplexity"]["cited_rate"],
            "gemini_rate": scan["by_engine"]["gemini"]["cited_rate"],
            "aio_rate": scan["by_engine"]["google_aio"]["cited_rate"],
            "detail_json": _dumps(scan["by_engine"]),
        },
    )
    publish(HUBDB_AI_MENTIONS_TABLE_ID)
    return row_id


def get_latest_snapshot(property_uuid: str) -> dict:
    """Read the most recent AI-mentions snapshot for a property from HubDB."""
    now = time.time()
    cached = _cache.get(property_uuid)
    if cached and (now - cached[0]) < CACHE_TTL:
        return cached[1]

    rows = read_rows(HUBDB_AI_MENTIONS_TABLE_ID, filters={"property_uuid": property_uuid}, limit=30)
    if not rows:
        payload = {"composite_index": None, "by_engine": {}, "history": []}
        _cache[property_uuid] = (now, payload)
        return payload

    rows.sort(key=lambda r: r.get("scanned_at") or "", reverse=True)
    latest = rows[0]
    payload = {
        "composite_index": latest.get("composite_index"),
        "scanned_at": latest.get("scanned_at"),
        "by_engine": {
            "chatgpt":    {"cited_rate": latest.get("chatgpt_rate")},
            "perplexity": {"cited_rate": latest.get("perplexity_rate")},
            "gemini":     {"cited_rate": latest.get("gemini_rate")},
            "google_aio": {"cited_rate": latest.get("aio_rate")},
        },
        "history": [
            {
                "date": r.get("scanned_at"),
                "composite": r.get("composite_index"),
                "chatgpt": r.get("chatgpt_rate"),
                "perplexity": r.get("perplexity_rate"),
                "gemini": r.get("gemini_rate"),
                "aio": r.get("aio_rate"),
            }
            for r in rows
        ],
    }
    _cache[property_uuid] = (now, payload)
    return payload


def _dumps(obj) -> str:
    import json
    try:
        return json.dumps(obj, default=str)
    except Exception:
        return "{}"
