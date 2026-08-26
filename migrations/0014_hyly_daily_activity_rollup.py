"""Build hyly_daily_activity_v1 — the RPM-owned rollup of Hyly's Convert
funnel — and repoint loop_convert_v1 at it (ADR 0022).

WHY THIS EXISTS
---------------
Migration 0004 created loop_convert_v1 against
`{hyly_dataset}.daily_activity_summary`, a table ADR 0015 assumed Hyly would
provide. It does not exist and never did. Hyly ships `ga_hyly_mti`: one row per
(contact, event), carrying per-contact funnel milestones
(h_cc / h_fts / h_ft / h_app / h_lease) and a `source_mapped` channel.

This migration:
  1. materialises our own rollup at property x date x channel, in a dataset we
     control, and
  2. replaces loop_convert_v1 so its published column contract is preserved
     while its inputs change.

Downstream (forecasting, /api/loop/*, the portal Loop subpage) is unaffected by
(2) except for `visitors` / `known_visitors`, which Hyly's funnel table does not
carry — those are page-view metrics living in `ga4_analytics_events`, not yet
ingested. They are retained in the view as NULL so the contract does not break.

CONFIG
------
  BIGQUERY_HYLY_PROJECT        project owning the rollup (landing zone)
  BIGQUERY_HYLY_DATASET        dataset owning the rollup
  BIGQUERY_HYLY_SOURCE_TABLE   fully-qualified vendor table

Unset config leaves the existing stub view alone rather than failing — Hyly is
a 15-property beta and most environments have no access.
"""

from pathlib import Path

TARGETS = ["bigquery"]

# Sentinel dates: Hyly carries h_cc values back to 2002-10-26. Without a floor
# every portal chart renders a 24-year x-axis.
DATE_FLOOR = "2015-01-01"

_ROLLUP_SQL = Path(__file__).resolve().parent.parent / "connectors" / "hyly" / "rollup_daily_activity.sql"


REAL_VIEW = """
CREATE OR REPLACE VIEW `{project}.{dataset}.loop_convert_v1` AS

WITH hyly_monthly_channel AS (
  SELECT
    h.hyly_property_id,
    DATE_TRUNC(h.activity_date, MONTH) AS month,
    h.channel AS source,
    SUM(h.leads)            AS contacts,
    SUM(h.tours_scheduled)  AS tours_scheduled,
    SUM(h.tours_completed)  AS tours_completed,
    SUM(h.applications)     AS applications,
    SUM(h.leases)           AS hyly_leases
  FROM `{hyly_project}.{hyly_dataset}.hyly_daily_activity_v1` h
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
  -- Page-view metrics are not in Hyly's funnel table. Held as NULL to keep the
  -- ADR 0015 column contract intact until ga4_analytics_events is ingested.
  CAST(NULL AS INT64) AS visitors,
  CAST(NULL AS INT64) AS known_visitors,
  hm.contacts,
  hm.tours_scheduled,
  hm.tours_completed,
  hm.applications,
  hm.hyly_leases,
  a.leases_last_30 AS aptiq_leases_last_30,
  a.applications_last_30,
  CAST(NULL AS FLOAT64) AS contact_rate,
  SAFE_DIVIDE(hm.hyly_leases, hm.contacts) AS lead_to_lease_rate
FROM hyly_monthly_channel hm
JOIN property_map pm USING (hyly_property_id)
LEFT JOIN `{project}.{dataset}.aptiq_snapshots` a
  ON a.property_uuid = pm.property_uuid
 AND DATE_TRUNC(DATE(a.snapshot_month), MONTH) = hm.month
"""


def up(ctx):
    if not (ctx.hyly_dataset and ctx.hyly_source_table):
        ctx.log("BIGQUERY_HYLY_DATASET / BIGQUERY_HYLY_SOURCE_TABLE not set — "
                "skipping rollup build, leaving existing loop_convert_v1 as-is.")
        return

    dest = f"{ctx.hyly_project}.{ctx.hyly_dataset}.hyly_daily_activity_v1"
    sql = _ROLLUP_SQL.read_text().format(
        src=ctx.hyly_source_table,
        dest=dest,
        date_floor=DATE_FLOOR,
    )
    ctx.log(f"Building rollup {dest} from {ctx.hyly_source_table}")
    ctx.run_bq(sql)
    ctx.log("Rollup built")

    ctx.log("Repointing loop_convert_v1 at the rollup")
    ctx.run_bq(ctx.render(REAL_VIEW))
    ctx.log("loop_convert_v1 now reads hyly_daily_activity_v1")


def down(ctx):
    if not (ctx.hyly_dataset and ctx.hyly_project):
        return
    ctx.run_bq(
        f"DROP TABLE IF EXISTS `{ctx.hyly_project}.{ctx.hyly_dataset}.hyly_daily_activity_v1`"
    )
    ctx.log("Dropped hyly_daily_activity_v1 (loop_convert_v1 left in place — "
            "re-run migration 0004 to restore the stub)")
