"""Red Light Report ingestion endpoint helpers.

Handles two intake paths:
  1. Single property run  — /api/red-light/run
     Called by n8n or manually. Accepts scored data + PDF text, runs the full pipeline.

  2. Bulk CSV ingest      — /api/red-light/ingest-csv
     Accepts NinjaCat CSV export, scores all rows via the benchmark engine,
     runs the full pipeline for each property, returns summary.

Scoring engine implements the 4-category weighted model from the Red Light Report spec:
  Market (25%) + Marketing (30%) + Leasing Funnel (25%) + Experience (20%)

Thresholds are calibrated to the approved benchmarks in the spec.
"""

import csv
import io
import logging
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger(__name__)


# ── Benchmark thresholds ──────────────────────────────────────────────────────
# Each metric: (green_min_or_max, yellow_boundary, red_threshold, direction)
# direction: "above" = higher is better, "below" = lower is better

BENCHMARKS = {
    # Marketing (weight 0.30)
    "engagement_rate":    (58,   48,  0,    "above"),   # % — >= 58 GREEN, 48-57 YELLOW, <48 RED
    "session_duration":   (90,   60,  0,    "above"),   # seconds — >= 90s GREEN, 60-89 YELLOW, <60 RED
    "cpc":                (2.50, 3.50, 999, "below"),   # $ — <= 2.50 GREEN, 2.51-3.50 YELLOW, >3.50 RED
    "ctr":                (5.0,  3.0,  0,   "above"),   # % — >= 5 GREEN, 3-4.9 YELLOW, <3 RED
    "cost_per_conversion":(100, 150,  999,  "below"),   # $ — <=100 GREEN, 101-150 YELLOW, >150 RED
    "conversion_rate":    (3.0,  1.0,  0,   "above"),   # % — >= 3 GREEN, 1-2.9 YELLOW, <1 RED
    # Leasing Funnel (weight 0.25)
    "lead_to_prospect":   (60,   50,   0,   "above"),   # % — >= 60 GREEN, 50-59 YELLOW, <50 RED
    "prospect_to_tour":   (35,   25,   0,   "above"),   # %
    "tour_to_application":(25,   18,   0,   "above"),   # %
    # Experience (weight 0.20)
    "call_answer_rate":   (50,   40,   0,   "above"),   # %
    "avg_response_time":  (5,    10,  999,  "below"),   # minutes — <=5 GREEN, 5-10 YELLOW, >10 RED
    # Market (weight 0.25) — compared to submarket
    "occupancy_vs_sub":   (0,    -3, -999,  "above"),   # delta pts — >= 0 GREEN, -3 to -0.1 YELLOW, <-3 RED
    "exposure_vs_sub":    (0,    3,   999,  "below"),   # delta pts — <=0 GREEN, 1-3 YELLOW, >3 RED
}

CATEGORY_WEIGHTS = {
    "market":    0.25,
    "marketing": 0.30,
    "funnel":    0.25,
    "experience":0.20,
}

METRIC_CATEGORIES = {
    "market":     ["occupancy_vs_sub", "exposure_vs_sub"],
    "marketing":  ["engagement_rate", "session_duration", "cpc", "ctr",
                   "cost_per_conversion", "conversion_rate"],
    "funnel":     ["lead_to_prospect", "prospect_to_tour", "tour_to_application"],
    "experience": ["call_answer_rate", "avg_response_time"],
}


def score_metric(metric_name, value):
    """Return 0-100 score for a single metric.

    GREEN band = 85-100, YELLOW band = 50-84, RED band = 0-49.
    Linear interpolation within bands.
    """
    if value is None:
        return 50  # neutral if missing

    green_thresh, yellow_thresh, red_thresh, direction = BENCHMARKS[metric_name]

    if direction == "above":
        if value >= green_thresh:
            # Scale 100 down to 85 as value approaches yellow threshold
            excess = value - green_thresh
            return min(100, 85 + min(15, excess * 0.5))
        elif value >= yellow_thresh:
            # Linear 50-84 between yellow and green thresholds
            span = green_thresh - yellow_thresh
            pos = value - yellow_thresh
            return 50 + int((pos / span) * 34) if span > 0 else 67
        else:
            # RED: 0-49 linear from 0 at floor
            floor = yellow_thresh * 0.3
            span = yellow_thresh - floor
            pos = max(0, value - floor)
            return int((pos / span) * 49) if span > 0 else 25
    else:  # below = lower is better
        if value <= green_thresh:
            return min(100, 85 + max(0, int((green_thresh - value) * 2)))
        elif value <= yellow_thresh:
            span = yellow_thresh - green_thresh
            pos = yellow_thresh - value
            return 50 + int((pos / span) * 34) if span > 0 else 67
        else:
            # RED
            span = yellow_thresh * 1.5 - yellow_thresh
            pos = max(0, (yellow_thresh * 1.5) - value)
            return int((pos / max(span, 1)) * 49)


def score_property(metrics):
    """Compute category scores and overall weighted score.

    Args:
        metrics: dict of metric_name -> float value (None = missing)

    Returns:
        dict with keys: overall, market, marketing, funnel, experience, status,
                        metric_scores (per-metric), flags (list of RED metrics)
    """
    category_scores = {}
    metric_scores = {}
    flags = []

    for category, metric_names in METRIC_CATEGORIES.items():
        scores = []
        for m in metric_names:
            val = metrics.get(m)
            s = score_metric(m, val)
            metric_scores[m] = {"value": val, "score": s}
            scores.append(s)
            if s < 50:
                flags.append({"metric": m, "value": val, "score": s, "category": category})
        category_scores[category] = int(sum(scores) / len(scores)) if scores else 50

    overall = sum(
        category_scores[c] * CATEGORY_WEIGHTS[c]
        for c in CATEGORY_WEIGHTS
    )
    overall = int(overall)

    if overall >= 75:
        status = "GREEN"
    elif overall >= 50:
        status = "YELLOW"
    else:
        status = "RED"

    return {
        "overall": overall,
        "market":   category_scores["market"],
        "marketing":category_scores["marketing"],
        "funnel":   category_scores["funnel"],
        "experience":category_scores["experience"],
        "status": status,
        "metric_scores": metric_scores,
        "flags": flags,
    }


# ── CSV column mapping ────────────────────────────────────────────────────────
# Maps NinjaCat CSV column names -> our internal metric names
# Update this when the actual NinjaCat schema is confirmed (Step 6/7 BLOCKER)
NINJACAT_COLUMN_MAP = {
    # These are placeholder names — update after schema inspection
    "engagement_rate":     "engagement_rate",
    "avg_session_duration":"session_duration",
    "cpc":                 "cpc",
    "ctr":                 "ctr",
    "cost_per_conversion": "cost_per_conversion",
    "conversion_rate":     "conversion_rate",
    "lead_to_prospect_pct":"lead_to_prospect",
    "prospect_to_tour_pct":"prospect_to_tour",
    "tour_to_app_pct":     "tour_to_application",
    "call_answer_rate":    "call_answer_rate",
    "avg_response_min":    "avg_response_time",
    "occupancy_delta":     "occupancy_vs_sub",
    "exposure_delta":      "exposure_vs_sub",
    # Identity columns
    "uuid":                "_uuid",
    "system_id":           "_system_id",
    "property_name":       "_property_name",
    "report_month":        "_report_month",
}


def parse_csv_row(row):
    """Map a NinjaCat CSV row dict to internal metrics + identity fields."""
    metrics = {}
    identity = {}
    for csv_col, internal_name in NINJACAT_COLUMN_MAP.items():
        raw = row.get(csv_col)
        if raw is None or raw == "":
            val = None
        else:
            try:
                val = float(str(raw).replace("%", "").replace("$", "").replace(",", "").strip())
            except (ValueError, AttributeError):
                val = None

        if internal_name.startswith("_"):
            identity[internal_name[1:]] = row.get(csv_col, "")
        else:
            metrics[internal_name] = val

    return identity, metrics


def run_single_property(payload):
    """Run the full pipeline for one property from a structured payload.

    Expected payload keys:
        property_uuid, ninjacat_system_id, report_month, property_name,
        company_id, rpmmarket, metrics (dict), pdf_text (optional)

    Returns pipeline result dict.
    """
    from red_light_pipeline import process_red_light_report

    property_uuid     = payload.get("property_uuid", "")
    ninjacat_system_id= payload.get("ninjacat_system_id", "")
    report_month      = payload.get("report_month") or datetime.utcnow().strftime("%Y-%m-01")
    property_name     = payload.get("property_name", "")
    company_id        = payload.get("company_id", "")
    pdf_text          = payload.get("pdf_text", "")
    metrics           = payload.get("metrics", {})

    if not property_uuid:
        return {"status": "error", "error": "Missing property_uuid"}

    scores = score_property(metrics)

    # Optionally update HubSpot company record with new health score
    if company_id:
        _update_hubspot_health_score(company_id, scores["overall"], scores["status"])

    result = process_red_light_report(
        property_uuid=property_uuid,
        ninjacat_system_id=ninjacat_system_id,
        report_month=report_month,
        scores=scores,
        pdf_text=pdf_text,
        report_type="red_light",
    )

    result["scores"] = {
        "overall":    scores["overall"],
        "market":     scores["market"],
        "marketing":  scores["marketing"],
        "funnel":     scores["funnel"],
        "experience": scores["experience"],
        "status":     scores["status"],
        "flag_count": len(scores["flags"]),
    }
    result["property_uuid"] = property_uuid
    result["property_name"] = property_name

    return result


def run_bulk_csv(csv_text):
    """Process a NinjaCat bulk CSV export.

    Returns summary dict with per-property results.
    NOTE: Schema columns are based on placeholder mapping above.
    UPDATE NINJACAT_COLUMN_MAP after Step 6/7 schema inspection.
    """
    results = []
    errors  = []

    reader = csv.DictReader(io.StringIO(csv_text))
    rows = list(reader)

    if not rows:
        return {"status": "error", "error": "Empty CSV"}

    logger.info("Bulk ingest: processing %d rows", len(rows))

    for row in rows:
        identity, metrics = parse_csv_row(row)
        uuid = identity.get("uuid", "")
        if not uuid:
            errors.append({"row": row, "error": "Missing uuid"})
            continue

        try:
            result = run_single_property({
                "property_uuid":       uuid,
                "ninjacat_system_id":  identity.get("system_id", ""),
                "report_month":        identity.get("report_month") or datetime.utcnow().strftime("%Y-%m-01"),
                "property_name":       identity.get("property_name", ""),
                "company_id":          row.get("hubspot_company_id", ""),
                "metrics":             metrics,
                "pdf_text":            "",
            })
            results.append(result)
        except Exception as e:
            logger.error("Row processing failed for uuid %s: %s", uuid, e)
            errors.append({"uuid": uuid, "error": str(e)})

    reds    = [r for r in results if r.get("scores", {}).get("status") == "RED"]
    yellows = [r for r in results if r.get("scores", {}).get("status") == "YELLOW"]
    greens  = [r for r in results if r.get("scores", {}).get("status") == "GREEN"]

    return {
        "status": "ok",
        "processed": len(results),
        "errors": len(errors),
        "summary": {"RED": len(reds), "YELLOW": len(yellows), "GREEN": len(greens)},
        "results": results,
        "error_details": errors,
    }


def _update_hubspot_health_score(company_id, score, status):
    """Write health score + status back to HubSpot company record."""
    import requests
    from config import HUBSPOT_API_KEY

    if not HUBSPOT_API_KEY or not company_id:
        return

    headers = {
        "Authorization": f"Bearer {HUBSPOT_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "properties": {
            "redlight_score":  str(score),
            "redlight_status": status,
            "redlight_run_date": datetime.utcnow().strftime("%Y-%m-%d"),
        }
    }
    try:
        r = requests.patch(
            f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}",
            headers=headers,
            json=payload,
            timeout=10,
        )
        if r.status_code not in (200, 204):
            logger.warning("HubSpot score update failed: %s %s", r.status_code, r.text[:200])
        else:
            logger.info("Updated HubSpot health score for company %s: %d %s", company_id, score, status)
    except Exception as e:
        logger.warning("HubSpot score update exception: %s", e)
