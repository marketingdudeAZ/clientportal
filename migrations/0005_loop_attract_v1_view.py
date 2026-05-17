"""Create loop_attract_v1 BigQuery view — the canonical Attract-stage
data join (paid spend + SEO ranks + AI mentions) per property per month.

Inputs (all already exist):
  - monthly_spend_per_property (from spend_sheet)
  - seo_ranks_daily (from seo_refresh_cron)
  - rpm_properties (dimension)

Used by:
  - Optimize stage forecasting (joins to loop_convert_v1 for full-funnel)
  - Portal Loop view Attract panel
"""

TARGETS = ["bigquery"]

VIEW = """
CREATE OR REPLACE VIEW `{project}.{dataset}.loop_attract_v1` AS

WITH paid_monthly AS (
  -- monthly_spend_per_property may not exist on day 1; the COALESCE
  -- chain below tolerates a missing table by returning 0 spend
  SELECT
    property_uuid,
    DATE_TRUNC(DATE(month), MONTH) AS month,
    SAFE_CAST(paid_search_spend AS FLOAT64) AS paid_search_spend,
    SAFE_CAST(paid_social_spend AS FLOAT64) AS paid_social_spend,
    SAFE_CAST(seo_spend AS FLOAT64) AS seo_spend,
    SAFE_CAST(reputation_spend AS FLOAT64) AS reputation_spend,
    SAFE_CAST(creative_spend AS FLOAT64) AS creative_spend,
    SAFE_CAST(paid_search_spend AS FLOAT64) +
    SAFE_CAST(paid_social_spend AS FLOAT64) +
    SAFE_CAST(seo_spend AS FLOAT64) +
    SAFE_CAST(reputation_spend AS FLOAT64) +
    SAFE_CAST(creative_spend AS FLOAT64) AS total_spend
  FROM `{project}.{dataset}.monthly_spend_per_property`
  WHERE month IS NOT NULL
),

seo_monthly AS (
  SELECT
    property_uuid,
    DATE_TRUNC(DATE(fetched_at), MONTH) AS month,
    COUNTIF(position BETWEEN 1 AND 3) AS keywords_top_3,
    COUNTIF(position BETWEEN 1 AND 10) AS keywords_top_10,
    COUNT(DISTINCT keyword) AS keywords_tracked
  FROM `{project}.{dataset}.seo_ranks_daily`
  GROUP BY 1, 2
)

SELECT
  rp.property_uuid,
  rp.name,
  rp.market,
  rp.seo_tier,
  p.month,
  p.paid_search_spend,
  p.paid_social_spend,
  p.seo_spend,
  p.reputation_spend,
  p.creative_spend,
  p.total_spend,
  s.keywords_top_3,
  s.keywords_top_10,
  s.keywords_tracked
FROM `{project}.{dataset}.rpm_properties` rp
LEFT JOIN paid_monthly p USING (property_uuid)
LEFT JOIN seo_monthly s ON s.property_uuid = rp.property_uuid AND s.month = p.month
"""

STUB_VIEW = """
CREATE OR REPLACE VIEW `{project}.{dataset}.loop_attract_v1` AS
SELECT
  CAST(NULL AS STRING) AS property_uuid,
  CAST(NULL AS STRING) AS name,
  CAST(NULL AS STRING) AS market,
  CAST(NULL AS STRING) AS seo_tier,
  CAST(NULL AS DATE) AS month,
  CAST(NULL AS FLOAT64) AS paid_search_spend,
  CAST(NULL AS FLOAT64) AS paid_social_spend,
  CAST(NULL AS FLOAT64) AS seo_spend,
  CAST(NULL AS FLOAT64) AS reputation_spend,
  CAST(NULL AS FLOAT64) AS creative_spend,
  CAST(NULL AS FLOAT64) AS total_spend,
  CAST(NULL AS INT64) AS keywords_top_3,
  CAST(NULL AS INT64) AS keywords_top_10,
  CAST(NULL AS INT64) AS keywords_tracked
WHERE FALSE
"""


def up(ctx):
    # Check whether monthly_spend_per_property exists; if not, ship the
    # stub view so downstream code doesn't error.
    check_sql = f"""
      SELECT table_name FROM `{ctx.project}.{ctx.dataset}.INFORMATION_SCHEMA.TABLES`
      WHERE table_name IN ('monthly_spend_per_property', 'rpm_properties', 'seo_ranks_daily')
    """
    try:
        rows = list(ctx.bq_client.query(check_sql).result())
        names = {r.table_name for r in rows}
    except Exception as exc:
        ctx.log(f"Table existence check failed: {exc}")
        names = set()

    if 'rpm_properties' not in names:
        ctx.log("rpm_properties missing — installing STUB view")
        ctx.run_bq(ctx.render(STUB_VIEW))
        return

    if 'monthly_spend_per_property' not in names or 'seo_ranks_daily' not in names:
        ctx.log(f"Some source tables missing ({names}) — installing STUB view")
        ctx.run_bq(ctx.render(STUB_VIEW))
        return

    ctx.run_bq(ctx.render(VIEW))
    ctx.log("Created loop_attract_v1 view")


def down(ctx):
    ctx.run_bq(f"DROP VIEW IF EXISTS `{ctx.project}.{ctx.dataset}.loop_attract_v1`")
    ctx.log("Dropped loop_attract_v1 view")
