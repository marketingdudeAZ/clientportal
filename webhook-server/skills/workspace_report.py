"""Monthly property marketing report — Layer 2 assembler for the Workspace.

Contract: docs/handoffs/PORTAL_WORKSPACE_BUILD_PLAN.md, "Report contract".

Two halves, deliberately separate:

  gather_live()  reads the portal's own connectors (read-only) into a `raw`
                 dict of receipts. Sections with no reachable, defined source
                 come back empty with a reason in raw["gaps"].
  assemble()     is pure: raw -> report. Every rate is recomputed from its
                 components here, every sentence is a template filled from
                 receipts, and nothing is invented. The fixture is built by
                 the same function (skills/workspace_report_export.py feeds it
                 the dashboard export), so tone and receipts can't drift.

Definitions follow the Halo metric library (Hyly). The live reader refuses any
lake object outside the library's allowlist.

Tone (Kyle, Sept 2026): the report goes to clients and owners. Calm section
labels; results first; issues stated as the next step RPM is taking; never
hide or soften a number. BANNED_PHRASES is enforced on all generated text.
"""

from __future__ import annotations

import calendar
import json
import logging
import math
import os
import re
from datetime import date, datetime, timedelta
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import Any, Callable, Iterable, Optional

logger = logging.getLogger(__name__)

# ── Sources ─────────────────────────────────────────────────────────────────

SRC_OCCUPANCY = "hyly_lake.t_oc_agg_occupancy_property"
SRC_ACTIVITY = "hyly_lake.t_ot_agg_resident_activity_property"
SRC_OPERATIONAL = "hyly_lake.t_oc_agg_occupancy_operational"
SRC_OCC_RATE = "hyly_lake.t_occupancy_rate"
SRC_CONTACT = "hyly_lake.t_contact_activity"
SRC_LEASE = "hyly_lake.pai_journey"
SRC_PROSPECT_JOURNEY = "hyly_lake.prospect_journey"
SRC_EXPORT_SPEND = "hyly_export.spend_manager"
SRC_EXPORT_MTA = "hyly_export.multi_touch_attribution"
SRC_EXPORT_WEBSITE = "hyly_export.website_ga4"
SRC_EXPORT_ADS = "hyly_export.google_ads"
SRC_EXPORT_VELOCITY = "hyly_export.conversion_velocity"
SRC_EXPORT_LISTING = "hyly_export.listing_apartments_com"
SRC_ILS = "portal_bq.apartmentscom_ils_resolved_v1"
SRC_BENCHMARK = "workspace_benchmarks.seeded"
SRC_TARGET = "workspace_benchmarks.reputation_target"

DEFAULT_LAKE_DATASET = "gds-prototype-20190629.rpm_living_nrt"
DEFAULT_HYLY_ORG_ID = "1747307582311553649"
MAX_BYTES_BILLED = 50 * 1024 * 1024  # library governance: every pathway runs under 5 MB

# The metric library's object allowlist. Anything else is refused, not rewritten.
LAKE_ALLOWLIST = frozenset({
    "conversion_triple_base_v1",
    f"pai_journey_{DEFAULT_HYLY_ORG_ID}",
    "prospect_journey",
    "t_contact_activity",
    "t_oc_agg_occupancy_operational",
    "t_oc_agg_occupancy_property",
    "t_occupancy_rate",
    "t_ot_agg_resident_activity_property",
})

# Notice fields were not captured before this date (library data gap).
NOTICE_DATA_STARTS = "2026-07-27"

TRUTHY = {"1", "true", "yes", "on"}
MINUS = "−"

# ── Tone ────────────────────────────────────────────────────────────────────

BANNED_PHRASES = (
    "leak", "none of them", "out the door", "flatters", "cannot all be right",
    "problem", "failing", "wasted", "burned",
)

SECTION_LABELS = {
    "occupancy": "Occupancy",
    "funnel": "Leasing funnel",
    "spend": "Spend and leases",
    "attribution": "Where leads came from",
    "website": "Website",
    "paid_search": "Paid search",
    "reputation": "Reputation",
    "listings": "Listings",
    "actions": "Next month",
}

LENSES = ("express", "tailor", "amplify", "evolve")

FUNNEL_STAGES = (
    ("created", "Leads created"),
    ("scheduled", "Scheduled"),
    ("toured", "Toured"),
    ("applied", "Applied"),
    ("leased", "Leased"),
)
DAYS_KEYS = {
    "created": "days_created_to_scheduled",
    "scheduled": "days_scheduled_to_toured",
    "toured": "days_toured_to_applied",
    "applied": "days_applied_to_leased",
}
STEP_NAMES = {
    "created": "lead to schedule",
    "scheduled": "schedule to tour",
    "toured": "tour to application",
    "applied": "application to lease",
}
INFLUENCE_STAGES = ("created", "scheduled", "toured", "applied", "leased")

REPUTATION_TARGET = 4.0
DEFAULT_BENCHMARKS = {"ctr_low": 0.03, "ctr_high": 0.05, "cost_per_conversion": 150}

PPC_SOURCE = "Google PayPerClick (PPC)"
WEBSITE_SOURCE = "Property Website"
ASSISTANT_SOURCE = "hyly"

SOURCE_DISPLAY = {
    "Property Website": "Property website",
    "Google My Business/Maps": "Google Business / Maps",
    "Google PayPerClick (PPC)": "Google Ads (PPC)",
    "Google.com": "Google.com",
    "Zillow": "Zillow",
    "Apple Maps": "Apple Maps",
    "Walking / Driving By": "Walking / driving by",
    "ApartmentList.com": "ApartmentList.com",
    "hyly": "Hyly assistant",
    "google": "google (untagged)",
    "Bing": "Bing",
    "Social Posting": "Social posting",
    "RENTCafe.com ILS": "RENTCafe.com ILS",
    "RentCafe": "RentCafe",
    "Transfer Unit": "Transfer unit",
    "Former/Referral  Resident": "Former / referral resident",
    "AirBnB": "AirBnB",
}
SOURCE_COLORS = {
    "Property Website": "#3D6FD1",
    "Google PayPerClick (PPC)": "#C94444",
    "Zillow": "#1E8E77",
    "hyly": "#E2661F",
    "Google My Business/Maps": "#8E63C7",
    "ApartmentList.com": "#B07A2A",
    "Apple Maps": "#64748B",
    "Walking / Driving By": "#2B8FA8",
    "Google.com": "#6C9A2E",
    "google": "#94A3B8",
}
FALLBACK_COLOR = "#A7A29A"
WEBSITE_SOURCE_DISPLAY = {"google": "Google", "(direct)": "Direct", "bing": "Bing"}


class ReportError(Exception):
    """Base for report failures a route turns into an HTTP status."""


class InvalidMonth(ReportError):
    """month is not YYYY-MM."""


class MonthUnavailable(ReportError):
    """The month is well-formed but has no data for this property."""


class PropertyUnavailable(ReportError):
    """The property can't be reported on (unknown, or no Hyly id)."""


class SourceError(ReportError):
    """A configured connector failed. Surface it; never render it as no data."""


# ── Receipts and formatting ─────────────────────────────────────────────────

def receipt(value: Any, source: str, as_of: Optional[str], **extra: Any) -> Optional[dict]:
    """{value, source, as_of}, or None when there is no value."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"receipt value must be a number, got {type(value).__name__}")
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    out = {"value": value, "source": source, "as_of": as_of}
    out.update(extra)
    return out


def val(r: Any) -> Any:
    return r.get("value") if isinstance(r, dict) else None


def derived(num: Optional[dict], den: Optional[dict], label: str, as_of: Optional[str]) -> Optional[dict]:
    """num ÷ den as a receipt. None when either side is missing or den is 0."""
    n, d = val(num), val(den)
    if n is None or d in (None, 0):
        return None
    return receipt(round(n / d, 6), f"derived:{label}", as_of)


def rate_from_components(parts: Iterable[tuple[float, float]]) -> Optional[float]:
    """Pool a rate across groups: Σnumerator ÷ Σdenominator.

    The library's aggregation rule for every percentage: never add or average
    percentages; sum the components, then divide.
    """
    num = den = 0.0
    for n, d in parts:
        if n is None or d is None:
            return None
        num += n
        den += d
    return (num / den) if den else None


def _half_up(value: float, dp: int = 0) -> float:
    return float(Decimal(repr(float(value))).quantize(Decimal(1).scaleb(-dp), rounding=ROUND_HALF_UP))


def _truncate(value: float, dp: int = 0) -> float:
    return float(Decimal(repr(float(value))).quantize(Decimal(1).scaleb(-dp), rounding=ROUND_DOWN))


def article(text: str) -> str:
    """'a' or 'an' before a written number ("an 85.5%", "an 11-day", "a 5.2%")."""
    t = text.lstrip("$\u2212-")
    return "an" if t[:1] == "8" or re.match(r"1[18](?:\D|$)", t) else "a"


def fmt_int(v: float) -> str:
    n = int(_half_up(v))
    return f"{MINUS}{abs(n):,}" if n < 0 else f"{n:,}"


def fmt_money(v: float, dp: int = 0) -> str:
    s = f"${_half_up(abs(v), dp):,.{dp}f}"
    return f"{MINUS}{s}" if v < 0 else s


def fmt_pct(frac: float, dp: int = 1) -> str:
    return f"{_half_up(frac * 100, dp):.{dp}f}%"


def fmt_days(v: float) -> str:
    return f"{_half_up(v, 1):.1f}"


def fmt_ratio(v: float, dp: int = 2) -> str:
    return f"{_half_up(v, dp):.{dp}f}"


def fmt_compact(v: float) -> str:
    return f"{_half_up(v / 1000, 1):.1f}K" if abs(v) >= 1000 else fmt_int(v)


def month_label(month: str) -> str:
    y, m = parse_month(month)
    return f"{calendar.month_name[m]} {y}"


def parse_month(month: str) -> tuple[int, int]:
    m = re.fullmatch(r"(\d{4})-(\d{2})", (month or "").strip())
    if not m or not 1 <= int(m.group(2)) <= 12:
        raise InvalidMonth(f"month must be YYYY-MM, got {month!r}")
    return int(m.group(1)), int(m.group(2))


def month_bounds(month: str) -> tuple[str, str]:
    y, m = parse_month(month)
    return f"{y:04d}-{m:02d}-01", f"{y:04d}-{m:02d}-{calendar.monthrange(y, m)[1]:02d}"


def last_complete_month(today: date) -> str:
    first = today.replace(day=1)
    prev = first - timedelta(days=1)
    return f"{prev.year:04d}-{prev.month:02d}"


def months_between(start: str, end: str) -> list[str]:
    y, m = parse_month(start)
    ey, em = parse_month(end)
    out = []
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def source_display(label: str) -> str:
    return SOURCE_DISPLAY.get(label, label)


# ── Text safety ─────────────────────────────────────────────────────────────

def banned_phrases_in(text: str) -> list[str]:
    low = (text or "").lower()
    return [p for p in BANNED_PHRASES if p in low]


def iter_strings(obj: Any, _path: str = "") -> Iterable[tuple[str, str]]:
    """Every string value (not key) in a report, with its path."""
    if isinstance(obj, str):
        yield _path, obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from iter_strings(v, f"{_path}.{k}" if _path else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from iter_strings(v, f"{_path}[{i}]")


def iter_receipts(obj: Any) -> Iterable[dict]:
    if isinstance(obj, dict):
        if "value" in obj and "source" in obj and "as_of" in obj:
            yield obj
        for v in obj.values():
            yield from iter_receipts(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from iter_receipts(v)


_NUM_RE = re.compile(r"(?<![A-Za-z0-9.])[−\-]?\$?\d[\d,]*(?:\.\d+)?%?K?")


def _numbers_in(text: str) -> list[tuple[str, float, int, str]]:
    """(token, magnitude, decimals, kind) for every number written in text."""
    out = []
    for m in _NUM_RE.finditer(text or ""):
        tok = m.group(0)
        s = tok.replace("−", "-").replace("$", "").replace(",", "")
        kind = "plain"
        if s.endswith("%"):
            s, kind = s[:-1], "pct"
        elif s.endswith("K"):
            s, kind = s[:-1], "k"
        try:
            num = float(s)
        except ValueError:
            continue
        dp = len(s.split(".")[1]) if "." in s else 0
        out.append((tok, abs(num), dp, kind))
    return out


def unsupported_numbers(text: str, allowed: Iterable[float]) -> list[str]:
    """Numbers in `text` that don't round (or truncate) from any allowed value."""
    raw_cands, pct_cands = set(), set()
    for a in allowed:
        if a is None:
            continue
        a = abs(float(a))
        raw_cands.add(a)
        pct_cands.add(a * 100)
    bad = []
    for tok, num, dp, kind in _numbers_in(text):
        target = num * 1000 if kind == "k" else num
        # A percent may come from a stored fraction; a dollar or count may not.
        cands = (pct_cands | raw_cands) if kind == "pct" else raw_cands
        ok = False
        for c in cands:
            if kind == "k":
                if abs(_half_up(c / 1000, dp) * 1000 - target) < 1e-6:
                    ok = True
                    break
                continue
            if abs(_half_up(c, dp) - num) < 1e-9 or abs(_truncate(c, dp) - num) < 1e-9:
                ok = True
                break
        if not ok:
            bad.append(tok)
    return bad


def allowed_numbers(report: dict) -> list[float]:
    nums = [r["value"] for r in iter_receipts(report) if isinstance(r.get("value"), (int, float))]
    try:
        y, m = parse_month(report.get("month", ""))
        nums += [y, m]
    except InvalidMonth:
        pass
    return nums


def validate_polished(text: str, report: dict) -> tuple[bool, list[str]]:
    """An LLM rewrite is accepted only if every number is in the data and no banned phrase appears."""
    problems = []
    if not text or not text.strip():
        return False, ["empty"]
    if len(text) > 900:
        problems.append("too long")
    problems += [f"banned: {p}" for p in banned_phrases_in(text)]
    problems += [f"unsupported number: {t}" for t in unsupported_numbers(text, allowed_numbers(report))]
    return (not problems), problems


# ── Fair Housing on actions ─────────────────────────────────────────────────

_TARGETING_RE = re.compile(
    r"\bradius\b|\bzip(?:\s*codes?)?\b|\bpostal\s+codes?\b|\baudience\s+layer\w*|\blayer(?:ed|ing)?\s+audiences?\b"
    r"|\bgeo-?fenc\w*|\bnarrow\w*\s+(?:the\s+)?(?:targeting|audience|geography)\b",
    re.IGNORECASE,
)


def action_fair_housing_hits(action: dict) -> list[str]:
    """Protected-class language (fair_housing.py) plus the targeting moves we never propose."""
    import fair_housing

    text = " ".join(str(action.get(k) or "") for k in ("title", "detail", "stake_label"))
    _, terms = fair_housing.validate_audience_terms(text)
    return list(terms) + [m.group(0) for m in _TARGETING_RE.finditer(text)]


def screen_actions(actions: list[dict]) -> list[dict]:
    kept = []
    for a in actions:
        hits = action_fair_housing_hits(a)
        if hits:
            logger.warning("workspace_report: dropped action %r (fair housing: %s)", a.get("title"), hits)
            continue
        kept.append(a)
    for i, a in enumerate(kept, 1):
        a["rank"] = i
    return kept


# ── Assembly (pure) ─────────────────────────────────────────────────────────

class _Ctx:
    def __init__(self, raw: dict):
        self.raw = raw
        self.v = raw.get("values") or {}
        self.as_of = raw.get("as_of")
        self.month = raw["month"]
        self.label = month_label(self.month)
        self.mname = self.label.split()[0]
        self.gaps: list[dict] = [dict(g) for g in (raw.get("gaps") or [])]

    def get(self, key: str) -> Optional[dict]:
        return self.v.get(key)

    def need(self, section: str, metric: str, value: Any, reason: str = "Not available for this month.") -> Any:
        if value is None and not any(g.get("section") == section and g.get("metric") == metric for g in self.gaps):
            self.gaps.append({"section": section, "metric": metric, "reason": reason})
        return value

    def first_touch(self, stage: str, label: Optional[str]) -> Optional[dict]:
        if not label:
            return None
        ft = self.raw.get("first_touch") or {}
        rows = ft.get(stage) or []
        for r in rows:
            if r["name"] == label:
                return r["count"]
        if rows and ft.get(f"{stage}__complete"):
            return receipt(0, rows[0]["count"]["source"], self.as_of)
        return None


def _occupancy(c: _Ctx) -> dict:
    v = c.get
    total, available = v("total_units"), v("available")
    leased_rate = None
    if val(total) and val(available) is not None:
        leased_rate = receipt(round((val(total) - val(available)) / val(total), 6),
                              "derived:(total_units-available)/total_units", c.as_of)
    occupied_rate = derived(v("occupied"), total, "occupied/total_units", c.as_of)
    exposure_rate = derived(available, total, "available/total_units", c.as_of)
    net = None
    if val(v("move_ins")) is not None and val(v("move_outs")) is not None:
        net = receipt(val(v("move_ins")) - val(v("move_outs")), "derived:move_ins-move_outs", c.as_of)

    for key, r in (("leased_rate", leased_rate), ("occupied_rate", occupied_rate), ("future_leases", v("leased_future")),
                   ("net_move_ins", net), ("delayed_move_ins", v("delayed_move_ins"))):
        c.need("occupancy", key, r)

    if val(leased_rate) is not None and val(occupied_rate) is not None:
        parts = [f"{fmt_pct(val(leased_rate))} leased and {fmt_pct(val(occupied_rate))} occupied"]
        if val(v("leased_future")) is not None:
            parts.append(f"with {fmt_int(val(v('leased_future')))} signed residents moving in")
        takeaway = ", ".join(parts)
        if val(net) is not None:
            takeaway += f"; net move-ins were {fmt_int(val(net))}."
        else:
            takeaway += "."
    else:
        takeaway = f"Occupancy readings aren't available for {c.label} yet."

    notes = []
    if val(occupied_rate) is not None and c.as_of:
        notes.append(f"Leased and occupied are month-end readings on {fmt_int(val(total))} units.")
    avg = v("avg_occupancy_rate")
    if val(avg) is not None and val(occupied_rate) is not None and abs(val(avg) - val(occupied_rate)) >= 0.01:
        notes.append(f"Hyly's daily average for the month reads {fmt_pct(val(avg), 2)} on a different unit count, "
                     "so it isn't directly comparable.")
    if c.as_of and c.as_of < NOTICE_DATA_STARTS:
        notes.append("Residents on notice aren't in the data before July 27, 2026, so exposure and future leases "
                     "for this month may read low.")
    return {
        "label": SECTION_LABELS["occupancy"],
        "takeaway": takeaway,
        "leased_rate": leased_rate,
        "occupied_rate": occupied_rate,
        "exposure_rate": exposure_rate,
        "average_occupancy_rate": avg,
        "total_units": total,
        "occupied": v("occupied"),
        "available": available,
        "future_leases": v("leased_future"),
        "move_ins": v("move_ins"),
        "move_outs": v("move_outs"),
        "net_move_ins": net,
        "vacant": v("vacant"),
        "vacant_rented": v("vacant_rented"),
        "vacant_unrented": v("vacant_unrented"),
        "delayed_move_ins": v("delayed_move_ins"),
        "note": " ".join(notes),
    }


def _funnel(c: _Ctx) -> dict:
    stages = []
    prev_key = None
    for key, name in FUNNEL_STAGES:
        count = c.need("funnel", key, c.get(key))
        rate = derived(count, c.get(prev_key), f"{key}/{prev_key}", c.as_of) if prev_key else None
        days = c.get(DAYS_KEYS[key]) if key in DAYS_KEYS else None
        if key in DAYS_KEYS:
            c.need("funnel", f"days_{key}_to_next", days,
                   "Days per step aren't defined in the metric library yet.")
        stages.append({"key": key, "name": name, "count": count, "rate": rate, "days_to_next": days})
        prev_key = key
    l2l = c.get("days_created_to_leased")

    created, leased = val(c.get("created")), val(c.get("leased"))
    if created is not None and leased is not None:
        takeaway = f"{fmt_int(created)} leads became {fmt_int(leased)} leases"
        takeaway += f"; lead to lease took {fmt_days(val(l2l))} days." if val(l2l) is not None else f" in {c.mname}."
    else:
        takeaway = f"Leasing activity isn't available for {c.label} yet."

    notes = [f"Counts are {c.mname} activity at each step, not one group of prospects followed through, "
             "and each rate compares a step with the step before it."]
    timed = [(val(s["days_to_next"]), s["key"]) for s in stages if val(s["days_to_next"]) is not None]
    if timed:
        days, key = max(timed)
        share = f", {fmt_pct(days / val(l2l), 0)} of the cycle" if val(l2l) else ""
        notes.append(f"The longest step was {STEP_NAMES[key]} at {fmt_days(days)} days{share}.")
        if any(s["days_to_next"] and s["days_to_next"]["source"] == SRC_EXPORT_VELOCITY for s in stages):
            notes.append("Days are Hyly's step velocity; we're confirming whether that's a median or an average.")
    return {
        "label": SECTION_LABELS["funnel"],
        "takeaway": takeaway,
        "stages": stages,
        "lead_to_lease_days": l2l,
        "net_applied": c.get("net_applied"),
        "note": " ".join(notes),
    }


def _vendor_is_google_ads(name: str) -> bool:
    return "google ads" in (name or "").lower()


def _google_ads_spend_differs(c: _Ctx) -> Optional[tuple[dict, dict]]:
    ads = c.get("ads_spend")
    for vd in c.raw.get("vendors") or []:
        if _vendor_is_google_ads(vd["name"]) and val(vd.get("spend")) and val(ads) is not None:
            a, b = val(vd["spend"]), val(ads)
            if abs(a - b) / max(a, b) > 0.05:
                return vd["spend"], ads
    return None


def _spend(c: _Ctx) -> dict:
    total = c.get("total_spend")
    vendors_raw = c.raw.get("vendors") or []
    rows = []
    reviewing = _google_ads_spend_differs(c)
    for vd in vendors_raw:
        spend = vd.get("spend")
        leads = c.first_touch("created", vd.get("first_touch_source"))
        leases = c.first_touch("leased", vd.get("first_touch_source"))
        rows.append({
            "name": vd["name"],
            "spend": spend,
            "share": derived(spend, total, "vendor_spend/total_spend", c.as_of),
            "leads": leads,
            "cost_per_lead": derived(spend, leads, "vendor_spend/first_touch_leads", c.as_of) if val(leads) else None,
            "leases": leases,
            "label": "Reviewing" if reviewing and _vendor_is_google_ads(vd["name"]) else None,
        })
    listed = sum(val(r["spend"]) or 0 for r in rows)
    if val(total) is not None and rows and val(total) - listed >= 0.5:
        rest = receipt(round(val(total) - listed, 2), "derived:total_spend-listed_vendors", c.as_of)
        rows.append({"name": "Unallocated", "spend": rest,
                     "share": derived(rest, total, "unallocated/total_spend", c.as_of),
                     "leads": None, "cost_per_lead": None, "leases": None, "label": None})
    if rows:
        spend_src = next((r["spend"]["source"] for r in rows if r["spend"]), SRC_EXPORT_SPEND)
        rows.append({"name": "Property website", "spend": receipt(0, f"{spend_src}:no_entry", c.as_of),
                     "share": None, "leads": c.first_touch("created", WEBSITE_SOURCE), "cost_per_lead": None,
                     "leases": c.first_touch("leased", WEBSITE_SOURCE), "label": "Unpaid"})
    c.need("spend", "total", total, "Spend by vendor isn't reachable from the portal's connectors yet.")

    priced = [r for r in rows if val(r["cost_per_lead"]) is not None]
    paid = [r for r in rows if val(r["spend"]) and r["name"] not in ("Unallocated",)]
    if val(total) is not None and paid:
        takeaway = f"{fmt_money(val(total))} across {len(paid)} vendors"
        if priced:
            best = min(priced, key=lambda r: val(r["cost_per_lead"]))
            takeaway += f"; {best['name']} had the lowest cost per lead at {fmt_money(val(best['cost_per_lead']))}."
        else:
            takeaway += "."
    else:
        takeaway = f"Spend by vendor isn't connected for {c.label} yet."
    note = ("Leads and leases here use each lead's first source (the vendor basis). Most paid visitors reach the "
            "property website before they inquire, so the website carries the first-touch lease credit; Where leads "
            "came from shows the paid sources' part earlier in the journey.") if rows else ""
    return {"label": SECTION_LABELS["spend"], "takeaway": takeaway, "total": total, "vendors": rows, "note": note}


def _shape(counts: dict, stage_sums: dict) -> str:
    sh = [(counts.get(k) or 0) / stage_sums[k] if stage_sums.get(k) else 0 for k in INFLUENCE_STAGES]
    on = [i for i, x in enumerate(sh) if x > 0]
    if len(on) <= 1:
        return "single_stage"
    reach = on[-1]
    early, late = (sh[0] + sh[1]) / 2, (sh[3] + sh[4]) / 2
    if reach <= 1:
        return "early_stages"
    if late > early * 1.25:
        return "later_stages"
    if early > late * 1.6:
        return "early_stages"
    return "present_throughout"


def _attribution(c: _Ctx) -> dict:
    ft_created = (c.raw.get("first_touch") or {}).get("created") or []
    influence = c.raw.get("influence") or None
    labels: list[str] = [r["name"] for r in ft_created]
    if influence:
        labels += [s["name"] for s in influence["sources"] if s["name"] not in labels]
    stage_sums = {}
    if influence:
        for k in INFLUENCE_STAGES:
            stage_sums[k] = sum(val(s.get(k)) or 0 for s in influence["sources"])

    sources = []
    for label in labels:
        inf = next((s for s in influence["sources"] if s["name"] == label), None) if influence else None
        stages = {k: inf.get(k) for k in INFLUENCE_STAGES} if inf else None
        counts = {k: val(stages[k]) for k in INFLUENCE_STAGES} if stages else {}
        created = c.first_touch("created", label)
        if not (val(created) or any(counts.values())):
            continue
        sources.append({
            "name": source_display(label),
            "source_label": label,
            "color": SOURCE_COLORS.get(label, FALLBACK_COLOR),
            "created": created,
            "influenced": stages["created"] if stages else None,
            "toured": stages["toured"] if stages else None,
            "leased": stages["leased"] if stages else None,
            "stages": stages,
            "shape": _shape(counts, stage_sums) if stages else None,
        })
    sources.sort(key=lambda s: (-(sum(val(x) or 0 for x in (s["stages"] or {}).values())), -(val(s["created"]) or 0)))
    if not influence:
        c.need("attribution", "influenced", None, "Multi-touch influence isn't defined in the metric library yet.")

    by_medium = {}
    for stage in INFLUENCE_STAGES:
        rows = (c.raw.get("by_medium") or {}).get(stage) or []
        if rows:
            by_medium[stage] = [{"name": r["name"], "count": r["count"],
                                 "share": derived(r["count"], c.get(stage), f"medium_count/{stage}", c.as_of)}
                                for r in rows]

    created_total = val(c.get("created"))
    prospects = influence["prospects"] if influence else None
    if ft_created and created_total:
        top = max(ft_created, key=lambda r: val(r["count"]) or 0)
        takeaway = (f"{source_display(top['name'])} was the first source for {fmt_int(val(top['count']))} of "
                    f"{fmt_int(created_total)} new leads")
        if prospects and val(prospects.get("created")) is not None:
            takeaway += f"; {fmt_int(val(prospects['created']))} prospects were influenced across all touches."
        else:
            takeaway += "."
    else:
        takeaway = f"Lead sources aren't available for {c.label} yet."

    notes = ["First touch credits each lead to the source that brought it in."]
    if influence:
        notes.append("Influenced counts a prospect once for every source that touched it, so the columns add up to "
                     "more than the prospect total.")
        assistant = next((s for s in sources if s["source_label"] == ASSISTANT_SOURCE), None)
        if assistant and val(assistant["toured"]) and prospects and val(prospects.get("toured")):
            notes.append(f"The Hyly assistant played a part in {fmt_int(val(assistant['toured']))} of the "
                         f"{fmt_int(val(prospects['toured']))} tours.")
    med = {r["name"].lower(): r for r in by_medium.get("created", [])}
    if "organic" in med and "cpc" in med and val(med["organic"]["share"]) is not None:
        notes.append(f"By medium, {fmt_pct(val(med['organic']['share']))} of new leads were organic and "
                     f"{fmt_pct(val(med['cpc']['share']))} were tagged paid search.")
    return {
        "label": SECTION_LABELS["attribution"],
        "takeaway": takeaway,
        "prospects": prospects,
        "sources": sources,
        "by_medium": by_medium,
        "note": " ".join(notes),
    }


def _website(c: _Ctx) -> dict:
    v = c.get
    sessions, users, engaged = v("ga_sessions"), v("ga_users"), v("ga_engaged_sessions")
    engagement = derived(engaged, sessions, "engaged_sessions/sessions", c.as_of)
    rows = []
    for ws in c.raw.get("website_sources") or []:
        s, e = ws.get("sessions"), ws.get("engaged_sessions")
        bounce = None
        if val(s) and val(e) is not None:
            bounce = receipt(round((val(s) - val(e)) / val(s), 6), "derived:(sessions-engaged_sessions)/sessions",
                             c.as_of)
        rows.append({
            "name": WEBSITE_SOURCE_DISPLAY.get(ws["name"], ws["name"]),
            "sessions": s,
            "engaged_sessions": e,
            "bounce_rate": bounce,
            "conversions": ws.get("conversions"),
            "label": "Low engagement" if val(bounce) is not None and val(bounce) >= 0.7 and (val(s) or 0) >= 50 else None,
        })
    floorplans = sorted(({"code": f["code"], "views_per_user": f["views_per_user"]}
                         for f in c.raw.get("floorplans") or [] if f.get("views_per_user")),
                        key=lambda f: -val(f["views_per_user"]))
    c.need("website", "sessions", sessions, "GA4 has no connector on the portal yet.")

    if val(sessions) is not None and val(users) is not None and val(engagement) is not None:
        takeaway = (f"{fmt_int(val(sessions))} sessions from {fmt_int(val(users))} users, "
                    f"{fmt_pct(val(engagement))} engaged")
        if floorplans:
            top = floorplans[0]
            takeaway += (f"; floor plan {top['code']} drew the most views per user "
                         f"({fmt_ratio(val(top['views_per_user']))}).")
        else:
            takeaway += "."
    else:
        takeaway = f"Website analytics aren't connected for {c.label} yet."

    notes = []
    if val(v("ga_avg_engagement_seconds")) is not None:
        notes.append(f"Average engagement time was {fmt_int(val(v('ga_avg_engagement_seconds')))} seconds.")
    for r in rows:
        if r["label"]:
            conv = f" and {fmt_int(val(r['conversions']))} conversions" if val(r["conversions"]) is not None else ""
            bounce = fmt_pct(val(r["bounce_rate"]))
            notes.append(f"{r['name']} sent {fmt_int(val(r['sessions']))} sessions with {article(bounce)} "
                         f"{bounce} bounce rate{conv}; we're reviewing where those "
                         "visitors land.")
    if val(v("ga_floorplan_views")) is not None:
        notes.append(f"The floor plans page was viewed {fmt_int(val(v('ga_floorplan_views')))} times.")
    return {
        "label": SECTION_LABELS["website"],
        "takeaway": takeaway,
        "sessions": sessions,
        "users": users,
        "engaged_sessions": engaged,
        "engagement_rate": engagement,
        "avg_engagement_seconds": v("ga_avg_engagement_seconds"),
        "conversions": v("ga_conversions"),
        "floorplan_page_views": v("ga_floorplan_views"),
        "sources": rows,
        "floorplans": floorplans,
        "note": " ".join(notes),
    }


def _benchmarks(c: _Ctx) -> dict:
    b = c.raw.get("benchmarks") or {}
    out = {}
    for k, default in DEFAULT_BENCHMARKS.items():
        out[k] = b.get(k) if isinstance(b.get(k), dict) else receipt(default, SRC_BENCHMARK, c.as_of)
    return out


def _paid_search(c: _Ctx) -> dict:
    v = c.get
    bench = _benchmarks(c)
    leads = c.first_touch("created", PPC_SOURCE)
    leases = c.first_touch("leased", PPC_SOURCE)
    ctr, conv = v("ads_ctr"), v("ads_conversions")
    c.need("paid_search", "impressions", v("ads_impressions"), "The Google Ads API has no connector on the portal yet.")
    if val(ctr) is not None:
        lo, hi = val(bench["ctr_low"]), val(bench["ctr_high"])
        where = "above" if val(ctr) > hi else ("within" if val(ctr) >= lo else "below")
        takeaway = (f"{fmt_pct(val(ctr), 2)} click-through rate, {where} the {fmt_int(lo * 100)}–"
                    f"{fmt_pct(hi, 0)} benchmark")
        if val(conv) is not None and val(leads) is not None:
            takeaway += (f"; we're aligning {fmt_int(val(conv))} ad conversions with the "
                         f"{fmt_int(val(leads))} leads in the CRM.")
        else:
            takeaway += "."
    else:
        takeaway = f"Paid search results aren't connected for {c.label} yet."
    note = ""
    if val(conv) is not None and val(leads) is not None:
        lease_txt = f", with {fmt_int(val(leases))} leases" if val(leases) is not None else ""
        note = (f"Google Ads recorded {fmt_int(val(conv))} conversions, and {fmt_int(val(leads))} leads with Google "
                f"Ads as their first source reached the CRM{lease_txt}. We're aligning the conversion definition "
                "with CRM leads before the next budget cycle.")
    return {
        "label": SECTION_LABELS["paid_search"],
        "takeaway": takeaway,
        "impressions": v("ads_impressions"),
        "clicks": v("ads_clicks"),
        "ctr": ctr,
        "cpc": v("ads_cpc"),
        "platform_spend": v("ads_spend"),
        "ad_conversions": conv,
        "cost_per_conversion": v("ads_cost_per_conversion"),
        "leads": leads,
        "leases": leases,
        "benchmark": bench,
        "note": note,
    }


def _reputation(c: _Ctx) -> dict:
    target = receipt(REPUTATION_TARGET, SRC_TARGET, c.as_of)
    platforms = []
    for p in c.raw.get("reputation") or []:
        score = p.get("score")
        status = "Not connected" if score is None else ("At target" if val(score) >= REPUTATION_TARGET else "Below target")
        platforms.append({"name": p["name"], "score": score, "reviews": p.get("reviews"), "target": target,
                          "status": status})
    if not platforms:
        c.need("reputation", "platforms", None, "Reputation has no connector on the portal yet.")
    scored = [p for p in platforms if p["score"]]
    if scored:
        parts = " and ".join(f"{p['name']} {fmt_ratio(val(p['score']), 1)}" for p in scored)
        at = all(p["status"] == "At target" for p in scored)
        takeaway = parts + (f", {'both' if len(scored) == 2 else 'all'} at or above the {fmt_ratio(REPUTATION_TARGET, 1)} target"
                            if at and len(scored) > 1 else "")
        missing = [p["name"] for p in platforms if not p["score"]]
        takeaway += (f"; {' and '.join(missing)} aren't connected yet." if len(missing) > 1 else
                     f"; {missing[0]} isn't connected yet." if missing else ".")
    else:
        takeaway = f"Reputation scores aren't connected for {c.label} yet."
    return {"label": SECTION_LABELS["reputation"], "takeaway": takeaway, "target": target, "platforms": platforms,
            "note": ""}


def _listings(c: _Ctx) -> dict:
    raw = c.raw.get("listings")
    placements = None
    if raw:
        placements = [{"name": p["name"], "tier": None, "impressions": p.get("impressions"), "leads": p.get("leads"),
                       "media_views": p.get("media_views"), "cost": None} for p in raw]
    else:
        c.need("listings", "placements", None, f"No listing placement data for {c.label}.")
    if placements:
        p = placements[0]
        takeaway = f"{p['name']}: {fmt_int(val(p['impressions']) or 0)} impressions and {fmt_int(val(p['leads']) or 0)} leads."
        note = "Placement tiers and costs aren't in the listing feed yet."
    else:
        takeaway = f"No listing placement data for {c.label} yet."
        note = "Connecting the placement feed will add cost per prospect, visit and lease for each listing service."
    return {"label": SECTION_LABELS["listings"], "takeaway": takeaway, "placements": placements, "note": note}


def _actions(c: _Ctx, sec: dict) -> list[dict]:
    out = []
    spend, web, funnel, occ = sec["spend"], sec["website"], sec["funnel"], sec["occupancy"]
    ps = sec["paid_search"]

    diff = _google_ads_spend_differs(c)
    conv, leads = val(ps["ad_conversions"]), val(ps["leads"])
    if diff or (conv is not None and leads is not None and conv > 3 * max(leads, 1)):
        detail = []
        if diff:
            detail.append(f"The spend manager shows {fmt_money(val(diff[0]))} and the ad platform "
                          f"{fmt_money(val(diff[1]))}.")
        if conv is not None and leads is not None:
            detail.append(f"The platform recorded {fmt_int(conv)} conversions, and {fmt_int(leads)} leads reached "
                          "the CRM.")
        detail.append("We'll align both before the next budget cycle.")
        out.append({"title": "Reconcile Google Ads spend and conversion tracking before renewal",
                    "detail": " ".join(detail),
                    "stake_label": f"{fmt_money(val(diff[0]))} monthly budget" if diff else f"{fmt_int(conv)} conversions",
                    "lens": "evolve", "clause": "reconciling Google Ads spend and conversion tracking before renewal"})

    priced = [r for r in spend["vendors"] if val(r["cost_per_lead"]) is not None]
    zillow_vendor = next((r for r in priced if r["name"].lower() == "zillow"), None)
    zillow_web = next((r for r in web["sources"] if r["name"].lower() == "zillow"), None)
    if zillow_vendor and zillow_web and zillow_web["label"] and \
            val(zillow_vendor["cost_per_lead"]) == min(val(r["cost_per_lead"]) for r in priced):
        out.append({"title": "Keep Zillow in the plan and improve where its visitors land",
                    "detail": (f"Zillow had the lowest cost per lead at {fmt_money(val(zillow_vendor['cost_per_lead']))}. "
                               f"Its {fmt_int(val(zillow_web['sessions']))} website sessions had "
                               f"{article(fmt_pct(val(zillow_web['bounce_rate'])))} "
                               f"{fmt_pct(val(zillow_web['bounce_rate']))} bounce rate, so we're testing a landing "
                               "page that matches the listing."),
                    "stake_label": f"{fmt_int(val(zillow_web['sessions']))} sessions", "lens": "tailor",
                    "clause": "improving where Zillow visitors land"})

    net, mi, mo = val(occ["net_move_ins"]), val(occ["move_ins"]), val(occ["move_outs"])
    if net is not None and net < 0:
        out.append({"title": "Bring retention into next month's plan",
                    "detail": (f"{fmt_int(mo)} move-outs and {fmt_int(mi)} move-ins put net move-ins at {fmt_int(net)}. "
                               "We'll bring renewal and resident-retention ideas to the next owner call."),
                    "stake_label": f"{fmt_int(net)} net move-ins", "lens": "evolve",
                    "clause": "bringing retention into the plan"})

    stages = {s["key"]: s for s in funnel["stages"]}
    a2l, l2l = val(stages["applied"]["days_to_next"]), val(funnel["lead_to_lease_days"])
    s2t = val(stages["scheduled"]["days_to_next"])
    if a2l is not None and l2l and a2l >= 0.4 * l2l:
        extra = f" Schedule to tour runs at {fmt_days(s2t)} days, so" if s2t is not None else ""
        out.append({"title": "Shorten the time from application to lease",
                    "detail": (f"Application to lease took {fmt_days(a2l)} of the {fmt_days(l2l)} days from lead to "
                               f"lease.{extra} we'll review the approval steps with the leasing team."
                               if extra else f"Application to lease took {fmt_days(a2l)} of the {fmt_days(l2l)} days "
                               "from lead to lease. We'll review the approval steps with the leasing team."),
                    "stake_label": f"{fmt_days(a2l)} days", "lens": "evolve",
                    "clause": f"working with the leasing team on the {fmt_days(a2l)} days from application to lease"})

    if web["floorplans"]:
        top = web["floorplans"][0]
        out.append({"title": f"Review floor plan {top['code']} pricing and availability against demand",
                    "detail": (f"Floor plan {top['code']} drew {fmt_ratio(val(top['views_per_user']))} views per user, "
                               "the most of any plan on the site."),
                    "stake_label": f"{fmt_ratio(val(top['views_per_user']))} views per user", "lens": "tailor",
                    "clause": f"reviewing floor plan {top['code']} pricing and availability"})

    for a in out:
        a["work_item_id"] = None
    return screen_actions(out)


def _discrepancies(c: _Ctx, sec: dict) -> list[dict]:
    out = []
    diff = _google_ads_spend_differs(c)
    if diff:
        out.append({"key": "google_ads_spend",
                    "text": (f"Google Ads spend differs between the spend manager ({fmt_money(val(diff[0]))}) and the "
                             f"ad platform ({fmt_money(val(diff[1]))}); we're reconciling before renewal."),
                    "values": [diff[0], diff[1]]})

    pairs = []
    for vd in c.raw.get("vendors") or []:
        src = vd.get("first_touch_source")
        vendor_leads = c.first_touch("created", src)
        match = next((s for s in c.raw.get("spend_by_source") or [] if s["name"] == src and s.get("leads")), None)
        if val(vendor_leads) is not None and match and val(match["leads"]) != val(vendor_leads):
            pairs.append((vd["name"], vendor_leads, match["leads"]))
    if pairs:
        bits = [f"{n} shows {fmt_int(val(a))} leads by vendor and {fmt_int(val(b))} by spend source" for n, a, b in pairs]
        out.append({"key": "lead_basis",
                    "text": "Lead counts depend on the basis: " + "; ".join(bits) + ". This report uses the vendor basis.",
                    "values": [r for _, a, b in pairs for r in (a, b)]})

    ps = sec["paid_search"]
    if val(ps["ad_conversions"]) is not None and val(ps["leads"]) is not None and \
            val(ps["ad_conversions"]) > 3 * max(val(ps["leads"]), 1):
        out.append({"key": "ads_conversions_vs_leads",
                    "text": (f"Google Ads recorded {fmt_int(val(ps['ad_conversions']))} conversions, while "
                             f"{fmt_int(val(ps['leads']))} leads list Google Ads as their first source; we're aligning "
                             "the conversion definition."),
                    "values": [ps["ad_conversions"], ps["leads"]]})

    med = {r["name"].lower(): r for r in sec["attribution"]["by_medium"].get("created", [])}
    ppc = c.first_touch("created", PPC_SOURCE)
    if "cpc" in med and val(ppc) is not None and val(med["cpc"]["count"]) != val(ppc):
        out.append({"key": "medium_vs_source",
                    "text": (f"{fmt_int(val(med['cpc']['count']))} new lead is tagged paid search by medium, while "
                             f"{fmt_int(val(ppc))} list Google Ads as their source; we're reviewing the tags."
                             if val(med["cpc"]["count"]) == 1 else
                             f"{fmt_int(val(med['cpc']['count']))} new leads are tagged paid search by medium, while "
                             f"{fmt_int(val(ppc))} list Google Ads as their source; we're reviewing the tags."),
                    "values": [med["cpc"]["count"], ppc]})

    occ = sec["occupancy"]
    if val(occ["average_occupancy_rate"]) is not None and val(occ["occupied_rate"]) is not None and \
            abs(val(occ["average_occupancy_rate"]) - val(occ["occupied_rate"])) >= 0.01:
        out.append({"key": "occupancy_basis",
                    "text": (f"Occupied ({fmt_pct(val(occ['occupied_rate']), 2)}) is the month-end reading on "
                             f"{fmt_int(val(occ['total_units']))} units; Hyly's daily average "
                             f"({fmt_pct(val(occ['average_occupancy_rate']), 2)}) uses a different unit count."),
                    "values": [occ["occupied_rate"], occ["average_occupancy_rate"], occ["total_units"]]})
    return out


_SOURCE_PHRASES = (
    ("hyly_lake.", "Hyly data lake (occupancy, leasing and lead sources)"),
    ("hyly_export.", "Hyly data-lake dashboard export (spend manager, multi-touch attribution, website and Google Ads)"),
    ("portal_bq.apartmentscom", "apartments.com listing feed"),
    ("june_report_design.", "reputation scores from the June report"),
    ("workspace_benchmarks.", "RPM benchmarks"),
)


def _sources_note(report: dict, c: _Ctx) -> str:
    used = {r["source"] for r in iter_receipts(report)}
    phrases = []
    for prefix, phrase in _SOURCE_PHRASES:
        if any(s.startswith(prefix) or (s.startswith("derived:") and False) for s in used):
            phrases.append(phrase)
    start = c.raw.get("period_start") or month_bounds(c.month)[0]
    end = c.as_of or month_bounds(c.month)[1]
    period = f"{int(start[8:10])}–{int(end[8:10])} {c.mname} {start[:4]}"
    return f"{period} · " + " · ".join(phrases) if phrases else period


def assemble(raw: dict) -> dict:
    """raw -> report. Pure: no I/O, no clock, no LLM."""
    c = _Ctx(raw)
    sec = {
        "occupancy": _occupancy(c),
        "funnel": _funnel(c),
        "spend": _spend(c),
        "attribution": _attribution(c),
        "website": _website(c),
        "paid_search": _paid_search(c),
        "reputation": _reputation(c),
        "listings": _listings(c),
    }
    actions = _actions(c, sec)
    occ, funnel = sec["occupancy"], sec["funnel"]

    leases = c.get("leased")
    cost_per_lease = derived(c.get("total_spend"), leases, "total_spend/leased", c.as_of) if val(leases) else None
    if val(leases) is not None:
        s1 = f"{fmt_int(val(leases))} {'lease' if val(leases) == 1 else 'leases'} in {c.mname}"
        s1 += f" at {fmt_money(val(cost_per_lease))} average cost per lease." if cost_per_lease else "."
    else:
        s1 = f"Leasing results for {c.label} aren't available yet."
    s2 = ""
    if val(occ["leased_rate"]) is not None:
        s2 = f"{fmt_pct(val(occ['leased_rate']))} leased"
        if val(occ["future_leases"]) is not None:
            s2 += f", with {fmt_int(val(occ['future_leases']))} signed residents moving in"
        if val(occ["net_move_ins"]) is not None and val(occ["net_move_ins"]) < 0:
            s2 += (f"; {fmt_int(val(occ['move_outs']))} move-outs and {fmt_int(val(occ['move_ins']))} move-ins put net "
                   f"move-ins at {fmt_int(val(occ['net_move_ins']))}.")
        else:
            s2 += "."
    clauses = [a["clause"] for a in actions[:2]]
    s3 = f"Next month we're {' and '.join(clauses)}." if clauses else ""
    summary_text = " ".join(s for s in (s1, s2, s3) if s)
    for a in actions:
        a.pop("clause", None)

    key_numbers = [
        {"key": "leased_rate", "label": "Leased", "value": occ["leased_rate"],
         "sub": f"{fmt_int(val(occ['total_units']))} units" if val(occ["total_units"]) is not None else None},
        {"key": "occupied_rate", "label": "Occupied", "value": occ["occupied_rate"],
         "sub": (f"{fmt_int(val(occ['occupied']))} of {fmt_int(val(occ['total_units']))}"
                 if val(occ["occupied"]) is not None and val(occ["total_units"]) is not None else None)},
        {"key": "net_move_ins", "label": "Net move-ins", "value": occ["net_move_ins"],
         "sub": (f"{fmt_int(val(occ['move_ins']))} in / {fmt_int(val(occ['move_outs']))} out"
                 if val(occ["net_move_ins"]) is not None else None)},
        {"key": "leads_created", "label": "New leads", "value": c.get("created"),
         "sub": f"{fmt_int(val(c.get('scheduled')))} scheduled a tour" if val(c.get("scheduled")) is not None else None},
        {"key": "cost_per_lease", "label": "Cost per lease", "value": cost_per_lease,
         "sub": "total spend ÷ leases" if cost_per_lease else None},
    ]
    c.need("summary", "cost_per_lease", cost_per_lease, "Needs spend, which isn't connected for this month.")

    if c.month == "2026-06":
        c.need("attribution", "june_backfill", None,
               "June 1–5, 2026 includes a historical backfill in one Hyly feed (ADR 0022). Lead counts here follow "
               "Hyly's created-lead definition; compare June with other months with care.")

    report = {
        "property": {k: raw.get("property", {}).get(k) for k in ("company_id", "name", "city", "state", "units")},
        "month": c.month,
        "month_label": c.label,
        "available_months": list(raw.get("available_months") or [c.month]),
        "as_of": c.as_of,
        "summary": {
            "lead": {"value": leases, "label": "lease" if val(leases) == 1 else "leases",
                     "sub": f"{fmt_money(val(cost_per_lease))} average cost per lease" if cost_per_lease else None},
            "text": summary_text,
            "polished": False,
        },
        "key_numbers": key_numbers,
        **sec,
        "actions": actions,
        "actions_takeaway": (f"{len(actions)} steps for next month, starting with "
                             f"{actions[0]['title'][0].lower() + actions[0]['title'][1:]}."
                             if actions else "No new steps this month."),
        "discrepancies": _discrepancies(c, sec),
        "gaps": c.gaps,
    }
    report["sources_note"] = _sources_note(report, c)
    return report


# ── Optional LLM polish ─────────────────────────────────────────────────────

POLISH_FLAG = "WORKSPACE_REPORT_LLM_POLISH"

_POLISH_SYSTEM = (
    "You edit the opening summary of a monthly apartment marketing report that goes to property owners. "
    "Keep it calm, plain and constructive: results first, then what RPM is doing next. Use only numbers that "
    "appear in the facts, written the same way. Do not add numbers, comparisons or claims. Two or three "
    "sentences. Never use the words: " + ", ".join(BANNED_PHRASES) + ". Return only the summary text."
)


def polish_enabled() -> bool:
    return os.environ.get(POLISH_FLAG, "").strip().lower() in TRUTHY


def polish_summary(report: dict, *, complete: Optional[Callable[..., Any]] = None) -> dict:
    """Rewrite summary.text through the LLM gateway when the flag is on.

    The rewrite is kept only if validate_polished() passes; otherwise the
    deterministic template stands. Any failure leaves the template in place.
    """
    if not polish_enabled():
        return report
    try:
        if complete is None:
            from skills import llm_gateway
            if not llm_gateway.is_configured():
                return report
            complete = llm_gateway.complete
        facts = {"month": report["month_label"], "summary_draft": report["summary"]["text"],
                 "key_numbers": [{"label": k["label"], "value": val(k["value"]), "sub": k["sub"]}
                                 for k in report["key_numbers"]],
                 "next_steps": [a["title"] for a in report["actions"][:3]]}
        resp = complete(json.dumps(facts, ensure_ascii=False), system=_POLISH_SYSTEM, model="sonnet",
                        max_tokens=600, purpose="workspace_report_polish")
        text = (getattr(resp, "text", resp) or "").strip()
        ok, problems = validate_polished(text, report)
        if ok:
            report["summary"]["text"] = text
            report["summary"]["polished"] = True
        else:
            logger.info("workspace_report: polish rejected (%s); keeping template", problems)
    except Exception as exc:  # noqa: BLE001 — polish is optional; the template is the answer
        logger.warning("workspace_report: polish failed, keeping template: %s", exc)
    return report


# ── Live gathering (read-only) ──────────────────────────────────────────────

QueryFn = Callable[[str, list], list]


def _default_query(sql: str, params: list) -> list[dict]:
    import bigquery_client
    from google.cloud import bigquery

    client = bigquery_client._get_client()
    cfg = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter(n, t, v) for n, t, v in params],
        maximum_bytes_billed=MAX_BYTES_BILLED,
    )
    return [dict(r.items()) for r in client.query(sql, job_config=cfg).result()]


class LakeReader:
    """Read-only access to the Hyly lake, limited to the library's allowlist."""

    def __init__(self, query: Optional[QueryFn] = None, dataset: Optional[str] = None,
                 org_id: Optional[str] = None):
        self._query = query or _default_query
        self.dataset = dataset or os.environ.get("WORKSPACE_REPORT_LAKE_DATASET") or DEFAULT_LAKE_DATASET
        self.org_id = org_id or os.environ.get("WORKSPACE_REPORT_HYLY_ORG_ID") or DEFAULT_HYLY_ORG_ID

    def ref(self, obj: str) -> str:
        if obj not in LAKE_ALLOWLIST:
            raise SourceError(f"{obj} is not in the metric library allowlist")
        return f"`{self.dataset}.{obj}`"

    @property
    def lease_object(self) -> str:
        return f"pai_journey_{self.org_id}"

    def query(self, sql: str, params: list) -> list[dict]:
        if re.search(r"\b(insert|update|delete|merge|create|drop|alter|truncate)\b", sql, re.IGNORECASE):
            raise SourceError("the report reader is read-only")
        try:
            return self._query(sql, params)
        except SourceError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SourceError(f"lake query failed: {exc}") from exc


def _iso(d: Any) -> Optional[str]:
    if d is None:
        return None
    if isinstance(d, datetime):
        return d.date().isoformat()
    if isinstance(d, date):
        return d.isoformat()
    return str(d)[:10]


def _num(x: Any) -> Any:
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return int(f) if f.is_integer() else f


def available_months(lake: LakeReader, pid: int, today: date) -> list[str]:
    rows = lake.query(f"SELECT MIN(as_of_date) AS first FROM {lake.ref('t_oc_agg_occupancy_property')} "
                      "WHERE property_id = @pid", [("pid", "INT64", pid)])
    first = _iso(rows[0]["first"]) if rows and rows[0].get("first") else None
    if not first:
        return []
    last = last_complete_month(today)
    return months_between(first[:7], last) if first[:7] <= last else []


_FT_STAGES = {
    # stage: (date column, dedup expression, exclude Leased?)
    "created": ("contact_created_date", "CONCAT(contact_name,'|',IFNULL(contact_sub_status,''))", True),
    "scheduled": ("first_scheduled_dt", "contact_id", False),
    "toured": ("first_completed_dt", "contact_id", False),
    "applied": ("first_application_dt", "contact_id", False),
}


def gather_live(identity: Any, month: str, lake: LakeReader, *, today: date,
                city: Optional[str] = None, state: Optional[str] = None,
                available: Optional[list[str]] = None,
                ils_query: Optional[QueryFn] = None) -> dict:
    """Read one property-month from the portal's connectors into `raw`."""
    start, end = month_bounds(month)
    capped_end = min(end, today.isoformat())
    pid = int(identity.hyly_property_id)
    P = [("pid", "INT64", pid), ("start", "DATE", start), ("end", "DATE", capped_end)]
    values: dict[str, Any] = {}
    gaps: list[dict] = []

    occ = lake.query(
        "SELECT as_of_date, total_units, rentable, occupied, vacant, available, vacant_rented, vacant_unrented, "
        f"notice_rented, notice_unrented, leased_future, excluded FROM {lake.ref('t_oc_agg_occupancy_property')} "
        "WHERE property_id = @pid AND as_of_date <= @end ORDER BY as_of_date DESC LIMIT 1", P)
    as_of = end
    if occ and _iso(occ[0]["as_of_date"]) and _iso(occ[0]["as_of_date"]) >= start:
        row = occ[0]
        as_of = _iso(row["as_of_date"])
        for k in ("total_units", "rentable", "occupied", "vacant", "available", "vacant_rented", "vacant_unrented",
                  "notice_rented", "notice_unrented", "leased_future", "excluded"):
            values[k] = receipt(_num(row.get(k)), SRC_OCCUPANCY, as_of)
        if as_of != end:
            gaps.append({"section": "occupancy", "metric": "reading_date",
                         "reason": f"The latest occupancy reading in {month_label(month)} is from {as_of}."})
    else:
        gaps.append({"section": "occupancy", "metric": "snapshot",
                     "reason": f"No occupancy reading for {month_label(month)}."})

    act = lake.query(
        "SELECT SUM(IF(event_type = 'move_in', count, 0)) AS move_ins, SUM(IF(event_type = 'move_out', count, 0)) "
        f"AS move_outs FROM {lake.ref('t_ot_agg_resident_activity_property')} "
        "WHERE property_id = @pid AND date BETWEEN @start AND @end", P)
    if act:
        values["move_ins"] = receipt(_num(act[0].get("move_ins")), SRC_ACTIVITY, as_of)
        values["move_outs"] = receipt(_num(act[0].get("move_outs")), SRC_ACTIVITY, as_of)

    ops = lake.query(
        f"SELECT as_of_date, delayed_move_ins FROM {lake.ref('t_oc_agg_occupancy_operational')} "
        "WHERE property_id = @pid AND as_of_date <= @end ORDER BY as_of_date DESC LIMIT 1", P)
    if ops and _iso(ops[0]["as_of_date"]) and _iso(ops[0]["as_of_date"]) >= start:
        values["delayed_move_ins"] = receipt(_num(ops[0].get("delayed_move_ins")), SRC_OPERATIONAL,
                                             _iso(ops[0]["as_of_date"]))

    rate = lake.query(
        f"SELECT AVG(SAFE_DIVIDE(occupied, units)) AS value FROM {lake.ref('t_occupancy_rate')} "
        "WHERE property_id = @pid AND DATE(created_at) BETWEEN @start AND @end", P)
    if rate and rate[0].get("value") is not None:
        values["avg_occupancy_rate"] = receipt(round(float(rate[0]["value"]), 6), SRC_OCC_RATE, as_of)

    ca = lake.ref("t_contact_activity")
    counts = lake.query(
        "SELECT "
        "COUNT(DISTINCT IF(DATE(contact_created_date) BETWEEN @start AND @end AND contact_status != 'Leased', "
        "contact_name, NULL)) AS created, "
        "COUNT(DISTINCT IF(DATE(first_scheduled_dt) BETWEEN @start AND @end, contact_id, NULL)) AS scheduled, "
        "COUNT(DISTINCT IF(DATE(first_completed_dt) BETWEEN @start AND @end, contact_id, NULL)) AS toured, "
        "COUNT(DISTINCT IF(DATE(first_application_dt) BETWEEN @start AND @end, contact_id, NULL)) AS applied "
        f"FROM {ca} WHERE property_id = @pid", P)
    if counts:
        for k in ("created", "scheduled", "toured", "applied"):
            values[k] = receipt(_num(counts[0].get(k)), SRC_CONTACT, as_of)

    lease_ref = lake.ref(lake.lease_object)
    leased = lake.query(
        f"SELECT COUNT(DISTINCT contact_id) AS value FROM {lease_ref} WHERE property_id = @pid "
        "AND event_name = 'h_ms_lease' AND DATE(event_date) BETWEEN @start AND @end", P)
    if leased:
        values["leased"] = receipt(_num(leased[0].get("value")), SRC_LEASE, as_of)

    net = lake.query(
        "SELECT COUNT(DISTINCT IF(event_type = 'pms_Application', contact_id, NULL)) - "
        "COUNT(DISTINCT IF(event_type = 'pms_CancelApplication', contact_id, NULL)) AS value "
        f"FROM {lake.ref('prospect_journey')} WHERE property_id = @pid AND DATE(activity_dt) BETWEEN @start AND @end", P)
    if net:
        values["net_applied"] = receipt(_num(net[0].get("value")), SRC_PROSPECT_JOURNEY, as_of)

    first_touch: dict[str, Any] = {}
    by_medium: dict[str, Any] = {}
    for dim, target in (("mta_first_source_name", first_touch), ("mta_first_medium_name", by_medium)):
        for stage, (col, dedup, exclude) in _FT_STAGES.items():
            if stage == "created" and dim == "mta_first_medium_name":
                dedup = "contact_name"
            where = " AND contact_status != 'Leased'" if exclude else ""
            rows = lake.query(
                f"SELECT {dim} AS label, COUNT(DISTINCT {dedup}) AS value FROM {ca} WHERE property_id = @pid "
                f"AND DATE({col}) BETWEEN @start AND @end{where} GROUP BY label ORDER BY value DESC", P)
            target[stage] = [{"name": r["label"] or "(none)", "count": receipt(_num(r["value"]), SRC_CONTACT, as_of)}
                             for r in rows]
            target[f"{stage}__complete"] = True
        rows = lake.query(
            f"WITH ev AS (SELECT DISTINCT contact_id FROM {lease_ref} WHERE property_id = @pid "
            "AND event_name = 'h_ms_lease' AND DATE(event_date) BETWEEN @start AND @end), "
            f"src AS (SELECT contact_id, ANY_VALUE({dim}) AS label FROM {ca} WHERE property_id = @pid GROUP BY contact_id) "
            "SELECT s.label, COUNT(DISTINCT ev.contact_id) AS value FROM ev LEFT JOIN src s USING (contact_id) "
            "GROUP BY s.label ORDER BY value DESC", P)
        target["leased"] = [{"name": r["label"] or "(none)", "count": receipt(_num(r["value"]), SRC_LEASE, as_of)}
                            for r in rows]
        target["leased__complete"] = True

    gaps += [
        {"section": "funnel", "metric": "days_to_next",
         "reason": "Days per step aren't defined in the metric library yet."},
        {"section": "spend", "metric": "vendors",
         "reason": "Spend by vendor lives in an object outside the metric library allowlist; /api/budget reports "
                   "the contracted plan, not spend."},
        {"section": "attribution", "metric": "influenced",
         "reason": "Multi-touch influence isn't defined in the metric library yet."},
        {"section": "website", "metric": "ga4", "reason": "GA4 has no connector on the portal yet."},
        {"section": "paid_search", "metric": "google_ads", "reason": "The Google Ads API has no connector on the portal yet."},
        {"section": "reputation", "metric": "platforms", "reason": "Reputation has no connector on the portal yet."},
    ]

    listings = _gather_listings(identity, start, capped_end, as_of, gaps, ils_query)

    return {
        "property": {"company_id": identity.company_id, "name": identity.name, "city": city, "state": state,
                     "units": values.get("total_units")},
        "month": month,
        "period_start": start,
        "as_of": as_of,
        "available_months": available or [month],
        "values": values,
        "vendors": [],
        "spend_by_source": [],
        "first_touch": first_touch,
        "by_medium": by_medium,
        "influence": None,
        "website_sources": [],
        "floorplans": [],
        "listings": listings,
        "reputation": [],
        "benchmarks": None,
        "gaps": gaps,
    }


def _gather_listings(identity: Any, start: str, end: str, as_of: str, gaps: list,
                     ils_query: Optional[QueryFn]) -> Optional[list[dict]]:
    project = os.environ.get("BIGQUERY_PROJECT_ID")
    dataset = os.environ.get("BIGQUERY_DATASET_PROD")
    if not (identity.uuid and project and dataset):
        gaps.append({"section": "listings", "metric": "placements",
                     "reason": "The apartments.com listing feed isn't configured for this property."})
        return None
    sql = ("SELECT SUM(total_impressions) AS impressions, SUM(total_leads) AS leads, "
           f"SUM(total_media_views) AS media_views FROM `{project}.{dataset}.apartmentscom_ils_resolved_v1` "
           "WHERE property_uuid = @uuid AND record_date BETWEEN @start AND @end")
    try:
        rows = (ils_query or _default_query)(sql, [("uuid", "STRING", identity.uuid), ("start", "DATE", start),
                                                    ("end", "DATE", end)])
    except Exception as exc:  # noqa: BLE001
        gaps.append({"section": "listings", "metric": "placements",
                     "reason": f"The apartments.com listing query failed: {str(exc)[:160]}"})
        return None
    if not rows or rows[0].get("impressions") is None:
        gaps.append({"section": "listings", "metric": "placements", "reason": "No apartments.com rows this month."})
        return None
    r = rows[0]
    return [{"name": "Apartments.com",
             "impressions": receipt(_num(r["impressions"]), SRC_ILS, as_of),
             "leads": receipt(_num(r["leads"]), SRC_ILS, as_of),
             "media_views": receipt(_num(r["media_views"]), SRC_ILS, as_of)}]


def _resolve(company_id: str):
    from skills import property_resolver

    try:
        identity = property_resolver.resolve(company_id)
    except LookupError as exc:
        raise PropertyUnavailable(str(exc)) from exc
    if not identity.hyly_property_id or not str(identity.hyly_property_id).isdigit():
        raise PropertyUnavailable("This property isn't in the Hyly reporting beta yet.")
    return identity


def _city_state(company_id: str) -> tuple[Optional[str], Optional[str]]:
    try:
        import hubspot_client
        props = hubspot_client.get_company(company_id, ["city", "state"]) or {}
        return props.get("city") or None, props.get("state") or None
    except Exception as exc:  # noqa: BLE001 — cosmetic; the report stands without it
        logger.info("workspace_report: city/state lookup failed for %s: %s", company_id, exc)
        return None, None


def build_report(company_id: str, month: Optional[str] = None, *, today: Optional[date] = None,
                 lake: Optional[LakeReader] = None) -> dict:
    """The report for one property-month, from live connectors."""
    today = today or date.today()
    if month is not None:
        parse_month(month)
    identity = _resolve(company_id)
    lake = lake or LakeReader()
    months = available_months(lake, int(identity.hyly_property_id), today)
    month = month or (months[-1] if months else None)
    if not month or month not in months:
        raise MonthUnavailable(f"No report data for {month or 'any month'} at this property.")
    city, state = _city_state(company_id)
    raw = gather_live(identity, month, lake, today=today, city=city, state=state, available=months)
    return polish_summary(assemble(raw))
