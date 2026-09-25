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
    | impression_share_lost      | Google Ads campaigns + AptIQ availability +  |
    |                            | Red Light status + the authorized budget     |
    | wasted_spend               | Google Ads search terms / keywords           |
    | homepage_landing_page      | Google Ads final URLs (+ AptIQ for the plan) |
    | conversion_tracking_broken | Google Ads cost/conversions, GA4 id, tag ids |
    | conversion_overcounting    | Google Ads conversions + GA4 conversions     |
    | dormant_ad_groups          | Google Ads ad groups + AptIQ availability    |
    | creative_gaps              | Google Ads ads and account assets            |
    | budget_pacing              | Google Ads cost + the authorized SKUs         |
    |                            | (INTERNAL ONLY — never shown to a client)    |

    Every rule returns [] when its inputs are missing. A missing source becomes
    a gap naming the source and the reason — never a zero, never a guess.

WHAT THIS MODULE REUSES RATHER THAN REBUILDS
    * `impression_share_lost` is a WRAPPER around `recommendation_gen`, which
      already owns that decision: its `Guardrails` (10% minimum loss, +50%
      maximum step, $10,000 ceiling) and its recovery math decide whether there
      is a recommendation and what the budget should be. This module adds only
      what that core does not know — that the property still has vacancy, and
      what the extra budget buys in clicks — and maps the result into the shared
      contract. `google_ads_islost.parse_islost` does the aggregation.
    * Any budget step is additionally bounded by `loop_autopilot`'s existing
      caps, `MAX_PERCENT_OF_CHANNEL` and `MAX_ABSOLUTE_AMOUNT`. They are
      imported, never redefined.
    * `budget_pacing` imports its bands from `workspace_signals.spend_pacing`
      rather than restating them, so the ranked queue and the signals page
      cannot disagree about what off pace means. It exists here because the two
      differ in measurement and surface: signals reads delivered spend from the
      NinjaCat feed, which has missed real Google Ads spend before, and lands on
      the signals page; this reads cost from the Google Ads API and lands in the
      queue, which has no signals adapter.
    * Occupancy drop, stale inventory, lease wave, lead drop and data staleness
      are `workspace_signals` rules. Nothing here duplicates them.

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

# Rules whose output is internal and must never be rendered to a client. The
# aggregator reads this, so an internal rule declares itself rather than relying
# on a reviewer noticing.
INTERNAL_ONLY_RULES = frozenset({"budget_pacing"})

# The paid search SKU on the signed deal, and the channel name
# `recommendation_gen` reasons over. `geofence` is deliberately not here: it is a
# targeting product, and nothing in this module may propose a change to one.
PAID_SEARCH_SKU = "search"

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

# Severity only. Whether there is a recommendation at all is
# `recommendation_gen.Guardrails.min_is_lost_pct`, which is the same 10% and is
# not restated here.
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

# Pacing bands are NOT redefined here. `workspace_signals.spend_pacing` owns
# them, and budget_pacing imports them, so the two surfaces cannot drift apart.

# How soon a person should start, by severity.
START_BY_DAYS = {"high": 2, "medium": 7, "low": 14}
VERIFY_AFTER_DAYS = 30


# ── small helpers ────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _today(today: Optional[date] = None) -> date:
    return today or date.today()


def _quarter(today: date) -> str:
    """`recommendation_gen`'s period bucket, so its idempotency key matches the
    one the self-checkout path already writes."""
    return "%d-Q%d" % (today.year, (today.month - 1) // 3 + 1)


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


_HOMEPAGE_PATHS = frozenset({"", "index", "index.html", "index.php", "home", "default"})

# A tag id that was cloned and never filled in: empty, zero, or still the
# template's own placeholder. Park 5 ran eight months at Google Ads id "0".
_PLACEHOLDER_ID = re.compile(r"^(?:aw|g|gtm|ua|)-?0*$|x{3,}|000000", re.IGNORECASE)


def is_homepage_url(url: Any) -> bool:
    """True when this final URL is the site root rather than a real page."""
    from urllib.parse import urlparse

    text = str(url or "").strip()
    if not text:
        return False
    parsed = urlparse(text if "//" in text else "//" + text)
    path = (parsed.path or "").strip("/").lower()
    if path not in _HOMEPAGE_PATHS:
        return False
    # A tracking query string does not make the homepage a floor-plan page, but
    # a real anchor or a search query might, so only a bare root counts.
    return not (parsed.fragment or "").strip("/")


def is_placeholder_id(value: Any) -> bool:
    if value is None:
        return True
    text = str(value).strip()
    if not text:
        return True
    return bool(_PLACEHOLDER_ID.search(text))


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
                 marketing_status: Optional[str] = None,
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
        # Red Light marketing status (RED / YELLOW / GREEN). It is
        # `recommendation_gen`'s trigger, not ours.
        self.marketing_status = marketing_status
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


def _islost_rows(campaigns: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Ad rows in the shape `google_ads_islost.parse_islost` already aggregates."""
    return [{"channel_type": (c.get("channel_type") or "SEARCH"),
             "budget_lost_is": _f(c.get("search_budget_lost_is"))}
            for c in campaigns]


def impression_share_lost(ctx: DigitalContext, today: date) -> List[Dict[str, Any]]:
    """Budget-capped search campaigns at a property that still has vacancy.

    A WRAPPER, not a second implementation. `recommendation_gen` already owns
    this decision — its `Guardrails` (10% minimum loss, +50% maximum step,
    $10,000 ceiling) and its recovery math decide whether there is a
    recommendation and what the budget should be — and
    `google_ads_islost.parse_islost` already aggregates the metric. This rule
    supplies the inputs, adds the two things that core cannot know (that the
    property still has units to lease, and what the extra budget buys in clicks
    at the account's own CPC), bounds the first step by `loop_autopilot`'s
    existing caps, and maps the result into the shared contract.

    Leads are quantified only when the leasing funnel exists for the property:
    platform conversions have overstated verified leads by up to 9x here.
    """
    import google_ads_islost as gads
    import recommendation_gen as rg
    # Autopilot's bound on a single step, imported so there is one definition.
    from loop_autopilot import MAX_ABSOLUTE_AMOUNT, MAX_PERCENT_OF_CHANNEL

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
    lost = (gads.parse_islost(_islost_rows(campaigns)) or {}).get("paid_search")
    if lost is None:
        return []

    # The existing decision core. Its current budget is the authorized paid
    # search amount from the signed deal; the Red Light status is its trigger.
    # Where either is unknown, the account's own measured spend and a YELLOW-
    # equivalent "the budget is capped" state stand in — never a bigger number
    # than the guardrails allow either way.
    authorized = _f((ctx.authorized.get("by_sku") or {}).get(PAID_SEARCH_SKU))
    signal = rg.ChannelSignal(
        channel=PAID_SEARCH_SKU,
        current_budget=authorized if authorized else round(cost, 2),
        impression_share_lost_pct=lost,
        marketing_status=(ctx.marketing_status or "YELLOW").upper(),
        active=True,
    )
    base = rg.recommend_for_channel(ctx.uuid, ctx.company_id, signal, _quarter(today))
    if base is None:                      # a guardrail said no; that answer stands
        return []

    first_step = round(min(base.delta,
                           signal.current_budget * MAX_PERCENT_OF_CHANNEL,
                           MAX_ABSOLUTE_AMOUNT), 2)
    if first_step <= 0:
        return []

    cpc = cost / clicks
    extra_clicks = first_step / cpc
    extra_cost = first_step
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
                "basis": "search impression share lost to budget",
                # What the existing core decided, carried through unchanged so
                # the two surfaces propose the same number for the same property.
                "decided_by": "recommendation_gen.recommend_for_channel",
                "current_budget": base.current_budget,
                "recommended_budget": base.recommended_budget,
                "full_delta_usd": base.delta,
                "capped_by": list(base.capped_by),
                "first_step_usd": first_step,
                "step_bound": ("loop_autopilot: %d%% of the channel, %s absolute"
                               % (round(MAX_PERCENT_OF_CHANNEL * 100),
                                  _money(MAX_ABSOLUTE_AMOUNT))),
                "recommendation_id": base.recommendation_id},
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


def homepage_landing_page(ctx: DigitalContext, today: date) -> List[Dict[str, Any]]:
    """Ads that spend on a floor plan and land the renter on the homepage.

    Found on the Chicago townhome lease-ups: the inventory campaigns existed,
    every ad pointed at the site root, and the renter had to start the search
    again. Landing-page alignment is an allowed optimization in a Special Ad
    Category — it changes where a click lands, not who sees the ad.
    """
    rule_key = "homepage_landing_page"
    groups = [g for g in ctx.ads_rows("ad_groups") if (_f(g.get("cost")) or 0) > 0]
    if not groups:
        return []
    total_cost = ctx.ads_cost or 0.0

    offenders = []
    for group in groups:
        urls = [u for u in (group.get("final_urls") or []) if u]
        if not urls or not all(is_homepage_url(u) for u in urls):
            continue
        name = group.get("ad_group_name") or ""
        buckets = buckets_in_text(name, group.get("campaign_name"))
        offenders.append({"ad_group": name, "campaign": group.get("campaign_name"),
                          "cost": _f(group.get("cost")) or 0.0,
                          "current_url": urls[0],
                          "names_a_floor_plan": bool(buckets),
                          "floor_plan_buckets": sorted(buckets)})
    if not offenders:
        return []
    homepage_cost = sum(o["cost"] for o in offenders)
    if homepage_cost < HOMEPAGE_MIN_USD:
        return []

    specific = [o for o in offenders if o["names_a_floor_plan"]]
    share = homepage_cost / total_cost if total_cost else 0.0
    severity = "high" if (specific or share >= HOMEPAGE_SHARE_HIGH) else "medium"
    confidence = 9 if specific else 7
    offenders.sort(key=lambda o: -o["cost"])

    receipts = [
        _receipt("Ad groups landing on the homepage", len(offenders), "google_ads",
                 ctx.ads_as_of),
        _receipt("Spend behind them (30 days)", _money(homepage_cost), "google_ads",
                 ctx.ads_as_of),
        _receipt("Largest of them",
                 "%s — %s to %s" % (offenders[0]["ad_group"],
                                    _money(offenders[0]["cost"]),
                                    offenders[0]["current_url"]),
                 "google_ads", ctx.ads_as_of),
    ]
    if specific:
        receipts.append(_receipt("Of those, ad groups named for a floor plan",
                                 len(specific), "google_ads", ctx.ads_as_of))
    if ctx.floor_plans():
        receipts.append(_receipt("Floor plans with units available now",
                                 len([p for p in ctx.floor_plans()
                                      if (_i(p.get("available_units")) or 0) > 0]),
                                 "aptiq_floor_plans", ctx.availability.get("as_of")))

    return [_rec(
        ctx, today, rule_key=rule_key, category="content",
        channels=["paid_search", "website"], severity=severity, confidence=confidence,
        found=("%s of search spend sends renters to the homepage instead of the plan or "
               "availability page the ad promised." % _money(homepage_cost)),
        receipts=receipts,
        expect=("Each ad group points at the page for what it advertises, so the renter "
                "lands on the units they searched for instead of starting again."),
        if_skip=("The click is paid for twice: once to bring the renter in, and again in "
                 "the ones who leave rather than search the site themselves."),
        kind="landing_page",
        params={"ad_groups": [{"ad_group": o["ad_group"], "campaign": o["campaign"],
                               "current_url": o["current_url"],
                               "floor_plan_buckets": o["floor_plan_buckets"]}
                              for o in offenders[:25]],
                "target": "the matching floor-plan or availability page",
                "candidate_paths": ["/floorplans", "/availability", "/apartments"],
                "verify_target_exists": True},
        executor="ninjacat", fair_housing_review=False,
        verify_metric="paid search bounce rate and floor-plan page views")]


def conversion_tracking_broken(ctx: DigitalContext, today: date) -> List[Dict[str, Any]]:
    """Spend measured against nothing: no conversions, no GA4 id, placeholder tags.

    One property ran eight months at Google Ads conversion id "0" because cloned
    tag containers keep their placeholders and nothing checked. Until this is
    fixed, every other number for the property is unsafe to quote.
    """
    rule_key = "conversion_tracking_broken"
    cost = ctx.ads_cost
    conversions = ctx.ads_conversions
    as_of = ctx.ads_as_of
    defects: List[Dict[str, Any]] = []

    if cost is not None and cost >= TRACKING_MIN_SPEND and conversions == 0:
        defects.append({"what": "spend with no tracked conversion", "severity": "high",
                        "receipt": _receipt("Spend with zero recorded conversions",
                                            _money(cost), "google_ads", as_of)})
    # A missing GA4 id is only a finding once there is spend it should be
    # measuring. With no ad account read at all it is a coverage gap, which
    # `gather` already reports — not a card telling somebody to fix nothing.
    if cost is not None and "property_id" in ctx.ga4 and not ctx.ga4.get("property_id"):
        defects.append({"what": "no GA4 property id on the record", "severity": "medium",
                        "receipt": _receipt("GA4 property id", "not set",
                                            "hubspot_company", ctx.ga4.get("as_of"))})
    placeholders = [(key, value) for key, value in sorted((ctx.tags or {}).items())
                    if key.endswith("_id") and is_placeholder_id(value)]
    for key, value in placeholders:
        defects.append({"what": "placeholder %s" % key.replace("_", " "),
                        "severity": "high" if (cost or 0) > 0 else "medium",
                        "receipt": _receipt(key.replace("_", " ").title(),
                                            value if value not in (None, "") else "empty",
                                            ctx.tags.get("source") or "tag_manager",
                                            ctx.tags.get("as_of"))})
    if not defects:
        return []

    severity = "high" if any(d["severity"] == "high" for d in defects) else "medium"
    confidence = 10 if (cost is not None or placeholders) else 7
    months = ctx.tags.get("unchanged_months")
    months_line = (" The tag setup has been in this state for %s months."
                   % months) if months else ""

    return [_rec(
        ctx, today, rule_key=rule_key, category="vendors",
        channels=["paid_search", "website"], severity=severity, confidence=confidence,
        found=("Conversions are not being measured here: %s.%s"
               % ("; ".join(d["what"] for d in defects), months_line)),
        receipts=[d["receipt"] for d in defects],
        expect=("Once the tag and conversion action are corrected, the property's cost "
                "per lead becomes a real number for the first time%s."
                % (" instead of a %s spend with nothing attached to it" % _money(cost)
                   if cost else "")),
        if_skip=("Spend continues against a measurement that records nothing, and every "
                 "optimization and report built on it is guesswork."),
        kind="tracking_fix",
        params={"defects": [d["what"] for d in defects],
                "ga4_property_id": ctx.ga4.get("property_id"),
                "tag_ids": {key: value for key, value in placeholders},
                "check": "conversion action, tag container ids, and the GA4 link"},
        executor="human", fair_housing_review=False,
        verify_metric="tracked conversions against site leads")]


def conversion_overcounting(ctx: DigitalContext, today: date) -> List[Dict[str, Any]]:
    """Platform conversions far above the site's own total.

    Observed at 9.4x and 3.9x on real properties. The finding is a measurement
    fault, not a result: a conversion action counting page views or duplicates.
    Never celebrate the number — correct it, so cost per lead means something.
    """
    rule_key = "conversion_overcounting"
    ads_conversions = ctx.ads_conversions
    site_conversions = _f(ctx.ga4.get("conversions"))
    if ads_conversions is None or site_conversions is None:
        return []
    if ads_conversions < OVERCOUNT_MIN_CONVERSIONS or site_conversions <= 0:
        return []
    ratio = ads_conversions / site_conversions
    if ratio < OVERCOUNT_RATIO:
        return []

    severity = "high" if ratio >= OVERCOUNT_RATIO_HIGH else "medium"
    cost = ctx.ads_cost
    receipts = [
        _receipt("Conversions reported by Google Ads", round(ads_conversions, 1),
                 "google_ads", ctx.ads_as_of),
        _receipt("Conversions recorded on the site", round(site_conversions, 1),
                 "ga4", ctx.ga4.get("as_of")),
        _receipt("Ratio", "%.1fx" % ratio, "google_ads vs ga4", ctx.ads_as_of),
    ]
    if cost:
        receipts.append(_receipt("Cost per conversion as reported",
                                 "$%.2f" % (cost / ads_conversions), "google_ads",
                                 ctx.ads_as_of))
        receipts.append(_receipt("Cost per conversion against the site total",
                                 "$%.2f" % (cost / site_conversions),
                                 "google_ads vs ga4", ctx.ads_as_of))
    return [_rec(
        ctx, today, rule_key=rule_key, category="vendors",
        channels=["paid_search", "website"], severity=severity, confidence=9,
        found=("Google Ads reports %.0f conversions where the site recorded %.0f — "
               "%.1f times as many." % (ads_conversions, site_conversions, ratio)),
        receipts=receipts,
        expect=("With the conversion action corrected, reported conversions land near "
                "the site's own total and cost per lead can be compared with other "
                "properties."),
        if_skip=("Reports keep showing %.1f times more leases-in-waiting than the site "
                 "saw, and budget decisions are made on the inflated number."
                 % ratio),
        kind="tracking_fix",
        params={"ads_conversions": round(ads_conversions, 1),
                "site_conversions": round(site_conversions, 1),
                "ratio": round(ratio, 2),
                "fix": ("count only the verified lead events; remove page-view and "
                        "duplicate counting from the conversion action")},
        executor="human", fair_housing_review=False,
        verify_metric="Google Ads conversions against the GA4 site total")]


def dormant_ad_groups(ctx: DigitalContext, today: date) -> List[Dict[str, Any]]:
    """Ad groups that are switched off, or on and invisible, while units sit empty.

    A paused ad group inside a running campaign is usually something nobody ever
    turned back on. A zero-impression ad group that is enabled is worse: it
    looks live in every report and reaches nobody.
    """
    rule_key = "dormant_ad_groups"
    groups = ctx.ads_rows("ad_groups")
    vacant = ctx.available_units
    if not groups or not vacant:
        return []

    silent, paused = [], []
    for group in groups:
        status = str(group.get("status") or "").upper()
        campaign_status = str(group.get("campaign_status") or "ENABLED").upper()
        name = group.get("ad_group_name") or ""
        impressions = _i(group.get("impressions"))
        if status == "ENABLED" and impressions == 0:
            silent.append(name)
        elif status == "PAUSED" and campaign_status == "ENABLED":
            paused.append(name)
    dormant = silent + paused
    if len(dormant) < DORMANT_MIN:
        return []

    vacancy = ctx.vacancy_by_bucket()
    wanted = [name for name in dormant
              if buckets_in_text(name) & set(vacancy)]        # dormant where units are empty
    severity = "high" if len(dormant) >= DORMANT_MIN_HIGH or wanted else "medium"
    kind = "new_ad_group" if wanted else "pause"

    receipts = [_receipt("Ad groups enabled with no impressions", len(silent),
                         "google_ads", ctx.ads_as_of),
                _receipt("Ad groups paused inside a running campaign", len(paused),
                         "google_ads", ctx.ads_as_of),
                _receipt("Units available now", vacant, "aptiq",
                         ctx.availability.get("as_of"))]
    if wanted:
        receipts.append(_receipt("Of those, ad groups for plans with vacancy",
                                 ", ".join(wanted[:6]), "google_ads", ctx.ads_as_of))

    if kind == "new_ad_group":
        expect = ("Rebuilt and live, these ad groups put the empty plans back in front "
                  "of renters who are already searching for them.")
        if_skip = ("The plans they were built for keep relying on the general campaigns, "
                   "which are competing for the same budget.")
    else:
        expect = ("Removed, the account shows what is actually running, and reports stop "
                  "counting structure that reaches nobody.")
        if_skip = ("Reports keep listing ad groups that reach nobody, and the next person "
                   "to review the account starts from a false picture.")

    return [_rec(
        ctx, today, rule_key=rule_key, category="cost", channels=["paid_search"],
        severity=severity, confidence=9,
        found=("%d ad groups are dormant — %d enabled with no impressions, %d paused "
               "inside a running campaign — while %d units are available."
               % (len(dormant), len(silent), len(paused), vacant)),
        receipts=receipts, expect=expect, if_skip=if_skip, kind=kind,
        params={"zero_impression_ad_groups": silent[:25],
                "paused_ad_groups": paused[:25],
                "ad_groups_for_vacant_plans": wanted[:25],
                "funding": "reallocate within the authorized paid search budget"},
        executor="ninjacat", fair_housing_review=bool(wanted),
        verify_metric="impressions by ad group")]


def creative_gaps(ctx: DigitalContext, today: date) -> List[Dict[str, Any]]:
    """Thin responsive ads, missing sitelinks, creative nobody has touched in months.

    Built from assets that already exist — the community brief, the floor-plan
    pages, approved photography on file. This rule never proposes a photo shoot.
    """
    rule_key = "creative_gaps"
    creatives = ctx.ads_rows("ads")
    cost = ctx.ads_cost
    if not creatives or not cost:
        return []

    thin, stale_days = [], None
    for ad in creatives:
        headlines = len(ad.get("headlines") or [])
        descriptions = len(ad.get("descriptions") or [])
        if headlines < RSA_MIN_HEADLINES or descriptions < RSA_MIN_DESCRIPTIONS:
            thin.append({"ad_group": ad.get("ad_group_name"), "headlines": headlines,
                         "descriptions": descriptions})
        age = _i(ad.get("days_since_change"))
        if age is not None:
            stale_days = age if stale_days is None else min(stale_days, age)

    sitelinks = _i(ctx.ads.get("sitelink_count"))
    stale = stale_days is not None and stale_days >= CREATIVE_STALE_DAYS
    if not thin and not stale and (sitelinks is None or sitelinks >= SITELINK_MIN):
        return []

    defects = []
    receipts = []
    if thin:
        defects.append("%d of %d responsive ads are below the asset counts the auction "
                       "expects" % (len(thin), len(creatives)))
        worst = min(thin, key=lambda t: t["headlines"])
        receipts.append(_receipt("Thinnest ad",
                                 "%s — %d headlines, %d descriptions"
                                 % (worst["ad_group"], worst["headlines"],
                                    worst["descriptions"]),
                                 "google_ads", ctx.ads_as_of))
        receipts.append(_receipt("Headlines expected per responsive ad",
                                 RSA_MIN_HEADLINES, "google_ads", ctx.ads_as_of))
    if sitelinks is not None and sitelinks < SITELINK_MIN:
        defects.append("the account runs %s sitelinks"
                       % ("no" if sitelinks == 0 else "only %d" % sitelinks))
        receipts.append(_receipt("Sitelinks in the account", sitelinks, "google_ads",
                                 ctx.ads_as_of))
    if stale:
        defects.append("no ad has changed in %d days" % stale_days)
        receipts.append(_receipt("Days since any ad changed", stale_days, "google_ads",
                                 ctx.ads_as_of))
    receipts.append(_receipt("Spend behind this creative (30 days)", _money(cost),
                             "google_ads", ctx.ads_as_of))

    severity = "high" if (thin and sitelinks == 0 and cost >= 500) else "medium"
    return [_rec(
        ctx, today, rule_key=rule_key, category="creative", channels=["paid_search"],
        severity=severity, confidence=8,
        found="The ads carrying %s a month are underbuilt: %s." % (_money(cost),
                                                                   "; ".join(defects)),
        receipts=receipts,
        expect=("A fuller set of headlines, descriptions and sitelinks, written from the "
                "community brief and the floor-plan pages already published, gives the "
                "auction more to work with at the same budget."),
        if_skip=("The same few lines keep serving against competitors running full "
                 "asset sets, and click-through stays where it is."),
        kind="creative_refresh",
        params={"thin_ads": thin[:25], "sitelink_count": sitelinks,
                "days_since_change": stale_days,
                "build_from": ("the community brief, the published floor-plan pages and "
                               "photography already on file — no new photo shoot"),
                "review": "Fair Housing review before anything publishes"},
        executor="portal", fair_housing_review=True,
        verify_metric="paid search click-through rate")]


def budget_pacing(ctx: DigitalContext, today: date) -> List[Dict[str, Any]]:
    """INTERNAL ONLY. Google Ads run rate against what the signed deal authorizes.

    NOT a second pacing number. `workspace_signals.spend_pacing` owns the bands
    and this rule imports them, so the two can never disagree about what "off
    pace" means. What differs is the measurement and the surface: signals reads
    delivered spend from `ninjacat_metrics` and lands on the signals page, this
    reads cost from the Google Ads API — the NinjaCat feed has missed real spend
    before — and lands in the ranked queue, which has no signals adapter.

    Pacing never goes to a client; it is an account-management number, and the
    portal's product rules are explicit about that. It is worth carrying because
    both directions cost real money: an underspend is service the client paid
    for and did not get, an overspend is money nobody authorized.
    """
    from skills.workspace_signals import (PACE_HIGH_HIGH, PACE_HIGH_LOW,
                                          PACE_MED_HIGH, PACE_MED_LOW)

    rule_key = "budget_pacing"
    by_sku = ctx.authorized.get("by_sku") or {}
    authorized = sum(_f(by_sku.get(key)) or 0.0 for key in (PAID_SEARCH_SKU, "pmax"))
    cost = ctx.ads_cost
    days = _i(ctx.window.get("days")) or WINDOW_DAYS
    if not authorized or cost is None or days <= 0:
        return []

    import calendar
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    run_rate = cost / days * days_in_month
    ratio = run_rate / authorized
    if PACE_MED_LOW <= ratio <= PACE_MED_HIGH:
        return []

    over = ratio > 1.0
    severity = "high" if (ratio < PACE_HIGH_LOW or ratio > PACE_HIGH_HIGH) else "medium"
    delta = abs(run_rate - authorized)
    receipts = [
        _receipt("Authorized paid search per month", _money(authorized),
                 "hubspot_deal_line_items", ctx.authorized.get("as_of")),
        _receipt("Google Ads spend in the last %d days" % days, _money(cost),
                 "google_ads", ctx.ads_as_of),
        _receipt("Run rate for the month", _money(run_rate), "google_ads", ctx.ads_as_of),
        _receipt("Pacing", "%d%% of authorized" % round(ratio * 100),
                 "google_ads vs hubspot_deal_line_items", ctx.ads_as_of),
    ]
    return [_rec(
        ctx, today, rule_key=rule_key, category="cost", channels=["paid_search"],
        severity=severity, confidence=9,
        found=("Internal only — not for a client: Google Ads is pacing at %d%% of the "
               "%s authorized for paid search."
               % (round(ratio * 100), _money(authorized))),
        receipts=receipts,
        expect=("Daily budgets brought back in line put the month within a few percent "
                "of the %s authorized, a swing of about %s."
                % (_money(authorized), _money(delta))),
        if_skip=(("The month closes about %s above what the deal authorizes, which "
                  "nobody has signed for." % _money(delta)) if over else
                 ("The month closes about %s under what the client paid for, and the "
                  "service is not delivered." % _money(delta))),
        kind="budget_change",
        params={"internal_only": True, "for_team": "account management",
                "direction": "reduce" if over else "increase",
                "authorized_monthly_usd": round(authorized, 2),
                "run_rate_monthly_usd": round(run_rate, 2),
                "difference_usd": round(delta, 2),
                "deal_id": ctx.authorized.get("deal_id")},
        executor="portal", fair_housing_review=False,
        verify_metric="Google Ads spend against authorized spend")]


# ── registry and entry point ─────────────────────────────────────────────────

RULES: "OrderedDict[str, Callable[[DigitalContext, date], List[Dict[str, Any]]]]" = OrderedDict((
    ("spend_not_on_vacancy", spend_not_on_vacancy),
    ("impression_share_lost", impression_share_lost),
    ("wasted_spend", wasted_spend),
    ("homepage_landing_page", homepage_landing_page),
    ("conversion_tracking_broken", conversion_tracking_broken),
    ("conversion_overcounting", conversion_overcounting),
    ("dormant_ad_groups", dormant_ad_groups),
    ("creative_gaps", creative_gaps),
    ("budget_pacing", budget_pacing),
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


# ══════════════════════════════════════════════════════════════════════════════
# LIVE READERS — every one of them degrades to a gap, never to a zero.
# ══════════════════════════════════════════════════════════════════════════════

# GA4 and the tag setup have no connector in this repo. These are the seams the
# portal fills when they do; unset, the rules that need them stay silent and
# `gather` records why. Signatures:
#   GA4_READER(ga4_property_id, start_date, end_date) -> {sessions, conversions, as_of}
#   TAG_READER(identity: dict) -> {"<name>_id": value, ..., "source", "as_of"}
GA4_READER: Optional[Callable[[str, str, str], Dict[str, Any]]] = None
TAG_READER: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None

# The five reads the rules need, in GAQL. They run through the existing
# credential-gated seam `google_ads_islost._run_gaql`, so this module adds no
# second set of credentials. NOTE for whoever implements that seam: field names
# moved in v25 (date segments were renamed and account-level assets changed
# shape), so verify each query against the API version in use before trusting a
# silent empty result.
ADS_QUERIES = {
    "campaigns": (
        "SELECT campaign.id, campaign.name, campaign.status, "
        "campaign.advertising_channel_type, campaign_budget.amount_micros, "
        "metrics.cost_micros, metrics.clicks, metrics.impressions, "
        "metrics.conversions, metrics.search_budget_lost_impression_share "
        "FROM campaign WHERE segments.date DURING LAST_30_DAYS"),
    "ad_groups": (
        "SELECT ad_group.id, ad_group.name, ad_group.status, campaign.name, "
        "campaign.status, metrics.impressions, metrics.clicks, metrics.cost_micros, "
        "metrics.conversions FROM ad_group WHERE segments.date DURING LAST_30_DAYS"),
    "keywords": (
        "SELECT ad_group_criterion.keyword.text, ad_group_criterion.keyword.match_type, "
        "ad_group.name, metrics.cost_micros, metrics.clicks, metrics.impressions, "
        "metrics.conversions FROM keyword_view WHERE segments.date DURING LAST_30_DAYS"),
    "search_terms": (
        "SELECT search_term_view.search_term, campaign.name, metrics.cost_micros, "
        "metrics.clicks, metrics.conversions FROM search_term_view "
        "WHERE segments.date DURING LAST_30_DAYS"),
    "ads": (
        "SELECT ad_group.name, ad_group_ad.ad.id, ad_group_ad.ad.type, "
        "ad_group_ad.ad.responsive_search_ad.headlines, "
        "ad_group_ad.ad.responsive_search_ad.descriptions, "
        "ad_group_ad.ad.final_urls, ad_group_ad.status FROM ad_group_ad "
        "WHERE ad_group_ad.status != 'REMOVED'"),
    # Sitelinks and the other extensions are account- and campaign-level assets;
    # this is one of the v25 shape changes worth verifying before trusting it.
    "assets": ("SELECT asset.type, asset.id, campaign.name FROM campaign_asset "
               "WHERE campaign_asset.status != 'REMOVED'"),
}

# Each field: our key -> the aliases a row may carry. A `*_micros` match is
# divided by a million, so the seam may hand back either form.
_ALIASES = {
    "campaigns": {
        "campaign_id": ("campaign_id", "campaign.id"),
        "campaign_name": ("campaign_name", "campaign.name"),
        "status": ("status", "campaign.status"),
        "channel_type": ("channel_type", "campaign.advertising_channel_type"),
        "budget": ("budget", "campaign_budget.amount_micros"),
        "cost": ("cost", "cost_micros", "metrics.cost_micros"),
        "clicks": ("clicks", "metrics.clicks"),
        "impressions": ("impressions", "metrics.impressions"),
        "conversions": ("conversions", "metrics.conversions"),
        "search_budget_lost_is": ("search_budget_lost_is", "budget_lost_is",
                                  "metrics.search_budget_lost_impression_share"),
    },
    "ad_groups": {
        "ad_group_name": ("ad_group_name", "ad_group.name"),
        "status": ("status", "ad_group.status"),
        "campaign_name": ("campaign_name", "campaign.name"),
        "campaign_status": ("campaign_status", "campaign.status"),
        "impressions": ("impressions", "metrics.impressions"),
        "clicks": ("clicks", "metrics.clicks"),
        "cost": ("cost", "cost_micros", "metrics.cost_micros"),
        "conversions": ("conversions", "metrics.conversions"),
        "final_urls": ("final_urls", "ad_group_ad.ad.final_urls"),
    },
    "keywords": {
        "keyword": ("keyword", "ad_group_criterion.keyword.text"),
        "match_type": ("match_type", "ad_group_criterion.keyword.match_type"),
        "ad_group_name": ("ad_group_name", "ad_group.name"),
        "cost": ("cost", "cost_micros", "metrics.cost_micros"),
        "clicks": ("clicks", "metrics.clicks"),
        "impressions": ("impressions", "metrics.impressions"),
        "conversions": ("conversions", "metrics.conversions"),
    },
    "search_terms": {
        "search_term": ("search_term", "search_term_view.search_term"),
        "campaign_name": ("campaign_name", "campaign.name"),
        "cost": ("cost", "cost_micros", "metrics.cost_micros"),
        "clicks": ("clicks", "metrics.clicks"),
        "conversions": ("conversions", "metrics.conversions"),
    },
    "ads": {
        "ad_group_name": ("ad_group_name", "ad_group.name"),
        "ad_id": ("ad_id", "ad_group_ad.ad.id"),
        "ad_type": ("ad_type", "ad_group_ad.ad.type"),
        "headlines": ("headlines", "ad_group_ad.ad.responsive_search_ad.headlines"),
        "descriptions": ("descriptions",
                         "ad_group_ad.ad.responsive_search_ad.descriptions"),
        "final_urls": ("final_urls", "ad_group_ad.ad.final_urls"),
        "days_since_change": ("days_since_change",),
    },
    "assets": {
        "asset_type": ("asset_type", "asset.type"),
        "asset_id": ("asset_id", "asset.id"),
        "campaign_name": ("campaign_name", "campaign.name"),
    },
}


def normalize_ads_rows(kind: str, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Library-agnostic rows in, the shape the rules read out."""
    out = []
    for row in rows or []:
        mapped: Dict[str, Any] = {}
        for key, aliases in _ALIASES[kind].items():
            for alias in aliases:
                if alias in row and row[alias] is not None:
                    value = row[alias]
                    if alias.endswith("_micros"):
                        value = round((_f(value) or 0.0) / 1_000_000.0, 2)
                    mapped[key] = value
                    break
        out.append(mapped)
    return out


def fetch_ads(customer_id: str) -> Dict[str, Any]:
    """The five Google Ads reads for one property. Raises when unconfigured."""
    import google_ads_islost as seam

    ads: Dict[str, Any] = {"customer_id": customer_id, "available": True,
                           "as_of": date.today().isoformat()}
    for kind, query in ADS_QUERIES.items():
        ads[kind] = normalize_ads_rows(kind, seam._run_gaql(customer_id, query))
    ads["sitelink_count"] = len([a for a in ads.get("assets") or []
                                 if "SITELINK" in str(a.get("asset_type") or "").upper()])
    return ads


def normalize_floor_plans(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """AptIQ floor-plan export rows -> the plans the rules read.

    The export repeats a plan across rows, so each plan is counted once, on the
    same identity `apt_iq_reader.read_floor_plans` dedups on.
    """
    plans, seen = [], set()
    for row in rows or []:
        name = str(row.get("Floor Plan Name") or "").strip()
        beds = _i(row.get("Beds"))
        identity = (name, beds, _f(row.get("Baths")), _i(row.get("Avg Sq Ft")))
        if not name or identity in seen:
            continue
        seen.add(identity)
        rent = None
        for key in ("Avg Asking Rent", "Asking Rent", "Avg Rent", "Effective Rent"):
            if _f(row.get(key)) is not None:
                rent = _f(row.get(key))
                break
        plans.append({"name": name, "beds": beds, "baths": _f(row.get("Baths")),
                      "sqft": _i(row.get("Avg Sq Ft")),
                      "available_units": _i(row.get("Available Units")) or 0,
                      "days_on_market": _i(row.get("Days on Market")),
                      "asking_rent": rent})
    return plans


def gather(company_id: str, *, today: Optional[date] = None,
           window_days: int = WINDOW_DAYS) -> DigitalContext:
    """Read every source the rules need for one property.

    Each source is read on its own and each failure becomes a gap, so a property
    with AptIQ but no Google Ads credentials still gets every rule that AptIQ
    alone can answer. Nothing here writes anywhere (R1 is safe through this
    path).
    """
    from skills import property_resolver

    day = _today(today)
    end = day - timedelta(days=1)
    start = end - timedelta(days=window_days - 1)
    identity = property_resolver.resolve(str(company_id))
    ids = identity.to_dict()

    ctx = DigitalContext(
        identity.company_id or str(company_id), property_name=identity.name,
        uuid=identity.uuid, domain=identity.domain, units=_i(identity.unit_count),
        window={"days": window_days, "start": start.isoformat(), "end": end.isoformat()},
        ga4={"property_id": identity.ga4_property_id})
    gaps = ctx.gaps          # append to the context's own list, not a copy of it

    # -- availability (AptIQ): 87 of 109 properties ---------------------------
    aptiq_id = str(ids.get("aptiq_property_id") or "").strip()
    if not aptiq_id:
        gaps.append(_gap("availability", "aptiq",
                         "This property has no availability id, so vacancy by floor "
                         "plan is not connected for it."))
    else:
        try:
            from skills import workspace_cache
            plans, as_of = workspace_cache.aptiq_floor_plans()
            rows = plans.get(aptiq_id) or []
            ctx.availability = {"as_of": as_of, "source": "aptiq_floor_plans",
                                "floor_plans": normalize_floor_plans(rows)}
            if not rows:
                gaps.append(_gap("availability", "aptiq_floor_plans",
                                 "The floor-plan export holds no rows for this property "
                                 "today."))
        except Exception as exc:  # noqa: BLE001
            logger.info("reco_digital: aptiq unavailable for %s: %s", company_id, exc)
            gaps.append(_gap("availability", "aptiq_floor_plans",
                             "The availability export could not be read (%s)."
                             % type(exc).__name__))

    # -- Google Ads: 77 of 109, and only once the API seam is configured ------
    import google_ads_islost as seam
    cid = seam.extract_property_cid(ids.get("google_ads_customer_id") or "")
    if not cid:
        gaps.append(_gap("ads", "google_ads",
                         "This property has no Google Ads customer id, so paid search "
                         "is not connected for it."))
    else:
        try:
            ctx.ads = fetch_ads(cid)
        except seam.GoogleAdsNotConfigured:
            gaps.append(_gap("ads", "google_ads",
                             "The Google Ads API is not configured on this server, so "
                             "the paid search rules did not run."))
        except Exception as exc:  # noqa: BLE001
            logger.error("reco_digital: google ads read failed for %s: %s", company_id,
                         exc, exc_info=True)
            gaps.append(_gap("ads", "google_ads",
                             "Google Ads could not be read (%s)." % type(exc).__name__))

    # -- GA4 and the tag setup: hooks until those connectors exist ------------
    if not identity.ga4_property_id:
        gaps.append(_gap("ga4", "hubspot_company",
                         "This property has no GA4 property id on its record."))
    elif GA4_READER is None:
        gaps.append(_gap("ga4", "ga4",
                         "GA4 has no connector on this server, so site conversions "
                         "could not be compared with the platform's own count."))
    else:
        try:
            site = GA4_READER(identity.ga4_property_id, start.isoformat(),
                              end.isoformat()) or {}
            ctx.ga4 = dict(site, property_id=identity.ga4_property_id)
        except Exception as exc:  # noqa: BLE001
            logger.error("reco_digital: ga4 read failed for %s: %s", company_id, exc)
            gaps.append(_gap("ga4", "ga4", "GA4 could not be read (%s)."
                             % type(exc).__name__))

    if TAG_READER is None:
        gaps.append(_gap("tags", "tag_manager",
                         "The tag setup is not readable from this server, so "
                         "placeholder measurement ids cannot be checked here."))
    else:
        try:
            ctx.tags = TAG_READER(ids) or {}
        except Exception as exc:  # noqa: BLE001
            logger.error("reco_digital: tag read failed for %s: %s", company_id, exc)
            gaps.append(_gap("tags", "tag_manager", "The tag setup could not be read "
                                                    "(%s)." % type(exc).__name__))

    # -- authorized spend (internal) -----------------------------------------
    try:
        import spend_sheet
        authorized = spend_sheet.get_company_monthly_spend(ctx.company_id) or {}
        ctx.authorized = {"by_sku": authorized.get("by_sku") or {},
                          "deal_id": authorized.get("deal_id"), "as_of": _now_iso()}
    except Exception as exc:  # noqa: BLE001
        logger.info("reco_digital: spend sheet unavailable for %s: %s", company_id, exc)
        gaps.append(_gap("authorized", "hubspot_deal_line_items",
                         "Authorized spend could not be read, so no budget change can "
                         "be proposed against it."))

    # -- Red Light status: `recommendation_gen`'s trigger for a budget change --
    try:
        import hubspot_client
        company = hubspot_client.get_company(ctx.company_id, ["redlight_status"]) or {}
        ctx.marketing_status = (company.get("redlight_status") or "").upper() or None
    except Exception as exc:  # noqa: BLE001
        logger.info("reco_digital: red light status unavailable for %s: %s",
                    company_id, exc)
        gaps.append(_gap("marketing_status", "red_light",
                         "The marketing status could not be read (%s)."
                         % type(exc).__name__))

    # -- leasing funnel: a bonus on the 14 properties that have it -----------
    hyly_id = str(ids.get("hyly_property_id") or "").strip()
    clicks = sum(_f(c.get("clicks")) or 0.0 for c in ctx.ads_rows("campaigns"))
    if hyly_id and clicks:
        try:
            import hyly_client
            if hyly_client.is_configured():
                channels = hyly_client.get_channel_summary(
                    hyly_id, start_date=start.isoformat(), end_date=end.isoformat()) or {}
                leads = sum((data or {}).get("leads") or 0
                            for name, data in channels.items()
                            if name != "_total" and _is_paid_search_channel(name))
                if leads:
                    ctx.funnel = {"leads": float(leads), "clicks": clicks,
                                  "as_of": end.isoformat(), "source": "hyly_rollup"}
        except Exception as exc:  # noqa: BLE001 — the funnel is a bonus, never a blocker
            logger.info("reco_digital: funnel unavailable for %s: %s", company_id, exc)

    return ctx


def _is_paid_search_channel(name: Any) -> bool:
    text = str(name or "").lower()
    return "paid search" in text or text in ("sem", "ppc", "google ads", "google_ads")
