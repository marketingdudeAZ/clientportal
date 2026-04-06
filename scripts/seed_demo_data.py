"""Seed demo data for The Quincy at Kierland (UUID: 43472867164).

Seeds:
  1. HubDB rpm_recommendations — one pending rec card (strategy_change)
  2. HubDB rpm_budget_tiers    — 15 rows (Good/Better/Best × 5 channels)

BigQuery seeding (red_light_history, report_insights) requires BIGQUERY_PROJECT_ID
and service account to be configured. Skipped if not configured.

Run: python3 scripts/seed_demo_data.py
"""

import os
import sys
import uuid as uuid_lib
from datetime import date

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    HUBSPOT_API_KEY,
    HUBDB_RECOMMENDATIONS_TABLE_ID,
    HUBDB_BUDGET_TIERS_TABLE_ID,
    BIGQUERY_PROJECT_ID,
    BIGQUERY_SERVICE_ACCOUNT_JSON,
)

HEADERS = {
    "Authorization": f"Bearer {HUBSPOT_API_KEY}",
    "Content-Type": "application/json",
}
HUBDB_BASE = "https://api.hubapi.com/cms/v3/hubdb/tables"

# Test property
TEST_UUID = "43472867164"
TEST_NAME = "The Quincy at Kierland"
REPORT_MONTH = "2026-03-01"


# ── HubDB rpm_recommendations ────────────────────────────────────────────────

DEMO_RECS = [
    {
        "rec_id": str(uuid_lib.uuid4()),
        "property_uuid": TEST_UUID,
        "source": "red_light",
        "rec_type": "strategy_change",
        "title": "Increase review response rate to improve resident experience score",
        "body": (
            "Your resident experience subscore dropped 8 points this month to 67. "
            "The primary driver is an unresponded review backlog — 23 reviews over "
            "the past 60 days have no response, which Google weighs heavily in local ranking. "
            "Activating the Response + Removal reputation management tier would address this "
            "within the current billing cycle."
        ),
        "action_required": "Approve to create a task for your reputation management team",
        "post_approval_action": "",  # Reserved — do not read in v1
        "status": "pending",
        "risk_level": "high",
        "created_date": "2026-03-06",
        "approved_date": None,
        "bq_row_ref": f"{TEST_UUID}:2026-03-01",
    },
    {
        "rec_id": str(uuid_lib.uuid4()),
        "property_uuid": TEST_UUID,
        "source": "red_light",
        "rec_type": "budget_change",
        "title": "Increase paid search budget ahead of peak leasing season",
        "body": (
            "Your market subscore of 78 shows strong competitive positioning, but "
            "paid search impression share dropped 14% month-over-month as two competing "
            "properties increased spend. Kierland is entering peak leasing season (April-June). "
            "A $500/month increase in paid search spend is projected to recover impression share "
            "and protect your lead velocity."
        ),
        "action_required": "Approve to have your AM create a budget change request",
        "post_approval_action": "",
        "status": "pending",
        "risk_level": "medium",
        "created_date": "2026-03-06",
        "approved_date": None,
        "bq_row_ref": f"{TEST_UUID}:2026-03-01",
    },
]


def seed_recommendations():
    print("\nSeeding rpm_recommendations...")
    if not HUBDB_RECOMMENDATIONS_TABLE_ID:
        print("  ERROR: HUBDB_RECOMMENDATIONS_TABLE_ID not set — run create_hubdb_tables_v2.py first")
        return

    # Clear existing demo rows for this property
    url = f"{HUBDB_BASE}/{HUBDB_RECOMMENDATIONS_TABLE_ID}/rows"
    r = requests.get(url + f"?property_uuid__eq={TEST_UUID}", headers=HEADERS)
    if r.status_code == 200:
        for row in r.json().get("results", []):
            row_id = row["id"]
            requests.delete(f"{url}/{row_id}", headers=HEADERS)

    for rec in DEMO_RECS:
        r = requests.post(url, headers=HEADERS, json=rec)
        if r.status_code == 201:
            print(f"  Created: {rec['title'][:60]}...")
        else:
            print(f"  ERROR {r.status_code}: {r.text[:200]}")

    # Publish draft
    pub_url = f"{HUBDB_BASE}/{HUBDB_RECOMMENDATIONS_TABLE_ID}/draft/publish"
    requests.post(pub_url, headers=HEADERS)
    print("  Published.")


# ── HubDB rpm_budget_tiers ───────────────────────────────────────────────────

BUDGET_TIERS = [
    # SEO
    {"channel": "seo", "tier_name": "good",   "tier_id": "seo_good",   "monthly_price": 500,  "hubspot_deal_value": 6000,  "description": "Local SEO: directory sync, citation building, monthly reporting. Best for stable, established properties."},
    {"channel": "seo", "tier_name": "better", "tier_id": "seo_better", "monthly_price": 800,  "hubspot_deal_value": 9600,  "description": "Standard SEO: local + off-page authority building, content calendar, competitor gap analysis. Recommended for most properties."},
    {"channel": "seo", "tier_name": "best",   "tier_id": "seo_best",   "monthly_price": 1300, "hubspot_deal_value": 15600, "description": "Premium SEO: full keyword program, ILS integration, local pin strategy, monthly strategic review. For competitive markets."},
    # Social
    {"channel": "social", "tier_name": "good",   "tier_id": "social_good",   "monthly_price": 300, "hubspot_deal_value": 3600,  "description": "Basic social: 8 posts/month across 2 platforms, community management, monthly reporting."},
    {"channel": "social", "tier_name": "better", "tier_id": "social_better", "monthly_price": 450, "hubspot_deal_value": 5400,  "description": "Standard social: 12 posts/month across 3 platforms, story content, event coverage, engagement monitoring."},
    {"channel": "social", "tier_name": "best",   "tier_id": "social_best",   "monthly_price": 700, "hubspot_deal_value": 8400,  "description": "Premium social: 20 posts/month, video reels, paid social boost credits, influencer coordination, weekly reporting."},
    # Reputation
    {"channel": "reputation", "tier_name": "good",   "tier_id": "rep_good",   "monthly_price": 190, "hubspot_deal_value": 2280, "description": "Response Only: professional responses to all reviews within 48 hours. Improves experience score and Google ranking signals."},
    {"channel": "reputation", "tier_name": "better", "tier_id": "rep_better", "monthly_price": 255, "hubspot_deal_value": 3060, "description": "Response + Removal: responses within 24 hours plus fraudulent/policy-violating review dispute service. Recommended for properties with active review challenges."},
    {"channel": "reputation", "tier_name": "best",   "tier_id": "rep_best",   "monthly_price": 400, "hubspot_deal_value": 4800, "description": "Full Reputation Management: response + removal + proactive review generation campaigns + monthly reputation audit."},
    # Paid Search
    {"channel": "paid_search", "tier_name": "good",   "tier_id": "ps_good",   "monthly_price": 500,  "hubspot_deal_value": 6000,  "description": "Good: Google Ads branded + conquest targeting, $500 management fee, $1,000 recommended ad spend."},
    {"channel": "paid_search", "tier_name": "better", "tier_id": "ps_better", "monthly_price": 800,  "hubspot_deal_value": 9600,  "description": "Better: branded + conquest + retargeting, $800 management, $2,000 recommended ad spend, weekly bid optimization."},
    {"channel": "paid_search", "tier_name": "best",   "tier_id": "ps_best",   "monthly_price": 1200, "hubspot_deal_value": 14400, "description": "Best: full program with display, YouTube, and Performance Max, $1,200 management, $3,000+ recommended spend."},
    # Paid Social
    {"channel": "paid_social", "tier_name": "good",   "tier_id": "pso_good",   "monthly_price": 400,  "hubspot_deal_value": 4800,  "description": "Good: Meta (Facebook/Instagram) ads, $400 management, $1,000 recommended ad spend, 2 ad sets."},
    {"channel": "paid_social", "tier_name": "better", "tier_id": "pso_better", "monthly_price": 650,  "hubspot_deal_value": 7800,  "description": "Better: Meta + TikTok, $650 management, $2,000 recommended ad spend, retargeting audiences, A/B creative testing."},
    {"channel": "paid_social", "tier_name": "best",   "tier_id": "pso_best",   "monthly_price": 1000, "hubspot_deal_value": 12000, "description": "Best: Meta + TikTok + Pinterest, $1,000 management, $3,000+ recommended spend, influencer integration, custom audience strategy."},
]


def seed_budget_tiers():
    print("\nSeeding rpm_budget_tiers...")
    if not HUBDB_BUDGET_TIERS_TABLE_ID:
        print("  ERROR: HUBDB_BUDGET_TIERS_TABLE_ID not set")
        return

    url = f"{HUBDB_BASE}/{HUBDB_BUDGET_TIERS_TABLE_ID}/rows"

    for tier in BUDGET_TIERS:
        r = requests.post(url, headers=HEADERS, json=tier)
        if r.status_code == 201:
            print(f"  Created: {tier['channel']} {tier['tier_name']}")
        elif r.status_code == 409:
            print(f"  Exists: {tier['channel']} {tier['tier_name']}")
        else:
            print(f"  ERROR {r.status_code}: {r.text[:200]}")

    pub_url = f"{HUBDB_BASE}/{HUBDB_BUDGET_TIERS_TABLE_ID}/draft/publish"
    requests.post(pub_url, headers=HEADERS)
    print(f"  Published. {len(BUDGET_TIERS)} tiers total.")


# ── BigQuery test data ────────────────────────────────────────────────────────

def seed_bigquery():
    """Seed BigQuery rpm_portal_dev with scores and insights for the demo property."""
    if not BIGQUERY_PROJECT_ID or not BIGQUERY_SERVICE_ACCOUNT_JSON:
        print("\nBigQuery seeding skipped — BIGQUERY_PROJECT_ID or BIGQUERY_SERVICE_ACCOUNT_JSON not set.")
        return
    if BIGQUERY_SERVICE_ACCOUNT_JSON == "path/to/service_account.json":
        print("\nBigQuery seeding skipped — service account not configured.")
        return

    print("\nSeeding BigQuery rpm_portal_dev...")

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
    # Force dev dataset
    os.environ["FLASK_ENV"] = "development"
    from bigquery_client import write_red_light_score, write_report_insights

    # Current month scores
    try:
        write_red_light_score(
            property_uuid=TEST_UUID,
            report_month=REPORT_MONTH,
            overall_score=72.0,
            market_score=78.0,
            marketing_score=68.0,
            funnel_score=70.0,
            experience_score=67.0,
            status="YELLOW",
        )
        print("  red_light_history (current month): OK")
    except Exception as e:
        print(f"  red_light_history (current): FAILED — {e}")

    # Previous month scores
    try:
        write_red_light_score(
            property_uuid=TEST_UUID,
            report_month="2026-02-01",
            overall_score=76.0,
            market_score=80.0,
            marketing_score=72.0,
            funnel_score=74.0,
            experience_score=75.0,
            status="YELLOW",
        )
        print("  red_light_history (previous month): OK")
    except Exception as e:
        print(f"  red_light_history (previous): FAILED — {e}")

    # Report insights
    demo_insights = [
        {"insight_type": "alert", "finding": "Unresponded review backlog of 23 reviews over past 60 days is depressing experience subscore", "recommendation": "Activate reputation response management tier to clear backlog within current billing cycle", "priority": "high"},
        {"insight_type": "alert", "finding": "Paid search impression share dropped 14% as competing properties increased spend", "recommendation": "Increase paid search budget by $500/month ahead of peak leasing season", "priority": "high"},
        {"insight_type": "performance", "finding": "Market position score of 78 reflects strong competitive positioning in Scottsdale submarket", "recommendation": None, "priority": "medium"},
        {"insight_type": "win", "finding": "SEO organic traffic increased 12% month-over-month following content calendar update", "recommendation": None, "priority": "low"},
        {"insight_type": "recommendation", "finding": "Leasing funnel conversion rate is below market average at 18% vs 24% benchmark", "recommendation": "Review ILS listing quality and ensure all photos and virtual tour assets are current", "priority": "medium"},
    ]

    try:
        write_report_insights(
            property_uuid=TEST_UUID,
            ninjacat_system_id="",
            report_month=REPORT_MONTH,
            report_type="red_light",
            insights_list=demo_insights,
            raw_text="Demo data — seeded by seed_demo_data.py",
        )
        print(f"  report_insights: OK ({len(demo_insights)} rows)")
    except Exception as e:
        print(f"  report_insights: FAILED — {e}")


if __name__ == "__main__":
    seed_recommendations()
    seed_budget_tiers()
    seed_bigquery()
    print("\nDone. Verify:")
    print("  - HubSpot CMS > HubDB > rpm_recommendations: 2 pending cards for Quincy")
    print("  - HubSpot CMS > HubDB > rpm_budget_tiers: 15 rows")
    print("  - BigQuery console > rpm_portal_dev: rows in red_light_history and report_insights")
