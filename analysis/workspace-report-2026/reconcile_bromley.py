"""Reconcile the Workspace report for The Bromley at Brighton Crossing, June 2026.

    python3 analysis/workspace-report-2026/reconcile_bromley.py \
        --export "/path/to/halo_bromley_june_metrics.csv" \
        [--live --env-file /path/to/.env] \
        > analysis/workspace-report-2026/bromley-june-reconciliation.md

Two comparisons, both read-only:

1. Export vs. report. Hyly's dashboard export is the definitive statement of
   what Hyly reports. Every export metric the report carries is compared with
   the value the report assembler produces from it (skills/workspace_report.py
   recomputes rates, shares and costs from components, so this is a real test
   of the aggregation rules, not an echo).
2. Live. With --live, the property is resolved in HubSpot by its stored Hyly id
   and the assembler's live gatherer runs against the lake through the
   portal's BigQuery client. Anything that cannot be reached is recorded with
   the reason. No secret is ever printed.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "webhook-server"))

from skills import workspace_report as wr  # noqa: E402
from skills import workspace_report_export as wx  # noqa: E402

REPUTATION = [{"name": "Google", "score": 4.3, "reviews": 135}, {"name": "Apartments.com", "score": 4.0, "reviews": 5},
              {"name": "Yelp"}, {"name": "Facebook"}]


def decimals(text: str) -> int:
    s = (text or "").strip().replace("$", "").replace(",", "").replace("%", "").replace("K", "").replace("s", "")
    return len(s.split(".")[1]) if "." in s else 0


class Table:
    def __init__(self):
        self.rows: list[dict] = []

    def add(self, section: str, metric: str, csv_text: Optional[str], report_value: Any, *, kind: str = "num",
            share_rule: bool = False, reason: str = "", live: Any = None, live_reason: str = "") -> None:
        csv_text = (csv_text or "").strip()
        csv_val, flags = wx.parse_number(csv_text)
        status, shown = "unreachable", "—"
        if report_value is None:
            status = "unreachable"
            reason = reason or "Not in the report input."
        elif csv_val is None:
            status = "unreachable"
            reason = reason or "Not in the export."
        else:
            dp = decimals(csv_text)
            if kind == "pct":
                pv = report_value * 100
                cv = csv_val * 100
                if share_rule:
                    ok = abs(wr._half_up(pv, 1) - wr._half_up(cv, 1)) < 1e-9
                else:
                    ok = abs(wr._half_up(pv, dp) - cv) < 10 ** -dp / 2 + 1e-9
                shown = f"{wr._half_up(pv, dp):.{dp}f}%"
            elif flags.get("approx"):
                ok = abs(wr._half_up(report_value / 1000, 1) * 1000 - csv_val) < 1e-6
                shown = wr.fmt_compact(report_value)
            else:
                ok = abs(wr._half_up(report_value, dp) - csv_val) < 10 ** -dp / 2 + 1e-9 or \
                    abs(wr._truncate(report_value, dp) - csv_val) < 1e-9
                shown = f"{report_value:,.{dp}f}" if isinstance(report_value, float) else f"{report_value:,}"
            status = "match" if ok else "mismatch"
        self.rows.append({"section": section, "metric": metric, "csv": csv_text or "—", "report": shown,
                          "status": status, "reason": reason, "live": live, "live_reason": live_reason})


def summary_text(ex: wx.ExportReader, section: str, *, field: str = "", name: str = "", source: str = "") -> str:
    for r in ex.parsed["summary"]:
        if r.get("Section") == section and (not field or r.get("Metric Field") == field) and \
                (not name or r.get("Metric Name") == name) and (not source or r.get("Data Source") == source):
            return r.get("Value", "")
    return ""


def ratio_text(ex: wx.ExportReader, field: str) -> str:
    return next((r.get("Value", "") for r in ex.parsed["ratios"] if r.get("Metric Field") == field), "")


def v(r: Any) -> Any:
    return wr.val(r)


def build(export: str, live_raw: Optional[dict], live_reason: str) -> tuple[Table, dict, dict]:
    ex = wx.ExportReader(wx.parse_export(export))
    raw = wx.raw_from_export(export, company_id="26136316506", city="Brighton", state="Colorado",
                             reputation=REPUTATION)
    rep = wr.assemble(raw)
    lv = (live_raw or {}).get("values", {})
    t = Table()

    def live_of(key: str):
        if live_raw is None:
            return None, live_reason
        r = lv.get(key)
        return (v(r), "") if r else (None, "Not returned by the live gatherer.")

    occ = rep["occupancy"]
    for field, label, value, kind, key in [
        ("total_units", "Total units", v(occ["total_units"]), "num", "total_units"),
        ("occupied", "Occupied units", v(occ["occupied"]), "num", "occupied"),
        ("vacant", "Vacant units", v(occ["vacant"]), "num", "vacant"),
        ("available", "Available units", v(occ["available"]), "num", "available"),
        ("vacant_rented", "Vacant rented", v(occ["vacant_rented"]), "num", "vacant_rented"),
        ("vacant_unrented", "Vacant unrented", v(occ["vacant_unrented"]), "num", "vacant_unrented"),
        ("leased_future", "Future leases", v(occ["future_leases"]), "num", "leased_future"),
        ("move_ins", "Move-ins", v(occ["move_ins"]), "num", "move_ins"),
        ("move_outs", "Move-outs", v(occ["move_outs"]), "num", "move_outs"),
        ("net_move_ins", "Net move-ins (recomputed)", v(occ["net_move_ins"]), "num", None),
        ("delayed_move_ins", "Delayed move-ins", v(occ["delayed_move_ins"]), "num", "delayed_move_ins"),
        ("pct_leased", "Leased rate (recomputed)", v(occ["leased_rate"]), "pct", None),
        ("pct_occupied", "Current occupancy (recomputed)", v(occ["occupied_rate"]), "pct", None),
        ("pct_exposure", "Exposure rate (recomputed)", v(occ["exposure_rate"]), "pct", None),
        ("occupancy_rate", "Average occupancy rate", v(occ["average_occupancy_rate"]), "pct", "avg_occupancy_rate"),
    ]:
        lval, lreason = live_of(key) if key else (None, live_reason if live_raw is None else "Derived in the report.")
        t.add("Occupancy", label, summary_text(ex, "Occupancy", field=field), value, kind=kind, live=lval,
              live_reason=lreason)

    stages = {s["key"]: s for s in rep["funnel"]["stages"]}
    for field, label, key in [("created_contact", "Newly created leads", "created"),
                              ("scheduled_contact", "1st scheduled", "scheduled"),
                              ("toured_contact", "1st toured", "toured"),
                              ("applied_contact", "1st applied", "applied"),
                              ("leased_contact", "Leased", "leased")]:
        lval, lreason = live_of(key)
        t.add("Funnel", label, summary_text(ex, "Lead Generation", field=field), v(stages[key]["count"]),
              live=lval, live_reason=lreason)
    lval, lreason = live_of("net_applied")
    t.add("Funnel", "Net applied", summary_text(ex, "Lead Generation", field="net_applied_contact"),
          v(rep["funnel"]["net_applied"]), live=lval, live_reason=lreason)
    for field, label, key in [("created_to_scheduled_contact_velocity", "Days lead → scheduled", "created"),
                              ("scheduled_to_toured_contact_velocity", "Days scheduled → toured", "scheduled"),
                              ("toured_to_applied_contact_velocity", "Days toured → applied", "toured"),
                              ("applied_to_leased_contact_velocity", "Days applied → leased", "applied")]:
        t.add("Funnel", label, ratio_text(ex, field), v(stages[key]["days_to_next"]),
              live_reason="No definition in the metric library; the live gatherer returns a gap.")
    t.add("Funnel", "Days lead → leased", ratio_text(ex, "created_to_leased_contact_velocity"),
          v(rep["funnel"]["lead_to_lease_days"]),
          live_reason="No definition in the metric library; the live gatherer returns a gap.")
    for field, label, num, den in [("created_to_scheduled_contact_ratio", "Created → scheduled rate", "scheduled", "created"),
                                   ("scheduled_to_toured_contact_ratio", "Scheduled → toured rate", "toured", "scheduled"),
                                   ("toured_to_applied_contact_ratio", "Toured → applied rate", "applied", "toured"),
                                   ("applied_to_leased_contact_ratio", "Applied → leased rate", "leased", "applied")]:
        csv_text = ratio_text(ex, field)
        csv_pct = f"{float(csv_text) * 100:.1f}%" if csv_text else ""
        t.add("Funnel", label, csv_pct, v(stages[num]["rate"]), kind="pct",
              reason="Different definitions: Hyly's ratio card is a cohort conversion; the report divides the "
                     "month's step counts (the June report designs did the same).",
              live_reason="Derived in the report.")

    vendors = {r["name"]: r for r in rep["spend"]["vendors"]}
    na_spend = "Outside the metric library allowlist; the live gatherer returns a gap."
    t.add("Spend", "Total spend", summary_text(ex, "Spend Manager", field="total_spend", source="All Sources"),
          v(rep["spend"]["total"]), live_reason=na_spend)
    cpl = next(k for k in rep["key_numbers"] if k["key"] == "cost_per_lease")
    t.add("Spend", "Cost per lease (recomputed)", summary_text(ex, "Spend Manager", field="cost_per_leased_contact",
                                                                source="All Sources"), v(cpl["value"]),
          live_reason=na_spend)
    for row in ex.ranking("Spend Manager", "Total Spend", "Vendors"):
        name = wx.VENDOR_MAP.get(row["label"].lower(), (row["label"], None))[0]
        t.add("Spend", f"{name} spend", _money(row["value"]), v(vendors[name]["spend"]), live_reason=na_spend)
        t.add("Spend", f"{name} share of spend (recomputed)", row["share_raw"], v(vendors[name]["share"]), kind="pct",
              share_rule=True, live_reason=na_spend)
    for row in ex.ranking("Spend Manager", "Cost per Created Lead", "Vendors"):
        name = wx.VENDOR_MAP.get(row["label"].lower(), (row["label"], None))[0]
        t.add("Spend", f"{name} cost per lead (recomputed)", _money(row["value"], 2), v(vendors[name]["cost_per_lead"]),
              live_reason=na_spend)
        t.add("Spend", f"{name} leads (vendor basis)", row["share_raw"], v(vendors[name]["leads"]), live_reason=na_spend)
    for row in ex.ranking("Spend Manager", "Cost per Created Lead", "Spend Sources"):
        ft = next((s for s in raw["first_touch"]["created"] if s["name"] == row["label"]), None)
        t.add("Spend", f"{wr.source_display(row['label'])} leads (spend-source basis)", row["share_raw"],
              v(ft["count"]) if ft else None,
              reason="The spend-source basis counts about twice the first-touch leads; the report uses the "
                     "vendor basis and lists this under known discrepancies.", live_reason=na_spend)

    ft_live = {r["name"]: v(r["count"]) for r in (live_raw or {}).get("first_touch", {}).get("created", [])}
    for row in ex.ranking("Lead Generation", "Newly Created Leads", "Lead Gen Sources"):
        src = next((s for s in rep["attribution"]["sources"] if s["source_label"] == row["label"]), None)
        t.add("First touch", f"New leads · {wr.source_display(row['label'])}", _count(row["value"]),
              v(src["created"]) if src else None, live=ft_live.get(row["label"]) if live_raw else None,
              live_reason=live_reason if live_raw is None else "")
    for stage, metric in (("scheduled", "1st Scheduled"), ("toured", "1st Toured"), ("applied", "1st Applied"),
                          ("leased", "Leased")):
        rows = {r["name"]: r for r in raw["first_touch"].get(stage, [])}
        for row in ex.ranking("Lead Generation", metric, "Lead Gen Sources"):
            t.add("First touch", f"{metric} · {wr.source_display(row['label'])}", _count(row["value"]),
                  v(rows[row["label"]]["count"]) if row["label"] in rows else None,
                  live_reason=live_reason if live_raw is None else "")
    for row in ex.ranking("Lead Generation", "Newly Created Leads", "Lead Gen Mediums"):
        med = next((m for m in rep["attribution"]["by_medium"].get("created", []) if m["name"] == row["label"]), None)
        t.add("First touch", f"New leads by medium · {row['label']}", _count(row["value"]),
              v(med["count"]) if med else None, live_reason=live_reason if live_raw is None else "")
        t.add("First touch", f"Medium share · {row['label']} (recomputed)", row["share_raw"],
              v(med["share"]) if med else None, kind="pct", share_rule=True, live_reason="Derived in the report.")

    na_mta = "Multi-touch influence isn't defined in the metric library; the live gatherer returns a gap."
    prospects = rep["attribution"]["prospects"]
    for stage in wr.INFLUENCE_STAGES:
        t.add("Multi-touch", f"{stage.title()} prospects", summary_text(ex, "Multi-Touch Attribution",
                                                                        field=f"{stage}_prospect"),
              v(prospects[stage]), live_reason=na_mta)
    for stage, metric in wx.INFLUENCE_STAGE_METRICS.items():
        for row in ex.ranking("Multi-Touch Attribution", metric, "Influencing Sources"):
            src = next((s for s in rep["attribution"]["sources"] if s["source_label"] == row["label"]), None)
            value = v(src["stages"][stage]) if src and src["stages"] else None
            t.add("Multi-touch", f"{metric} · {wr.source_display(row['label'])}", _count(row["value"]), value,
                  live_reason=na_mta)

    web = rep["website"]
    na_ga = "GA4 has no connector on the portal; the live gatherer returns a gap."
    for field, label, value, kind in [("user", "Users", v(web["users"]), "num"),
                                      ("sessions", "Sessions", v(web["sessions"]), "num"),
                                      ("engaged_sessions", "Engaged sessions", v(web["engaged_sessions"]), "num"),
                                      ("total_conversions", "Conversions", v(web["conversions"]), "num"),
                                      ("engagement_rate", "Engagement rate (recomputed)", v(web["engagement_rate"]), "pct"),
                                      ("average_engagement_time", "Average engagement time (s)",
                                       v(web["avg_engagement_seconds"]), "num")]:
        t.add("Website", label, summary_text(ex, "Website (GA4)", field=field), value, kind=kind, live_reason=na_ga)
    bounce = summary_text(ex, "Website (GA4)", field="bounce_rate")
    t.add("Website", "Bounce rate (1 − engagement, recomputed)", bounce,
          1 - v(web["engagement_rate"]) if web["engagement_rate"] else None, kind="pct", live_reason=na_ga)
    fp = next((r for r in ex.ranking("Website (GA4)", "Views", "Website Page Paths") if r["label"] == "/floorplans/"), None)
    t.add("Website", "Floor plans page views", _count(fp["value"]) if fp else "", v(web["floorplan_page_views"]),
          live_reason=na_ga)
    by_name = {s["name"]: s for s in web["sources"]}
    display = {"google": "Google", "(direct)": "Direct", "bing": "Bing"}
    engaged = {r["label"]: r for r in ex.ranking("Website (GA4)", "Engaged Sessions", "Website Sources")}
    conv = {r["label"]: r for r in ex.ranking("Website (GA4)", "Total Conversions", "Website Conversion Events")}
    bounce_rows = {r["label"]: r for r in ex.ranking("Website (GA4)", "Bounce Rate", "Website Sources")}
    for row in ex.ranking("Website (GA4)", "Sessions", "Website Sources")[:wx.WEBSITE_SOURCE_LIMIT]:
        s = by_name.get(display.get(row["label"], row["label"]))
        t.add("Website", f"Sessions · {row['label']}", _count(row["value"]), v(s["sessions"]), live_reason=na_ga)
        if row["label"] in engaged:
            t.add("Website", f"Engaged sessions · {row['label']}", _count(engaged[row["label"]]["value"]),
                  v(s["engaged_sessions"]), live_reason=na_ga)
        if row["label"] in conv:
            t.add("Website", f"Conversions · {row['label']}", _count(conv[row["label"]]["value"]), v(s["conversions"]),
                  live_reason=na_ga)
        if row["label"] in bounce_rows:
            t.add("Website", f"Bounce rate · {row['label']} (recomputed)", f"{bounce_rows[row['label']]['value'] * 100:.2f}%",
                  v(s["bounce_rate"]), kind="pct", live_reason=na_ga)
    plans = {f["code"]: f for f in web["floorplans"]}
    for row in ex.ranking("Website (GA4)", "Views per User", "Website Page Paths"):
        if "/floorplans/" in row["label"] and row["label"] != "/floorplans/":
            code = row["label"].rstrip("/").split("/")[-1].upper()
            t.add("Website", f"Views per user · floor plan {code}", f"{row['value']:.2f}",
                  v(plans[code]["views_per_user"]) if code in plans else None, live_reason=na_ga)

    ps = rep["paid_search"]
    na_ads = "The Google Ads API has no connector on the portal; the live gatherer returns a gap."
    for name, label, value, kind in [("Ad Impressions", "Impressions", v(ps["impressions"]), "num"),
                                     ("Ad Clicks", "Clicks", v(ps["clicks"]), "num"),
                                     ("Ad Click through Rate", "Click-through rate", v(ps["ctr"]), "pct"),
                                     ("Ad Cost per Click", "Cost per click", v(ps["cpc"]), "num"),
                                     ("Ad Conversions", "Ad conversions", v(ps["ad_conversions"]), "num"),
                                     ("Ad Cost per Conversion", "Cost per conversion", v(ps["cost_per_conversion"]), "num"),
                                     ("Ad Total Spend", "Platform spend", v(ps["platform_spend"]), "num")]:
        t.add("Paid search", label, summary_text(ex, "Google Ads", name=name), value, kind=kind, live_reason=na_ads)
    clicks, impressions = v(ps["clicks"]), v(ps["impressions"])
    t.add("Paid search", "Click-through rate recomputed from clicks ÷ impressions",
          summary_text(ex, "Google Ads", name="Ad Click through Rate"), clicks / impressions, kind="pct",
          reason="The export rounds impressions to 21.5K, so recomputing gives 5.21% against the platform's 5.20%. "
                 "The report shows the platform's reported rate.", live_reason=na_ads)
    t.add("Paid search", "Cost per conversion recomputed from spend ÷ conversions",
          summary_text(ex, "Google Ads", name="Ad Cost per Conversion"),
          v(ps["platform_spend"]) / v(ps["ad_conversions"]),
          reason="Spend is shown rounded to the dollar in the export; the report shows the platform's reported "
                 "cost per conversion.", live_reason=na_ads)

    listing = (rep["listings"]["placements"] or [{}])[0]
    na_ils = (live_reason if live_raw is None else "Reads apartmentscom_ils_resolved_v1 by uuid when configured.")
    for field, label, key in [("impressions", "Listing impressions", "impressions"), ("leads", "Listing leads", "leads"),
                              ("media_views", "Listing media views", "media_views")]:
        t.add("Listings", label, summary_text(ex, "Hayley", field=field), v(listing.get(key)), live_reason=na_ils)
    return t, rep, raw


def _money(value: Optional[float], dp: int = 0) -> str:
    return "" if value is None else f"${value:,.{dp}f}"


def _count(value: Optional[float]) -> str:
    if value is None:
        return ""
    return f"{int(value):,}" if float(value).is_integer() else f"{value}"


def try_live(env_file: Optional[str]) -> tuple[Optional[dict], str, list[str]]:
    notes: list[str] = []
    if env_file:
        try:
            from dotenv import load_dotenv
            load_dotenv(env_file, override=True)
        except Exception as exc:  # noqa: BLE001
            return None, f"Could not load the env file: {type(exc).__name__}.", notes
    hyly_id = "1865695607790353330"
    identity = None
    try:
        import hubspot_client
        rows = hubspot_client.search_companies(
            [{"propertyName": "hyly_property_id", "operator": "EQ", "value": hyly_id}],
            ["name", "hyly_property_id", "uuid", "city", "state"])
        if len(rows) == 1:
            p = rows[0].get("properties", {})
            notes.append(f"HubSpot: company {rows[0].get('id')} ({p.get('name')}, {p.get('city')}, {p.get('state')}) "
                         f"carries hyly_property_id {hyly_id}, the id in the export header. Resolved by stored id, not "
                         "by name (ADR 0022 §8).")

            class Identity:
                company_id = rows[0].get("id")
                name = p.get("name")
                hyly_property_id = hyly_id
                uuid = p.get("uuid")

            identity = Identity()
        else:
            notes.append(f"HubSpot: {len(rows)} companies carry hyly_property_id {hyly_id}; expected exactly one.")
    except Exception as exc:  # noqa: BLE001
        notes.append(f"HubSpot lookup unavailable: {type(exc).__name__}.")
    if identity is None:
        return None, "The property could not be resolved in HubSpot.", notes
    try:
        raw = wr.gather_live(identity, "2026-06", wr.LakeReader(), today=date.today())
        return raw, "", notes
    except wr.SourceError as exc:
        text = str(exc)
        if "SERVICE_ACCOUNT" in text or "credentials" in text.lower():
            reason = ("Lake unreachable locally: the configured BigQuery service-account key file isn't present on "
                      "this machine.")
        else:
            reason = f"Lake query failed: {text[:200]}"
        return None, reason, notes


def render(t: Table, rep: dict, live_raw: Optional[dict], live_reason: str, notes: list[str], export: str) -> str:
    counts = {s: sum(1 for r in t.rows if r["status"] == s) for s in ("match", "mismatch", "unreachable")}
    live_counts = {"match": 0, "mismatch": 0, "unreachable": 0}
    for r in t.rows:
        if r["live"] is None:
            live_counts["unreachable"] += 1
        else:
            try:
                csv_val, _ = wx.parse_number(r["csv"])
                ok = csv_val is not None and abs(float(r["live"]) - csv_val) < 0.5
            except (TypeError, ValueError):
                ok = False
            live_counts["match" if ok else "mismatch"] += 1
    out = [
        "# Workspace report reconciliation — The Bromley at Brighton Crossing, June 2026",
        "",
        f"*Generated {date.today().isoformat()} by `analysis/workspace-report-2026/reconcile_bromley.py`. Read-only.*",
        "",
        "Definitive source: Hyly's data-lake dashboard export for this property and month (680 metric rows). "
        "Definitions: the Halo metric library.",
        "",
        "## Summary",
        "",
        "| Comparison | Match | Mismatch | Unreachable |",
        "|---|---|---|---|",
        f"| Export vs. report assembler | {counts['match']} | {counts['mismatch']} | {counts['unreachable']} |",
        f"| Export vs. live lake (portal BigQuery client) | {live_counts['match']} | {live_counts['mismatch']} | "
        f"{live_counts['unreachable']} |",
        "",
        "**Live status.** " + (live_reason or "Live gatherer ran.") + (
            " The Hyly MCP connector, the other read-only path, needed re-authorization, so no lake query could run "
            "from this session. Every lake value below is therefore unverified live; the SQL the assembler would run "
            "is untested against the warehouse (see *Live SQL to verify*)." if live_raw is None else ""),
        "",
    ]
    if notes:
        out += ["**Live reads that did run.**", ""] + [f"- {n}" for n in notes] + [""]
    out += [
        "## Mismatches",
        "",
        "| Section | Metric | Export | Report | Reason |",
        "|---|---|---|---|---|",
    ]
    out += [f"| {r['section']} | {r['metric']} | {r['csv']} | {r['report']} | {r['reason']} |"
            for r in t.rows if r["status"] == "mismatch"] or ["| — | none | | | |"]
    out += [
        "",
        "## Biggest discrepancies worth Kyle's attention",
        "",
        "These are differences between sources, not errors in the report. Each is either shown on the report under "
        "*Known discrepancies* or listed as an open question.",
        "",
        "1. **Google Ads spend: $12,140 (spend manager) vs. $5,269 (ad platform).** A $6,871 gap, 2.3×. The report "
        "uses the spend manager for vendor spend and the platform for paid-search efficiency, and states both.",
        "2. **Funnel conversion: step rates vs. Hyly's ratio cards.** June step counts give 35.9% created → scheduled; "
        "Hyly's ratio card gives 20.7%. Toured → applied is 73.7% vs. 10.5%. The cards are cohort conversions; the "
        "June report designs (and this report) divide step counts. Both are valid, but they must never share a label.",
        "3. **Lead basis doubles.** The spend-source basis shows Zillow 12 and Google PPC 14 leads; the vendor basis "
        "and first-touch source both show 6 and 7. The 2× pattern suggests the spend-source view double-counts.",
        "4. **Ad conversions vs. CRM leads: 73.33 vs. 7.** The platform counts about ten conversions per CRM lead, and "
        "CPC is the medium on only 1 of those 7 Google Ads first-touch leads.",
        "5. **Occupancy: 88.96% month-end vs. 93.59% daily average.** Different objects and unit bases (299 vs. 314). "
        "Notice data is missing before 2026-07-27, so June exposure (5.69%) and future leases (16) read low.",
        "6. **The design PDFs carried numbers the export doesn't support.** \"Hyly assistant 3 created\" matches the "
        "listing section's 3 leads, not a lead source; Google Ads toured shows 0 in the designs but 1 in the export's "
        "multi-touch toured breakdown; Meta's \"1 lead\" has no matching first-touch source. The report follows the "
        "export: Meta leads are null with a gap.",
        "7. **The metric library's own breakdown disagrees with the export.** The library derived Google Business "
        "13 / Zillow 5 / Google.com 4 new leads by source; the export reports 10 / 6 / 6. The live first-touch query "
        "follows the library, so expect these three rows to differ live until the dedupe rule is settled.",
        "",
        "## Full table",
        "",
        "Status compares the export with the report assembler's value. *Live* is the value the portal's live "
        "gatherer returned, or why it has none.",
        "",
        "| Section | Metric | Export | Report | Status | Live | Reason |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in t.rows:
        live = r["live"] if r["live"] is not None else f"unreachable: {r['live_reason'] or live_reason}"
        out.append(f"| {r['section']} | {r['metric']} | {r['csv']} | {r['report']} | {r['status']} | {live} | "
                   f"{r['reason']} |")
    out += [
        "",
        "## Export metrics the report doesn't carry",
        "",
        "Occupancy Trend (94.31%, formulation unresolved in the library), Rentable and Excluded units, the notice "
        "fields (unpopulated before 2026-07-27), Total Applied (a structural duplicate of 1st Applied), the paid-source "
        "cost-per-stage cards, GA4 new users, views, event counts and page titles, Google Ads CPM, conversion rate, "
        "revenue and the unlabeled campaign and keyword rows, the weekly bump-chart series, and Facebook / Instagram "
        "organic social.",
        "",
        "## Live SQL to verify",
        "",
        "When credentials are available, run this script with `--live --env-file <path>`. The gatherer issues these "
        "read-only queries (50 MB byte cap, metric-library allowlist):",
        "",
        "- `t_oc_agg_occupancy_property`: newest `as_of_date` ≤ period end; stocks.",
        "- `t_ot_agg_resident_activity_property`: `SUM(IF(event_type=…, count, 0))` over `date`.",
        "- `t_oc_agg_occupancy_operational`: `delayed_move_ins` at the newest `as_of_date`.",
        "- `t_occupancy_rate`: `AVG(SAFE_DIVIDE(occupied, units))` over `DATE(created_at)`.",
        "- `t_contact_activity`: created (distinct `contact_name`, `contact_status != 'Leased'`), scheduled / toured "
        "/ applied (distinct `contact_id` on `first_scheduled_dt` / `first_completed_dt` / `first_application_dt`), "
        "and the `mta_first_source_name` / `mta_first_medium_name` breakdowns.",
        "- `pai_journey_<org>`: distinct `contact_id` with `event_name = 'h_ms_lease'`, plus the joined source "
        "breakdown.",
        "- `prospect_journey`: `pms_Application` less `pms_CancelApplication` distinct contacts.",
        "",
        "Assumptions to confirm on the first live run: `property_id` is INT64 in every object; HubSpot's "
        "`hyly_property_id` is the same 19-digit id; `contact_created_date` and `event_date` cast cleanly with `DATE()`.",
        "",
    ]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--export", required=True)
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--env-file")
    args = ap.parse_args()
    live_raw, live_reason, notes = (try_live(args.env_file) if args.live else
                                    (None, "Live run not requested.", []))
    table, rep, _ = build(args.export, live_raw, live_reason)
    print(render(table, rep, live_raw, live_reason, notes, args.export))


if __name__ == "__main__":
    main()
