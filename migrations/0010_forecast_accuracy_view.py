"""Create forecast_accuracy view — joins forecast_runs to next-month
AptIQ realized leases to measure prediction accuracy over time.

For each forecast row, find the AptIQ snapshot with snapshot_month =
forecast.run_at_month + horizon_days. That's the realized outcome.
Compute the absolute and relative error, plus a CI-hit boolean.

Once enough months accumulate (3+ forecasts per property), we can
answer:
  - Is the methodology overconfident (CI breaches > stated rate)?
  - Are some property cohorts more predictable than others?
  - Does forecast accuracy improve when more trailing data exists?

This is the empirical foundation for Forecasting v2 (channel-attributed
regression). Without measuring v1, we can't justify the engineering
cost of v2.
"""

TARGETS = ["bigquery"]

DDL = """
CREATE OR REPLACE VIEW `{project}.{dataset}.forecast_accuracy` AS

WITH forecasts AS (
  -- For each property, pick the LATEST forecast per month — there can
  -- be many runs per month while iterating; we only validate the final
  SELECT
    forecast_id, property_uuid, run_at, horizon_days,
    methodology, forecast_leases, ci_low, ci_high, confidence_level,
    DATE_TRUNC(DATE(run_at), MONTH) AS run_month,
    DATE_ADD(DATE_TRUNC(DATE(run_at), MONTH), INTERVAL horizon_days DAY) AS predict_for_date,
    ROW_NUMBER() OVER (
      PARTITION BY property_uuid, DATE_TRUNC(DATE(run_at), MONTH)
      ORDER BY run_at DESC
    ) AS _rn
  FROM `{project}.{dataset}.forecast_runs`
  WHERE forecast_leases IS NOT NULL
),

realized AS (
  -- AptIQ leases_last_30 represents trailing 30 days as of snapshot_month.
  -- For a forecast made at run_month with 30-day horizon, the realized
  -- outcome is the leases_last_30 from snapshot_month = run_month + 1.
  SELECT
    property_uuid,
    DATE_TRUNC(snapshot_month, MONTH) AS realized_month,
    leases_last_30 AS realized_leases
  FROM `{project}.{dataset}.aptiq_snapshots_latest`
  WHERE leases_last_30 IS NOT NULL
)

SELECT
  f.forecast_id,
  f.property_uuid,
  f.run_at,
  f.run_month,
  f.horizon_days,
  f.methodology,
  f.forecast_leases,
  f.ci_low,
  f.ci_high,
  f.confidence_level,
  r.realized_leases,
  ABS(f.forecast_leases - r.realized_leases) AS abs_error,
  SAFE_DIVIDE(ABS(f.forecast_leases - r.realized_leases), r.realized_leases) AS rel_error,
  -- CI hit: was the realized value inside the predicted CI?
  (r.realized_leases >= f.ci_low AND r.realized_leases <= f.ci_high) AS ci_hit,
  -- Bias direction
  CASE
    WHEN r.realized_leases > f.forecast_leases THEN 'under_forecast'
    WHEN r.realized_leases < f.forecast_leases THEN 'over_forecast'
    ELSE 'exact'
  END AS bias_direction
FROM forecasts f
LEFT JOIN realized r
  ON r.property_uuid = f.property_uuid
 AND r.realized_month = DATE_ADD(f.run_month, INTERVAL 1 MONTH)
WHERE f._rn = 1
"""


def up(ctx):
    ctx.run_bq(ctx.render(DDL))
    ctx.log("Created forecast_accuracy view")


def down(ctx):
    ctx.run_bq(
        f"DROP VIEW IF EXISTS `{ctx.project}.{ctx.dataset}.forecast_accuracy`"
    )
    ctx.log("Dropped forecast_accuracy view")
