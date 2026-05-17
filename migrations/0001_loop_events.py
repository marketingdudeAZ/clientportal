"""Create loop_events table — the central Loop Event Bus (ADR 0010).

Partitioned by DATE(occurred_at), clustered by property_uuid + stage so
property-scoped + stage-filtered queries hit a single partition + a tiny
cluster slice.

Idempotent: CREATE TABLE IF NOT EXISTS — re-running this is safe.
"""

TARGETS = ["bigquery"]

DDL = """
CREATE TABLE IF NOT EXISTS `{project}.{dataset}.loop_events` (
  event_id        STRING NOT NULL,
  property_uuid   STRING,
  company_id      STRING,
  stage           STRING NOT NULL,
  event_type      STRING NOT NULL,
  occurred_at     TIMESTAMP NOT NULL,
  recorded_at     TIMESTAMP NOT NULL,
  source          STRING,
  source_id       STRING,
  trigger         STRING,
  magnitude       FLOAT64,
  payload         STRING,
  status          STRING,
  runtime_ms      INT64,
  error_message   STRING,
  parent_event_id STRING
)
PARTITION BY DATE(occurred_at)
CLUSTER BY property_uuid, stage
"""


def up(ctx):
    ctx.run_bq(ctx.render(DDL))
    ctx.log("Created loop_events table")


def down(ctx):
    ctx.run_bq(f"DROP TABLE IF EXISTS `{ctx.project}.{ctx.dataset}.loop_events`")
    ctx.log("Dropped loop_events table")
