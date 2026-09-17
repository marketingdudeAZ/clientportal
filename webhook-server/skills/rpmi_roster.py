"""Layer 2 — the RPMI property roster, and how much of it we can actually see.

WHY THIS EXISTS
    "How many RPMI properties are there?" has had a different answer in every
    place it was asked, because the answer depends on two things nobody wrote
    down: which `client` spelling you filtered on, and whether a HubSpot record
    is a property or a second record pointing at a property you already counted.
    This module is the one place that decides both, and it reports what it
    cannot see instead of rounding it to zero.

THE TWO SPELLINGS
    The owner is stored as two distinct values on the company `client` property:
    `RPM Investments` (79 records) and `RPMI` (37). Filtering on either alone
    silently drops the other — a third of the portfolio, or two thirds of it.
    Both are queried and merged here. (Verified by sweeping all 1,928 companies
    for any `client` containing "rpm"; those are the only two. n8n/LIVE-
    WORKFLOWS.md, 11 Sept 2026, re-measured 17 Sept 2026 — still 37 + 79 = 116.)

THE PROPERTY NAMES, MEASURED NOT ASSUMED
    `skills/property_resolver.py` reads `market` and `unit_count`. Neither is
    the populated field on these records. Measured live against the 109 managed
    RPMI companies on 17 Sept 2026 (HubSpot has 848 company properties; these
    are the ones that carry the answer):

        market / region     market            5 / 109   ← what the resolver reads
                            rpmmarket       109 / 109   ← "RPM Market", the real one
                            rpmregion       109 / 109   ← "Digital Region"; 108 read
                                                          "RPMI", 1 reads "Houston
                                                          (HOU)" — an ownership
                                                          label, not a geography
                            aptiq_market_name 80 / 109  ← AptIQ's own market name
                            finmarket         0 / 109
                            markets__c        0 / 109
        units               unit_count        0 / 109   ← not a HubSpot property at all
                            totalunits      107 / 109   ← "Total Units", number
                            number_of_units   5 / 109   ← a string field, near-empty
        place               city            109 / 109
                            state           108 / 109
        site                website         109 / 109
                            domain          109 / 109   (5 records differ from website)

    So: market is `rpmmarket`, region is `rpmregion`, units is `totalunits`.
    The two records with no `totalunits` (The Louis Las Colinas, The Everly at
    Yorktown) return units=None with a gap — never 0, which would read as a
    property with no apartments in it.

    The resolver's `market`/`unit_count` reads are left alone here; fixing them
    is a change to every caller of PropertyIdentity and belongs in its own PR.
    This module does not route around the resolver — it answers a different
    question (the whole roster at once, not one property's ids).

DE-DUPLICATION
    A website is not a property, and a HubSpot record is not a website. Three
    sites carry more than one record (measured 17 Sept 2026 on the 109 managed):
    `volumeapartments.com` (8 records, Volume 1-8), `meshapartments.com` (4:
    Mesh Portfolio, Mesh, Mesh 2, Mesh III) and `livehamptonlakesapts.com` (2:
    "Hampton Lakes" under RPMI and "Lakeside" under RPM Investments are the same
    site). 109 records collapse to 98 sites. Following the n8n workflow, the
    collapsed record is not dropped: every row carries `records_covered` and
    `also_covers`, so nothing disappears quietly.

    What is NOT merged: a record with no website (Porter Westside), and a record
    whose website is the corporate domain. `5508 Parkcrest LLC` reads
    rpmliving.com today; on 11 Sept Mesh 2 and Mesh III read it too. Merging on
    that placeholder would have collapsed three unrelated properties into one
    row and quietly lost two of them, so `SHARED_SITES` keys those records by
    company_id instead and says so in their gaps.

WHAT COUNTS AS "MANAGED"
    `plestatus IN ('RPM Managed', 'Onboarding')` — 109 of the 116. The other
    seven are 4 Dispositioning, 1 Disposition Complete and 2 blank, and they are
    excluded unless `include_unmanaged=True` asks for them.

RULES THIS MODULE KEEPS
    * READ ONLY. Nothing here writes to HubSpot, so R1 (code never writes
      `uuid`) cannot be violated through this path.
    * Every count carries its denominator and the property it was read from.
    * A field HubSpot does not have comes back as None with a `gaps` entry
      naming the field and the property that was empty. Never 0, never "".
    * Layer 3 calls this; this calls `hubspot_client` (CLAUDE.md, Layer 2).
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

# The serve-stale cache entry the workspace sources already use. Private by name
# only — `workspace_cache` owns the policy and this module reuses it rather than
# growing a second, subtly different TTL cache.
from skills.workspace_cache import _Entry

logger = logging.getLogger(__name__)

# The two spellings of the same owner. Order is the order they are queried.
CLIENT_VALUES: Tuple[str, ...] = ("RPMI", "RPM Investments")

# plestatus values that mean "we run marketing for this property today".
MANAGED_STATUSES: Tuple[str, ...] = ("RPM Managed", "Onboarding")

# coverage key -> the HubSpot company property that carries that platform's id.
PLATFORM_ID_PROPERTIES: Dict[str, str] = {
    "hyly": "hyly_property_id",
    "aptiq": "aptiq_property_id",
    "ga4": "ga4_property_id",
    "google_ads": "google_ads_customer_id",
    "ninjacat": "ninjacat_system_id",
}

# Everything read in one pass. The market/units names here are the measured
# ones (see the module docstring), not the ones the resolver assumes.
ROSTER_PROPERTIES: Tuple[str, ...] = (
    "name", "client", "plestatus", "uuid",
    "website", "domain",
    "rpmmarket", "rpmregion", "city", "state",
    "totalunits",
) + tuple(PLATFORM_ID_PROPERTIES.values())

# Sites that are not a property's site. A record whose website is the corporate
# domain is not sharing a site with another property — it has no site of its
# own — so these are never merged. `5508 Parkcrest LLC` reads rpmliving.com
# today and three more did in the 11 Sept n8n sweep (Mesh 2, Mesh III, and it);
# without this, the next time two of them do, two unrelated properties collapse
# into one row and one of them disappears from the roster.
SHARED_SITES = frozenset({"rpmliving.com"})

SOURCE = "hubspot:companies.search client IN ('RPMI', 'RPM Investments')"

_ROSTER_TTL = float(os.environ.get("RPMI_ROSTER_TTL", "900"))  # 15 min


# ── field readers ────────────────────────────────────────────────────────────


def _norm(value: Any) -> Optional[str]:
    """A blank HubSpot property comes back as None, "" or "  ". All are absent."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _host(*candidates: Any) -> Optional[str]:
    """First usable candidate reduced to a bare host — the de-duplication key.

    HubSpot stores the same site three ways across these records
    ("https://www.x.com/", "www.x.com", "x.com"), so a raw string compare finds
    far fewer duplicates than there are.
    """
    for candidate in candidates:
        text = _norm(candidate)
        if not text:
            continue
        text = re.sub(r"^[a-z]+://", "", text.lower(), flags=re.I)
        text = re.sub(r"^www\.", "", text)
        text = text.split("/")[0].split("?")[0].strip().rstrip(".")
        if text and "." in text:
            return text
    return None


def _units(value: Any) -> Optional[int]:
    """`totalunits` is a HubSpot number, which arrives as "668" or "668.0".

    Returns None — never 0 — when it is missing or unparseable. A property with
    0 units is a property with no apartments in it, and that is never the fact;
    the fact is that we do not know.
    """
    text = _norm(value)
    if text is None:
        return None
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


def _gap(field: str, source: str, message: str) -> Dict[str, str]:
    return {"field": field, "source": source, "message": message}


# ── HubSpot read ─────────────────────────────────────────────────────────────


def _fetch_records() -> List[Dict[str, Any]]:
    """Every RPMI company record, both spellings, fully paged.

    Two searches rather than one `IN` filter so the per-spelling counts stay
    visible — the day one spelling drops to zero is the day a HubSpot workflow
    started rewriting `client`, and a merged total hides that.
    """
    import hubspot_client  # late import: avoids a cycle via config

    by_id: Dict[str, Dict[str, Any]] = {}
    for spelling in CLIENT_VALUES:
        rows = hubspot_client.search_companies(
            [{"propertyName": "client", "operator": "EQ", "value": spelling}],
            list(ROSTER_PROPERTIES),
        )
        logger.info("rpmi roster: client=%r returned %d records", spelling, len(rows))
        for row in rows:
            company_id = _norm(row.get("id"))
            if company_id:
                by_id[company_id] = row
    return list(by_id.values())


def _to_row(record: Dict[str, Any]) -> Dict[str, Any]:
    """One HubSpot record -> one roster row, with its gaps attached."""
    props = record.get("properties") or {}
    company_id = str(record.get("id") or props.get("hs_object_id") or "")

    ids = {key: _norm(props.get(prop)) for key, prop in PLATFORM_ID_PROPERTIES.items()}
    units = _units(props.get("totalunits"))

    gaps: List[Dict[str, str]] = []
    for key, prop in PLATFORM_ID_PROPERTIES.items():
        if ids[key] is None:
            gaps.append(_gap(key, f"hubspot:{prop}",
                             f"no {prop} on company {company_id} — that platform "
                             "cannot be read for this property"))
    if units is None:
        gaps.append(_gap("units", "hubspot:totalunits",
                         f"totalunits is empty on company {company_id} — unit "
                         "count unknown, not zero"))
    for field, prop in (("market", "rpmmarket"), ("region", "rpmregion"),
                        ("city", "city"), ("state", "state"), ("uuid", "uuid")):
        if _norm(props.get(prop)) is None:
            gaps.append(_gap(field, f"hubspot:{prop}",
                             f"{prop} is empty on company {company_id}"))

    domain = _host(props.get("website"), props.get("domain"))
    if domain is None:
        gaps.append(_gap("domain", "hubspot:website,domain",
                         f"no usable site on company {company_id} — it cannot be "
                         "crawled or de-duplicated by site"))
    elif domain in SHARED_SITES:
        gaps.append(_gap("domain", "hubspot:website",
                         f"company {company_id} points at {domain}, the corporate "
                         "site, not a property site — it has no site of its own "
                         "to crawl and is never merged with another record"))

    return {
        "company_id": company_id,
        "uuid": _norm(props.get("uuid")),
        "name": _norm(props.get("name")),
        "website": _norm(props.get("website")),
        "domain": domain,
        "market": _norm(props.get("rpmmarket")),
        "region": _norm(props.get("rpmregion")),
        "city": _norm(props.get("city")),
        "state": _norm(props.get("state")),
        "units": units,
        "plestatus": _norm(props.get("plestatus")),
        "client": _norm(props.get("client")),
        "coverage": {key: ids[key] is not None for key in PLATFORM_ID_PROPERTIES},
        "ids": ids,
        "records_covered": 1,
        "also_covers": [],
        "gaps": gaps,
        "source": SOURCE,
    }


def _build() -> List[Dict[str, Any]]:
    """Cache builder: every record, managed or not, one row each, undeduped."""
    rows = [_to_row(record) for record in _fetch_records()]
    rows.sort(key=lambda r: ((r["name"] or "~").lower(), r["company_id"]))
    return rows


# ── cache ────────────────────────────────────────────────────────────────────
# Same serve-stale entry the workspace sources use: fresh -> serve; stale ->
# serve the last good copy and refresh behind the request; cold -> build once
# however many threads arrive together. This is read per dashboard render, so a
# synchronous 2-request HubSpot search on every render is the thing to avoid.
# Deliberately NOT registered in workspace_cache.ENTRIES — `warm()` is the demo
# path and this is not on it yet.

_ROSTER = _Entry("rpmi_roster", _ROSTER_TTL, _build)


def clear_cache() -> None:
    """Drop the cached roster. For tests and for a forced re-read."""
    _ROSTER.clear()


def _read() -> Tuple[List[Dict[str, Any]], Optional[str]]:
    return _ROSTER.get()


# ── public API ───────────────────────────────────────────────────────────────


def _dedup_key(row: Dict[str, Any]) -> str:
    """What makes two records the same property: one site, one property.

    A record with no site, or one pointing at the corporate domain, is keyed by
    its own company_id instead — it stands alone rather than being merged into
    whatever else happens to carry the same placeholder.
    """
    domain = row["domain"]
    if not domain or domain in SHARED_SITES:
        return f"company:{row['company_id']}"
    return domain


def _pick_primary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Which record represents a site that several records share.

    Most platform ids first — that is the record the data actually hangs off —
    then a uuid (addressable at all), then name and company_id so the choice is
    the same on every render rather than whatever HubSpot listed first.
    """
    return sorted(
        rows,
        key=lambda r: (-sum(r["coverage"].values()), r["uuid"] is None,
                       (r["name"] or "~").lower(), r["company_id"]),
    )[0]


def get_roster(include_unmanaged: bool = False) -> List[Dict[str, Any]]:
    """The roster, one row per site, newest cached read.

    `include_unmanaged=True` adds the dispositioning and blank-status records.
    They are excluded by default because they are on their way out of the
    portfolio and counting them overstates what we run today.

    Rows that share a site are collapsed onto the record with the most platform
    coverage; the others survive in that row's `also_covers` with their own ids
    and unit counts, so nothing is lost by the collapse. A record with no usable
    website is never merged into anything — it is its own row, keyed by
    company_id.
    """
    rows, as_of = _read()
    if not include_unmanaged:
        rows = [r for r in rows if (r["plestatus"] or "") in MANAGED_STATUSES]

    by_site: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        by_site.setdefault(_dedup_key(row), []).append(row)

    roster: List[Dict[str, Any]] = []
    for site, group in by_site.items():
        primary = dict(_pick_primary(group))
        others = [r for r in group if r["company_id"] != primary["company_id"]]
        primary["records_covered"] = len(group)
        primary["also_covers"] = [
            {"company_id": r["company_id"], "uuid": r["uuid"], "name": r["name"],
             "units": r["units"], "client": r["client"], "coverage": dict(r["coverage"])}
            for r in sorted(others, key=lambda r: ((r["name"] or "~").lower(), r["company_id"]))
        ]
        primary["gaps"] = list(primary["gaps"])
        if others:
            primary["gaps"].append(_gap(
                "records_covered", "hubspot:website",
                f"{len(group)} HubSpot records share {site} — this row speaks for "
                f"all of them; see also_covers"))
        primary["as_of"] = as_of
        roster.append(primary)

    roster.sort(key=lambda r: ((r["name"] or "~").lower(), r["company_id"]))
    return roster


def coverage_summary(include_unmanaged: bool = False) -> Dict[str, Any]:
    """How much of the roster each platform can actually see.

    Counted over HubSpot RECORDS, not sites — a platform id lives on a record,
    and the Volume 1-8 records each carry their own. Site counts are reported
    alongside so the two are never confused for one another.

    Percentages carry the denominator they were taken over; a bare percentage is
    how "79" and "77" became "most of them".
    """
    rows, as_of = _read()
    if not include_unmanaged:
        rows = [r for r in rows if (r["plestatus"] or "") in MANAGED_STATUSES]
    total = len(rows)

    def _tally(label: str, prop: str, present: int) -> Dict[str, Any]:
        return {
            "field": label,
            "source": f"hubspot:{prop}",
            "present": present,
            "missing": total - present,
            "of": total,
            "pct": round(100.0 * present / total, 1) if total else None,
        }

    coverage = {
        key: _tally(key, prop, sum(1 for r in rows if r["coverage"][key]))
        for key, prop in PLATFORM_ID_PROPERTIES.items()
    }
    coverage["uuid"] = _tally("uuid", "uuid", sum(1 for r in rows if r["uuid"]))
    coverage["market"] = _tally("market", "rpmmarket", sum(1 for r in rows if r["market"]))
    coverage["units"] = _tally("units", "totalunits", sum(1 for r in rows if r["units"] is not None))

    sites: Dict[str, int] = {}
    for row in rows:
        key = _dedup_key(row)
        sites[key] = sites.get(key, 0) + 1

    by_client: Dict[str, int] = {}
    for row in rows:
        by_client[row["client"] or "(blank)"] = by_client.get(row["client"] or "(blank)", 0) + 1

    counted_units = [r["units"] for r in rows if r["units"] is not None]
    gaps: List[Dict[str, str]] = []
    if len(counted_units) < total:
        gaps.append(_gap("units_total", "hubspot:totalunits",
                         f"{total - len(counted_units)} of {total} records have no "
                         "totalunits — the unit total is a floor, not the portfolio"))
    for key, tally in coverage.items():
        if tally["missing"]:
            gaps.append(_gap(key, tally["source"],
                             f"{tally['missing']} of {total} records carry no "
                             f"{tally['source'].split(':', 1)[1]}"))

    return {
        "as_of": as_of,
        "source": SOURCE,
        "include_unmanaged": include_unmanaged,
        "records": total,
        "records_by_client": by_client,
        "managed_statuses": list(MANAGED_STATUSES),
        "sites": len(sites),
        "sites_with_multiple_records": sum(1 for n in sites.values() if n > 1),
        "records_without_a_site": sum(1 for r in rows if not r["domain"]),
        "records_on_a_shared_site": sum(1 for r in rows if r["domain"] in SHARED_SITES),
        "units_total": sum(counted_units) if counted_units else None,
        "units_counted_over": len(counted_units),
        "coverage": coverage,
        "gaps": gaps,
    }
