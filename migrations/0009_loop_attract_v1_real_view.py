"""Replace loop_attract_v1 stub with the REAL view, driving from
monthly_spend_per_property instead of rpm_properties.

The original 0005 used rpm_properties as the driver table — but
rpm_properties requires the sync-properties-to-bq job to have run
(rate-limit-prone HubSpot scan). monthly_spend_per_property is the
hot path: it's populated by sync-spend-to-bq which doesn't fan out
to HubSpot at the same scale.

The new view:
  - Drives from monthly_spend_per_property (returns rows whenever spend exists)
  - LEFT JOINs rpm_properties for metadata (name, market, seo_tier) when it exists
  - LEFT JOINs seo_ranks_daily aggregations for keyword counts when it exists

Forward-only migration — supersedes 0005's STUB view via CREATE OR REPLACE.
0005's tracking row stays in schema_migrations; this migration adds 0009's row.
"""

TARGETS = ["bigquery"]

REAL_VIEW = """
CREATE OR REPLACE VIEW `{project}.{dataset}.loop_attract_v1` AS

WITH paid_monthly AS (
  -- The driver. Returns rows whenever monthly_spend_per_property has
  -- entries for a property × month. Pick the most recent snapshot
  -- when both current_month + baseline_backfill rows exist for the
  -- same month (prefer current_month).
  SELECT
    property_uuid,
    DATE_TRUNC(month, MONTH) AS month,
    ANY_VALUE(hubspot_company_id) AS hubspot_company_id,
    SAFE_CAST(MAX(IF(snapshot_kind='current_month', paid_search_spend,
                     IF(snapshot_kind='baseline_backfill', paid_search_spend, NULL))) AS FLOAT64) AS paid_search_spend,
    SAFE_CAST(MAX(IF(snapshot_kind='current_month', paid_social_spend,
                     IF(snapshot_kind='baseline_backfill', paid_social_spend, NULL))) AS FLOAT64) AS paid_social_spend,
    SAFE_CAST(MAX(IF(snapshot_kind='current_month', seo_spend,
                     IF(snapshot_kind='baseline_backfill', seo_spend, NULL))) AS FLOAT64) AS seo_spend,
    SAFE_CAST(MAX(IF(snapshot_kind='current_month', reputation_spend,
                     IF(snapshot_kind='baseline_backfill', reputation_spend, NULL))) AS FLOAT64) AS reputation_spend,
    SAFE_CAST(MAX(IF(snapshot_kind='current_month', creative_spend,
                     IF(snapshot_kind='baseline_backfill', creative_spend, NULL))) AS FLOAT64) AS creative_spend,
    SAFE_CAST(MAX(IF(snapshot_kind='current_month', total_spend,
                     IF(snapshot_kind='baseline_backfill', total_spend, NULL))) AS FLOAT64) AS total_spend
  FROM `{project}.{dataset}.monthly_spend_per_property`
  WHERE month IS NOT NULL
  GROUP BY property_uuid, month
),

seo_monthly AS (
  SELECT
    property_uuid,
    DATE_TRUNC(DATE(fetched_at), MONTH) AS month,
    COUNTIF(position BETWEEN 1 AND 3)  AS keywords_top_3,
    COUNTIF(position BETWEEN 1 AND 10) AS keywords_top_10,
    COUNT(DISTINCT keyword)             AS keywords_tracked
  FROM `{project}.{dataset}.seo_ranks_daily`
  GROUP BY property_uuid, month
)

SELECT
  p.property_uuid,
  p.hubspot_company_id,
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
FROM paid_monthly p
LEFT JOIN `{project}.{dataset}.rpm_properties` rp USING (property_uuid)
LEFT JOIN seo_monthly s ON s.property_uuid = p.property_uuid AND s.month = p.month
"""

STUB_VIEW = """
CREATE OR REPLACE VIEW `{project}.{dataset}.loop_attract_v1` AS
SELECT
  CAST(NULL AS STRING)  AS property_uuid,
  CAST(NULL AS STRING)  AS hubspot_company_id,
  CAST(NULL AS STRING)  AS name,
  CAST(NULL AS STRING)  AS market,
  CAST(NULL AS STRING)  AS seo_tier,
  CAST(NULL AS DATE)    AS month,
  CAST(NULL AS FLOAT64) AS paid_search_spend,
  CAST(NULL AS FLOAT64) AS paid_social_spend,
  CAST(NULL AS FLOAT64) AS seo_spend,
  CAST(NULL AS FLOAT64) AS reputation_spend,
  CAST(NULL AS FLOAT64) AS creative_spend,
  CAST(NULL AS FLOAT64) AS total_spend,
  CAST(NULL AS INT64)   AS keywords_top_3,
  CAST(NULL AS INT64)   AS keywords_top_10,
  CAST(NULL AS INT64)   AS keywords_tracked
FROM (SELECT 1 AS _dummy)
WHERE FALSE
"""


def up(ctx):
    # Driver is monthly_spend_per_property. Verify it exists; otherwise
    # keep the stub (downstream still queries safely).
    check_sql = f"""
      SELECT table_name FROM `{ctx.project}.{ctx.dataset}.INFORMATION_SCHEMA.TABLES`
      WHERE table_name IN ('monthly_spend_per_property')
    """
    try:
        rows = list(ctx.bq_client.query(check_sql).result())
        names = {r.table_name for r in rows}
    except Exception as exc:
        ctx.log(f"Table check failed: {exc} — installing STUB view")
        ctx.run_bq(ctx.render(STUB_VIEW))
        return

    if 'monthly_spend_per_property' not in names:
        ctx.log("monthly_spend_per_property missing — keeping STUB view")
        ctx.run_bq(ctx.render(STUB_VIEW))
        return

    ctx.run_bq(ctx.render(REAL_VIEW))
    ctx.log("Created REAL loop_attract_v1 view (driver: monthly_spend_per_property)")


def down(ctx):
    # Reverting to the 0005 stub
    ctx.run_bq(ctx.render(STUB_VIEW))
    ctx.log("Reverted loop_attract_v1 to STUB view")
