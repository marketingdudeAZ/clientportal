"""Central HubSpot client — the single request pipeline for HubSpot access.

Today 39 files (109 call sites) each hand-roll their own `_headers()`, token
read, timeout, and (inconsistent) error handling. That sprawl is what let the
"every webhook 401'd" outage happen, and it leaves the R1 immutable-uuid rule
enforced nowhere but in people's heads. This module collapses all of that into
one place.

STRANGLER NOTE: only NEW Loop 1 code imports this. The 38 legacy call sites keep
their own boilerplate until their loops roll out (migrating all 39 at once is the
big-bang that caused the outage). The shapes here are drop-in so that later
migration is mechanical.

Request pipeline (every call flows through `_request`):

    caller (recommendation_gen / self_checkout / open-deal check)
          │
          ▼
    ┌──────────────────────────────────────────────────────────┐
    │ 1. R1 GUARD    write payload touches `uuid`? → R1Violation  │  enforced ONCE
    │ 2. CACHE       read-through hit (GET only)? → return cached  │  perf
    │ 3. SESSION     one shared requests.Session + timeout         │  one transport
    │ 4. SEND ───────────────────────────────────► HubSpot          │
    │ 5. 401 → reload token, retry once, else HubSpotAuthError      │  the outage fix
    │ 6. 429 → respect Retry-After, backoff, retry up to N          │  rate-limit cascade
    │ 7. on write → invalidate cache for that company_id            │  write-through
    └──────────────────────────────────────────────────────────┘

R1 (IMMUTABLE_RULES.md): code MUST NEVER write the company `uuid` property — not
on create, update, batch, or patch. A HubSpot workflow owns it. The guard here
raises rather than trusting every caller to remember.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

API_BASE = "https://api.hubapi.com"
_COMPANIES = f"{API_BASE}/crm/v3/objects/companies"
_DEALS = f"{API_BASE}/crm/v3/objects/deals"

# R1: company properties code may never write. The guard rejects any write whose
# property map contains one of these keys. `uuid` is the stable join key owned by
# a HubSpot workflow (see IMMUTABLE_RULES.md R1).
IMMUTABLE_COMPANY_PROPERTIES = frozenset({"uuid"})

# Identity rollup: the join keys that live on the company record. The portal
# reads these once and joins everything else (BigQuery analytics, Fluency,
# ClickUp) off them — one list, single source. Property names per CLAUDE.md
# "Identity (R1)" + the Data Stack table. Add new foreign IDs here as their
# connectors come online.
IDENTITY_PROPERTIES = (
    "uuid",
    "aptiq_property_id",
    "aptiq_market_id",
    "hyly_property_id",
    "name",
)

# Dealstages considered "closed" — used to find OPEN deals for the duplicate
# budget guard. Closed Won / Closed Lost are terminal; everything else is open.
_CLOSED_DEALSTAGES = frozenset({"closedwon", "closedlost"})

_TIMEOUT = 10
_MAX_RETRIES = 3            # for 429 backoff
_CACHE_TTL = float(os.environ.get("HUBSPOT_CACHE_TTL", "60"))  # seconds


# ── errors ──────────────────────────────────────────────────────────────────


class HubSpotError(RuntimeError):
    """Any non-recoverable HubSpot API error (status surfaced, not swallowed)."""


class HubSpotAuthError(HubSpotError):
    """A 401 that survived a token reload + retry. Fail loud, not silent."""


class R1Violation(Exception):
    """Code attempted to write an immutable company property (e.g. `uuid`)."""


# ── auth / transport ─────────────────────────────────────────────────────────

_SESSION: requests.Session | None = None


def _session() -> requests.Session:
    """One pooled session for the process (connection reuse vs 39 ad-hoc calls)."""
    global _SESSION
    if _SESSION is None:
        _SESSION = requests.Session()
    return _SESSION


def _api_key() -> str:
    # Re-read on every call so a rotated secret is picked up without a restart;
    # this is also the seam where a real OAuth refresh would slot in.
    return os.environ.get("HUBSPOT_API_KEY", "")


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
    }


def _request(method: str, url: str, **kwargs: Any) -> requests.Response:
    """The single transport: 401-reload-retry-once + 429-backoff, then raise.

    Returns the Response on 2xx. Raises HubSpotAuthError on a persistent 401 and
    HubSpotError on any other non-2xx, so callers never have to re-implement
    error handling (the thing the 39 scattered sites each got slightly wrong).
    """
    kwargs.setdefault("timeout", _TIMEOUT)
    attempts_429 = 0
    did_auth_retry = False

    while True:
        resp = _session().request(method, url, headers=_headers(), **kwargs)

        if resp.status_code == 401 and not did_auth_retry:
            # The outage was 401s cascading with no retry. Reload the token
            # (picks up a rotation) and try exactly once more before failing.
            did_auth_retry = True
            logger.warning("hubspot 401 on %s %s — reloading token, retry once", method, url)
            continue
        if resp.status_code == 401:
            raise HubSpotAuthError(f"401 after token reload: {method} {url}")

        if resp.status_code == 429 and attempts_429 < _MAX_RETRIES:
            attempts_429 += 1
            delay = _retry_after_seconds(resp, attempts_429)
            logger.warning("hubspot 429 on %s %s — backoff %.1fs (attempt %d)",
                           method, url, delay, attempts_429)
            time.sleep(delay)
            continue

        if resp.status_code >= 400:
            raise HubSpotError(f"{resp.status_code} {method} {url}: {resp.text[:300]}")

        return resp


def _retry_after_seconds(resp: requests.Response, attempt: int) -> float:
    """Respect HubSpot's Retry-After header; fall back to exponential backoff."""
    hdr = resp.headers.get("Retry-After")
    if hdr:
        try:
            return min(float(hdr), 30.0)
        except ValueError:
            pass
    return min(2.0 ** attempt, 30.0)  # 2s, 4s, 8s ...


# ── R1 guard ─────────────────────────────────────────────────────────────────


def _reject_immutable(properties: dict | None) -> None:
    """Raise R1Violation if a write payload touches an immutable property."""
    if not properties:
        return
    bad = IMMUTABLE_COMPANY_PROPERTIES & set(properties.keys())
    if bad:
        raise R1Violation(
            f"R1: code may never write company {sorted(bad)} — a HubSpot "
            f"workflow owns it (see IMMUTABLE_RULES.md)."
        )


# ── read-through cache (GET company / identity only) ─────────────────────────
#
# Identity (uuid + foreign IDs) is stable, so it caches well and keeps the spine
# off the rate-limited API. Writes invalidate the whole company entry so a stale
# identity can never feed an exact-join path.

_CACHE: dict[tuple, tuple[float, dict]] = {}


def _cache_key(company_id: str, properties: tuple[str, ...]) -> tuple:
    return (company_id, tuple(sorted(properties)))


def _cache_get(key: tuple) -> dict | None:
    hit = _CACHE.get(key)
    if not hit:
        return None
    stamped, value = hit
    if (time.monotonic() - stamped) > _CACHE_TTL:
        _CACHE.pop(key, None)
        return None
    return value


def _cache_put(key: tuple, value: dict) -> None:
    _CACHE[key] = (time.monotonic(), value)


def invalidate(company_id: str) -> None:
    """Drop every cached read for a company (called after any write to it)."""
    for key in [k for k in _CACHE if k[0] == company_id]:
        _CACHE.pop(key, None)


def clear_cache() -> None:
    _CACHE.clear()


# ── public surface (what the Loop 1 slice calls) ─────────────────────────────


def get_company(company_id: str, properties: list[str] | None = None) -> dict:
    """Read a company's properties in one round-trip. Cached (read-through)."""
    props = tuple(properties or IDENTITY_PROPERTIES)
    key = _cache_key(company_id, props)
    cached = _cache_get(key)
    if cached is not None:
        return cached
    resp = _request(
        "GET", f"{_COMPANIES}/{company_id}",
        params={"properties": ",".join(props)},
    )
    data = resp.json().get("properties") or {}
    _cache_put(key, data)
    return data


def get_company_identity(company_id: str) -> dict:
    """The identity rollup: {uuid, aptiq_property_id, aptiq_market_id, ...}.

    One call returns every join key the portal needs so downstream reads are
    exact joins, never fuzzy. Cached.
    """
    return get_company(company_id, list(IDENTITY_PROPERTIES))


def patch_company(company_id: str, properties: dict) -> dict:
    """Update a company. R1-guarded; invalidates the company's cache on success."""
    _reject_immutable(properties)
    resp = _request(
        "PATCH", f"{_COMPANIES}/{company_id}",
        json={"properties": properties},
    )
    invalidate(company_id)
    return resp.json()


def create_company(properties: dict) -> dict:
    """Create a company. R1-guarded — the uuid is set later by the workflow."""
    _reject_immutable(properties)
    resp = _request("POST", _COMPANIES, json={"properties": properties})
    return resp.json()


def batch_patch_companies(items: list[dict]) -> dict:
    """Batch update. R1-guarded per item; invalidates each touched company."""
    for item in items:
        _reject_immutable(item.get("properties"))
    resp = _request(
        "POST", f"{_COMPANIES}/batch/update",
        json={"inputs": items},
    )
    for item in items:
        if item.get("id"):
            invalidate(item["id"])
    return resp.json()


def search_companies(filters: list[dict], properties: list[str] | None = None) -> list[dict]:
    """CRM search. Returns the `results` list (empty on no match)."""
    payload: dict[str, Any] = {"filterGroups": [{"filters": filters}]}
    if properties:
        payload["properties"] = properties
    resp = _request("POST", f"{_COMPANIES}/search", json=payload)
    return resp.json().get("results", [])


def get_open_deals_for_company(
    company_id: str, channel: str | None = None
) -> list[dict]:
    """Open (non-closed) deals associated with a company — the dup-budget guard.

    Reads the company→deals associations, batch-reads those deals' stages, and
    returns the ones not in a closed stage. `channel` is reserved for the
    caller to filter line items; kept in the signature so the suppression check
    and the authoritative write-time check share one entry point.
    """
    assoc = _request(
        "GET", f"{_COMPANIES}/{company_id}/associations/deals"
    ).json().get("results", [])
    deal_ids = [a.get("toObjectId") or a.get("id") for a in assoc]
    deal_ids = [d for d in deal_ids if d]
    if not deal_ids:
        return []
    read = _request(
        "POST", f"{_DEALS}/batch/read",
        json={"properties": ["dealstage", "dealname"],
              "inputs": [{"id": str(d)} for d in deal_ids]},
    ).json().get("results", [])
    return [
        d for d in read
        if (d.get("properties") or {}).get("dealstage") not in _CLOSED_DEALSTAGES
    ]


# ── deals ────────────────────────────────────────────────────────────────────
# No R1 guard: R1 protects the COMPANY `uuid` property only. Deal properties
# (including launch_date__c) are free to write.


def get_deal(deal_id: str, properties: list[str] | None = None) -> dict:
    """Read a deal's properties."""
    params = {"properties": ",".join(properties)} if properties else None
    return _request("GET", f"{_DEALS}/{deal_id}", params=params).json().get("properties") or {}


def patch_deal(deal_id: str, properties: dict) -> dict:
    """Update a deal (e.g. set launch_date__c, advance dealstage)."""
    return _request("PATCH", f"{_DEALS}/{deal_id}", json={"properties": properties}).json()


def search_deals(filters: list[dict], properties: list[str] | None = None,
                 limit: int = 100) -> list[dict]:
    """CRM deal search — used by the re-arm sweep to find stranded deals."""
    payload: dict[str, Any] = {"filterGroups": [{"filters": filters}], "limit": limit}
    if properties:
        payload["properties"] = properties
    return _request("POST", f"{_DEALS}/search", json=payload).json().get("results", [])
