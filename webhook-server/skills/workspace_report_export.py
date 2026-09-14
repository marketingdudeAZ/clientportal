"""Read Hyly's data-lake dashboard export into the report's raw metrics.

The export ("Data Lake CSV Dashboard") is the definitive statement of what
Hyly reports for one property and month. This module turns it into the same
`raw` dict the live assembler builds from BigQuery, so that:

  * the Workspace fixture is produced by the same `assemble()` as live data
    (the tone rules and receipts cannot drift between the two), and
  * the reconciliation compares like with like: export raw vs. live raw.

It never invents a value. A breakdown row that the export omits is 0 only
when the rows it does carry already account for the whole stage (their shares
sum to 100%); otherwise the value is None and a gap is recorded.
"""

from __future__ import annotations

import csv
import re
from typing import Any, Optional

from skills.workspace_report import (
    SRC_ACTIVITY,
    SRC_CONTACT,
    SRC_EXPORT_ADS,
    SRC_EXPORT_LISTING,
    SRC_EXPORT_MTA,
    SRC_EXPORT_SPEND,
    SRC_EXPORT_VELOCITY,
    SRC_EXPORT_WEBSITE,
    SRC_LEASE,
    SRC_OCCUPANCY,
    SRC_OCC_RATE,
    SRC_OPERATIONAL,
    SRC_PROSPECT_JOURNEY,
    receipt,
)

FUNNEL_STAGE_METRICS = {
    "created": "Newly Created Leads",
    "scheduled": "1st Scheduled",
    "toured": "1st Toured",
    "applied": "1st Applied",
    "net_applied": "Net Applied",
    "leased": "Leased",
}

INFLUENCE_STAGE_METRICS = {
    "created": "Created Prospects",
    "scheduled": "Scheduled Prospects",
    "toured": "Toured Prospects",
    "applied": "Applied Prospects",
    "leased": "Leased Prospects",
}

# Spend Manager vendor labels -> (display name, first-touch lead source label).
# Meta has no first-touch source: "Social Posting" is organic posting, not the
# paid social budget, so mapping them would invent a lead count.
VENDOR_MAP = {
    "google ads": ("Google Ads", "Google PayPerClick (PPC)"),
    "zillow per month": ("Zillow", "Zillow"),
    "paid social budget (meta)": ("Paid social (Meta)", None),
}

WEBSITE_SOURCE_LIMIT = 4


def parse_number(text: str) -> tuple[Optional[float], dict]:
    """Parse an export cell. Returns (value, flags).

    "$15,715.00 " -> 15715.0 · "5.69%" -> 0.0569 (flags.percent) ·
    "21.5K" -> 21500.0 (flags.approx) · "55s" -> 55.0 · "" -> None.
    """
    flags: dict = {}
    s = (text or "").strip().replace("$", "").replace(",", "").replace(" ", "")
    if not s:
        return None, flags
    s = s.replace("−", "-")
    mult = 1.0
    if s.endswith("%"):
        s, mult = s[:-1], 0.01
        flags["percent"] = True
    elif s.endswith("K"):
        s, mult = s[:-1], 1000.0
        flags["approx"] = True
    elif s.endswith("s"):
        s = s[:-1]
    try:
        value = float(s) * mult
    except ValueError:
        return None, flags
    if flags.get("percent"):
        value = round(value, 6)
    return value, flags


def _num(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return int(value) if float(value).is_integer() else value


def parse_export(path: str) -> dict:
    """Split the export into its four tables plus the header block."""
    out: dict[str, Any] = {"meta": {}, "summary": [], "rankings": [], "trends": [], "ratios": []}
    table = None
    header: Optional[list[str]] = None
    names = {
        "summary metrics": "summary",
        "rankings by dimension": "rankings",
        "trend & milestone charts": "trends",
        "ratio cards": "ratios",
    }
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.reader(fh):
            first = (row[0] if row else "").strip()
            if first.startswith("###"):
                table = names.get(first.strip("# ").strip().lower())
                header = None
                continue
            if not any(c.strip() for c in row):
                continue
            if table is None:
                out["meta"][first] = row[1].strip() if len(row) > 1 else ""
                continue
            if header is None:
                header = [h.strip() for h in row]
                continue
            out[table].append({h: (row[i] if i < len(row) else "") for i, h in enumerate(header) if h})
    return out


class ExportReader:
    """Lookups over a parsed export."""

    def __init__(self, parsed: dict):
        self.parsed = parsed
        meta = parsed["meta"]
        m = re.search(r"^(.*?)\s*\(ID:\s*(\d+)\)", meta.get("Property", ""))
        self.property_name = m.group(1).strip() if m else meta.get("Property", "")
        self.hyly_property_id = m.group(2) if m else None
        dr = re.findall(r"\d{4}-\d{2}-\d{2}", meta.get("Date Range", ""))
        self.period_start = dr[0] if dr else None
        self.period_end = dr[1] if len(dr) > 1 else None
        self.month = self.period_start[:7] if self.period_start else None

    def summary(self, section: str, *, field: str = "", name: str = "",
                data_source: str = "") -> tuple[Optional[float], dict]:
        for r in self.parsed["summary"]:
            if r.get("Section") != section:
                continue
            if field and r.get("Metric Field") != field:
                continue
            if name and r.get("Metric Name") != name:
                continue
            if data_source and r.get("Data Source") != data_source:
                continue
            return parse_number(r.get("Value", ""))
        return None, {}

    def ratio(self, field: str) -> Optional[float]:
        for r in self.parsed["ratios"]:
            if r.get("Metric Field") == field:
                return parse_number(r.get("Value", ""))[0]
        return None

    def ranking(self, section: str, metric: str, dimension: str) -> list[dict]:
        """Union of Top and Bottom rows for one breakdown, by label, in rank order."""
        seen: dict[str, dict] = {}
        for r in self.parsed["rankings"]:
            if (r.get("Section"), r.get("Metric Name"), r.get("Dimension (Breakdown)")) != (section, metric, dimension):
                continue
            label = r.get("Label", "")
            if label in seen:
                continue
            value, flags = parse_number(r.get("Value", ""))
            share, _ = parse_number(r.get("% Share", ""))
            rank_text = (r.get("Rank") or "").strip()
            seen[label] = {"label": label, "value": value, "share": share,
                           "share_raw": (r.get("% Share") or "").strip(),
                           "rank": int(rank_text) if rank_text.isdigit() else None, "flags": flags}
        return sorted(seen.values(), key=lambda x: (x["rank"] or 999))


def _complete(rows: list[dict]) -> bool:
    """True when a breakdown's shares already sum to (about) 100%."""
    shares = [r["share"] for r in rows if r.get("share") is not None]
    return bool(rows) and len(shares) == len(rows) and sum(shares) >= 0.995


def raw_from_export(path: str, *, company_id: Optional[str] = None,
                    city: Optional[str] = None, state: Optional[str] = None,
                    reputation: Optional[list[dict]] = None,
                    reputation_source: str = "june_report_design.reputation",
                    benchmarks: Optional[dict] = None,
                    available_months: Optional[list[str]] = None) -> dict:
    """Build the assembler's raw input from the export file at `path`."""
    ex = ExportReader(parse_export(path))
    as_of = ex.period_end
    values: dict[str, Any] = {}
    reported: dict[str, Any] = {}
    gaps: list[dict] = []

    def put(key: str, value: Optional[float], source: str, **extra) -> None:
        values[key] = receipt(_num(value), source, as_of, **extra)

    # Occupancy: stocks at month end, flows over the month.
    for key in ("total_units", "rentable", "occupied", "vacant", "available", "vacant_rented",
                "vacant_unrented", "notice_rented", "notice_unrented", "leased_future", "excluded"):
        put(key, ex.summary("Occupancy", field=key)[0], SRC_OCCUPANCY)
    put("move_ins", ex.summary("Occupancy", field="move_ins")[0], SRC_ACTIVITY)
    put("move_outs", ex.summary("Occupancy", field="move_outs")[0], SRC_ACTIVITY)
    put("delayed_move_ins", ex.summary("Occupancy", field="delayed_move_ins")[0], SRC_OPERATIONAL)
    put("avg_occupancy_rate", ex.summary("Occupancy", field="occupancy_rate")[0], SRC_OCC_RATE)
    for key in ("pct_leased", "pct_occupied", "pct_exposure", "pct_trend", "net_move_ins"):
        reported[key] = ex.summary("Occupancy", field=key)[0]

    # Funnel counts.
    put("created", ex.summary("Lead Generation", field="created_contact")[0], SRC_CONTACT)
    put("scheduled", ex.summary("Lead Generation", field="scheduled_contact")[0], SRC_CONTACT)
    put("toured", ex.summary("Lead Generation", field="toured_contact")[0], SRC_CONTACT)
    put("applied", ex.summary("Lead Generation", field="applied_contact")[0], SRC_CONTACT)
    put("net_applied", ex.summary("Lead Generation", field="net_applied_contact")[0], SRC_PROSPECT_JOURNEY)
    put("leased", ex.summary("Lead Generation", field="leased_contact")[0], SRC_LEASE)

    # Days per step: Hyly's velocity cards. Median vs. mean is not documented.
    for key, field in (("days_created_to_scheduled", "created_to_scheduled_contact_velocity"),
                       ("days_scheduled_to_toured", "scheduled_to_toured_contact_velocity"),
                       ("days_toured_to_applied", "toured_to_applied_contact_velocity"),
                       ("days_applied_to_leased", "applied_to_leased_contact_velocity"),
                       ("days_created_to_leased", "created_to_leased_contact_velocity")):
        v = ex.ratio(field)
        put(key, round(v, 4) if v is not None else None, SRC_EXPORT_VELOCITY)
    gaps.append({"section": "funnel", "metric": "days_to_next",
                 "reason": "Days per step are Hyly's velocity figures; the metric library doesn't define "
                           "them yet, so whether they're medians or averages is unconfirmed."})

    # Spend.
    put("total_spend", ex.summary("Spend Manager", field="total_spend", data_source="All Sources")[0],
        SRC_EXPORT_SPEND)
    reported["cost_per_lease"] = ex.summary("Spend Manager", field="cost_per_leased_contact",
                                            data_source="All Sources")[0]
    vendors = []
    for row in ex.ranking("Spend Manager", "Total Spend", "Vendors"):
        display, ft_source = VENDOR_MAP.get(row["label"].lower(), (row["label"], None))
        vendors.append({"name": display, "raw_label": row["label"], "first_touch_source": ft_source,
                        "spend": receipt(_num(row["value"]), SRC_EXPORT_SPEND, as_of),
                        "reported_share": row["share"]})
        if ft_source is None:
            gaps.append({"section": "spend", "metric": f"{display} leads",
                         "reason": f"No first-touch lead source maps to the {display} budget, so its leads and "
                                   "leases aren't attributed."})
    spend_by_source = []
    cpl_by_source = {r["label"]: r for r in ex.ranking("Spend Manager", "Cost per Created Lead", "Spend Sources")}
    cpl_by_vendor = {r["label"]: r for r in ex.ranking("Spend Manager", "Cost per Created Lead", "Vendors")}
    for row in ex.ranking("Spend Manager", "Total Spend", "Spend Sources"):
        cpl = cpl_by_source.get(row["label"])
        spend_by_source.append({
            "name": row["label"],
            "spend": receipt(_num(row["value"]), SRC_EXPORT_SPEND, as_of),
            "leads": receipt(_num(parse_number(cpl["share_raw"])[0]), SRC_EXPORT_SPEND, as_of) if cpl else None,
        })
    vendor_leads_reported = {}
    for label, r in cpl_by_vendor.items():
        vendor_leads_reported[label] = _num(parse_number(r["share_raw"])[0])
    reported["vendor_leads"] = vendor_leads_reported

    # First touch by source and medium.
    first_touch: dict[str, list[dict]] = {}
    by_medium: dict[str, list[dict]] = {}
    source_for_stage = {"created": SRC_CONTACT, "scheduled": SRC_CONTACT, "toured": SRC_CONTACT,
                        "applied": SRC_CONTACT, "net_applied": SRC_PROSPECT_JOURNEY, "leased": SRC_LEASE}
    for stage, metric in FUNNEL_STAGE_METRICS.items():
        for dim, target in (("Lead Gen Sources", first_touch), ("Lead Gen Mediums", by_medium)):
            rows = ex.ranking("Lead Generation", metric, dim)
            if not rows:
                continue
            target[stage] = [{"name": r["label"],
                              "count": receipt(_num(r["value"]), source_for_stage[stage], as_of)}
                             for r in rows]
            target[stage + "__complete"] = _complete(rows)  # type: ignore[assignment]

    # Multi-touch influence.
    influence = None
    prospects = {}
    for stage in INFLUENCE_STAGE_METRICS:
        v = ex.summary("Multi-Touch Attribution", field=f"{stage}_prospect")[0]
        prospects[stage] = receipt(_num(v), SRC_EXPORT_MTA, as_of)
    if any(prospects.values()):
        per_stage = {stage: ex.ranking("Multi-Touch Attribution", metric, "Influencing Sources")
                     for stage, metric in INFLUENCE_STAGE_METRICS.items()}
        labels: list[str] = []
        for rows in per_stage.values():
            for r in rows:
                if r["label"] not in labels:
                    labels.append(r["label"])
        sources = []
        for label in labels:
            entry: dict[str, Any] = {"name": label}
            for stage, rows in per_stage.items():
                hit = next((r for r in rows if r["label"] == label), None)
                if hit is not None:
                    entry[stage] = receipt(_num(hit["value"]), SRC_EXPORT_MTA, as_of)
                elif _complete(rows):
                    entry[stage] = receipt(0, SRC_EXPORT_MTA, as_of)
                else:
                    entry[stage] = None
            sources.append(entry)
        influence = {"prospects": prospects, "sources": sources}

    # Website (GA4 as landed in the lake).
    put("ga_users", ex.summary("Website (GA4)", field="user")[0], SRC_EXPORT_WEBSITE)
    put("ga_sessions", ex.summary("Website (GA4)", field="sessions")[0], SRC_EXPORT_WEBSITE)
    put("ga_engaged_sessions", ex.summary("Website (GA4)", field="engaged_sessions")[0], SRC_EXPORT_WEBSITE)
    put("ga_conversions", ex.summary("Website (GA4)", field="total_conversions")[0], SRC_EXPORT_WEBSITE)
    put("ga_views", ex.summary("Website (GA4)", field="views")[0], SRC_EXPORT_WEBSITE)
    put("ga_avg_engagement_seconds", ex.summary("Website (GA4)", field="average_engagement_time")[0],
        SRC_EXPORT_WEBSITE)
    reported["engagement_rate"] = ex.summary("Website (GA4)", field="engagement_rate")[0]
    reported["bounce_rate"] = ex.summary("Website (GA4)", field="bounce_rate")[0]
    page_views = {r["label"]: r for r in ex.ranking("Website (GA4)", "Views", "Website Page Paths")}
    fp = page_views.get("/floorplans/")
    put("ga_floorplan_views", fp["value"] if fp else None, SRC_EXPORT_WEBSITE)

    sessions = ex.ranking("Website (GA4)", "Sessions", "Website Sources")
    engaged = {r["label"]: r for r in ex.ranking("Website (GA4)", "Engaged Sessions", "Website Sources")}
    conversions = {r["label"]: r for r in ex.ranking("Website (GA4)", "Total Conversions",
                                                     "Website Conversion Events")}
    bounce = {r["label"]: r for r in ex.ranking("Website (GA4)", "Bounce Rate", "Website Sources")}
    website_sources = []
    for r in sessions[:WEBSITE_SOURCE_LIMIT]:
        e = engaged.get(r["label"])
        c = conversions.get(r["label"])
        website_sources.append({
            "name": r["label"],
            "sessions": receipt(_num(r["value"]), SRC_EXPORT_WEBSITE, as_of),
            "engaged_sessions": receipt(_num(e["value"]), SRC_EXPORT_WEBSITE, as_of) if e else None,
            "conversions": receipt(_num(c["value"]), SRC_EXPORT_WEBSITE, as_of) if c else None,
            "reported_bounce_rate": bounce[r["label"]]["value"] if r["label"] in bounce else None,
        })

    floorplans = []
    for r in ex.ranking("Website (GA4)", "Views per User", "Website Page Paths"):
        m = re.search(r"/floorplans/([a-z0-9.\-]+)/?$", r["label"], re.IGNORECASE)
        if m:
            floorplans.append({"code": m.group(1).upper(),
                               "views_per_user": receipt(_num(r["value"]), SRC_EXPORT_WEBSITE, as_of)})

    # Google Ads.
    imp, imp_flags = ex.summary("Google Ads", name="Ad Impressions")
    values["ads_impressions"] = receipt(_num(imp), SRC_EXPORT_ADS, as_of,
                                        **({"approx": True} if imp_flags.get("approx") else {}))
    put("ads_clicks", ex.summary("Google Ads", name="Ad Clicks")[0], SRC_EXPORT_ADS)
    put("ads_ctr", ex.summary("Google Ads", name="Ad Click through Rate")[0], SRC_EXPORT_ADS)
    put("ads_cpc", ex.summary("Google Ads", name="Ad Cost per Click")[0], SRC_EXPORT_ADS)
    put("ads_conversions", ex.summary("Google Ads", name="Ad Conversions")[0], SRC_EXPORT_ADS)
    put("ads_cost_per_conversion", ex.summary("Google Ads", name="Ad Cost per Conversion")[0], SRC_EXPORT_ADS)
    put("ads_spend", ex.summary("Google Ads", name="Ad Total Spend")[0], SRC_EXPORT_ADS)

    # Listings: the export's listing section carries apartments.com metrics
    # (search/details impressions, phone/email leads, 3D tour views).
    listings = None
    impressions = ex.summary("Hayley", field="impressions")[0]
    if impressions is not None:
        listings = [{
            "name": "Apartments.com",
            "impressions": receipt(_num(impressions), SRC_EXPORT_LISTING, as_of),
            "leads": receipt(_num(ex.summary("Hayley", field="leads")[0]), SRC_EXPORT_LISTING, as_of),
            "media_views": receipt(_num(ex.summary("Hayley", field="media_views")[0]), SRC_EXPORT_LISTING, as_of),
        }]
        gaps.append({"section": "listings", "metric": "tier_and_cost",
                     "reason": "Placement tier and cost aren't in the listing feed yet. Chat, tour and "
                               "application lead types aren't populated, so they're left out rather than "
                               "shown as zero."})

    rep = []
    for p in reputation or []:
        score = p.get("score")
        reviews = p.get("reviews")
        rep.append({"name": p["name"],
                    "score": receipt(score, reputation_source, as_of) if score is not None else None,
                    "reviews": receipt(reviews, reputation_source, as_of) if reviews is not None else None})
        if score is None:
            gaps.append({"section": "reputation", "metric": p["name"], "reason": "Not connected"})
    if reputation:
        gaps.append({"section": "reputation", "metric": "scores",
                     "reason": "Reputation scores come from the June report design; the lake export doesn't "
                               "carry them, so they aren't reconciled."})

    return {
        "property": {"company_id": company_id, "name": ex.property_name, "city": city, "state": state,
                     "units": values.get("total_units"), "hyly_property_id": ex.hyly_property_id},
        "month": ex.month,
        "period_start": ex.period_start,
        "as_of": as_of,
        "available_months": available_months or ([ex.month] if ex.month else []),
        "values": values,
        "reported": reported,
        "vendors": vendors,
        "spend_by_source": spend_by_source,
        "first_touch": first_touch,
        "by_medium": by_medium,
        "influence": influence,
        "website_sources": website_sources,
        "floorplans": floorplans,
        "listings": listings,
        "reputation": rep,
        "benchmarks": benchmarks,
        "gaps": gaps,
    }
