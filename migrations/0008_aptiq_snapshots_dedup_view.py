"""Add aptiq_snapshots_latest view that dedupes by (property_uuid, snapshot_month).

The aptiq_snapshots table accumulates duplicates because two writers
both insert into it:
  1. webhook-server/redlight_v2.py:persist_snapshot() — writes one row
     per Red Light v2 report run (current month)
  2. webhook-server/server.py:aptiq-backfill-history — writes 13 rows
     per backfill (trailing months)

If both fire for the same property in the same month, we get duplicates.
After today's Ashton run we saw 27 rows for ~13 unique months.

This is a non-destructive cleanup: instead of deleting duplicate rows
(streaming buffer + audit value risk), we add a VIEW that picks the
most recently snapshotted row per (property_uuid, snapshot_month).
Callers should prefer this view over the raw table.

The Forecasting Engine's get_trailing_data() can be patched to read
from this view in a follow-up. Today, the duplicates just inflate
data_months without affecting correctness (the forecast math sums
spend × inv_cpl which is identical whatever the row count).
"""

TARGETS = ["bigquery"]

DDL = """
CREATE OR REPLACE VIEW `{project}.{dataset}.aptiq_snapshots_latest` AS
SELECT
  property_uuid,
  hubspot_company_id,
  aptiq_property_id,
  snapshot_month,
  occupancy,
  leased_percent,
  exposure,
  available_units,
  leases_last_30,
  applications_last_30,
  asking_rent,
  ner,
  rent_psf,
  monthly_service_cost,
  cost_per_lease,
  snapshotted_at,
  raw_payload
FROM (
  SELECT
    *,
    ROW_NUMBER() OVER (
      PARTITION BY property_uuid, snapshot_month
      ORDER BY snapshotted_at DESC
    ) AS _rn
  FROM `{project}.{dataset}.aptiq_snapshots`
)
WHERE _rn = 1
"""


def up(ctx):
    ctx.run_bq(ctx.render(DDL))
    ctx.log("Created aptiq_snapshots_latest deduped view")


def down(ctx):
    ctx.run_bq(
        f"DROP VIEW IF EXISTS `{ctx.project}.{ctx.dataset}.aptiq_snapshots_latest`"
    )
    ctx.log("Dropped aptiq_snapshots_latest view")
