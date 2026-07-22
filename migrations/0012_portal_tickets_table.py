"""Create the portal_tickets mapping table.

One append-only row per ticket the client portal creates in ClickUp, so
"what's open for this property" is an exact task_id ↔ company_id lookup rather
than fuzzy matching (docs/ticket-page-scope.md §4). Written by
webhook-server/portal_tickets.py on each successful create.

Idempotent: CREATE TABLE IF NOT EXISTS.
"""

TARGETS = ["bigquery"]

DDL = """
CREATE TABLE IF NOT EXISTS `{project}.{dataset}.portal_tickets` (
  task_id        STRING NOT NULL,
  company_id     STRING,
  property_uuid  STRING,
  ticket_type    STRING,
  submitted_by   STRING,
  created_at     TIMESTAMP
)
"""


def up(ctx):
    ctx.run_bq(ctx.render(DDL))
    ctx.log("Created portal_tickets mapping table")


def down(ctx):
    ctx.run_bq(f"DROP TABLE IF EXISTS `{ctx.project}.{ctx.dataset}.portal_tickets`")
    ctx.log("Dropped portal_tickets mapping table")
