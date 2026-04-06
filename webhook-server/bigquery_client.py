"""BigQuery client module for RPM Portal.

Shared BigQuery connection and query helpers used by:
- red_light_pipeline.py  — writes scores and insights
- digest.py              — reads scores and insights for Claude digest
- server.py              — called on portal load

All queries use rpm_portal_dev in dev, rpm_portal in prod.
Set BIGQUERY_DATASET_DEV / BIGQUERY_DATASET_PROD in .env.

CRITICAL: Queries against ninjacat_metrics are blocked until Step 6/7
schema inspection is complete. Use get_ninjacat_metrics() only after
actual column names are documented.
"""

import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    BIGQUERY_PROJECT_ID,
    BIGQUERY_SERVICE_ACCOUNT_JSON,
    BIGQUERY_DATASET_PROD,
    BIGQUERY_DATASET_DEV,
)

logger = logging.getLogger(__name__)

_client = None


def _get_client():
    global _client
    if _client is not None:
        return _client

    try:
        from google.cloud import bigquery
        from google.oauth2 import service_account
    except ImportError:
        raise RuntimeError("google-cloud-bigquery not installed. Run: pip install google-cloud-bigquery google-auth")

    if not BIGQUERY_PROJECT_ID:
        raise RuntimeError("BIGQUERY_PROJECT_ID not set in .env")

    sa_path = BIGQUERY_SERVICE_ACCOUNT_JSON
    if not sa_path or sa_path == "path/to/service_account.json":
        raise RuntimeError("BIGQUERY_SERVICE_ACCOUNT_JSON not configured in .env")

    if not os.path.exists(sa_path):
        raise RuntimeError(f"Service account file not found: {sa_path}")

    with open(sa_path) as f:
        sa_info = json.load(f)

    from google.oauth2 import service_account as sa_module
    creds = sa_module.Credentials.from_service_account_info(
        sa_info,
        scopes=["https://www.googleapis.com/auth/bigquery"],
    )
    _client = bigquery.Client(project=BIGQUERY_PROJECT_ID, credentials=creds)
    return _client


def _dataset():
    """Return the active dataset name based on environment."""
    env = os.getenv("FLASK_ENV", "production")
    if env == "development":
        return BIGQUERY_DATASET_DEV
    return BIGQUERY_DATASET_PROD


def insert_rows(table_name, rows):
    """Insert rows into a BigQuery table. rows is a list of dicts."""
    from google.cloud import bigquery
    client = _get_client()
    table_ref = f"{BIGQUERY_PROJECT_ID}.{_dataset()}.{table_name}"
    errors = client.insert_rows_json(table_ref, rows)
    if errors:
        raise RuntimeError(f"BigQuery insert errors for {table_name}: {errors}")


def query(sql, params=None):
    """Run a parameterized BigQuery query and return list of row dicts.

    params: list of google.cloud.bigquery.ScalarQueryParameter
    """
    from google.cloud import bigquery
    client = _get_client()
    job_config = bigquery.QueryJobConfig(query_parameters=params or [])
    job = client.query(sql, job_config=job_config)
    results = job.result()
    return [dict(row) for row in results]


# ── Red Light History ──────────────────────────────────────────────────────

def write_red_light_score(property_uuid, report_month, overall_score,
                           market_score, marketing_score, funnel_score,
                           experience_score, status):
    """Insert one row into red_light_history. Called after each scoring run."""
    from datetime import datetime
    rows = [{
        "property_uuid": property_uuid,
        "report_month": report_month,           # "YYYY-MM-DD" (first of month)
        "overall_score": float(overall_score),
        "market_score": float(market_score),
        "marketing_score": float(marketing_score),
        "funnel_score": float(funnel_score),
        "experience_score": float(experience_score),
        "status": status,                        # "RED" / "YELLOW" / "GREEN"
        "scored_at": datetime.utcnow().isoformat() + "Z",
    }]
    insert_rows("red_light_history", rows)
    logger.info("Wrote red_light_history for %s %s", property_uuid, report_month)


def get_red_light_current_and_prev(property_uuid):
    """Fetch current and previous month scores for digest generation.

    Returns dict with 'current' and 'previous' keys, each a row dict or None.
    Filters to report_month matching current calendar month, and one month prior.
    """
    from google.cloud import bigquery
    dataset = _dataset()
    sql = f"""
        SELECT
            report_month,
            overall_score,
            market_score,
            marketing_score,
            funnel_score,
            experience_score,
            status
        FROM `{BIGQUERY_PROJECT_ID}.{dataset}.red_light_history`
        WHERE property_uuid = @uuid
          AND report_month >= DATE_TRUNC(DATE_SUB(CURRENT_DATE(), INTERVAL 1 MONTH), MONTH)
        ORDER BY report_month DESC
        LIMIT 2
    """
    params = [bigquery.ScalarQueryParameter("uuid", "STRING", property_uuid)]
    rows = query(sql, params)

    result = {"current": None, "previous": None}
    if len(rows) >= 1:
        result["current"] = rows[0]
    if len(rows) >= 2:
        result["previous"] = rows[1]
    return result


# ── Report Insights ────────────────────────────────────────────────────────

def write_report_insights(property_uuid, ninjacat_system_id, report_month,
                           report_type, insights_list, raw_text):
    """Insert extracted insights into report_insights.

    insights_list: list of dicts with keys: insight_type, finding, recommendation, priority
    raw_text: full AI insights section from the PDF
    """
    from datetime import datetime
    now = datetime.utcnow().isoformat() + "Z"
    rows = []
    for item in insights_list:
        rows.append({
            "property_uuid": property_uuid,
            "ninjacat_system_id": ninjacat_system_id or "",
            "report_month": report_month,
            "report_type": report_type,
            "insight_type": item.get("insight_type", ""),
            "finding": item.get("finding", ""),
            "recommendation": item.get("recommendation"),    # nullable
            "priority": item.get("priority", "low"),
            "raw_text": raw_text,
            "ingested_at": now,
        })
    if rows:
        insert_rows("report_insights", rows)
        logger.info("Wrote %d report_insights for %s %s", len(rows), property_uuid, report_month)


def get_top_insights(property_uuid, report_month=None, limit=5):
    """Fetch top insights for a property for digest generation.

    report_month: "YYYY-MM-DD" or None to use current month.
    Returns list of dicts with finding, recommendation, priority, insight_type.
    """
    from google.cloud import bigquery
    dataset = _dataset()
    month_filter = "FORMAT_DATE('%Y-%m-%d', DATE_TRUNC(CURRENT_DATE(), MONTH))"
    if report_month:
        month_filter = f"'{report_month}'"

    sql = f"""
        SELECT insight_type, finding, recommendation, priority
        FROM `{BIGQUERY_PROJECT_ID}.{dataset}.report_insights`
        WHERE property_uuid = @uuid
          AND report_month = {month_filter}
        ORDER BY
            CASE priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
            ingested_at DESC
        LIMIT @lim
    """
    params = [
        bigquery.ScalarQueryParameter("uuid", "STRING", property_uuid),
        bigquery.ScalarQueryParameter("lim", "INT64", limit),
    ]
    return query(sql, params)
