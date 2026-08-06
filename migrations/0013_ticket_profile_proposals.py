"""Create the ticket_profile_proposals table.

A completed ClickUp ticket PROPOSES property-profile changes; a human accepts
or rejects them in the portal (docs/ticket-to-profile-loop.md). Nothing writes
to the profile without that accept.

Append-only state log, not a mutable row: one row per state transition
(`proposed` → `accepted` | `rejected`), latest `created_at` per `proposal_id`
wins on read. Same shape as loop_events, and it avoids BigQuery's streaming
buffer, which blocks UPDATE on freshly-inserted rows.

`proposal_id` is deterministic — sha1(task_id, field_key) — so a re-fired
ticket webhook re-proposes the same id instead of duplicating the card, and
the accept/reject endpoints can address a proposal without a lookup table.

Idempotent: CREATE TABLE IF NOT EXISTS.
"""

TARGETS = ["bigquery"]

DDL = """
CREATE TABLE IF NOT EXISTS `{project}.{dataset}.ticket_profile_proposals` (
  proposal_id             STRING NOT NULL,
  task_id                 STRING,
  company_id              STRING,
  property_uuid           STRING,
  ticket_type             STRING,
  field_key               STRING,
  field_label             STRING,
  proposed_value          STRING,
  current_value           STRING,
  current_source          STRING,
  conflicts_with_override BOOL,
  extractor               STRING,
  status                  STRING,
  actor                   STRING,
  note                    STRING,
  created_at              TIMESTAMP
)
"""


def up(ctx):
    ctx.run_bq(ctx.render(DDL))
    ctx.log("Created ticket_profile_proposals table")


def down(ctx):
    ctx.run_bq(
        f"DROP TABLE IF EXISTS `{ctx.project}.{ctx.dataset}.ticket_profile_proposals`"
    )
    ctx.log("Dropped ticket_profile_proposals table")
