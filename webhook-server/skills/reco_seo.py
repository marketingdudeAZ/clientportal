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


def _money(amount: float) -> str:
    """'154.50', or '155' when it is whole. Never rounded down to look smaller."""
    cents = round(float(amount) * 100)
    return str(cents // 100) if cents % 100 == 0 else "%.2f" % (cents / 100.0)


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
        token = _norm(name)
        if name.lower() in declared:
            continue
        if token.strip() and token in blob:
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
                "params": {"changes_copy": False,
                           "markup": list(AVAILABILITY_SCHEMA_TYPES[:3]),
                           "pages": [p.get("url") for p in pages[:10] if p.get("url")],
                           "fields": ["availability", "unit count", "square footage"]
                                     + (["price"] if rent else []),
                           "price_source": (source if rent else
                                            "no measured price — omit price rather "
                                            "than publish an unverified one")}},
        verify_metric="plan pages carrying readable availability markup", verify_days=30)


# ── rule 3 — questions we are not in the answer to ───────────────────────────

def _fh_ok(text: Any) -> bool:
    """True when a phrase is safe to turn into a page brief.

    A renter may type anything; that does not make it something we should
    write a page around. A tracked question carrying protected-class
    vocabulary is dropped from the brief rather than passed through, because
    the brief is our copy even when the question was someone else's.
    """
    return _fair_housing_review([str(text or "")]) is None


def _measured(question: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """(engines that answered without us, engines that measured at all).

    An engine with no reading for a question is not a miss. Counting it as
    one would turn thin coverage into a finding, which is the failure mode
    this whole module exists to avoid.
    """
    missing, measured = [], []
    for engine, state in (question.get("engines") or {}).items():
        named = (state or {}).get("named")
        cited = (state or {}).get("cited")
        if named is None and cited is None:
            continue
        measured.append(engine)
        if not (named or cited):
            missing.append(engine)
    return missing, measured


def rule_share_of_answer_gap(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Tracked questions where the engines answer without this community."""
    questions = [q for q in _clean_list(data.get("questions")) if q.get("text")]
    if not questions:
        return []

    gaps: List[Dict[str, Any]] = []
    per_engine: Dict[str, Dict[str, int]] = {}
    for question in questions:
        missing, measured = _measured(question)
        for engine in measured:
            bucket = per_engine.setdefault(engine, {"measured": 0, "missing": 0})
            bucket["measured"] += 1
            if engine in missing:
                bucket["missing"] += 1
        if missing and _fh_ok(question.get("text")):
            gaps.append({"question": question.get("text"),
                         "topic": question.get("topic"),
                         "intent": question.get("intent"),
                         "engines_missing": sorted(missing),
                         "fact_required": _fact_for(question.get("text"))})
    if not gaps or not per_engine:
        return []

    source = data.get("questions_source") or "ai_mentions"
    as_of = _iso_day(data.get("questions_as_of")) or _iso_day(data.get("as_of"))
    measured_total = sum(b["measured"] for b in per_engine.values())
    missing_total = sum(b["missing"] for b in per_engine.values())

    receipts = []
    for engine in sorted(per_engine):
        bucket = per_engine[engine]
        receipts.append(_receipt(
            "%s answered without this community" % engine,
            "%d of %d question%s measured" % (bucket["missing"], bucket["measured"],
                                              "" if bucket["measured"] == 1 else "s"),
            source, as_of))

    fanout = []
    for row in _clean_list(data.get("fanout")):
        query = str(row.get("query") or "").strip()
        if not query or not _fh_ok(query):
            continue
        fanout.append({"query": query, "engine": row.get("engine"),
                       "times_seen": _int(row.get("count")),
                       "fact_required": _fact_for(query)})
    if fanout:
        receipts.append(_receipt("Follow-up searches the engines ran",
                                 len(fanout), data.get("fanout_source") or source, as_of))

    # Citation-only sources cannot tell "named but not linked" from "absent",
    # so the finding is the same but our confidence in it is not.
    knows_named = any((s or {}).get("named") is not None
                      for q in questions for s in (q.get("engines") or {}).values())
    share = 1.0 - (float(missing_total) / measured_total) if measured_total else 0.0
    severity = "high" if share <= 0.25 else ("medium" if share <= 0.6 else "low")

    return _emit(
        "seo_share_of_answer_gap", data,
        category="content", channels=[CHANNEL_AI_SEARCH, CHANNEL_WEBSITE],
        severity=severity, confidence=9 if knows_named else 6,
        found=("The engines answer %d of the %d tracked question readings without "
               "naming this community." % (missing_total, measured_total)),
        receipts=receipts,
        expect=("One page per question, answering it with this community's own "
                "facts. %d question%s currently have%s nowhere to point."
                % (len(gaps), "" if len(gaps) == 1 else "s", "s" if len(gaps) == 1 else "")),
        if_skip=("The engines keep answering these questions with listing sites and "
                 "other communities, which is where the renter goes next."),
        action={"kind": "content_brief", "executor": "portal",
                "requires_signed_deal": True, "fair_housing_review": True,
                "params": {"questions": gaps, "fanout": fanout,
                           "one_page_per": "question",
                           "differentiation_gate": True,
                           "measured_by": source}},
        verify_metric="tracked questions naming this community", verify_days=45)


# ── rule 4 — an engine repeating something that stopped being true ───────────

def rule_answer_quotes_stale_fact(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """An engine is telling renters something the brief says is no longer so.

    This is the failure mode that costs a tour: the renter arrives holding a
    pet policy or a fee we changed months ago.
    """
    claims = _clean_list(data.get("claims"))
    facts = data.get("brief_facts") or {}
    if not claims or not facts:
        return []

    conflicts = []
    for claim in claims:
        field = str(claim.get("conflicts_brief_field") or "").strip()
        text = str(claim.get("claim_text") or "").strip()
        if not field or not text:
            continue
        current = (facts.get(field) or {}).get("value")
        if not current:
            continue
        if _norm(text) == _norm(current):
            continue
        conflicts.append({"field": field, "label": (facts.get(field) or {}).get("label") or field,
                          "claimed": text, "correct": str(current),
                          "engine": claim.get("engine"), "cited_url": claim.get("source_url"),
                          "as_of": _iso_day(claim.get("as_of"))})
    if not conflicts:
        return []

    source = data.get("claims_source") or "geo_claims"
    receipts = []
    for row in conflicts[:6]:
        receipts.append(_receipt("%s says the %s is “%s”"
                                 % (row["engine"] or "An engine", row["label"], row["claimed"]),
                                 row["cited_url"] or "no source linked", source, row["as_of"]))
        receipts.append(_receipt("The brief records “%s”" % row["correct"],
                                 row["label"], "community_brief",
                                 _iso_day((facts.get(row["field"]) or {}).get("last_edited"))))

    money = {"fees", "fee_schedule", "specials", "concessions", "pricing"}
    severity = "high" if any(r["field"] in money or r["field"].startswith("pet")
                             for r in conflicts) else "medium"
    fields = ", ".join(sorted({r["label"] for r in conflicts}))

    return _emit(
        "seo_answer_quotes_stale_fact", data,
        category="compliance", channels=[CHANNEL_AI_SEARCH, CHANNEL_WEBSITE],
        severity=severity, confidence=8,
        found="An engine is quoting a %s that the brief says is out of date." % fields,
        receipts=receipts,
        expect=("The current %s stated plainly on the page the engines cite, so the "
                "next answer carries it." % fields),
        if_skip=("Renters keep arriving with the old %s, and the correction happens "
                 "on a tour instead of on the page." % fields),
        action={"kind": "page_fix", "executor": "human",
                "requires_signed_deal": False, "fair_housing_review": True,
                "params": {"corrections": conflicts, "source_of_truth": "community_brief"}},
        verify_metric="engine answers carrying the current facts", verify_days=30)


# ── rule 5 (new) — the total a renter will actually pay ──────────────────────

def rule_fee_transparency_gap(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Engines name the real cost as the problem, and the page does not show it.

    This is an honest-pricing fix, not a persuasion tactic. A renter who finds
    the recurring charges on the pricing page can decide; one who finds them
    after a tour has been misled, and the engines are already saying so.
    """
    signals = _clean_list(data.get("fee_signals"))
    fees = data.get("fees") or {}
    if not signals or not fees:
        return []
    if fees.get("disclosed") is not False:        # None means we never checked
        return []

    known = _clean_list(fees.get("known_fees"))
    source = data.get("fee_signals_source") or "ai_answers"
    receipts = []
    for signal in signals[:4]:
        quote = str(signal.get("quote") or "").strip()
        if not quote:
            continue
        receipts.append(_receipt("%s names cost as a weakness"
                                 % (signal.get("engine") or "An engine"),
                                 quote, source, _iso_day(signal.get("as_of"))))
    if not receipts:
        return []

    monthly = sum(_float(f.get("amount")) or 0.0 for f in known
                  if str(f.get("period") or "monthly").lower().startswith("month"))
    for fee in known[:6]:
        receipts.append(_receipt("Recurring charge recorded: %s" % (fee.get("name") or "fee"),
                                 fee.get("amount"), fee.get("source") or "community_brief",
                                 _iso_day(fee.get("as_of"))))
    receipts.append(_receipt("Pricing page checked for a charges table",
                             fees.get("page_url") or "pricing page",
                             data.get("pages_source") or "site_crawl",
                             _iso_day(data.get("pages_as_of")) or _iso_day(data.get("as_of"))))

    expect = ("Base rent and the recurring charges shown together on the pricing "
              "page, so the total is on the page a renter decides from.")
    if monthly:
        # To the cent. Rounding a charge down is the same failure the rule is
        # about, in miniature.
        expect = ("Base rent and $%s a month of recurring charges shown together on "
                  "the pricing page, so the total is on the page a renter decides "
                  "from." % _money(monthly))

    return _emit(
        "seo_fee_transparency_gap", data,
        category="compliance", channels=[CHANNEL_WEBSITE, CHANNEL_AI_SEARCH],
        severity="high", confidence=8,
        found=("Engines name added charges as this community's weakness, and the "
               "pricing page does not show them."),
        receipts=receipts,
        expect=expect,
        if_skip=("The complaint keeps getting repeated back to every renter who "
                 "asks, and the first honest number arrives after a tour."),
        action={"kind": "page_fix", "executor": "human",
                "requires_signed_deal": False, "fair_housing_review": True,
                "params": {"add": "a plain table of recurring monthly charges beside base rent",
                           "charges": [{"name": f.get("name"), "amount": _float(f.get("amount")),
                                        "period": f.get("period") or "monthly"} for f in known],
                           "page": fees.get("page_url"),
                           "rule": "state what is charged; never present a charge as a discount"}},
        verify_metric="engine answers naming cost as a weakness", verify_days=60)


# ── rule 6 (new) — nothing in the form engines actually quote ────────────────

def rule_answer_format_gap(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Whole topics where every comparable community is named and this one is not.

    The gate matters here more than anywhere: the answer is one genuinely
    useful page per real question, not the same page published 109 times.
    """
    coverage = _clean_list(data.get("topic_coverage"))
    if not coverage:
        return []

    absent = []
    for row in coverage:
        topic = str(row.get("topic") or "").strip()
        ours = _int(row.get("our_mentions"))
        theirs = _int(row.get("competitor_mentions")) or 0
        prompts = _int(row.get("prompts")) or 0
        if not topic or ours is None:
            continue
        if ours == 0 and theirs > 0 and prompts >= 1 and _fh_ok(topic):
            absent.append({"topic": topic, "questions": prompts,
                           "others_named": theirs,
                           "communities": len(_clean_list(row.get("competitors")) or
                                              row.get("competitors") or []),
                           "fact_required": _fact_for(topic)})
    if not absent:
        return []

    source = data.get("topic_coverage_source") or "ai_answer_tracking"
    as_of = _iso_day(data.get("questions_as_of")) or _iso_day(data.get("as_of"))
    receipts = []
    for row in absent[:6]:
        receipts.append(_receipt(
            "Topic “%s”: this community named 0 times" % row["topic"],
            "%d other communities named %d times across %d question%s"
            % (row["communities"], row["others_named"], row["questions"],
               "" if row["questions"] == 1 else "s"),
            source, as_of))

    formats = _clean_list(data.get("cited_formats"))
    ours = {str(f).strip().lower() for f in (data.get("our_formats") or [])}
    missing_formats = []
    for fmt in formats:
        name = str(fmt.get("format") or "").strip()
        share = _float(fmt.get("share"))
        if not name:
            continue
        if name.lower() not in ours:
            missing_formats.append({"format": name, "share": share})
            receipts.append(_receipt("Engines cite %s for these questions" % name,
                                     ("%d%% of citations" % round(share * 100))
                                     if share is not None else "cited",
                                     source, as_of))

    return _emit(
        "seo_answer_format_gap", data,
        category="content", channels=[CHANNEL_AI_SEARCH, CHANNEL_WEBSITE],
        severity="high" if len(absent) >= 2 else "medium", confidence=8,
        found=("On %d topic%s the engines name comparable communities and never "
               "this one." % (len(absent), "" if len(absent) == 1 else "s")),
        receipts=receipts,
        expect=("One page per topic, each answering a question a renter actually "
                "asks with facts only this community can give. Each draft goes "
                "through the differentiation gate before it is published."),
        if_skip=("These topics stay someone else's answer, and the community is "
                 "absent from the moment a renter is choosing."),
        action={"kind": "content_brief", "executor": "portal",
                "requires_signed_deal": True, "fair_housing_review": True,
                "params": {"topics": absent, "formats_engines_cite": missing_formats,
                           "one_page_per": "question",
                           "differentiation_gate": True,
                           "purpose_test": ("write it only if a resident would want to "
                                            "read it; a page written to rank is the "
                                            "thing the policy forbids")}},
        verify_metric="topics where the engines name this community", verify_days=60)


# ── rule 7 — the differentiation gate ────────────────────────────────────────

def _shingles(text: Any, size: int = DUPLICATE_SHINGLE_SIZE) -> set:
    words = _words(text)
    if len(words) < size:
        return set()
    return {tuple(words[i:i + size]) for i in range(len(words) - size + 1)}


def similarity(left: Any, right: Any) -> float:
    """Jaccard overlap of five-word runs. 0.0 when either side is too short."""
    a, b = _shingles(left), _shingles(right)
    if not a or not b:
        return 0.0
    return round(len(a & b) / float(len(a | b)), 4)


def rule_near_duplicate_page(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """A draft that reads like another community's page, caught before publishing.

    109 properties publishing the same page with the name swapped is the
    pattern the scaled-content policy exists to catch, and it is also simply
    a page no resident wanted. The gate runs on drafts, because after
    publication the remedy is a removal rather than an edit.
    """
    drafts = [d for d in _clean_list(data.get("drafts")) if d.get("text")]
    corpus = [c for c in _clean_list(data.get("corpus")) if c.get("text")]
    if not drafts or not corpus:
        return []

    pairs = []
    for draft in drafts:
        if len(_words(draft.get("text"))) < DUPLICATE_MIN_WORDS:
            continue
        for other in corpus:
            if str(other.get("company_id") or "") == str(draft.get("company_id") or
                                                         data.get("company_id")) \
                    and (other.get("url") or other.get("title")) == \
                    (draft.get("url") or draft.get("title")):
                continue
            score = similarity(draft.get("text"), other.get("text"))
            if score >= DUPLICATE_WARN:
                pairs.append({"draft": draft.get("title") or draft.get("url"),
                              "matches": other.get("title") or other.get("url"),
                              "at_property": other.get("property_name"),
                              "overlap": score})
    if not pairs:
        return []

    pairs.sort(key=lambda p: -p["overlap"])
    worst = pairs[0]["overlap"]
    receipts = [_receipt("Drafts compared", len(drafts), "content_briefs",
                         _iso_day(data.get("as_of")))]
    for pair in pairs[:5]:
        receipts.append(_receipt("“%s” overlaps “%s”" % (pair["draft"], pair["matches"]),
                                 "%d%% of its five-word runs" % round(pair["overlap"] * 100),
                                 "differentiation_gate", _iso_day(data.get("as_of"))))

    return _emit(
        "seo_near_duplicate_page", data,
        category="compliance", channels=[CHANNEL_WEBSITE, CHANNEL_ORGANIC],
        severity="high" if worst >= DUPLICATE_HIGH else "medium", confidence=9,
        found=("%d draft%s read like a page already written for another community."
               % (len(pairs), "" if len(pairs) == 1 else "s")),
        receipts=receipts,
        expect=("Each page rewritten around facts only this community can give — "
                "its plans, its charges, its street — or dropped. Nothing publishes "
                "at this overlap."),
        if_skip=("The portfolio publishes the same page under 100 names, which is "
                 "the pattern the scaled-content policy is written to catch, and "
                 "no reader is served by either copy."),
        action={"kind": "content_brief", "executor": "portal",
                "requires_signed_deal": False, "fair_housing_review": True,
                "params": {"publish_blocked": True, "gate": "differentiation",
                           "pairs": pairs,
                           "threshold": DUPLICATE_WARN,
                           "rewrite_around": ["floor_plans", "availability", "fees",
                                              "what is within walking distance"]}},
        verify_metric="drafts clearing the differentiation gate", verify_days=21)


# ── rule 8 — the pages a renter lands on ─────────────────────────────────────

def _is_money_page(page: Dict[str, Any]) -> bool:
    if page.get("is_money_page") is not None:
        return bool(page["is_money_page"])
    page_type = str(page.get("page_type") or "").strip().lower()
    if page_type:
        return page_type in MONEY_PAGE_TYPES
    url = str(page.get("url") or "").lower()
    path = url.split("//", 1)[-1]
    path = path[path.find("/"):] if "/" in path else "/"
    if path in ("/", ""):
        return True
    return any(hint in path for hint in _MONEY_PATH_HINTS)


def rule_money_page_metadata_gap(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Titles, descriptions and headings missing on the pages that matter."""
    pages = _clean_list(data.get("pages"))
    read = [p for p in pages if any(k in p for k in ("title", "meta_description", "h1"))]
    money = [p for p in read if _is_money_page(p)]
    if not money:
        return []

    titles: Dict[str, List[str]] = {}
    for page in money:
        title = str(page.get("title") or "").strip().lower()
        if title:
            titles.setdefault(title, []).append(str(page.get("url") or ""))

    defects = []
    for page in money:
        problems = []
        if not str(page.get("title") or "").strip():
            problems.append("no title")
        elif len(titles.get(str(page["title"]).strip().lower(), [])) > 1:
            problems.append("a title shared with another page here")
        if not str(page.get("meta_description") or "").strip():
            problems.append("no description")
        if "h1" in page and not str(page.get("h1") or "").strip():
            problems.append("no heading")
        headings = page.get("headings")
        if isinstance(headings, list):
            h1s = [h for h in headings
                   if str((h or {}).get("level") if isinstance(h, dict) else "").strip() == "1"]
            if len(h1s) > 1:
                problems.append("more than one top heading")
        if problems:
            defects.append({"url": page.get("url"), "problems": problems})
    if not defects:
        return []

    source = data.get("pages_source") or "site_crawl"
    as_of = _iso_day(data.get("pages_as_of")) or _iso_day(data.get("as_of"))
    receipts = [_receipt("Pages a renter lands on, checked", len(money), source, as_of)]
    for row in defects[:6]:
        receipts.append(_receipt(row["url"] or "a page", ", ".join(row["problems"]),
                                 source, as_of))

    return _emit(
        "seo_money_page_metadata_gap", data,
        category="content", channels=[CHANNEL_ORGANIC, CHANNEL_WEBSITE],
        severity="high" if len(defects) >= 3 else "medium", confidence=9,
        found=("%d of the %d pages a renter lands on are missing the title, "
               "description or heading that says what they are."
               % (len(defects), len(money))),
        receipts=receipts,
        expect=("Each of those pages naming itself: what the page is, which "
                "community, and what a renter can do on it."),
        if_skip=("Search results and AI answers keep describing these pages in "
                 "whatever words they can scrape together."),
        action={"kind": "page_fix", "executor": "human",
                "requires_signed_deal": False, "fair_housing_review": True,
                "params": {"pages": defects,
                           "write": "one title and one description per page, "
                                    "describing that page and nothing else"}},
        verify_metric="landing pages carrying their own title and description",
        verify_days=30)


def rule_orphan_page(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Pages nothing on the site links to."""
    pages = [p for p in _clean_list(data.get("pages")) if "internal_links_in" in p]
    if not pages:
        return []

    orphans = []
    for page in pages:
        if _int(page.get("internal_links_in")) != 0:
            continue
        url = str(page.get("url") or "")
        path = url.split("//", 1)[-1]
        path = path[path.find("/"):] if "/" in path else "/"
        if path in ("/", "") or str(page.get("page_type") or "").lower() in ("home", "homepage"):
            continue
        orphans.append(url)
    if not orphans:
        return []

    source = data.get("pages_source") or "site_crawl"
    as_of = _iso_day(data.get("pages_as_of")) or _iso_day(data.get("as_of"))
    receipts = [_receipt("Pages with a link count", len(pages), source, as_of)]
    for url in orphans[:8]:
        receipts.append(_receipt(url, "nothing on the site links here", source, as_of))

    return _emit(
        "seo_orphan_page", data,
        category="content", channels=[CHANNEL_WEBSITE, CHANNEL_ORGANIC],
        severity="medium", confidence=8,
        found=("%d page%s on the site %s reachable from no other page."
               % (len(orphans), "" if len(orphans) == 1 else "s",
                  "is" if len(orphans) == 1 else "are")),
        receipts=receipts,
        expect=("Each of those pages linked from the page it belongs under — a "
                "plan page from the plan list, a neighborhood page from the "
                "neighborhood section."),
        if_skip=("They stay invisible to anything that finds pages by following "
                 "links, which is most of what reads the site."),
        action={"kind": "internal_link", "executor": "human",
                "requires_signed_deal": False, "fair_housing_review": False,
                "params": {"changes_copy": False, "pages": orphans,
                           "link_from": "the section index the page belongs under"}},
        verify_metric="pages with at least one link in", verify_days=30)


# ── rule 9 — the profile a renter finds before the site ──────────────────────

def _digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


# "15000 N Scottsdale Rd" and "15000 North Scottsdale Road" are the same
# address written two ways. Flagging that as an inconsistency across 100
# properties would bury the listings that genuinely disagree.
_STREET_FORMS = {
    "n": "north", "s": "south", "e": "east", "w": "west",
    "ne": "northeast", "nw": "northwest", "se": "southeast", "sw": "southwest",
    "st": "street", "rd": "road", "ave": "avenue", "av": "avenue",
    "blvd": "boulevard", "dr": "drive", "ln": "lane", "ct": "court",
    "pkwy": "parkway", "hwy": "highway", "cir": "circle", "trl": "trail",
    "ste": "suite", "apt": "apartment", "bldg": "building", "fl": "floor",
}


def _address_key(value: Any) -> str:
    """An address reduced to its words, with the usual abbreviations expanded."""
    return " ".join(_STREET_FORMS.get(w, w) for w in _words(value))


def rule_local_profile_gap(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Business-profile gaps, and the same community described three ways.

    A renter who searches the community by name meets the profile before the
    site, and so does an engine looking for the basic facts.
    """
    local = data.get("local") or {}
    profile = local.get("profile") or {}
    listings = _clean_list(local.get("listings"))
    if not profile and len(listings) < 2:
        return []

    source = local.get("source") or "business_profile"
    as_of = _iso_day(local.get("as_of")) or _iso_day(data.get("as_of"))
    receipts, defects = [], []

    for key, label in (("primary_category", "no primary category"),
                       ("hours", "no opening hours"),
                       ("phone", "no phone number"),
                       ("website", "no link to the property site"),
                       ("description", "no description")):
        if key in profile and not profile.get(key):
            defects.append(label)
            receipts.append(_receipt("Business profile: %s" % label, "empty", source, as_of))

    if len(listings) >= 2:
        for field, reader, label in (("name", lambda v: _norm(v).strip(), "name"),
                                     ("address", _address_key, "street address"),
                                     ("phone", _digits, "phone number")):
            values = {}
            for row in listings:
                if not row.get(field):
                    continue
                values.setdefault(reader(row[field]), []).append(row.get("source") or "a listing")
            if len(values) > 1:
                defects.append("a %s that differs between listings" % label)
                for value, where in list(values.items())[:4]:
                    receipts.append(_receipt("%s on %s" % (label.capitalize(),
                                                           ", ".join(where)),
                                             value, source, as_of))
    if not defects:
        return []

    return _emit(
        "seo_local_profile_gap", data,
        category="vendors", channels=[CHANNEL_LOCAL, CHANNEL_ORGANIC],
        severity="high" if len(defects) >= 3 else "medium", confidence=8,
        found="The business profile for this community is %s." % ", ".join(defects[:3]),
        receipts=receipts,
        expect=("One set of facts — name, street address, phone, hours, category — "
                "matching across the profile and the listings that carry it."),
        if_skip=("A renter searching the community by name meets three versions of "
                 "it, and so does anything that reads those listings."),
        action={"kind": "listing_change", "executor": "human",
                "requires_signed_deal": False, "fair_housing_review": True,
                "params": {"fix": defects, "match_to": "the property site",
                           "route": "the listings vendor that owns these profiles"}},
        verify_metric="profile fields matching the property site", verify_days=30)


# ── rule 10 — pages too slow or too awkward to use ───────────────────────────

def rule_core_web_vitals_blocking(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Measured field performance on the pages a renter has to use."""
    rows = _clean_list(data.get("vitals"))
    if not rows:
        return []

    failing, receipts, severe = [], [], False
    for row in rows:
        url = str(row.get("url") or "")
        problems = []
        lcp = _float(row.get("lcp_ms"))
        inp = _float(row.get("inp_ms"))
        cls = _float(row.get("cls"))
        if lcp is not None and lcp > CWV_LCP_MS:
            problems.append("takes %.1fs to show its main content" % (lcp / 1000.0))
            severe = severe or lcp >= CWV_LCP_POOR_MS
        if inp is not None and inp > CWV_INP_MS:
            problems.append("waits %dms before responding to a tap" % int(inp))
        if cls is not None and cls > CWV_CLS:
            problems.append("shifts under the reader by %.2f" % cls)
        if row.get("mobile_usable") is False:
            problems.append("is not usable on a phone")
            severe = True
        if not problems:
            continue
        failing.append({"url": url, "problems": problems})
        receipts.append(_receipt(url or "a page", "; ".join(problems),
                                 row.get("source") or "field_measurement",
                                 _iso_day(row.get("as_of")) or _iso_day(data.get("as_of"))))
    if not failing:
        return []

    receipts.insert(0, _receipt("Pages with field measurements", len(rows),
                                rows[0].get("source") or "field_measurement",
                                _iso_day(rows[0].get("as_of")) or _iso_day(data.get("as_of"))))
    return _emit(
        "seo_core_web_vitals_blocking", data,
        category="vendors", channels=[CHANNEL_WEBSITE, CHANNEL_ORGANIC],
        severity="high" if severe else "medium", confidence=8,
        found=("%d page%s measured slower or less usable than the threshold a "
               "renter will sit through." % (len(failing), "" if len(failing) == 1 else "s")),
        receipts=receipts,
        expect=("Those pages inside the thresholds: main content under %.1fs, a "
                "response under %dms, and usable on a phone."
                % (CWV_LCP_MS / 1000.0, CWV_INP_MS)),
        if_skip=("Renters leave before the page finishes, and the site keeps being "
                 "ranked on how it performs rather than what it says."),
        action={"kind": "page_fix", "executor": "human",
                "requires_signed_deal": False, "fair_housing_review": False,
                "params": {"pages": failing, "changes_copy": False,
                           "thresholds": {"main_content_ms": CWV_LCP_MS,
                                          "response_ms": CWV_INP_MS, "shift": CWV_CLS},
                           "route": "the website vendor that owns this template"}},
        verify_metric="pages inside the field thresholds", verify_days=45)


# ── rule 11 — facts nobody has confirmed in a season ─────────────────────────

def rule_brief_fact_stale(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Brief fields that feed the site's FAQ and AI answers, untouched 90+ days.

    Nothing here says the fact is wrong — only that nobody has confirmed it
    since the spring, while the engines have been quoting it all along.
    """
    facts = data.get("brief_facts") or {}
    if not facts:
        return []
    today = _today(data.get("_today"))

    stale = []
    measured = 0
    for key, field in facts.items():
        if not isinstance(field, dict):
            continue
        feeds = [str(f).lower() for f in (field.get("feeds") or [])]
        quoted = key in QUOTED_FACT_FIELDS or any(
            f in ("website_faq", "ai_answers") for f in feeds)
        if not quoted or not field.get("value"):
            continue
        age = _days_since(field.get("last_edited"), today)
        if age is None:
            continue
        measured += 1
        if age >= STALE_FIELD_DAYS:
            stale.append({"key": key, "label": field.get("label") or key, "days": age,
                          "last_edited": _iso_day(field.get("last_edited"))})
    if not stale or not measured:
        return []

    stale.sort(key=lambda s: -s["days"])
    receipts = [_receipt("Quoted facts with a recorded edit date", measured,
                         "community_brief", _iso_day(data.get("as_of")))]
    for row in stale[:6]:
        receipts.append(_receipt("“%s” last confirmed" % row["label"],
                                 "%s, %d days ago" % (row["last_edited"], row["days"]),
                                 "community_brief", row["last_edited"]))

    oldest = stale[0]
    return _emit(
        "seo_brief_fact_stale", data,
        category="content", channels=[CHANNEL_AI_SEARCH, CHANNEL_WEBSITE],
        severity="high" if oldest["days"] >= 2 * STALE_FIELD_DAYS else "medium",
        confidence=7,
        found=("%d fact%s the site's FAQ and the AI answers quote %s not been "
               "confirmed in %d days." % (len(stale), "" if len(stale) == 1 else "s",
                                          "has" if len(stale) == 1 else "have",
                                          oldest["days"])),
        receipts=receipts,
        expect=("Each of those confirmed or corrected, so what the FAQ says and "
                "what an engine repeats are both current."),
        if_skip=("They keep being quoted as current, and the first time anyone "
                 "checks is when a renter disagrees with one."),
        action={"kind": "page_fix", "executor": "portal",
                "requires_signed_deal": False, "fair_housing_review": True,
                "params": {"confirm_or_update": [s["key"] for s in stale],
                           "feeds": ["the website FAQ", "AI answers"],
                           "threshold_days": STALE_FIELD_DAYS}},
        verify_metric="quoted facts confirmed in the last 90 days", verify_days=30)


# ── registry ─────────────────────────────────────────────────────────────────
# Each entry: the function, the data keys it cannot work without, and the
# sentence run() records when those keys are empty. The requirement list is
# what makes "we did not measure this" different from "nothing is wrong".

# The rules whose action puts words in front of a renter. Every one of them
# sets `fair_housing_review: true`; the rest change markup, links or load time
# and would only add noise to a copy review. Stated here rather than inferred
# from the action kind, because "page_fix" covers both a rewritten paragraph
# and a template that loads too slowly.
COPY_PRODUCING_RULES = frozenset({
    "seo_missing_floor_plan_page",
    "seo_share_of_answer_gap",
    "seo_answer_quotes_stale_fact",
    "seo_fee_transparency_gap",
    "seo_answer_format_gap",
    "seo_near_duplicate_page",
    "seo_money_page_metadata_gap",
    "seo_local_profile_gap",
    "seo_brief_fact_stale",
})

RULES: Dict[str, Callable[[Dict[str, Any]], List[Dict[str, Any]]]] = {
    "seo_missing_floor_plan_page": rule_missing_floor_plan_page,
    "seo_availability_not_machine_readable": rule_availability_not_machine_readable,
    "seo_share_of_answer_gap": rule_share_of_answer_gap,
    "seo_answer_quotes_stale_fact": rule_answer_quotes_stale_fact,
    "seo_fee_transparency_gap": rule_fee_transparency_gap,
    "seo_answer_format_gap": rule_answer_format_gap,
    "seo_near_duplicate_page": rule_near_duplicate_page,
    "seo_money_page_metadata_gap": rule_money_page_metadata_gap,
    "seo_orphan_page": rule_orphan_page,
    "seo_local_profile_gap": rule_local_profile_gap,
    "seo_core_web_vitals_blocking": rule_core_web_vitals_blocking,
    "seo_brief_fact_stale": rule_brief_fact_stale,
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
    "seo_share_of_answer_gap": {
        "needs": ("questions",),
        "why": ("AI answers are not measured for this property yet — no tracked "
                "questions from the vendor, the GEO tables or the mentions audit."),
    },
    "seo_answer_quotes_stale_fact": {
        "needs": ("claims", "brief_facts"),
        "why": ("Catching a stale answer needs both what an engine said and what "
                "the brief currently records."),
    },
    "seo_fee_transparency_gap": {
        "needs": ("fee_signals", "fees"),
        "why": ("Needs an engine naming cost as a weakness and a check of whether "
                "the pricing page discloses the charges."),
    },
    "seo_answer_format_gap": {
        "needs": ("topic_coverage",),
        "why": "Needs per-topic coverage for this community and comparable ones.",
    },
    "seo_near_duplicate_page": {
        "needs": ("drafts", "corpus"),
        "why": ("The gate needs drafts to check and the pages already written "
                "for other communities to check them against."),
    },
    "seo_money_page_metadata_gap": {
        "needs": ("pages",),
        "why": "Needs a read of the site that captured titles, descriptions and headings.",
    },
    "seo_orphan_page": {
        "needs": ("pages",),
        "why": "Needs a read of the site that counted the links into each page.",
    },
    "seo_local_profile_gap": {
        "needs": ("local",),
        "why": ("Needs the business profile and the listings that repeat it; "
                "neither is connected to the portal yet."),
    },
    "seo_core_web_vitals_blocking": {
        "needs": ("vitals",),
        "why": "Needs field performance measurements for this property's pages.",
    },
    "seo_brief_fact_stale": {
        "needs": ("brief_facts",),
        "why": ("Needs a last-edited date per brief field; the brief stores the "
                "values but not when each one was last confirmed."),
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
    _gather_ai_answers(identity, data, gaps)

    if not data.get("pages"):
        gaps.append(_gap("pages", "site_crawl",
                         "No read of this property's website is stored, so page-level "
                         "rules cannot run. A crawl or the vendor's site health "
                         "supplies it."))
    return data, gaps


def _gather_ai_answers(identity: Any, data: Dict[str, Any],
                       gaps: List[Dict[str, str]]) -> None:
    """AI-answer facts, vendor first, then our own tracking, then the audit.

    The vendor measures this for the properties it has a project for — one of
    them today — so the fallbacks are not a nicety. Where both exist the
    vendor wins for AI-answer facts, and every receipt says which one it was.
    """
    props = identity.to_dict() if identity is not None else {}
    domain = props.get("website") or props.get("domain")
    if domain and _merge_vendor(str(domain), data, gaps):
        return
    if props.get("uuid") and _merge_geo(str(props["uuid"]), data, gaps):
        return
    if props.get("uuid") and _merge_ai_mentions(identity, data, gaps):
        return
    gaps.append(_gap("questions", "ai_answer_tracking",
                     "AI answers are not measured for this property yet, so what "
                     "the engines say about it is unknown rather than fine."))


def _merge_vendor(domain: str, data: Dict[str, Any],
                  gaps: List[Dict[str, str]]) -> bool:
    """The vendor connector, when a project exists for this website."""
    try:
        import searchable_client as sc
    except Exception as exc:  # noqa: BLE001 — unconfigured server, not an error
        logger.debug("reco_seo: vendor connector unavailable (%s)", exc)
        return False
    try:
        project = sc.project_for_domain(domain)
    except Exception as exc:  # noqa: BLE001
        logger.info("reco_seo: vendor lookup failed for %s (%s)", domain, exc)
        project = None
    project_id = (project or {}).get("id") if isinstance(project, dict) else project
    if not project_id:
        gaps.append(_gap("questions", "searchable",
                         "No AI-visibility project exists for %s yet; this property "
                         "is not being measured by the vendor." % domain))
        return False

    payload = {}
    for name in ("visibility", "sentiment", "sources", "site_health", "opportunities"):
        reader = getattr(sc, name, None)
        if not callable(reader):
            continue
        try:
            payload[name] = reader(project_id)
        except Exception as exc:  # noqa: BLE001
            logger.info("reco_seo: vendor %s failed for %s (%s)", name, project_id, exc)
    merged = merge_searchable(data, **payload)
    if not merged:
        gaps.append(_gap("questions", "searchable",
                         "The vendor project for %s has no readings yet." % domain))
    return merged


def merge_searchable(data: Dict[str, Any], *, visibility: Any = None,
                     sentiment: Any = None, sources: Any = None,
                     site_health: Any = None, opportunities: Any = None) -> bool:
    """Vendor payloads → the keys the rules read. Returns whether anything landed.

    Kept separate from the connector on purpose: the shape a vendor returns is
    the thing most likely to change, and this is the only place that knows it.
    Anything unrecognized is ignored rather than guessed at.
    """
    landed = False
    _ = opportunities        # read by the queue, not by a rule

    questions = _questions_from_visibility(visibility)
    if questions:
        data["questions"] = questions
        data["questions_source"] = "searchable"
        data["questions_as_of"] = _iso_day((visibility or {}).get("as_of")) or data.get("as_of")
        landed = True

    topics = _topics_from_visibility(visibility)
    if topics:
        data["topic_coverage"] = topics
        data["topic_coverage_source"] = "searchable"
        landed = True

    signals = _fee_signals_from_sentiment(sentiment)
    if signals:
        data["fee_signals"] = signals
        data["fee_signals_source"] = "searchable_sentiment"
        landed = True

    pages = _pages_from_site_health(site_health)
    if pages:
        data["pages"] = pages
        data["pages_source"] = "searchable_site_health"
        landed = True

    formats = _clean_list((sources or {}).get("formats") if isinstance(sources, dict) else None)
    if formats:
        data["cited_formats"] = [{"format": f.get("format") or f.get("type"),
                                  "share": _float(f.get("share"))} for f in formats]
        landed = True
    return landed


def _questions_from_visibility(payload: Any) -> List[Dict[str, Any]]:
    """Per-prompt, per-engine readings.

    A platform with no reading for a prompt comes back as null, and stays null:
    "we did not measure it" is not "the engine left us out".
    """
    if not isinstance(payload, dict):
        return []
    rows = payload.get("prompts") or payload.get("questions") or []
    out: List[Dict[str, Any]] = []
    for row in _clean_list(rows):
        text = str(row.get("text") or row.get("prompt") or "").strip()
        if not text:
            continue
        engines: Dict[str, Dict[str, Any]] = {}
        for entry in _clean_list(row.get("platformBreakdown") or row.get("engines")):
            name = str(entry.get("platform") or entry.get("engine") or "").strip()
            if not name:
                continue
            engines[name] = {"named": entry.get("mentioned"), "cited": entry.get("cited")}
        if isinstance(row.get("engines"), dict):
            for name, state in row["engines"].items():
                engines[str(name)] = {"named": (state or {}).get("named"),
                                      "cited": (state or {}).get("cited")}
        if not engines:
            continue
        topics = row.get("topics") or []
        topic = None
        if topics:
            first = topics[0]
            topic = first.get("name") if isinstance(first, dict) else str(first)
        out.append({"text": text, "topic": topic or row.get("topic"),
                    "intent": row.get("intentCategory") or row.get("intent"),
                    "branded": bool(row.get("isBranded") or row.get("branded")),
                    "engines": engines,
                    "responses": _int((row.get("metrics") or {}).get("totalResponses"))})
    return out


def _topics_from_visibility(payload: Any) -> List[Dict[str, Any]]:
    """Per-topic coverage for this community against comparable ones."""
    if not isinstance(payload, dict):
        return []
    out = []
    for row in _clean_list(payload.get("topics")):
        name = str(row.get("topic") or row.get("name") or "").strip()
        if not name:
            continue
        out.append({"topic": name,
                    "prompts": _int(row.get("prompts") or row.get("promptCount")),
                    "our_mentions": _int(row.get("our_mentions") or row.get("brandMentions")),
                    "competitor_mentions": _int(row.get("competitor_mentions") or
                                                row.get("competitorMentions")),
                    "competitors": row.get("competitors") or []})
    return out


_FEE_WORDS = ("fee", "fees", "cost", "charge", "charges", "expensive", "pricing")


def _fee_signals_from_sentiment(payload: Any) -> List[Dict[str, Any]]:
    """The weaknesses an engine states aloud, narrowed to what it costs."""
    if not isinstance(payload, dict):
        return []
    out = []
    for row in _clean_list(payload.get("weaknesses") or payload.get("negatives")):
        quote = str(row.get("quote") or row.get("text") or "").strip()
        if not quote:
            continue
        if any(word in quote.lower() for word in _FEE_WORDS):
            out.append({"quote": quote, "engine": row.get("platform") or row.get("engine"),
                        "as_of": row.get("as_of") or row.get("date")})
    return out


def _pages_from_site_health(payload: Any) -> List[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    out = []
    for row in _clean_list(payload.get("pages")):
        url = str(row.get("url") or "").strip()
        if not url:
            continue
        page = {"url": url, "title": row.get("title"),
                "meta_description": row.get("metaDescription") or row.get("meta_description"),
                "h1": row.get("h1"), "page_type": row.get("type") or row.get("page_type")}
        for key in ("jsonld_types", "internal_links_in", "headings", "word_count"):
            if key in row:
                page[key] = row[key]
        out.append(page)
    return out


def _merge_geo(uuid: str, data: Dict[str, Any], gaps: List[Dict[str, str]]) -> bool:
    """Our own GEO tracking tables, where the pilot has written rows."""
    try:
        from skills import workspace_visibility as wv
        ctx = type("Ctx", (), {"uuid": uuid, "company_id": data.get("company_id"),
                               "props": {}})()
        local: List[Any] = []
        audit = wv.geo_audit(ctx, local)
    except Exception as exc:  # noqa: BLE001 — tables absent until the pilot runs
        logger.debug("reco_seo: GEO tables unavailable for %s (%s)", uuid, exc)
        return False
    if not audit or not audit.get("prompts"):
        return False
    questions = []
    for row in wv._group_prompts(audit["prompts"]):
        questions.append({"text": row.get("text"), "topic": row.get("topic"),
                          "intent": row.get("intent"), "engines": row.get("engines") or {}})
    if not questions:
        return False
    data["questions"] = questions
    data["questions_source"] = "geo_responses"
    data["fanout"] = [{"query": f.get("query_text"), "engine": f.get("engine"),
                       "count": _int(f.get("n"))} for f in (audit.get("fanout") or [])]
    data["fanout_source"] = "geo_fanout"
    _ = gaps
    return True


def _merge_ai_mentions(identity: Any, data: Dict[str, Any],
                       gaps: List[Dict[str, str]]) -> bool:
    """The weekly mentions audit: citation per prompt, and nothing finer."""
    try:
        from skills import workspace_visibility as wv
        ctx = type("Ctx", (), {"uuid": identity.to_dict().get("uuid"),
                               "company_id": data.get("company_id"), "props": {}})()
        local: List[Any] = []
        rows = wv.ai_mentions_prompts(ctx, local)
    except Exception as exc:  # noqa: BLE001
        logger.debug("reco_seo: mentions audit unavailable (%s)", exc)
        return False
    if not rows:
        return False
    data["questions"] = [{"text": r.get("text"), "topic": r.get("topic"),
                          "intent": r.get("intent"), "engines": r.get("engines") or {}}
                         for r in rows]
    data["questions_source"] = "ai_mentions"
    gaps.append(_gap("questions.named", "ai_mentions",
                     "The mentions audit records whether a page was cited, not "
                     "whether the community was named, so a gap here is measured "
                     "less precisely than the vendor measures it."))
    return True


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
