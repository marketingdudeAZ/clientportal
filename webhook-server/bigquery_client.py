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


# ── RPM Properties reference table ─────────────────────────────────────────
# This is the "dimension" table that keeps market / unit_count / name for every
# active RPM property, synced nightly from HubSpot. NinjaCat's export doesn't
# need to carry these fields — BQ joins metrics -> properties on property_uuid.
#
# Schema (see BIGQUERY_SETUP.md for the CREATE TABLE DDL):
#   property_uuid STRING NOT NULL, hubspot_company_id STRING,
#   ninjacat_system_id STRING, name STRING, market STRING,
#   unit_count INT64, occupancy_status STRING, plestatus STRING,
#   updated_at TIMESTAMP
# Clustered on property_uuid for fast joins.


def upsert_rpm_properties(rows):
    """Replace the rpm_properties table contents with `rows`.

    Uses a DELETE + INSERT inside a BQ transaction (MERGE would be nicer but
    requires a staging table). Safe to run nightly. `rows` is a list of dicts
    shaped like the schema above.
    """
    from google.cloud import bigquery
    client  = _get_client()
    dataset = _dataset()
    table   = f"{BIGQUERY_PROJECT_ID}.{dataset}.rpm_properties"

    # Truncate then insert — simplest pattern, table is tiny (<1000 rows).
    client.query(f"TRUNCATE TABLE `{table}`").result()
    if rows:
        errors = client.insert_rows_json(table, rows)
        if errors:
            raise RuntimeError(f"BigQuery rpm_properties insert errors: {errors}")
    logger.info("rpm_properties refreshed with %d rows", len(rows))


# ── NinjaCat Metrics ───────────────────────────────────────────────────────
# Schema (see BIGQUERY_SETUP.md for the CREATE TABLE DDL):
#   date DATE, property_uuid STRING, ninjacat_system_id STRING,
#   property_name STRING, market STRING, unit_count INT64,
#   channel STRING, spend FLOAT64, impressions INT64, clicks INT64,
#   leads INT64, conversions INT64, ingested_at TIMESTAMP
# Partitioned by date, clustered on (property_uuid, channel).
#
# Channel values are normalized on ingest to the portal's vocabulary:
#   google_ads | meta | seo_organic | video_creative | other


def is_bigquery_configured():
    """Return True if BQ env vars look set up. Used for graceful fallback."""
    if not BIGQUERY_PROJECT_ID:
        return False
    sa = BIGQUERY_SERVICE_ACCOUNT_JSON
    if not sa or sa == "path/to/service_account.json":
        return False
    if not os.path.exists(sa):
        return False
    return True


def get_ninjacat_current_perf(property_uuid):
    """Return current-month perf for one property from ninjacat_metrics.

    Aggregates the most recent complete month of daily rows, grouped by channel.
    Returns dict shaped for the portal's `current_perf` contract:

        {
            "leads":    <int total>,
            "cpl":      <float, blended>,
            "spend":    <float total>,
            "month":    "YYYY-MM",
            "channels": {"google_ads": {"spend", "leads", "cpl"}, ...}
        }

    Returns None if no data or BQ not configured.
    """
    from google.cloud import bigquery
    dataset = _dataset()
    sql = f"""
        SELECT
            FORMAT_DATE('%Y-%m', date) AS report_month,
            channel,
            SUM(spend)      AS spend,
            SUM(leads)      AS leads
        FROM `{BIGQUERY_PROJECT_ID}.{dataset}.ninjacat_metrics`
        WHERE property_uuid = @uuid
          AND date >= DATE_TRUNC(DATE_SUB(CURRENT_DATE(), INTERVAL 1 MONTH), MONTH)
          AND date <  DATE_TRUNC(CURRENT_DATE(), MONTH)
        GROUP BY report_month, channel
    """
    params = [bigquery.ScalarQueryParameter("uuid", "STRING", property_uuid)]
    rows = query(sql, params)
    if not rows:
        return None

    month = rows[0]["report_month"]
    total_spend = sum((r.get("spend") or 0) for r in rows)
    total_leads = sum((r.get("leads") or 0) for r in rows)
    blended_cpl = round(total_spend / total_leads, 2) if total_leads > 0 else None
    channels = {}
    for r in rows:
        ch = r.get("channel") or "other"
        s = r.get("spend") or 0
        l = r.get("leads") or 0
        channels[ch] = {
            "spend": round(s, 2),
            "leads": int(l),
            "cpl":   round(s / l, 2) if l > 0 else None,
        }
    return {
        "leads":    int(total_leads),
        "cpl":      blended_cpl,
        "spend":    round(total_spend, 2),
        "month":    month,
        "channels": channels,
    }


def get_ninjacat_benchmarks(market, size_band):
    """Return channel × month benchmark rows for a market + size_band segment.

    Joins ninjacat_metrics (daily perf) against rpm_properties (uuid → market,
    unit_count) so NinjaCat only needs to emit property_uuid — market and
    unit_count live in HubSpot and are synced to rpm_properties by the nightly
    sync job (POST /api/internal/sync-properties-to-bq).

    Uses the last 12 months of data. Rolls up daily rows to monthly per-property
    aggregates, then computes quartiles across properties. Returns list of dicts
    shaped identically to the seeded /api/benchmarks response:

        [{channel, month, market, size_band, median_cpl,
          median_leads_per_1k_spend, p25_cpl, p75_cpl, sample_size,
          data_source: "bigquery"}]

    Returns empty list if no data. Caller decides whether to fall back to seeded.
    """
    from google.cloud import bigquery
    dataset = _dataset()
    # Map size_band -> unit range. Matches the portal's frontend convention
    # (see loadForecast: units < 200 -> small, <= 400 -> mid, else large).
    if size_band == "small":
        size_filter = "p.unit_count < 200"
    elif size_band == "large":
        size_filter = "p.unit_count > 400"
    else:
        size_filter = "p.unit_count >= 200 AND p.unit_count <= 400"

    sql = f"""
        WITH monthly_per_prop AS (
            SELECT
                m.property_uuid,
                m.channel,
                EXTRACT(MONTH FROM m.date) AS month,
                SUM(m.spend) AS spend,
                SUM(m.leads) AS leads
            FROM `{BIGQUERY_PROJECT_ID}.{dataset}.ninjacat_metrics` AS m
            JOIN `{BIGQUERY_PROJECT_ID}.{dataset}.rpm_properties`  AS p
              ON m.property_uuid = p.property_uuid
            WHERE p.market = @market
              AND {size_filter}
              AND m.date >= DATE_SUB(CURRENT_DATE(), INTERVAL 12 MONTH)
            GROUP BY m.property_uuid, m.channel, month
            HAVING spend > 0 AND leads > 0
        )
        SELECT
            channel,
            month,
            APPROX_QUANTILES(SAFE_DIVIDE(spend, leads), 4) AS cpl_q,
            APPROX_QUANTILES(SAFE_DIVIDE(leads * 1000.0, spend), 4) AS lpk_q,
            COUNT(DISTINCT property_uuid) AS sample_size
        FROM monthly_per_prop
        GROUP BY channel, month
        HAVING sample_size >= 3
    """
    params = [bigquery.ScalarQueryParameter("market", "STRING", market or "")]
    rows = query(sql, params)

    out = []
    for r in rows:
        cpl_q = r.get("cpl_q") or []
        lpk_q = r.get("lpk_q") or []
        # APPROX_QUANTILES with 4 returns 5 values: [min, q25, q50, q75, max]
        if len(cpl_q) < 5:
            continue
        out.append({
            "channel":    r.get("channel"),
            "month":      int(r.get("month")),
            "market":     market or "portfolio",
            "size_band":  size_band,
            "median_cpl": round(float(cpl_q[2]), 2),
            "p25_cpl":    round(float(cpl_q[1]), 2),
            "p75_cpl":    round(float(cpl_q[3]), 2),
            "median_leads_per_1k_spend": round(float(lpk_q[2]), 3),
            "sample_size": int(r.get("sample_size") or 0),
            "data_source": "bigquery",
        })
    return out


# ── SEO rank history (daily time series) ───────────────────────────────────

def write_seo_rank_snapshot(property_uuid, rows):
    """Append a daily rank snapshot for a property.

    rows: list of dicts with keys: keyword, position (int|null),
          url (str|null), volume, difficulty, fetched_at (ISO datetime).
    """
    from config import BIGQUERY_SEO_RANKS_TABLE
    from datetime import datetime
    now = datetime.utcnow().isoformat() + "Z"
    records = []
    for r in rows:
        records.append({
            "property_uuid": property_uuid,
            "keyword": r["keyword"],
            "position": r.get("position"),
            "url": r.get("url"),
            "volume": r.get("volume"),
            "difficulty": r.get("difficulty"),
            "fetched_at": r.get("fetched_at", now),
        })
    if records:
        insert_rows(BIGQUERY_SEO_RANKS_TABLE, records)


def get_seo_rank_history(property_uuid, days=90):
    """Return time series of keyword ranks for a property.

    Result: list of {keyword, position, url, fetched_at} ordered by fetched_at DESC.
    """
    from google.cloud import bigquery
    from config import BIGQUERY_SEO_RANKS_TABLE
    dataset = _dataset()
    sql = f"""
        SELECT keyword, position, url, volume, difficulty, fetched_at
        FROM `{BIGQUERY_PROJECT_ID}.{dataset}.{BIGQUERY_SEO_RANKS_TABLE}`
        WHERE property_uuid = @uuid
          AND fetched_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)
        ORDER BY fetched_at DESC
    """
    params = [
        bigquery.ScalarQueryParameter("uuid", "STRING", property_uuid),
        bigquery.ScalarQueryParameter("days", "INT64", days),
    ]
    return query(sql, params)


def get_seo_rank_latest(property_uuid):
    """Most recent position per keyword for a property."""
    from google.cloud import bigquery
    from config import BIGQUERY_SEO_RANKS_TABLE
    dataset = _dataset()
    sql = f"""
        WITH ranked AS (
          SELECT keyword, position, url, volume, difficulty, fetched_at,
                 ROW_NUMBER() OVER (PARTITION BY keyword ORDER BY fetched_at DESC) rn
          FROM `{BIGQUERY_PROJECT_ID}.{dataset}.{BIGQUERY_SEO_RANKS_TABLE}`
          WHERE property_uuid = @uuid
        )
        SELECT keyword, position, url, volume, difficulty, fetched_at
        FROM ranked WHERE rn = 1
    """
    params = [bigquery.ScalarQueryParameter("uuid", "STRING", property_uuid)]
    return query(sql, params)


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
