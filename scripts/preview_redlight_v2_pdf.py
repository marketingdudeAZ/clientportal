"""Generate a preview PDF of the Red Light v2 report using synthetic data.

This is a developer convenience — it does NOT call HubSpot, ApartmentIQ,
BigQuery, or Claude. It feeds a hand-built payload through the same
ReportLab renderer the production endpoint uses, so reviewers can eyeball
the layout without deploying.

Usage:
    python scripts/preview_redlight_v2_pdf.py [--out samples/redlight_v2_preview.pdf]
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

from redlight_v2_pdf import render_pdf


# Plausible numbers for Ashton on West Dallas (Dallas mid-rise, ~280 units).
# These are illustrative only — the production endpoint pulls real values
# from ApartmentIQ and the HubSpot deal line items.
SAMPLE_PAYLOAD = {
    "property_name":     "Ashton on West Dallas",
    "report_date":       "2026-05-13",
    "report_month":      "2026-05-01",
    "last_month_label":  "April 2026",
    "last_year_label":   "May 2025",
    "current": {
        "occupancy":            93.4,
        "leased_percent":       95.7,
        "exposure":             5.8,
        "available_units":      18,
        "leases_last_30":       11,
        "applications_last_30": 17,
        "monthly_service_cost": 9_450.00,
        "cost_per_lease":       859.09,
        "submarket_name":       "Uptown / Oak Lawn",
        "market_name":          "Dallas-Fort Worth",
    },
    "last_month": {
        "occupancy":            92.1,
        "leased_percent":       94.3,
        "exposure":             6.6,
        "available_units":      21,
        "leases_last_30":       9,
        "monthly_service_cost": 9_450.00,
        "cost_per_lease":       1050.00,
    },
    "last_year": {
        "occupancy":            91.0,
        "leased_percent":       92.8,
        "exposure":             7.4,
        "available_units":      24,
        "leases_last_30":       8,
        "monthly_service_cost": 8_200.00,
        "cost_per_lease":       1025.00,
    },
    "mom_comparison": [
        {"key": "occupancy",            "label": "Occupancy",
         "format": "percent",  "current": 93.4, "prior": 92.1,
         "delta": {"abs": 1.3,   "pct": 1.41,  "direction": "up"}},
        {"key": "available_units",      "label": "ATR (Available to Rent)",
         "format": "int",      "current": 18,   "prior": 21,
         "delta": {"abs": -3,    "pct": -14.29, "direction": "down"}},
        {"key": "leases_last_30",       "label": "Leases (last 30 days)",
         "format": "int",      "current": 11,   "prior": 9,
         "delta": {"abs": 2,     "pct": 22.22, "direction": "up"}},
        {"key": "leased_percent",       "label": "Leased %",
         "format": "percent",  "current": 95.7, "prior": 94.3,
         "delta": {"abs": 1.4,   "pct": 1.48,  "direction": "up"}},
        {"key": "exposure",             "label": "Exposure %",
         "format": "percent",  "current": 5.8,  "prior": 6.6,
         "delta": {"abs": -0.8,  "pct": -12.12, "direction": "down"}},
        {"key": "monthly_service_cost", "label": "Monthly Service Cost",
         "format": "currency", "current": 9450, "prior": 9450,
         "delta": {"abs": 0,     "pct": 0,      "direction": "flat"}},
        {"key": "cost_per_lease",       "label": "Cost per Lease",
         "format": "currency", "current": 859.09, "prior": 1050,
         "delta": {"abs": -190.91, "pct": -18.18, "direction": "down"}},
    ],
    "yoy_comparison": [
        {"key": "occupancy",            "label": "Occupancy",
         "format": "percent",  "current": 93.4, "prior": 91.0,
         "delta": {"abs": 2.4,   "pct": 2.64,  "direction": "up"}},
        {"key": "available_units",      "label": "ATR (Available to Rent)",
         "format": "int",      "current": 18,   "prior": 24,
         "delta": {"abs": -6,    "pct": -25.0, "direction": "down"}},
        {"key": "leases_last_30",       "label": "Leases (last 30 days)",
         "format": "int",      "current": 11,   "prior": 8,
         "delta": {"abs": 3,     "pct": 37.5,  "direction": "up"}},
        {"key": "leased_percent",       "label": "Leased %",
         "format": "percent",  "current": 95.7, "prior": 92.8,
         "delta": {"abs": 2.9,   "pct": 3.13,  "direction": "up"}},
        {"key": "exposure",             "label": "Exposure %",
         "format": "percent",  "current": 5.8,  "prior": 7.4,
         "delta": {"abs": -1.6,  "pct": -21.62, "direction": "down"}},
        {"key": "monthly_service_cost", "label": "Monthly Service Cost",
         "format": "currency", "current": 9450, "prior": 8200,
         "delta": {"abs": 1250,  "pct": 15.24, "direction": "up"}},
        {"key": "cost_per_lease",       "label": "Cost per Lease",
         "format": "currency", "current": 859.09, "prior": 1025,
         "delta": {"abs": -165.91, "pct": -16.19, "direction": "down"}},
    ],
    "where_going": (
        "Your trajectory is healthy. Occupancy has climbed 1.3 points over the "
        "last 30 days and 2.4 points year-over-year, while available units have "
        "dropped from 24 to 18 in the same window. If lease velocity holds at "
        "11 per 30 days, you should clear 95% occupancy within the next 30-45 "
        "days. The biggest risk is application drop-off: 17 applications "
        "converted to 11 leases — watch tour-to-application conversion closely "
        "or your forward pipeline will tighten."
    ),
    "how_got_here": (
        "The biggest mover was cost per lease, down 18% month-over-month and "
        "16% year-over-year while service spend stayed flat. That means the "
        "media mix is producing more leases per dollar — a sign that the "
        "current campaign weighting is working. Occupancy gains followed lower "
        "exposure (down 0.8 points MoM) and a higher leases-last-30 count, "
        "suggesting demand is outpacing supply rather than driven by price "
        "concessions. The cost discipline plus volume lift is what moved you "
        "out of the prior-year position."
    ),
    "trailing_trend": [],  # not exercised in this preview
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--out",
        default=os.path.join(os.path.dirname(__file__), "..", "samples",
                             "redlight_v2_preview_ashton_on_west_dallas.pdf"),
    )
    args = ap.parse_args()

    out_path = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    pdf_bytes = render_pdf(SAMPLE_PAYLOAD)
    with open(out_path, "wb") as f:
        f.write(pdf_bytes)
    print(f"Wrote {len(pdf_bytes):,} bytes to {out_path}")


if __name__ == "__main__":
    main()
