"""Create loop_convert_v1 BigQuery view — the canonical Convert-stage
data foundation that joins Hyly attribution to AptIQ leases (ADR 0015).

The Optimize stage and Forecasting Engine read from this view, never
from raw Hyly tables. If Hyly renames a column on their side, this view
fails loudly on the next forecast run (rather than silently dropping
data).

Requires:
  - BIGQUERY_HYLY_DATASET env var pointing at Hyly's BQ dataset
  - rpm_properties has hyly_property_id column (migration 0003)
  - aptiq_snapshots already exists (created by earlier work)

If Hyly's dataset isn't configured yet, this migration creates a STUB
view that returns no rows but has the same shape, so downstream code
that queries the view doesn't error. The migration is re-runnable; when
Hyly's beta lands, re-run with BIGQUERY_HYLY_DATASET set and the real
view replaces the stub.
"""

TARGETS = ["bigquery"]

STUB_VIEW = """
CREATE OR REPLACE VIEW `{project}.{dataset}.loop_convert_v1` AS
SELECT
  CAST(NULL AS STRING) AS property_uuid,
  CAST(NULL AS STRING) AS name,
  CAST(NULL AS STRING) AS market,
  CAST(NULL AS DATE)   AS month,
  CAST(NULL AS STRING) AS source,
  CAST(NULL AS INT64)  AS visitors,
  CAST(NULL AS INT64)  AS known_visitors,
  CAST(NULL AS INT64)  AS contacts,
  CAST(NULL AS INT64)  AS aptiq_leases_last_30,
  CAST(NULL AS INT64)  AS applications_last_30,
  CAST(NULL AS FLOAT64) AS contact_rate,
  CAST(NULL AS FLOAT64) AS lead_to_lease_rate
WHERE FALSE
"""

REAL_VIEW = """
CREATE OR REPLACE VIEW `{project}.{dataset}.loop_convert_v1` AS

WITH hyly_monthly_channel AS (
  SELECT
    h.property_id AS hyly_property_id,
    DATE_TRUNC(DATE(h.event_date), MONTH) AS month,
    h.source,
    SUM(COALESCE(h.visitors, 0)) AS visitors,
    SUM(COALESCE(h.known_visitors, 0)) AS known_visitors,
    SUM(COALESCE(h.converted_contacts, 0)) AS contacts
  FROM `{project}.{hyly_dataset}.daily_activity_summary` h
  GROUP BY 1, 2, 3
),

property_map AS (
  SELECT
    rp.property_uuid,
    rp.hyly_property_id,
    rp.aptiq_property_id,
    rp.name,
    rp.market
  FROM `{project}.{dataset}.rpm_properties` rp
  WHERE rp.hyly_property_id IS NOT NULL AND rp.hyly_property_id != ''
)

SELECT
  pm.property_uuid,
  pm.name,
  pm.market,
  hm.month,
  hm.source,
  hm.visitors,
  hm.known_visitors,
  hm.contacts,
  a.leases_last_30 AS aptiq_leases_last_30,
  a.applications_last_30,
  SAFE_DIVIDE(hm.contacts, hm.visitors) AS contact_rate,
  SAFE_DIVIDE(a.leases_last_30, hm.contacts) AS lead_to_lease_rate
FROM hyly_monthly_channel hm
JOIN property_map pm USING (hyly_property_id)
LEFT JOIN `{project}.{dataset}.aptiq_snapshots` a
  ON a.property_uuid = pm.property_uuid
 AND DATE_TRUNC(DATE(a.snapshot_month), MONTH) = hm.month
"""


def up(ctx):
    if not ctx.hyly_dataset:
        ctx.log("BIGQUERY_HYLY_DATASET not set — creating STUB view. "
                "Re-run this migration after Hyly beta env vars land.")
        ctx.run_bq(ctx.render(STUB_VIEW))
        ctx.log("Created STUB loop_convert_v1 view")
        return

    ctx.log(f"Creating REAL loop_convert_v1 view from {ctx.hyly_dataset}")
    ctx.run_bq(ctx.render(REAL_VIEW))
    ctx.log("Created loop_convert_v1 view")


def down(ctx):
    ctx.run_bq(f"DROP VIEW IF EXISTS `{ctx.project}.{ctx.dataset}.loop_convert_v1`")
    ctx.log("Dropped loop_convert_v1 view")
