"""Layer 2 — paid media and website recommendations, from data we already hold.

WHY THIS EXISTS
    The recommendations this portal produced were mostly listing changes: swap a
    photo, refresh an ILS description. Those are the cheapest thing to say and
    the least likely to move a lease. The findings that actually moved money in
    the 2026 audits were digital performance findings — 244 vacant one-bedroom
    and studio units with roughly no ad spend aimed at them, inventory campaigns
    whose every ad pointed at the homepage, a property that ran eight months at
    Google Ads conversion id "0", platform conversions running 9.4x the site's
    own total. Each of those is a fixed rule over data we already have. This
    module is those rules.

FAIR HOUSING IS ABSOLUTE
    Housing is a Special Ad Category on every major ad platform. No rule here
    may EVER propose tightening or widening a radius, ZIP or postal targeting,
    adding or removing an audience, a lookalike, a demographic, an income or age
    segment, or any retargeting or remarketing. Those changes are illegal in
    housing advertising, not merely discouraged. What IS allowed, and what these
    rules propose: budgets, ad scheduling, keywords and negative keywords, ad
    copy and creative, landing pages, bidding strategy, and fixing measurement.
    `fair_housing_refusal()` enforces this on every recommendation before it
    leaves `_rec()`, so a rule cannot emit a targeting action even by accident —
    a violation raises and the rule is reported as skipped. Copy that will reach
    a channel additionally carries `fair_housing_review: true`, which routes it
    through the portal's existing review before publication.

WHAT EACH RULE NEEDS (RPMI: 109 managed properties)
    Coverage decides which rules can run. Google Ads customer id on 77
    properties, GA4 property id on 79, AptIQ availability on 87, the Hyly
    lead-to-lease funnel on only 14. So the rules are built on Google Ads +
    AptIQ + GA4, and the funnel is a bonus that sharpens a number when it is
    there and is never required.

    | rule_key                   | needs                                        |
    |----------------------------|----------------------------------------------|
    | spend_not_on_vacancy       | AptIQ floor plans + Google Ads ad groups     |
    | impression_share_lost      | Google Ads campaigns + AptIQ availability    |
    | wasted_spend               | Google Ads search terms / keywords           |
    | homepage_landing_page      | Google Ads final URLs (+ AptIQ for the plan) |
    | conversion_tracking_broken | Google Ads cost/conversions, GA4 id, tag ids |
    | conversion_overcounting    | Google Ads conversions + GA4 conversions     |
    | dormant_ad_groups          | Google Ads ad groups + AptIQ availability    |
    | creative_gaps              | Google Ads ads and account assets            |
    | budget_pacing              | Google Ads cost + spend_sheet authorization  |

    Every rule returns [] when its inputs are missing. A missing source becomes
    a gap naming the source and the reason — never a zero, never a guess.

DATA SEAMS
    Google Ads reads go through `google_ads_islost._run_gaql`, the existing
    credential-gated seam (the `google-ads` library plus a developer token and
    OAuth). Until that lands, `gather()` records a gap and the six Google Ads
    rules stay silent. GA4 and the tag setup have no connector in this repo at
    all, so they are module-level hooks (`GA4_READER`, `TAG_READER`) that the
    portal sets when those connectors exist; unset, they are gaps too.

OUTPUT
    `run(company_id)` is the only entry point an aggregator needs. Every
    recommendation matches the shared contract that `skills/reco_engine.py`
    validates, including the two things it refuses outright: targeting language
    anywhere in the action, and a spend-increasing action that does not require
    a signed deal. At most one recommendation per rule per property per day, so
    the contract's `id` (`rule_key:company_id:date`) is unique by construction.
"""
from __future__ import annotations

import logging
import re
from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

PRODUCER = "reco_digital"

CATEGORIES = ("cost", "vendors", "content", "creative", "compliance")
SEVERITIES = ("high", "medium", "low")
ACTION_KINDS = ("budget_change", "new_ad_group", "pause", "creative_refresh",
                "tracking_fix", "landing_page", "none")
EXECUTORS = ("ninjacat", "portal", "human")

# Money never moves on a card. `reco_engine` refuses either of these kinds
# without a signed deal, so `_rec` sets it rather than trusting each rule.
SPEND_INCREASING_KINDS = frozenset({"budget_change", "new_ad_group"})

# Rules whose output is internal and must never be rendered to a client.
INTERNAL_ONLY_RULES = frozenset({"budget_pacing"})

# ── Fair Housing ─────────────────────────────────────────────────────────────
# Substrings that only appear when an action steers by audience or geography.
# A superset of reco_engine.FORBIDDEN_ACTION_TERMS (a test pins that), because
# this module must never hand the engine something the engine has to refuse.
FORBIDDEN_TERMS = (
    "radius", "zip", "postal", "audience", "lookalike", "look-alike",
    "remarketing", "remarket", "retarget", "demographic", "age range", "gender",
    "income target", "geo target", "geotarget", "location target", "geofence",
    "proximity", "affinity segment", "in-market", "customer match", "user list",
)


class FairHousingViolation(RuntimeError):
    """A rule tried to emit a targeting change. It is a bug, and it stops here."""


# ── thresholds (the rules, in one place so they can be tuned) ────────────────

WINDOW_DAYS = 30                      # every Google Ads read is a 30-day window

VACANCY_MIN_UNITS = 6                 # a bucket worth its own ad group
VACANCY_MIN_UNITS_HIGH = 15
VACANCY_SPEND_SHARE = 0.05            # "roughly nothing is pointed at it"

IS_LOST_MIN = 0.10                    # budget-lost impression share worth acting on
IS_LOST_HIGH = 0.25

WASTE_MIN_USD = 100.0
WASTE_SHARE_MEDIUM, WASTE_SHARE_HIGH = 0.10, 0.20
DUPLICATE_MIN = 3                     # keywords competing with themselves

HOMEPAGE_MIN_USD = 50.0
HOMEPAGE_SHARE_HIGH = 0.50

TRACKING_MIN_SPEND = 100.0            # spend that should have produced a conversion
OVERCOUNT_RATIO = 2.0
OVERCOUNT_RATIO_HIGH = 4.0
OVERCOUNT_MIN_CONVERSIONS = 10

DORMANT_MIN = 1
DORMANT_MIN_HIGH = 5

RSA_MIN_HEADLINES = 8                 # Google's own "good" threshold
RSA_MIN_DESCRIPTIONS = 3
SITELINK_MIN = 4
CREATIVE_STALE_DAYS = 180

PACE_LOW, PACE_HIGH = 0.75, 1.15
PACE_LOW_HIGH, PACE_HIGH_HIGH = 0.50, 1.30

# How soon a person should start, by severity.
START_BY_DAYS = {"high": 2, "medium": 7, "low": 14}
VERIFY_AFTER_DAYS = 30


# ── small helpers ────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _today(today: Optional[date] = None) -> date:
    return today or date.today()


def _gap(field: str, source: str, message: str) -> Dict[str, str]:
    """A missing source, named. Never a zero in place of an unknown."""
    return {"field": field, "source": source, "message": message}


def _receipt(label: str, value: Any, source: str, as_of: Optional[str]) -> Dict[str, Any]:
    return {"label": label, "value": value, "source": source, "as_of": as_of}


def _f(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(str(value).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None


def _i(value: Any) -> Optional[int]:
    f = _f(value)
    return int(round(f)) if f is not None else None


def _money(value: Optional[float]) -> str:
    return "$%s" % format(int(round(value or 0)), ",")


def _flatten(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(_flatten(k) + " " + _flatten(v) for k, v in value.items())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_flatten(v) for v in value)
    return str(value)


def safe_terms(terms: List[str]) -> Tuple[List[str], int]:
    """Drop observed terms that contain targeting language, and count them.

    Search terms and keywords are DATA, not proposals — but they travel in
    action params, and a single renter searching "apartments 77002 zip" would
    otherwise make the whole recommendation look like a targeting change and get
    it refused. Withheld terms are reported as a count, so nothing is hidden.
    """
    kept, withheld = [], 0
    for term in terms:
        text = str(term or "").lower()
        if any(bad in text for bad in FORBIDDEN_TERMS):
            withheld += 1
            continue
        kept.append(term)
    return kept, withheld


# ── bedroom buckets: the join between availability and ad structure ──────────

BUCKETS = ("studio", "1_bed", "2_bed", "3_bed", "4_plus")
BUCKET_LABELS = {"studio": "studio", "1_bed": "one-bedroom", "2_bed": "two-bedroom",
                 "3_bed": "three-bedroom", "4_plus": "four-bedroom and larger"}

_BUCKET_PATTERNS = (
    ("studio", re.compile(r"\b(studio|efficiency|0\s*(?:bed|br|bd))\b")),
    ("1_bed", re.compile(r"\b(1\s*(?:bed|br|bd|bedroom)s?|one\s*bed(?:room)?s?|1br|1-bed)\b")),
    ("2_bed", re.compile(r"\b(2\s*(?:bed|br|bd|bedroom)s?|two\s*bed(?:room)?s?|2br|2-bed)\b")),
    ("3_bed", re.compile(r"\b(3\s*(?:bed|br|bd|bedroom)s?|three\s*bed(?:room)?s?|3br|3-bed)\b")),
    ("4_plus", re.compile(r"\b(4\+?\s*(?:bed|br|bd|bedroom)s?|four\s*bed(?:room)?s?|4br)\b")),
)


def bucket_for_beds(beds: Any) -> Optional[str]:
    b = _i(beds)
    if b is None:
        return None
    if b <= 0:
        return "studio"
    return {1: "1_bed", 2: "2_bed", 3: "3_bed"}.get(b, "4_plus")


def buckets_in_text(*texts: Any) -> set:
    """Which bedroom buckets a campaign, ad group or URL names.

    Ad-group naming is a proxy, not a fact, which is why rules built on it carry
    a lower confidence than rules built on measured numbers.
    """
    joined = " ".join(str(t or "") for t in texts).lower().replace("_", " ")
    return {name for name, pattern in _BUCKET_PATTERNS if pattern.search(joined)}


# ── the recommendation builder (the only way a rule may emit) ────────────────

def fair_housing_refusal(reco: Dict[str, Any]) -> Optional[str]:
    """Why this recommendation must never be shown, or None. Same test the
    downstream engine applies, run here first so a bug never leaves the module."""
    action = reco.get("action") or {}
    params = action.get("params") or {}
    haystack = " ".join([
        str(action.get("kind") or ""), str(reco.get("found") or ""),
        str(reco.get("expect") or ""), str(reco.get("if_skip") or ""),
        " ".join(str(k) for k in params),
        " ".join(_flatten(v) for v in params.values()),
    ]).lower()
    for term in FORBIDDEN_TERMS:
        if term in haystack:
            return ("would change audience or geographic targeting (%r); housing is "
                    "a Special Ad Category" % term)
    if action.get("kind") in SPEND_INCREASING_KINDS and not action.get("requires_signed_deal"):
        return "would change spend without requiring a signed deal"
    return None


def _rec(ctx: "DigitalContext", today: date, *, rule_key: str, category: str,
         channels: List[str], severity: str, confidence: int, found: str,
         receipts: List[Dict[str, Any]], expect: str, if_skip: str,
         kind: str, params: Dict[str, Any], executor: str,
         fair_housing_review: bool, verify_metric: str,
         requires_signed_deal: Optional[bool] = None) -> Dict[str, Any]:
    """Build one recommendation in the shared contract, or refuse to build it."""
    if category not in CATEGORIES:
        raise ValueError("unknown category %r" % category)
    if severity not in SEVERITIES:
        raise ValueError("unknown severity %r" % severity)
    if kind not in ACTION_KINDS:
        raise ValueError("unknown action kind %r" % kind)
    if executor not in EXECUTORS:
        raise ValueError("unknown executor %r" % executor)
    confidence = int(confidence)
    if not 1 <= confidence <= 10:
        raise ValueError("confidence %s out of range" % confidence)
    if kind != "none" and not receipts:
        raise ValueError("%s: an action with no receipts" % rule_key)
    for receipt in receipts:
        if not receipt.get("source"):
            raise ValueError("%s: a receipt with no source" % rule_key)

    if requires_signed_deal is None:
        requires_signed_deal = kind in SPEND_INCREASING_KINDS
    if kind in SPEND_INCREASING_KINDS:
        requires_signed_deal = True          # never optional; money moves on a signature

    reco = {
        "id": "%s:%s:%s" % (rule_key, ctx.company_id, today.isoformat()),
        "company_id": ctx.company_id,
        "property_name": ctx.property_name,
        "rule_key": rule_key,
        "category": category,
        "channels": list(channels),
        "severity": severity,
        "confidence": confidence,
        "found": found,
        "receipts": receipts,
        "expect": expect,
        "if_skip": if_skip,
        "action": {
            "kind": kind,
            "params": params,
            "executor": executor,
            "requires_signed_deal": bool(requires_signed_deal),
            "fair_housing_review": bool(fair_housing_review),
        },
        "start_by": (today + timedelta(days=START_BY_DAYS[severity])).isoformat(),
        "verify": {"metric": verify_metric,
                   "when": (today + timedelta(days=VERIFY_AFTER_DAYS)).isoformat()},
    }
    refusal = fair_housing_refusal(reco)
    if refusal:
        raise FairHousingViolation("%s %s" % (rule_key, refusal))
    return reco


# ── the resolved input bundle every rule reads ───────────────────────────────

class DigitalContext(object):
    """Everything the rules read, already joined, with gaps for what is missing.

    Built by `gather()` from live sources, or constructed directly in a test or
    by a caller that already holds the data. A source that could not be reached
    leaves its section empty and adds a gap — no section is ever half-invented.
    """

    def __init__(self, company_id: str, *, property_name: Optional[str] = None,
                 uuid: Optional[str] = None, domain: Optional[str] = None,
                 units: Optional[int] = None,
                 ads: Optional[Dict[str, Any]] = None,
                 availability: Optional[Dict[str, Any]] = None,
                 ga4: Optional[Dict[str, Any]] = None,
                 tags: Optional[Dict[str, Any]] = None,
                 funnel: Optional[Dict[str, Any]] = None,
                 authorized: Optional[Dict[str, Any]] = None,
                 window: Optional[Dict[str, Any]] = None,
                 gaps: Optional[List[Dict[str, str]]] = None):
        self.company_id = str(company_id)
        self.property_name = property_name
        self.uuid = uuid
        self.domain = domain
        self.units = units
        self.ads = ads or {}
        self.availability = availability or {}
        self.ga4 = ga4 or {}
        self.tags = tags or {}
        self.funnel = funnel or {}
        self.authorized = authorized or {}
        self.window = window or {"days": WINDOW_DAYS}
        self.gaps = list(gaps or [])

    # -- Google Ads -----------------------------------------------------------
    @property
    def ads_as_of(self) -> Optional[str]:
        return self.ads.get("as_of")

    def ads_rows(self, kind: str) -> List[Dict[str, Any]]:
        return list(self.ads.get(kind) or [])

    @property
    def ads_cost(self) -> Optional[float]:
        """Total measured cost in the window, or None when Google Ads is unread."""
        campaigns = self.ads_rows("campaigns")
        if campaigns:
            return round(sum(_f(c.get("cost")) or 0.0 for c in campaigns), 2)
        groups = self.ads_rows("ad_groups")
        if groups:
            return round(sum(_f(g.get("cost")) or 0.0 for g in groups), 2)
        return None

    @property
    def ads_conversions(self) -> Optional[float]:
        campaigns = self.ads_rows("campaigns")
        if not campaigns:
            return None
        return round(sum(_f(c.get("conversions")) or 0.0 for c in campaigns), 2)

    # -- AptIQ ----------------------------------------------------------------
    def floor_plans(self) -> List[Dict[str, Any]]:
        return list(self.availability.get("floor_plans") or [])

    @property
    def available_units(self) -> Optional[int]:
        plans = self.floor_plans()
        if plans:
            return sum(_i(p.get("available_units")) or 0 for p in plans)
        return _i(self.availability.get("available_units"))

    def vacancy_by_bucket(self) -> "OrderedDict[str, Dict[str, Any]]":
        """{bucket: {units, plans, monthly_rent}} from the floor-plan export."""
        out: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        for plan in self.floor_plans():
            bucket = bucket_for_beds(plan.get("beds"))
            units = _i(plan.get("available_units")) or 0
            if not bucket or units <= 0:
                continue
            entry = out.setdefault(bucket, {"units": 0, "plans": [], "monthly_rent": 0.0,
                                            "rent_known": True})
            entry["units"] += units
            entry["plans"].append(plan.get("name") or bucket)
            rent = _f(plan.get("asking_rent"))
            if rent is None:
                entry["rent_known"] = False
            else:
                entry["monthly_rent"] += rent * units
        return out


# ══════════════════════════════════════════════════════════════════════════════
# RULES — each one fires on real-shaped input and returns [] when inputs are
# missing. At most one recommendation per rule, so the contract id is unique.
# ══════════════════════════════════════════════════════════════════════════════

def spend_not_on_vacancy(ctx: DigitalContext, today: date) -> List[Dict[str, Any]]:
    """Vacant units in floor plans no ad group is pointed at.

    The highest-value rule in the set. The Hyly pilot found 244 vacant
    one-bedroom and studio units at one property carrying roughly no spend aimed
    at them — about $402k of monthly rent with nothing advertising it. Vacancy
    comes from the AptIQ floor-plan export; where spend points comes from ad
    group and campaign names, their keywords and their landing paths.
    """
    rule_key = "spend_not_on_vacancy"
    vacancy = ctx.vacancy_by_bucket()
    groups = ctx.ads_rows("ad_groups")
    total_cost = ctx.ads_cost
    if not vacancy or not groups or not total_cost:
        return []

    keywords_by_group: Dict[str, List[str]] = {}
    for kw in ctx.ads_rows("keywords"):
        keywords_by_group.setdefault(str(kw.get("ad_group_name") or ""), []).append(
            str(kw.get("keyword") or ""))

    spend_by_bucket: Dict[str, float] = {b: 0.0 for b in BUCKETS}
    attributed = 0.0
    for group in groups:
        cost = _f(group.get("cost")) or 0.0
        if cost <= 0:
            continue
        name = group.get("ad_group_name") or ""
        found_buckets = buckets_in_text(
            name, group.get("campaign_name"), " ".join(group.get("final_urls") or []),
            " ".join(keywords_by_group.get(str(name), [])))
        if len(found_buckets) != 1:
            continue                         # ambiguous or general — credited to nobody
        bucket = found_buckets.pop()
        spend_by_bucket[bucket] += cost
        attributed += cost

    starved = [(data["units"], bucket, data) for bucket, data in vacancy.items()
               if data["units"] >= VACANCY_MIN_UNITS
               and spend_by_bucket.get(bucket, 0.0) <= total_cost * VACANCY_SPEND_SHARE]
    if not starved:
        return []
    starved.sort(reverse=True)
    units_total = sum(u for u, _, _ in starved)
    labels = ", ".join(BUCKET_LABELS[b] for _, b, _ in starved[:3])
    as_of = ctx.availability.get("as_of")

    rent = sum(d["monthly_rent"] for _, _, d in starved if d["rent_known"])
    rent_known = all(d["rent_known"] for _, _, d in starved) and rent > 0

    severity = "high" if units_total >= VACANCY_MIN_UNITS_HIGH else "medium"
    confidence = 8 if attributed >= total_cost * 0.5 else 6
    pointed = sum(spend_by_bucket.get(b, 0.0) for _, b, _ in starved)

    receipts = [
        _receipt("Vacant units in these plans", units_total, "aptiq_floor_plans", as_of),
        _receipt("Plans", ", ".join(p for _, _, d in starved for p in d["plans"][:6]),
                 "aptiq_floor_plans", as_of),
        _receipt("Spend pointed at those plans (30 days)", _money(pointed),
                 "google_ads", ctx.ads_as_of),
        _receipt("Total paid search spend (30 days)", _money(total_cost),
                 "google_ads", ctx.ads_as_of),
    ]
    if rent_known:
        receipts.append(_receipt("Monthly rent sitting in those plans", _money(rent),
                                 "aptiq_floor_plans", as_of))

    expect = ("Ad groups built for these plans, funded from the %s already running, put "
              "paid traffic in front of the units that are actually empty."
              % _money(total_cost))
    if rent_known:
        expect = ("Ad groups built for these plans put paid traffic in front of %s of "
                  "monthly rent that currently has %s of spend behind it."
                  % (_money(rent), _money(pointed)))
    return [_rec(
        ctx, today, rule_key=rule_key, category="cost", channels=["paid_search"],
        severity=severity, confidence=confidence,
        found=("%d vacant %s units have %s of the %s in paid search behind them."
               % (units_total, labels, _money(pointed), _money(total_cost))),
        receipts=receipts, expect=expect,
        if_skip=("These units keep renting on whatever finds them, while the budget "
                 "keeps working on plans that are already leased."),
        kind="new_ad_group",
        params={"floor_plan_buckets": [b for _, b, _ in starved],
                "plans": [p for _, _, d in starved for p in d["plans"]],
                "vacant_units": units_total,
                "funding": "reallocate within the authorized paid search budget",
                "current_spend_on_those_plans_usd": round(pointed, 2)},
        executor="ninjacat", fair_housing_review=True,
        verify_metric="paid search clicks and leads for these floor plans")]


def impression_share_lost(ctx: DigitalContext, today: date) -> List[Dict[str, Any]]:
    """Budget-capped search campaigns at a property that still has vacancy.

    Budget-lost impression share says how much of the available demand the
    budget never reached. Quantified in clicks at the campaign's own CPC —
    leads only when the leasing funnel exists for the property, because platform
    conversions have overstated verified leads by up to 9x here.
    """
    rule_key = "impression_share_lost"
    campaigns = [c for c in ctx.ads_rows("campaigns")
                 if _f(c.get("search_budget_lost_is")) is not None
                 and (_f(c.get("cost")) or 0) > 0]
    vacant = ctx.available_units
    if not campaigns or not vacant:
        return []

    cost = sum(_f(c.get("cost")) or 0.0 for c in campaigns)
    clicks = sum(_f(c.get("clicks")) or 0.0 for c in campaigns)
    if cost <= 0 or clicks <= 0:
        return []
    lost = sum((_f(c.get("search_budget_lost_is")) or 0.0) * (_f(c.get("cost")) or 0.0)
               for c in campaigns) / cost          # cost-weighted, not a flat average
    lost = min(max(lost, 0.0), 0.95)
    if lost < IS_LOST_MIN:
        return []

    cpc = cost / clicks
    extra_clicks = clicks * lost / (1.0 - lost)
    extra_cost = extra_clicks * cpc
    severity = "high" if (lost >= IS_LOST_HIGH and vacant >= VACANCY_MIN_UNITS) else "medium"

    receipts = [
        _receipt("Search impression share lost to budget", "%d%%" % round(lost * 100),
                 "google_ads", ctx.ads_as_of),
        _receipt("Spend in the window", _money(cost), "google_ads", ctx.ads_as_of),
        _receipt("Clicks in the window", int(clicks), "google_ads", ctx.ads_as_of),
        _receipt("Average cost per click", "$%.2f" % cpc, "google_ads", ctx.ads_as_of),
        _receipt("Units available now", vacant, "aptiq",
                 ctx.availability.get("as_of")),
    ]
    lead_line = ""
    leads = _f(ctx.funnel.get("leads"))
    funnel_clicks = _f(ctx.funnel.get("clicks"))
    if leads and funnel_clicks:
        rate = leads / funnel_clicks
        lead_line = (" At this property's measured lead rate that is about %d more leads."
                     % round(extra_clicks * rate))
        receipts.append(_receipt("Leads per click (verified funnel)", round(rate, 3),
                                 "hyly_rollup", ctx.funnel.get("as_of")))

    return [_rec(
        ctx, today, rule_key=rule_key, category="cost", channels=["paid_search"],
        severity=severity, confidence=9,
        found=("Search is losing %d%% of available impressions to budget while %d units "
               "are available." % (round(lost * 100), vacant)),
        receipts=receipts,
        expect=("About %d more clicks a month at the current $%.2f cost per click, for "
                "roughly %s more spend.%s"
                % (round(extra_clicks), cpc, _money(extra_cost), lead_line)),
        if_skip=("The campaigns keep stopping early each day and the demand that is "
                 "already searching goes to whoever is still bidding."),
        kind="budget_change",
        params={"channel": "paid_search",
                "campaigns": [c.get("campaign_name") for c in campaigns],
                "current_monthly_spend_usd": round(cost, 2),
                "additional_monthly_spend_usd": round(extra_cost, 2),
                "basis": "search impression share lost to budget"},
        executor="ninjacat", fair_housing_review=False,
        verify_metric="search impression share lost to budget")]


def wasted_spend(ctx: DigitalContext, today: date) -> List[Dict[str, Any]]:
    """Spend that bought no lead: dead search terms, dead keywords, duplicates.

    Three findings share one recommendation because they share one fix — a
    negatives and keyword pass. Keywords and negatives are allowed in a Special
    Ad Category; they are about what the renter typed, not who they are.
    """
    rule_key = "wasted_spend"
    terms = ctx.ads_rows("search_terms")
    keywords = ctx.ads_rows("keywords")
    total_cost = ctx.ads_cost
    if not (terms or keywords) or not total_cost:
        return []

    dead_terms = sorted(
        [t for t in terms if (_f(t.get("cost")) or 0) > 0
         and (_f(t.get("conversions")) or 0) == 0],
        key=lambda t: -(_f(t.get("cost")) or 0))
    dead_keywords = sorted(
        [k for k in keywords if (_f(k.get("cost")) or 0) > 0
         and (_f(k.get("conversions")) or 0) == 0],
        key=lambda k: -(_f(k.get("cost")) or 0))

    seen: Dict[str, set] = {}
    for kw in keywords:
        text = str(kw.get("keyword") or "").strip().lower()
        if text:
            seen.setdefault(text, set()).add(str(kw.get("ad_group_name") or ""))
    duplicates = sorted(text for text, groups in seen.items() if len(groups) > 1)

    wasted = sum(_f(t.get("cost")) or 0.0 for t in dead_terms) or \
        sum(_f(k.get("cost")) or 0.0 for k in dead_keywords)
    share = wasted / total_cost if total_cost else 0.0
    if wasted < WASTE_MIN_USD and len(duplicates) < DUPLICATE_MIN:
        return []

    severity = ("high" if share >= WASTE_SHARE_HIGH else
                "medium" if share >= WASTE_SHARE_MEDIUM else "low")
    term_names, term_withheld = safe_terms(
        [str(t.get("search_term") or "") for t in dead_terms[:25]])
    kw_names, kw_withheld = safe_terms(
        [str(k.get("keyword") or "") for k in dead_keywords[:25]])
    dup_names, dup_withheld = safe_terms(duplicates[:25])

    receipts = [_receipt("Spend on searches that produced no lead", _money(wasted),
                         "google_ads", ctx.ads_as_of),
                _receipt("Share of total spend", "%d%%" % round(share * 100),
                         "google_ads", ctx.ads_as_of)]
    if dead_terms:
        receipts.append(_receipt("Searches with spend and no lead", len(dead_terms),
                                 "google_ads_search_terms", ctx.ads_as_of))
        top = dead_terms[0]
        safe_top, _ = safe_terms([str(top.get("search_term") or "")])
        if safe_top:
            receipts.append(_receipt("Most expensive of them",
                                     "%s — %s" % (safe_top[0], _money(_f(top.get("cost")))),
                                     "google_ads_search_terms", ctx.ads_as_of))
    if dead_keywords:
        receipts.append(_receipt("Keywords with spend and no lead", len(dead_keywords),
                                 "google_ads_keywords", ctx.ads_as_of))
    if duplicates:
        receipts.append(_receipt("Keywords competing with themselves across ad groups",
                                 len(duplicates), "google_ads_keywords", ctx.ads_as_of))

    return [_rec(
        ctx, today, rule_key=rule_key, category="cost", channels=["paid_search"],
        severity=severity, confidence=9,
        found=("%s of search spend — %d%% of the total — bought clicks that produced no "
               "lead." % (_money(wasted), round(share * 100))),
        receipts=receipts,
        expect=("A negatives and keyword pass returns about %s a month to searches that "
                "do convert, at the same total budget." % _money(wasted)),
        if_skip=("The same searches keep billing every month and the reported cost per "
                 "lead stays higher than the campaigns deserve."),
        kind="pause",
        params={"add_negative_keywords": term_names,
                "pause_keywords": kw_names,
                "duplicate_keywords": dup_names,
                "terms_withheld_for_review": term_withheld + kw_withheld + dup_withheld,
                "estimated_monthly_recovery_usd": round(wasted, 2)},
        executor="ninjacat", fair_housing_review=True,
        verify_metric="paid search cost per lead")]


# ── registry and entry point ─────────────────────────────────────────────────

RULES: "OrderedDict[str, Callable[[DigitalContext, date], List[Dict[str, Any]]]]" = OrderedDict((
    ("spend_not_on_vacancy", spend_not_on_vacancy),
    ("impression_share_lost", impression_share_lost),
    ("wasted_spend", wasted_spend),
))


def run(company_id: str, *, today: Optional[date] = None,
        rules: Optional[List[str]] = None,
        context: Optional[DigitalContext] = None) -> Dict[str, Any]:
    """Every digital rule for one property. Never raises.

    Returns {"company_id", "recommendations", "gaps", "rules_run",
    "rules_skipped"}. Each rule runs on its own, so one dead credential or one
    bad row costs that rule and nothing else; the failure is reported as a
    skipped rule plus a gap rather than an empty, confident answer.
    """
    day = _today(today)
    recommendations: List[Dict[str, Any]] = []
    rules_run: List[str] = []
    rules_skipped: List[Dict[str, str]] = []

    if context is None:
        try:
            context = gather(company_id, today=day)
        except Exception as exc:  # noqa: BLE001 — a lookup failure is a gap, not a crash
            logger.error("reco_digital: gather failed for %s: %s", company_id, exc,
                         exc_info=True)
            return {"company_id": str(company_id), "recommendations": [],
                    "gaps": [_gap("recommendations", PRODUCER,
                                  "The property's data could not be read, so no digital "
                                  "rule ran.")],
                    "rules_run": [], "as_of": _now_iso(),
                    "rules_skipped": [{"rule_key": key, "reason": "no data"}
                                      for key in RULES]}
    gaps = list(context.gaps)

    for rule_key, rule in RULES.items():
        if rules and rule_key not in rules:
            continue
        try:
            produced = rule(context, day) or []
        except FairHousingViolation as exc:
            logger.error("reco_digital: %s refused on Fair Housing: %s", rule_key, exc)
            rules_skipped.append({"rule_key": rule_key,
                                  "reason": "refused on Fair Housing grounds"})
            gaps.append(_gap(rule_key, PRODUCER,
                             "This rule produced a change housing advertising does not "
                             "allow, so it was withheld."))
        except Exception as exc:  # noqa: BLE001 — one rule must not kill the rest
            logger.error("reco_digital: %s failed for %s: %s", rule_key, company_id, exc,
                         exc_info=True)
            rules_skipped.append({"rule_key": rule_key,
                                  "reason": "could not run (%s)" % type(exc).__name__})
            gaps.append(_gap(rule_key, PRODUCER, "This rule could not complete its run."))
        else:
            rules_run.append(rule_key)
            recommendations.extend(produced)

    return {"company_id": str(company_id), "recommendations": recommendations,
            "gaps": gaps, "rules_run": rules_run, "rules_skipped": rules_skipped,
            "as_of": _now_iso()}


def gather(company_id: str, *, today: Optional[date] = None) -> DigitalContext:
    """Placeholder until the live readers land in this file (pass 3)."""
    return DigitalContext(str(company_id))
