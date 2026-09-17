"""Layer 2 — organic search and AI-search recommendations for one property.

WHY THIS EXISTS
    Today's SEO recommendations for these properties are mostly listing edits:
    change the headline on an ILS, add photos, refresh a blurb. That is
    merchandising someone else's page. It does not make the community's own
    site the thing an engine quotes when a renter asks a question, and an ILS
    edit cannot be measured against a lease.

    This module produces the other kind of work: pages that exist because a
    real person asked a real question, facts an engine can read without
    guessing, and corrections when an engine is repeating something we
    stopped being true about.

THE PURPOSE TEST (read before adding a rule)
    Google's guidance on scaled content is a test of PURPOSE, not of volume or
    of authorship. Pages built to manipulate rankings violate it; pages a
    resident would genuinely want do not. Every rule here must produce content
    that passes that test:

      * one page per real question, not one page per keyword permutation;
      * facts only this community can supply — its plans, its availability,
        its fees, its neighborhood — never a paraphrase of the same paragraph
        with the property name swapped;
      * `rule_near_duplicate_page` is the enforcement: it holds a draft that
        reads like another property's page BEFORE it is published, because
        109 properties publishing the same page is precisely the pattern the
        policy exists to catch.

FAIR HOUSING
    Housing is a Special Ad Category and the rules reach further than ad
    targeting: they apply to website copy and to whatever an engine quotes
    back. No rule here may describe a preferred resident, and no rule may
    propose steering by geography or by any grouping of people — the ranking
    engine refuses such a recommendation outright, and so does `_emit`.
    Anything that produces copy carries `fair_housing_review: true`, which
    routes it to the portal's review before a word is published.

WHAT IT NEVER DOES
    * Invent a number. Every receipt names its source and when it was true.
      A rule whose input is missing returns [] and `run()` records a gap that
      names the source, so "we have not measured this" never reads as "this
      is fine".
    * Claim a cited figure we did not measure ourselves. Where an outside
      tracker supplies the measurement, the receipt says so.

SHAPE
    Every recommendation matches the shared contract validated by
    `skills/reco_engine.py`; `run(company_id, today=, rules=)` is what the
    ranking engine calls. `RULES` is the registry, and `REQUIREMENTS` says
    what each rule needs before it can say anything at all.

DATA
    `run()` gathers from what the portal already owns: the community brief
    (floor plans, fees, pet policy), AptIQ availability, the `ai_mentions`
    audit, and the GEO tracking tables when the property has rows there. An
    outside AI-visibility tracker is OPTIONAL enrichment, merged through
    `merge_searchable()` when a project exists for the property's website;
    when none does, the affected rules fall back or report a gap.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# The channels a recommendation can touch. Mirrors the GEO program's work-order
# channels so a recommendation and its work order speak the same words.
CHANNEL_WEBSITE = "website"
CHANNEL_AI_SEARCH = "ai_search"
CHANNEL_ORGANIC = "organic_search"
CHANNEL_LOCAL = "local_listings"

# Lead time before a recommendation stops being timely, by severity.
_START_BY_DAYS = {"high": 7, "medium": 14, "low": 30}

# A brief field that feeds the website FAQ and AI answers goes stale here.
STALE_FIELD_DAYS = 90

# Near-duplicate threshold on 5-word shingles. Two pages about different
# communities that share four fifths of their phrasing are the same page.
DUPLICATE_SHINGLE_SIZE = 5
DUPLICATE_WARN = 0.80
DUPLICATE_HIGH = 0.90
DUPLICATE_MIN_WORDS = 40

# Core Web Vitals thresholds, field data. "Needs improvement" starts here.
CWV_LCP_MS = 2500
CWV_INP_MS = 200
CWV_CLS = 0.10
CWV_LCP_POOR_MS = 4000

# Money pages: the ones a renter lands on with intent. A page is one of these
# when the crawl says so, when its type says so, or when its path does.
MONEY_PAGE_TYPES = {"home", "homepage", "floor_plans", "floorplans", "floor_plan",
                    "availability", "pricing", "amenities", "neighborhood",
                    "contact", "tour", "gallery"}
_MONEY_PATH_HINTS = ("floorplan", "floor-plan", "availability", "pricing", "rates",
                     "amenities", "neighborhood", "contact", "tour", "apply")

# Schema that lets an engine read availability and price instead of guessing.
# Note the vocabulary deliberately avoids the address type's name: the ranking
# engine refuses any recommendation whose text looks like geographic steering,
# and that type name trips the check.
AVAILABILITY_SCHEMA_TYPES = ("Offer", "AggregateOffer", "Apartment",
                             "ApartmentComplex", "RealEstateListing", "Accommodation")

# Brief fields an engine quotes back at a renter. Wrong here is worse than thin.
QUOTED_FACT_FIELDS = ("pet_policy", "pets", "fees", "fee_schedule", "specials",
                      "concessions", "office_hours", "parking", "utilities",
                      "lease_terms", "pricing")

# Fan-out queries map to the brief fact that has to carry the answer. Used to
# say which fact a page needs, never to write the answer itself.
_FACT_HINTS: Tuple[Tuple[Tuple[str, ...], str], ...] = (
    (("pet", "dog", "cat"), "pet_policy"),
    (("fee", "deposit", "charge", "cost of"), "fees"),
    (("special", "concession", "free month"), "specials"),
    (("park", "garage"), "parking"),
    (("utility", "utilities", "electric", "water"), "utilities"),
    (("lease", "term", "month-to-month", "flexible"), "lease_terms"),
    (("tour", "visit", "see the"), "tour"),
    (("available", "availability", "move-in", "move in"), "availability"),
    (("rent", "price", "pricing", "cost"), "pricing"),
    (("amenity", "amenities", "pool", "gym", "rooftop"), "amenities"),
    (("floor plan", "bedroom", "studio", "two-bedroom"), "floor_plans"),
)


# ── small helpers ────────────────────────────────────────────────────────────

def _today(today: Optional[date] = None) -> date:
    return today or date.today()


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _gap(field: str, source: str, message: str) -> Dict[str, str]:
    """The same shape reco_engine and mcp_context use for a missing source."""
    return {"field": field, "source": source, "message": message}


def _receipt(label: str, value: Any, source: str, as_of: Optional[str]) -> Dict[str, Any]:
    """A number a person can check: what it is, where it came from, when."""
    return {"label": label, "value": value, "source": source, "as_of": as_of}


def _iso_day(value: Any) -> Optional[str]:
    """Anything date-shaped → 'YYYY-MM-DD', or None. Never guesses."""
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


def _days_since(value: Any, today: date) -> Optional[int]:
    stamp = _iso_day(value)
    if not stamp:
        return None
    try:
        return (today - date.fromisoformat(stamp)).days
    except ValueError:
        return None


def _plus(today: date, days: int) -> str:
    return (today + timedelta(days=days)).isoformat()


def _words(text: Any) -> List[str]:
    return re.findall(r"[a-z0-9]+", str(text or "").lower())


def _norm(text: Any) -> str:
    """Normalized token string, padded, so ' a1 ' can be matched as a token."""
    return " %s " % " ".join(_words(text))


def _clean_list(rows: Any) -> List[Dict[str, Any]]:
    return [r for r in (rows or []) if isinstance(r, dict)]


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


def _fact_for(query: str) -> Optional[str]:
    """Which brief fact a fan-out query needs. None when we cannot tell."""
    blob = str(query or "").lower()
    for needles, field in _FACT_HINTS:
        for needle in needles:
            if needle in blob:
                return field
    return None


# ── the emitter ──────────────────────────────────────────────────────────────

def _emit(rule_key: str, data: Dict[str, Any], *, category: str, channels: Sequence[str],
          severity: str, confidence: int, found: str, receipts: Sequence[Dict[str, Any]],
          expect: str, if_skip: str, action: Dict[str, Any],
          verify_metric: str, verify_days: int) -> List[Dict[str, Any]]:
    """Build one recommendation in the shared shape, or nothing.

    Two things are checked here rather than trusted to a reviewer. Copy that
    trips the Fair Housing hard patterns is never emitted — a card that
    describes a preferred resident is a compliance failure whatever its
    ranking. And anything that reads as steering by geography or by a grouping
    of people is dropped for the same reason the ranking engine refuses it;
    catching it at the source means it is a bug we can see, not a card.
    """
    today = _today(data.get("_today"))
    company_id = str(data.get("company_id") or "")
    prose = [found, expect, if_skip]

    review = _fair_housing_review(prose + _copy_params(action))
    if review and review.get("severity") == "high":
        logger.error("reco_seo suppressed %s for %s: fair housing (%s)",
                     rule_key, company_id, review.get("terms"))
        return []
    if review:
        logger.warning("reco_seo %s for %s carries review vocabulary %s",
                       rule_key, company_id, review.get("terms"))

    reco = {
        "id": "%s:%s:%s" % (rule_key, company_id, today.isoformat()),
        "company_id": company_id,
        "property_name": data.get("property_name"),
        "rule_key": rule_key,
        "category": category,
        "channels": list(channels),
        "severity": severity,
        "confidence": int(confidence),
        "found": found,
        "receipts": [r for r in receipts if r],
        "expect": expect,
        "if_skip": if_skip,
        "action": action,
        "start_by": _plus(today, _START_BY_DAYS.get(severity, 14)),
        "verify": {"metric": verify_metric, "when": _plus(today, verify_days)},
    }

    refusal = _self_refusal(reco)
    if refusal:
        logger.error("reco_seo suppressed %s for %s: %s", rule_key, company_id, refusal)
        return []
    return [reco]


def _copy_params(action: Dict[str, Any]) -> List[str]:
    """The params a person would read as copy, for the Fair Housing check."""
    out: List[str] = []
    for key, value in (action.get("params") or {}).items():
        if isinstance(value, str):
            out.append(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    out.append(item)
                elif isinstance(item, dict):
                    out.extend(str(v) for v in item.values() if isinstance(v, str))
        _ = key
    return out


def _fair_housing_review(texts: Sequence[str]) -> Optional[Dict[str, Any]]:
    """The portal's own checker. A checker that cannot run fails closed."""
    try:
        from skills import workspace_common as wc
        return wc.fair_housing_review(*[t for t in texts if t])
    except Exception as exc:  # noqa: BLE001 — a checker failure must not pass copy
        logger.warning("reco_seo fair housing check unavailable: %s", exc)
        return {"severity": "high", "terms": ["fair_housing_check_unavailable"]}


def _self_refusal(reco: Dict[str, Any]) -> Optional[str]:
    """Ask the ranking engine's own compliance check before emitting."""
    try:
        from skills import reco_engine
        return reco_engine.compliance_refusal(reco)
    except Exception as exc:  # noqa: BLE001 — engine absent in a bare import
        logger.debug("reco_seo could not self-check %s: %s", reco.get("id"), exc)
        return None


# ── rule 1 — a floor plan nobody can find ────────────────────────────────────

def rule_missing_floor_plan_page(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """A plan we lease but never wrote a page for.

    An engine asked for a two-bedroom in this submarket can only answer with
    a page that is about the two-bedroom. An ad pointed at the homepage has
    the same problem from the other side: the renter has to hunt.
    """
    plans = _clean_list(data.get("floor_plans"))
    pages = _clean_list(data.get("pages"))
    if not plans or not pages:
        return []

    blob = " ".join(_norm("%s %s %s" % (p.get("url") or "", p.get("title") or "",
                                        p.get("h1") or "")) for p in pages)
    declared = {str(p.get("floor_plan") or "").strip().lower()
                for p in pages if p.get("floor_plan")}

    missing = []
    for plan in plans:
        name = str(plan.get("name") or "").strip()
        if not name:
            continue
        if name.lower() in declared or _norm(name).strip() and _norm(name) in blob:
            continue
        missing.append(plan)
    if not missing:
        return []

    as_of = _iso_day(data.get("floor_plans_as_of")) or _iso_day(data.get("as_of"))
    source = data.get("floor_plans_source") or "community_brief_floor_plans"
    vacant = sum(_int(p.get("available")) or 0 for p in missing)

    receipts = [_receipt("Pages read on the property site", len(pages),
                         data.get("pages_source") or "site_crawl",
                         _iso_day(data.get("pages_as_of")))]
    for plan in missing[:8]:
        beds = _int(plan.get("beds"))
        sqft = _int(plan.get("sqft"))
        shape = ", ".join(x for x in [
            ("studio" if beds == 0 else "%d bed" % beds) if beds is not None else "",
            ("%d sq ft" % sqft) if sqft else "",
            ("%d available" % _int(plan.get("available")))
            if _int(plan.get("available")) else "",
        ] if x)
        receipts.append(_receipt("No page for floor plan “%s”" % plan.get("name"),
                                 shape or "no detail recorded", source, as_of))

    severity = "high" if vacant else "medium"
    counted = "%d floor plan%s" % (len(missing), "" if len(missing) == 1 else "s")
    expect = ("A page for each of the %s, carrying that plan's own layout, "
              "square footage and what is open now." % counted)
    if vacant:
        expect += " %d of those homes are open today." % vacant

    return _emit(
        "seo_missing_floor_plan_page", data,
        category="content", channels=[CHANNEL_WEBSITE, CHANNEL_ORGANIC, CHANNEL_AI_SEARCH],
        severity=severity, confidence=8,
        found="%s we lease %s no page on the property site."
              % (counted, "has" if len(missing) == 1 else "have"),
        receipts=receipts,
        expect=expect,
        if_skip=("Searches and AI answers for those layouts keep landing on the "
                 "homepage, where the renter has to start over."),
        action={"kind": "content_brief", "executor": "portal",
                "requires_signed_deal": True, "fair_housing_review": True,
                "params": {"pages": [{"floor_plan": p.get("name"), "beds": _int(p.get("beds")),
                                      "baths": _float(p.get("baths")), "sqft": _int(p.get("sqft")),
                                      "available": _int(p.get("available"))}
                                     for p in missing],
                           "one_page_per": "floor plan",
                           "facts_required": ["floor_plans", "availability"]}},
        verify_metric="floor-plan pages live on the property site", verify_days=45)


# ── rule 2 — availability an engine cannot read ──────────────────────────────

def rule_availability_not_machine_readable(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """We know what is open; the page does not say so in a readable way.

    An engine that has to infer availability from prose will not quote it. The
    fix is markup over facts we already hold, not new copy.
    """
    pages = [p for p in _clean_list(data.get("pages")) if "jsonld_types" in p]
    if not pages:
        return []
    availability = data.get("availability") or {}
    plans = _clean_list(data.get("floor_plans"))
    open_units = _int(availability.get("available_units"))
    if open_units is None:
        open_units = sum(_int(p.get("available")) or 0 for p in plans) or None
    if open_units is None:
        return []

    marked = []
    for page in pages:
        types = {str(t).strip().lower() for t in (page.get("jsonld_types") or [])}
        if types & {t.lower() for t in AVAILABILITY_SCHEMA_TYPES}:
            marked.append(page)
    if marked:
        return []

    as_of = _iso_day(availability.get("as_of")) or _iso_day(data.get("as_of"))
    source = availability.get("source") or data.get("floor_plans_source") or "aptiq"
    found_types = sorted({str(t) for p in pages for t in (p.get("jsonld_types") or [])})

    receipts = [
        _receipt("Homes open now", open_units, source, as_of),
        _receipt("Pages checked for readable markup", len(pages),
                 data.get("pages_source") or "site_crawl", _iso_day(data.get("pages_as_of"))),
        _receipt("Markup types found on those pages",
                 ", ".join(found_types) if found_types else "none", "site_crawl",
                 _iso_day(data.get("pages_as_of"))),
    ]
    rent = _float(availability.get("asking_rent"))
    if rent:
        receipts.append(_receipt("Asking rent recorded", round(rent), source, as_of))

    expect = ("Availability marked up on the plan and availability pages, so an "
              "engine can quote what is open — %d home%s today — instead of "
              "guessing." % (open_units, "" if open_units == 1 else "s"))
    return _emit(
        "seo_availability_not_machine_readable", data,
        category="content", channels=[CHANNEL_WEBSITE, CHANNEL_AI_SEARCH],
        severity="medium" if open_units < 10 else "high", confidence=7,
        found=("We know what is open, but no page states it in markup an engine "
               "can read."),
        receipts=receipts,
        expect=expect,
        if_skip=("Engines keep answering availability questions about this "
                 "community from listing sites rather than from its own site."),
        action={"kind": "schema", "executor": "human",
                "requires_signed_deal": False, "fair_housing_review": False,
                "params": {"markup": list(AVAILABILITY_SCHEMA_TYPES[:3]),
                           "pages": [p.get("url") for p in pages[:10] if p.get("url")],
                           "fields": ["availability", "unit count", "square footage"]
                                     + (["price"] if rent else []),
                           "price_source": (source if rent else
                                            "no measured price — omit price rather "
                                            "than publish an unverified one")}},
        verify_metric="plan pages carrying readable availability markup", verify_days=30)


# ── registry ─────────────────────────────────────────────────────────────────
# Each entry: the function, the data keys it cannot work without, and the
# sentence run() records when those keys are empty. The requirement list is
# what makes "we did not measure this" different from "nothing is wrong".

RULES: Dict[str, Callable[[Dict[str, Any]], List[Dict[str, Any]]]] = {
    "seo_missing_floor_plan_page": rule_missing_floor_plan_page,
    "seo_availability_not_machine_readable": rule_availability_not_machine_readable,
}

REQUIREMENTS: Dict[str, Dict[str, Any]] = {
    "seo_missing_floor_plan_page": {
        "needs": ("floor_plans", "pages"),
        "why": "Matching plans to pages needs both the plan list and a read of the site.",
    },
    "seo_availability_not_machine_readable": {
        "needs": ("pages", "availability"),
        "why": "Judging markup needs a read of the site and a measured availability figure.",
    },
}


def _missing_inputs(rule_key: str, data: Dict[str, Any]) -> List[str]:
    needs = REQUIREMENTS.get(rule_key, {}).get("needs") or ()
    return [key for key in needs if not data.get(key)]


def run(company_id: str, *, today: Optional[date] = None,
        rules: Optional[Sequence[str]] = None,
        data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Every SEO and AI-search rule for one property.

    `data` is the gathered evidence; pass it to run the rules against evidence
    you already hold, or leave it out and this gathers what the portal owns.
    A rule with nothing to read is skipped by name and reason — never silently
    counted as clean.
    """
    today = _today(today)
    gaps: List[Dict[str, str]] = []
    if data is None:
        data, gathered_gaps = gather(company_id, today=today)
        gaps.extend(gathered_gaps)
    else:
        data = dict(data)
    data.setdefault("company_id", company_id)
    data["_today"] = today

    wanted = [k for k in (rules or list(RULES)) if k in RULES]
    recommendations: List[Dict[str, Any]] = []
    rules_run: List[str] = []
    rules_skipped: List[Dict[str, Any]] = []

    for rule_key in wanted:
        missing = _missing_inputs(rule_key, data)
        if missing:
            rules_skipped.append({
                "rule_key": rule_key,
                "missing": missing,
                "reason": REQUIREMENTS.get(rule_key, {}).get("why") or
                          "This rule has no measurement to read.",
            })
            continue
        try:
            produced = RULES[rule_key](data) or []
        except Exception as exc:  # noqa: BLE001 — one rule must not sink the set
            logger.error("reco_seo rule %s failed for %s: %s", rule_key, company_id,
                         exc, exc_info=True)
            rules_skipped.append({"rule_key": rule_key, "missing": [],
                                  "reason": "This rule could not complete its run."})
            continue
        rules_run.append(rule_key)
        recommendations.extend(produced)

    return {
        "company_id": company_id,
        "recommendations": recommendations,
        "gaps": gaps,
        "rules_run": rules_run,
        "rules_skipped": rules_skipped,
        "as_of": _now_iso(),
    }


# ── gathering ────────────────────────────────────────────────────────────────

def gather(company_id: str, *, today: Optional[date] = None) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    """Read what the portal owns for this property. Missing sources become gaps.

    Nothing here raises: a source that is down produces a gap naming it, and
    the rules that needed it are skipped rather than answered from memory.
    """
    today = _today(today)
    data: Dict[str, Any] = {"company_id": company_id, "as_of": today.isoformat()}
    gaps: List[Dict[str, str]] = []

    identity = None
    try:
        from skills import property_resolver as pr
        identity = pr.resolve(company_id)
        data["property_name"] = identity.to_dict().get("name")
    except Exception as exc:  # noqa: BLE001
        logger.info("reco_seo: identity unavailable for %s (%s)", company_id, exc)
        gaps.append(_gap("property_name", "property_resolver",
                         "This property could not be resolved, so its name and "
                         "platform ids are not attached to these findings."))

    _gather_brief(company_id, data, gaps)
    _gather_availability(identity, data, gaps)

    gaps.append(_gap("pages", "site_crawl",
                     "No read of this property's website is stored, so page-level "
                     "rules cannot run. A crawl or an outside tracker supplies it."))
    return data, gaps


def _gather_brief(company_id: str, data: Dict[str, Any], gaps: List[Dict[str, str]]) -> None:
    """Floor plans and quoted facts from the community brief (override wins)."""
    try:
        import community_brief as cb
        props = cb.load_company_state(company_id)
    except Exception as exc:  # noqa: BLE001
        logger.info("reco_seo: brief unavailable for %s (%s)", company_id, exc)
        gaps.append(_gap("floor_plans", "community_brief",
                         "The community brief could not be read for this property."))
        return

    plans = _brief_floor_plans(props)
    if plans:
        data["floor_plans"] = plans
        data["floor_plans_source"] = "community_brief_floor_plans"
    else:
        gaps.append(_gap("floor_plans", "community_brief",
                         "No structured floor plans are recorded for this property."))


def _brief_floor_plans(props: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The brief's floor-plan table, override first, as plain rows."""
    import json

    for key in ("fluency_floor_plans_override", "fluency_floor_plans_json"):
        raw = (props or {}).get(key)
        if not raw:
            continue
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            continue
        rows = parsed.get("rows") if isinstance(parsed, dict) else parsed
        if isinstance(rows, list) and rows:
            return [r for r in rows if isinstance(r, dict) and r.get("name")]
    return []


def _gather_availability(identity: Any, data: Dict[str, Any],
                         gaps: List[Dict[str, str]]) -> None:
    if identity is None:
        return
    aptiq_id = identity.to_dict().get("aptiq_property_id")
    if not aptiq_id:
        gaps.append(_gap("availability", "aptiq",
                         "This property has no availability id, so what is open "
                         "today is not connected for it."))
        return
    try:
        import apartmentiq_client as aptiq
        snap = aptiq.get_property_snapshot(str(aptiq_id)) or {}
    except Exception as exc:  # noqa: BLE001
        logger.info("reco_seo: availability unavailable for %s (%s)", aptiq_id, exc)
        gaps.append(_gap("availability", "aptiq",
                         "The availability source is temporarily unavailable."))
        return
    if not snap:
        gaps.append(_gap("availability", "aptiq",
                         "No availability snapshot exists for this property today."))
        return
    data["availability"] = {
        "available_units": _int(snap.get("available_units")),
        "asking_rent": _float(snap.get("asking_rent")),
        "occupancy": _float(snap.get("occupancy")),
        "source": "aptiq_api" if snap.get("_source") == "api" else "aptiq_daily_csv",
        "as_of": data.get("as_of"),
    }
