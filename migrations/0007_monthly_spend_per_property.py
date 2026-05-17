"""Create monthly_spend_per_property table.

Per-property × per-month × per-channel spend snapshot. Populated by the
new /api/internal/sync-spend-to-bq endpoint, which reads current
monthly recurring spend from spend_sheet.py (HubSpot deals + line items)
and snapshots it for the current month.

Channel taxonomy: paid_search / paid_social / seo / reputation / creative.
SKU-to-channel mapping lives in webhook-server/spend_sheet_to_channels.py
(close to spend_sheet so they evolve together).

Idempotent: CREATE TABLE IF NOT EXISTS. Schema includes a `snapshot_kind`
column so we can distinguish:
  - "current_month"      — the live monthly recurring spend snapshot
  - "baseline_backfill"  — current spend retroactively applied to prior
                            months to give Forecasting trailing inputs
                            before natural history accumulates
"""

TARGETS = ["bigquery"]

DDL = """
CREATE TABLE IF NOT EXISTS `{project}.{dataset}.monthly_spend_per_property` (
  property_uuid       STRING NOT NULL,
  hubspot_company_id  STRING,
  month               DATE NOT NULL,        -- first of month, e.g. 2026-05-01
  snapshot_kind       STRING NOT NULL,      -- 'current_month' | 'baseline_backfill'
  recorded_at         TIMESTAMP NOT NULL,
  paid_search_spend   FLOAT64,
  paid_social_spend   FLOAT64,
  seo_spend           FLOAT64,
  reputation_spend    FLOAT64,
  creative_spend      FLOAT64,
  total_spend         FLOAT64,
  raw_by_sku          STRING,               -- JSON: SKU column key -> amount
  deal_id             STRING,
  deal_name           STRING
)
PARTITION BY month
CLUSTER BY property_uuid
"""


def up(ctx):
    ctx.run_bq(ctx.render(DDL))
    ctx.log("Created monthly_spend_per_property table")


def down(ctx):
    ctx.run_bq(
        f"DROP TABLE IF EXISTS `{ctx.project}.{ctx.dataset}.monthly_spend_per_property`"
    )
    ctx.log("Dropped monthly_spend_per_property table")
