"""Create the rpm_portal.assets index table (ADR 0020, Stage 1).

The portal's list / rename / remove surface reads this table instead of the
Drive API, so browsing the Files section doesn't cost a Drive round-trip per
asset. Drive holds the bytes; this holds the pointers.

`drive_file_ids` and `variants_json` are STRING columns holding JSON rather
than REPEATED STRUCTs on purpose: the variant *set* is not settled (ADR 0020
action item #3 has the paid team confirming the ad-size list), and a JSON blob
absorbs a size being added or dropped without a schema migration on a table the
portal reads live.

Clustered on (uuid, status) — every portal read is "live assets for this one
property", and every archive flips exactly that pair.

Idempotent: CREATE TABLE IF NOT EXISTS.
"""

TARGETS = ["bigquery"]

DDL = """
CREATE TABLE IF NOT EXISTS `{project}.{dataset}.assets` (
  uuid            STRING NOT NULL,
  asset_id        STRING NOT NULL,
  name            STRING,
  kind            STRING,
  status          STRING,
  drive_folder_id STRING,
  drive_file_ids  STRING,
  variants_json   STRING,
  source          STRING,
  created_at      TIMESTAMP,
  updated_at      TIMESTAMP
)
CLUSTER BY uuid, status
"""


def up(ctx):
    ctx.run_bq(ctx.render(DDL))
    ctx.log("Created assets index table (ADR 0020)")


def down(ctx):
    ctx.run_bq(f"DROP TABLE IF EXISTS `{ctx.project}.{ctx.dataset}.assets`")
    ctx.log("Dropped assets index table")
