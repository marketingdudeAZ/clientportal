"""Create forecast_runs table — output of the Forecasting Engine (ADR 0009).

One row per (property × forecast horizon × run). The most recent row per
property is the active forecast the portal reads. Older rows are kept
for backtest analysis.
"""

TARGETS = ["bigquery"]

DDL = """
CREATE TABLE IF NOT EXISTS `{project}.{dataset}.forecast_runs` (
  forecast_id        STRING NOT NULL,
  property_uuid      STRING NOT NULL,
  run_at             TIMESTAMP NOT NULL,
  horizon_days       INT64 NOT NULL,
  methodology        STRING,
  forecast_leases    FLOAT64,
  ci_low             FLOAT64,
  ci_high            FLOAT64,
  confidence_level   FLOAT64,
  inputs_payload     STRING,
  channel_allocation STRING,
  recommendations    STRING,
  observed_leases    INT64
)
PARTITION BY DATE(run_at)
CLUSTER BY property_uuid
"""


def up(ctx):
    ctx.run_bq(ctx.render(DDL))
    ctx.log("Created forecast_runs table")


def down(ctx):
    ctx.run_bq(f"DROP TABLE IF EXISTS `{ctx.project}.{ctx.dataset}.forecast_runs`")
    ctx.log("Dropped forecast_runs table")
