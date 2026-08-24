"""Property Resolver — one place that answers "what are this property's ids?".

SPEC.md calls this "the most important shared service" and it was never built.
Instead the lookup was inlined in about eleven places, each with its own cache
policy or none:

    server.py:145 _resolve_company_id_by_uuid   module-global dict, never expires
    server.py:1862 _resolve_uuid                separate inner function
    loop_autopilot.py:76 _get_company_by_uuid   own CRM search
    redlight_v2_run.py:59 _hs_find_company_by_uuid
    seo_refresh_cron.py:83 _find_property_uuid
    budget_reconcile.py:272 _uuids_for          batch variant
    clickup_recap.py:80 _one_with_uuid
    clickup_notes.py:57 resolve_company_id
    brief_ai_drafter.py:411 resolve_company_by_domain
    portfolio.py _search_companies

Consequences we actually hit: the Atwood/Henry work needed six identifiers for
one property and had to assemble them by hand from three systems, and 54
NinjaCat accounts turned out to be unlinked in HubSpot precisely because no
single function was responsible for knowing.

R1: this module READS `uuid`. It never writes it — `hubspot_client` raises
`R1Violation` at the boundary if anything tries.

Migration posture: strangler, same as `hubspot_client`. New code imports this;
the existing call sites move over one at a time. Do not attempt a big-bang
migration of all eleven — that shape is what caused the "every webhook 401'd"
outage.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass, asdict, field
from typing import Any, Iterable

logger = logging.getLogger(__name__)

# Every join key the portal needs, in one read. Superset of
# hubspot_client.IDENTITY_PROPERTIES — that tuple predates GA4, Google Ads and
# apartments.com landing on the company record.
IDENTITY_PROPERTIES: tuple[str, ...] = (
    "uuid",
    "name",
    "domain",
    "website",
    "market",
    "unit_count",
    "plestatus",
    "occupancy",
    "property_code",
    "ninjacat_system_id",
    "ga4_property_id",
    "google_ads_customer_id",
    "aptiq_property_id",
    "aptiq_market_id",
    "hyly_property_id",
    "apartmentscom_property_id",
)

_CACHE_TTL = float(os.environ.get("PROPERTY_RESOLVER_CACHE_TTL", "300"))
_cache: dict[str, tuple[float, "PropertyIdentity"]] = {}
_lock = threading.Lock()


class PropertyNotFound(LookupError):
    """No property matched the identifier. Callers should fail loudly.

    Deliberately an exception rather than a None return. `hyly_client` swallowed
    its errors and returned [], which presented a broken integration as "no
    data" for weeks — see ADR 0022's post-mortem. Absence and failure must not
    look alike.
    """


class AmbiguousProperty(LookupError):
    """More than one property matched. Never guess which."""


@dataclass(frozen=True)
class PropertyIdentity:
    """Every id for one property. `company_id` and `uuid` are usually equal but
    are NOT interchangeable: the HubSpot workflow only sets `uuid` once a deal is
    associated, so a newly created company has a company_id and no uuid.
    """

    company_id: str
    uuid: str | None = None
    name: str | None = None
    domain: str | None = None
    market: str | None = None
    unit_count: str | None = None
    plestatus: str | None = None
    occupancy: str | None = None          # "Lease Up" | "Stable"
    property_code: str | None = None
    ninjacat_id: str | None = None
    ga4_property_id: str | None = None
    google_ads_customer_id: str | None = None
    aptiq_property_id: str | None = None
    aptiq_market_id: str | None = None
    hyly_property_id: str | None = None
    apartmentscom_property_id: str | None = None
    _raw: dict = field(default_factory=dict, repr=False, compare=False)

    @property
    def is_managed(self) -> bool:
        return (self.plestatus or "") in ("RPM Managed", "Onboarding")

    @property
    def is_lease_up(self) -> bool:
        """Lease-up status. NOTE: read from `occupancy`, not `occupancy_status`.

        Two company properties carry the label "Occupancy Status". `occupancy`
        (2022, radio, Lease Up|Stable) is populated on 873 of 875 records;
        `occupancy_status` (2026, select, Stabilized|Lease-Up|In-Transition|
        Renovation) is populated on 29 and every one of those holds the value
        "Stable", which is not in its own option set. Matching on label picks the
        wrong one.
        """
        return (self.occupancy or "") == "Lease Up"

    def missing(self) -> list[str]:
        """Which join keys this property lacks. Drives coverage reporting."""
        return [
            k for k in ("uuid", "ninjacat_id", "ga4_property_id", "aptiq_property_id")
            if not getattr(self, k)
        ]

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("_raw", None)
        return d


def _norm(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _from_hubspot(record: dict, company_id: str | None = None) -> PropertyIdentity:
    """Build an identity from either shape HubSpot hands back.

    `search_companies` returns records as {"id", "properties"}; `get_company`
    returns the bare properties dict with no id — hence the explicit override.
    """
    p = record.get("properties") if "properties" in record else record
    p = p or {}
    return PropertyIdentity(
        company_id=str(company_id or record.get("id") or p.get("hs_object_id") or ""),
        uuid=_norm(p.get("uuid")),
        name=_norm(p.get("name")),
        domain=_norm(p.get("domain")) or _norm(p.get("website")),
        market=_norm(p.get("market")),
        unit_count=_norm(p.get("unit_count")),
        plestatus=_norm(p.get("plestatus")),
        occupancy=_norm(p.get("occupancy")),
        property_code=_norm(p.get("property_code")),
        ninjacat_id=_norm(p.get("ninjacat_system_id")),
        ga4_property_id=_norm(p.get("ga4_property_id")),
        google_ads_customer_id=_norm(p.get("google_ads_customer_id")),
        aptiq_property_id=_norm(p.get("aptiq_property_id")),
        aptiq_market_id=_norm(p.get("aptiq_market_id")),
        hyly_property_id=_norm(p.get("hyly_property_id")),
        apartmentscom_property_id=_norm(p.get("apartmentscom_property_id")),
        _raw=p,
    )


def _cache_get(key: str) -> PropertyIdentity | None:
    with _lock:
        hit = _cache.get(key)
        if not hit:
            return None
        expires, value = hit
        if time.time() > expires:
            _cache.pop(key, None)
            return None
        return value


def _cache_put(identity: PropertyIdentity) -> None:
    """Index one identity under every key it can be found by."""
    expires = time.time() + _CACHE_TTL
    with _lock:
        for key in filter(None, (
            f"cid:{identity.company_id}",
            f"uuid:{identity.uuid}" if identity.uuid else None,
            f"nc:{identity.ninjacat_id}" if identity.ninjacat_id else None,
            f"code:{identity.property_code}" if identity.property_code else None,
        )):
            _cache[key] = (expires, identity)


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def _search_one(prop: str, value: str) -> PropertyIdentity:
    import hubspot_client  # late import: avoids a cycle via config

    results = hubspot_client.search_companies(
        [{"propertyName": prop, "operator": "EQ", "value": value}],
        list(IDENTITY_PROPERTIES),
    )
    if not results:
        raise PropertyNotFound(f"no company with {prop}={value!r}")
    if len(results) > 1:
        names = ", ".join((r.get("properties") or {}).get("name", "?") for r in results[:5])
        raise AmbiguousProperty(f"{len(results)} companies with {prop}={value!r}: {names}")
    return _from_hubspot(results[0])


def _search_by_name(value: str) -> PropertyIdentity:
    """Resolve a property by name. EQ first, then a token search.

    Names are how people actually refer to properties — "Atwood at Rivulon" is
    what someone types, not 30912193455 — but they are not identifiers: they
    are not unique, not stable, and not always typed the way HubSpot stores
    them ("The Atwood at Rivulon"). So this tries exact first, falls back to a
    token match, and refuses rather than guessing when more than one survives.
    """
    import hubspot_client

    value = value.strip()
    results = hubspot_client.search_companies(
        [{"propertyName": "name", "operator": "EQ", "value": value}],
        list(IDENTITY_PROPERTIES),
    )
    if not results:
        results = hubspot_client.search_companies(
            [{"propertyName": "name", "operator": "CONTAINS_TOKEN", "value": value}],
            list(IDENTITY_PROPERTIES),
        )

    if not results:
        raise PropertyNotFound(f"no company named {value!r}")

    if len(results) > 1:
        # One exact case-insensitive hit among several token matches is not
        # ambiguous — "Atwood" matching both "The Atwood" and "Atwood Park"
        # is, but an exact name is the answer.
        exact = [r for r in results
                 if ((r.get("properties") or {}).get("name") or "").strip().lower()
                 == value.lower()]
        if len(exact) == 1:
            return _from_hubspot(exact[0])
        names = ", ".join((r.get("properties") or {}).get("name", "?")
                          for r in results[:5])
        raise AmbiguousProperty(
            f"{len(results)} companies match name {value!r}: {names}. "
            f"Pass a company_id or uuid instead.")

    return _from_hubspot(results[0])


def resolve(identifier: str | int, *, kind: str | None = None,
            use_cache: bool = True) -> PropertyIdentity:
    """Resolve any known identifier to the full identity.

    `kind` forces interpretation; otherwise it is inferred. Inference order
    matters: a HubSpot company_id and a uuid are both long digit strings and are
    usually equal, so company_id is tried first and uuid second — cheapest exact
    read before a search. Anything that is neither digits nor domain-shaped is
    treated as a property name, which is the only identifier a person types.

    Raises PropertyNotFound / AmbiguousProperty rather than returning None.
    """
    ident = str(identifier).strip()
    if not ident:
        raise PropertyNotFound("empty identifier")

    if use_cache and kind in (None, "company_id", "uuid"):
        for k in (f"cid:{ident}", f"uuid:{ident}"):
            hit = _cache_get(k)
            if hit:
                return hit

    import hubspot_client

    def _finish(identity: PropertyIdentity) -> PropertyIdentity:
        _cache_put(identity)
        return identity

    if kind == "ninjacat" or (kind is None and _cache_get(f"nc:{ident}")):
        cached = _cache_get(f"nc:{ident}")
        if cached:
            return cached
    if kind == "ninjacat":
        return _finish(_search_one("ninjacat_system_id", ident))
    if kind == "property_code":
        return _finish(_search_one("property_code", ident))
    if kind == "aptiq":
        return _finish(_search_one("aptiq_property_id", ident))
    if kind == "ga4":
        return _finish(_search_one("ga4_property_id", ident))
    if kind == "domain":
        return _finish(_search_one("domain", _normalize_domain(ident)))
    if kind == "name":
        return _finish(_search_by_name(ident))

    # Inference path for a bare numeric id.
    if ident.isdigit():
        try:
            record = hubspot_client.get_company(ident, list(IDENTITY_PROPERTIES))
            # get_company returns the bare properties dict; a miss can surface as
            # {} or as a raised HubSpotError depending on HubSpot's mood.
            if record and record.get("hs_object_id") or record.get("name"):
                return _finish(_from_hubspot(record, company_id=ident))
        except Exception as exc:                      # noqa: BLE001
            logger.debug("company_id read failed for %s: %s", ident, exc)
        return _finish(_search_one("uuid", ident))

    if "." in ident or "/" in ident:
        return _finish(_search_one("domain", _normalize_domain(ident)))

    # Anything left is prose — treat it as a name. Before this, a caller who
    # passed "Atwood at Rivulon" got "cannot infer identifier kind", which
    # reads as "no such property" to whoever typed it.
    return _finish(_search_by_name(ident))


def _normalize_domain(value: str) -> str:
    d = re.sub(r"^https?://", "", value.strip(), flags=re.I)
    d = re.sub(r"^www\.", "", d, flags=re.I)
    return d.split("/")[0].strip().lower()


def resolve_many(identifiers: Iterable[str | int], *, kind: str | None = None,
                 skip_errors: bool = True) -> dict[str, PropertyIdentity]:
    """Resolve a batch. Unresolvable ids are logged and skipped by default.

    Returns {original_identifier: identity} so callers can see which inputs fell
    out rather than silently getting a shorter list back.
    """
    out: dict[str, PropertyIdentity] = {}
    for ident in identifiers:
        key = str(ident)
        try:
            out[key] = resolve(key, kind=kind)
        except (PropertyNotFound, AmbiguousProperty) as exc:
            if not skip_errors:
                raise
            logger.warning("resolve failed for %s: %s", key, exc)
    return out
