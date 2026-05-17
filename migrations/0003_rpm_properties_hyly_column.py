"""Add hyly_property_id column to rpm_properties dimension table.

rpm_properties is synced nightly from HubSpot (sync_properties_to_bq
endpoint). Adding the column enables the loop_convert_v1 view (next
migration) to join Hyly's BQ tables by hyly_property_id.

Idempotent: ALTER TABLE ADD COLUMN IF NOT EXISTS.
"""

TARGETS = ["bigquery"]

DDL = """
ALTER TABLE `{project}.{dataset}.rpm_properties`
ADD COLUMN IF NOT EXISTS hyly_property_id STRING,
ADD COLUMN IF NOT EXISTS aptiq_property_id STRING,
ADD COLUMN IF NOT EXISTS aptiq_market_id STRING,
ADD COLUMN IF NOT EXISTS seo_tier STRING,
ADD COLUMN IF NOT EXISTS loop_mode STRING
"""


def up(ctx):
    # If rpm_properties doesn't exist yet, skip — the sync endpoint
    # creates it on first run. This migration only augments an
    # already-created table.
    check_sql = f"""
      SELECT table_name FROM `{ctx.project}.{ctx.dataset}.INFORMATION_SCHEMA.TABLES`
      WHERE table_name = 'rpm_properties'
    """
    try:
        rows = list(ctx.bq_client.query(check_sql).result())
    except Exception as exc:
        ctx.log(f"INFORMATION_SCHEMA check failed: {exc}")
        rows = []
    if not rows:
        ctx.log("rpm_properties table not yet created — skipping column add. "
                "Will need to re-run this migration after first nightly sync, "
                "or amend the sync endpoint to write these columns.")
        return
    ctx.run_bq(ctx.render(DDL))
    ctx.log("Added hyly_property_id, aptiq_property_id, aptiq_market_id, "
            "seo_tier, loop_mode columns to rpm_properties")


def down(ctx):
    # ALTER TABLE DROP COLUMN is supported in BQ but irreversible.
    # Provide it for symmetry; rarely used in practice.
    sql = f"""
      ALTER TABLE `{ctx.project}.{ctx.dataset}.rpm_properties`
      DROP COLUMN IF EXISTS hyly_property_id,
      DROP COLUMN IF EXISTS aptiq_property_id,
      DROP COLUMN IF EXISTS aptiq_market_id,
      DROP COLUMN IF EXISTS seo_tier,
      DROP COLUMN IF EXISTS loop_mode
    """
    ctx.run_bq(sql)
    ctx.log("Removed hyly/aptiq/tier/mode columns from rpm_properties")
