"""Create hubspot_contacts_for_match table — hashed-only contact store
for Google Ads Customer Match audiences.

Per Google's Customer Match spec:
  - Email, phone, first/last name → hashed (SHA-256 hex, lowercase)
  - Country, postal_code → stored RAW (Google requires un-hashed for match)
  - contact_id is the HubSpot CRM id — our audit pointer back to the source

Privacy posture (intentional):
  - Raw email / phone / name never persist to BigQuery. They exist in
    memory only during the in-flight hashing performed by
    webhook-server/customer_match_export.py and are dropped immediately
    after the SHA-256 is computed.
  - Country + postal stay raw because Google's match algorithm requires
    them un-hashed and they are not PII at that granularity.

Partitioned by DATE(synced_at) so daily snapshots are cheap to query.
Clustered by list_id + contact_id so per-list dedup queries hit a tiny
slice of the table.

Append-only model: every sync writes a new row per (list_id, contact_id,
synced_at). The CSV builder reads the latest row per (list_id, contact_id)
via ROW_NUMBER() OVER (...) — same pattern as aptiq_snapshots_latest.

source_signature is a short SHA-1 of (list_id + contact_id + email_sha256
+ phone_sha256) so a re-sync where nothing changed produces an
identifiable identical row.
"""

TARGETS = ["bigquery"]

DDL = """
CREATE TABLE IF NOT EXISTS `{project}.{dataset}.hubspot_contacts_for_match` (
  contact_id          STRING NOT NULL,
  list_id             STRING NOT NULL,
  synced_at           TIMESTAMP NOT NULL,
  email_sha256        STRING,
  phone_sha256        STRING,
  first_name_sha256   STRING,
  last_name_sha256    STRING,
  country             STRING,
  postal_code         STRING,
  source_signature    STRING,
  hubspot_lifecycle   STRING,
  marketing_status    STRING
)
PARTITION BY DATE(synced_at)
CLUSTER BY list_id, contact_id
"""

# Companion view: latest row per (list_id, contact_id). The CSV
# builder reads this — never the raw table — so the snapshot stays
# clean even if a daily sync ran twice.
VIEW = """
CREATE OR REPLACE VIEW `{project}.{dataset}.hubspot_contacts_for_match_latest` AS
SELECT * EXCEPT(_rn) FROM (
  SELECT
    *,
    ROW_NUMBER() OVER (
      PARTITION BY list_id, contact_id
      ORDER BY synced_at DESC
    ) AS _rn
  FROM `{project}.{dataset}.hubspot_contacts_for_match`
)
WHERE _rn = 1
"""


def up(ctx):
    ctx.run_bq(ctx.render(DDL))
    ctx.log("Created hubspot_contacts_for_match table")
    ctx.run_bq(ctx.render(VIEW))
    ctx.log("Created hubspot_contacts_for_match_latest view")


def down(ctx):
    ctx.run_bq(
        f"DROP VIEW IF EXISTS `{ctx.project}.{ctx.dataset}.hubspot_contacts_for_match_latest`"
    )
    ctx.run_bq(
        f"DROP TABLE IF EXISTS `{ctx.project}.{ctx.dataset}.hubspot_contacts_for_match`"
    )
    ctx.log("Dropped hubspot_contacts_for_match table + view")
