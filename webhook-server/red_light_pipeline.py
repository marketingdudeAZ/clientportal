"""Phase 3, Steps 8-9: Red Light Report pipeline — BigQuery write extension.

Extends the existing Red Light scoring workflow with two new steps:
  Step A (Step 8): Write Red Light scores to rpm_portal.red_light_history
  Step B (Step 9): Extract AI insights from PDF text, write to rpm_portal.report_insights

Also writes high-priority findings to HubDB rpm_recommendations (Step 23).

Usage:
    from red_light_pipeline import process_red_light_report
    process_red_light_report(
        property_uuid="43472867164",
        ninjacat_system_id="12345",
        report_month="2026-03-01",
        scores={
            "overall": 72, "market": 78, "marketing": 65,
            "funnel": 70, "experience": 75, "status": "YELLOW"
        },
        pdf_text="<full extracted PDF text>",
        report_type="red_light",
    )
"""

import json
import logging
import os
import sys
import uuid as uuid_lib

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    ANTHROPIC_API_KEY,
    CLAUDE_AGENT_MODEL,
    HUBSPOT_API_KEY,
    HUBDB_RECOMMENDATIONS_TABLE_ID,
)
from bigquery_client import write_red_light_score, write_report_insights

logger = logging.getLogger(__name__)

HUBDB_BASE = "https://api.hubapi.com/cms/v3/hubdb/tables"
HS_HEADERS = {
    "Authorization": f"Bearer {HUBSPOT_API_KEY}",
    "Content-Type": "application/json",
}

# Section 6.2 system prompt
INSIGHTS_EXTRACTION_PROMPT = """You are extracting insights from an RPM Living property marketing report.
Return a JSON array only. No preamble. No markdown. Valid JSON only.
Each item in the array is one finding with these fields:
{ "insight_type": "performance|recommendation|alert|win",
  "finding": "the specific finding in one sentence",
  "recommendation": "the specific action recommended, or null if finding only",
  "priority": "high|medium|low" }
Extract every distinct finding and recommendation from the text.
Do not combine findings. One finding per array item."""


def extract_insights(pdf_text):
    """Call Claude with the Section 6.2 prompt to extract structured insights.

    Returns list of insight dicts. Falls back to [] on failure.
    """
    if not ANTHROPIC_API_KEY:
        logger.warning("ANTHROPIC_API_KEY not set — skipping insight extraction")
        return []

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model=CLAUDE_AGENT_MODEL,
            max_tokens=2000,
            system=INSIGHTS_EXTRACTION_PROMPT,
            messages=[{"role": "user", "content": pdf_text}],
        )
        raw = message.content[0].text.strip()
        insights = json.loads(raw)
        if not isinstance(insights, list):
            raise ValueError("Expected JSON array")
        return insights
    except Exception as e:
        logger.error("Insight extraction failed: %s", e)
        return []


def write_recs_to_hubdb(property_uuid, insights, report_month):
    """Write high-priority findings to HubDB rpm_recommendations.

    Only writes insights with priority=high and a non-null recommendation.
    Rec type is classified by Claude based on content.
    """
    if not HUBDB_RECOMMENDATIONS_TABLE_ID:
        logger.warning("HUBDB_RECOMMENDATIONS_TABLE_ID not set — skipping rec ingest")
        return

    high_priority = [
        i for i in insights
        if i.get("priority") == "high" and i.get("recommendation")
    ]

    for insight in high_priority:
        rec_id = str(uuid_lib.uuid4())
        rec_type = _classify_rec_type(insight)

        row = {
            "rec_id": rec_id,
            "property_uuid": property_uuid,
            "source": "red_light",
            "rec_type": rec_type,
            "title": insight["finding"][:80],
            "body": insight.get("recommendation", ""),
            "action_required": insight.get("recommendation", ""),
            "post_approval_action": "",     # Reserved — do not read in v1
            "status": "pending",
            "risk_level": "high",
            "created_date": report_month[:10],
            "approved_date": None,
            "bq_row_ref": f"{property_uuid}:{report_month}",
        }

        url = f"{HUBDB_BASE}/{HUBDB_RECOMMENDATIONS_TABLE_ID}/rows"
        r = requests.post(url, headers=HS_HEADERS, json=row)
        if r.status_code == 201:
            logger.info("Created rec card %s for %s", rec_id, property_uuid)
        else:
            logger.error("Failed to create rec card: %s %s", r.status_code, r.text[:200])


def _classify_rec_type(insight):
    """Classify a recommendation into budget_change, strategy_change, or package_upgrade."""
    text = (insight.get("finding", "") + " " + (insight.get("recommendation") or "")).lower()
    if any(w in text for w in ["budget", "spend", "paid", "advertising", "cost"]):
        return "budget_change"
    if any(w in text for w in ["package", "upgrade", "tier", "enroll", "add service"]):
        return "package_upgrade"
    return "strategy_change"


def process_red_light_report(property_uuid, ninjacat_system_id, report_month,
                              scores, pdf_text, report_type="red_light"):
    """Full pipeline: score → BigQuery write → insight extraction → BigQuery write → HubDB rec cards.

    Args:
        property_uuid: RPM UUID string
        ninjacat_system_id: NinjaCat account ID string
        report_month: "YYYY-MM-01" first day of reporting month
        scores: dict with keys overall, market, marketing, funnel, experience, status
        pdf_text: full extracted text from the PDF report
        report_type: "red_light" / "seo_local_pin" / "competitor_gap" / "ninjacat_monthly"
    """
    errors = []

    # Step A (Step 8): Write scores to red_light_history
    try:
        write_red_light_score(
            property_uuid=property_uuid,
            report_month=report_month,
            overall_score=scores["overall"],
            market_score=scores["market"],
            marketing_score=scores["marketing"],
            funnel_score=scores["funnel"],
            experience_score=scores["experience"],
            status=scores["status"],
        )
        logger.info("Step 8 complete: red_light_history written for %s", property_uuid)
    except Exception as e:
        logger.error("Step 8 failed: %s", e)
        errors.append(f"red_light_history write: {e}")

    # Step B (Step 9): Extract insights and write to report_insights + HubDB
    if pdf_text and pdf_text.strip():
        insights = extract_insights(pdf_text)
        if insights:
            try:
                write_report_insights(
                    property_uuid=property_uuid,
                    ninjacat_system_id=ninjacat_system_id,
                    report_month=report_month,
                    report_type=report_type,
                    insights_list=insights,
                    raw_text=pdf_text,
                )
                logger.info("Step 9 complete: %d insights written for %s", len(insights), property_uuid)
            except Exception as e:
                logger.error("Step 9 report_insights write failed: %s", e)
                errors.append(f"report_insights write: {e}")

            # Step 23: Write high-priority recs to HubDB
            try:
                write_recs_to_hubdb(property_uuid, insights, report_month)
            except Exception as e:
                logger.error("HubDB rec ingest failed: %s", e)
                errors.append(f"hubdb rec write: {e}")
        else:
            logger.info("Step 9: No insights extracted for %s", property_uuid)
    else:
        logger.info("Step 9: No PDF text provided — skipping insight extraction")

    return {"errors": errors, "status": "ok" if not errors else "partial"}
