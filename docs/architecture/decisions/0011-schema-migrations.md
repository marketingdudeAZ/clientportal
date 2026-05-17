# ADR 0011 — Schema migration pattern

**Status:** Accepted
**Date:** 2026-05-16

## Context

`scripts/` contains 6 one-off table creation scripts (`create_hubdb_*.py`,
`create_portal_properties.py`, etc.). Each was run once, succeeded, and
now exists as institutional dust — no record of whether it's been applied,
no way to roll back, no way to know if the current production schema
matches what's checked in.

When AptIQ silently renamed a column in the daily CSV, we'd find out only
when reports started rendering empty. Schema drift surfaces as silent data
quality issues that users notice before we do.

We need: **migrations as code, with a tracking table that records what
has been applied.**

## Decision

Adopt a lightweight homegrown migration runner. NOT alembic (too heavy for
BQ + HubDB + HubSpot CRM mixed schemas). NOT a separate ORM. Just numbered
Python files with `up()` and (optional) `down()` functions plus a tracking
table.

### File layout

```
migrations/
  0001_loop_events.py
  0002_jobs_collapsed_into_loop_events.py
  0003_hyly_daily_activity.py
  0004_hyly_contact_submits.py
  0005_hyly_website_visits.py
  0006_forecast_runs.py
  0007_schema_migrations_tracking.py    # bootstrap itself
  _runner.py                            # the executor
  _common.py                            # shared helpers (BQ client, HubDB client)
```

Each migration is a self-contained Python file:

```python
# migrations/0001_loop_events.py
"""Create loop_events table in BigQuery.

Idempotent: re-running checks existence first.
"""

DDL = """
CREATE TABLE IF NOT EXISTS `{project}.{dataset}.loop_events` (
  event_id STRING NOT NULL,
  ...
)
PARTITION BY DATE(occurred_at)
CLUSTER BY property_uuid, stage
"""

def up(ctx):
    """Apply this migration. ctx provides bq_client + dataset + project."""
    sql = DDL.format(project=ctx.project, dataset=ctx.dataset)
    ctx.bq_client.query(sql).result()
    ctx.log("Created loop_events table")


def down(ctx):
    """Reverse this migration. Optional — many migrations are forward-only."""
    sql = f"DROP TABLE IF EXISTS `{ctx.project}.{ctx.dataset}.loop_events`"
    ctx.bq_client.query(sql).result()
```

### Tracking table

```sql
CREATE TABLE IF NOT EXISTS `{project}.{dataset}.schema_migrations` (
  version   STRING NOT NULL,            -- "0001", "0002", ...
  filename  STRING NOT NULL,            -- "0001_loop_events.py"
  applied_at TIMESTAMP NOT NULL,
  applied_by STRING,                    -- env name or user
  runtime_ms INT64,
  checksum   STRING                     -- SHA1 of the migration file content
)
```

Migrations are applied in numeric order. The runner queries
`schema_migrations` for the highest applied version and applies everything
after it.

### Runner CLI

```
python3 migrations/_runner.py status        # what's applied vs pending
python3 migrations/_runner.py up             # apply all pending
python3 migrations/_runner.py up 0005        # apply up through 0005
python3 migrations/_runner.py down 0003      # rollback to (after) 0003
python3 migrations/_runner.py verify         # checksum check — has any
                                             # applied migration's content
                                             # changed since it ran?
```

### Cross-system migrations

Migrations can target BigQuery, HubDB, or HubSpot CRM custom properties.
The migration file declares which:

```python
TARGETS = ["bigquery"]                     # default
# or
TARGETS = ["bigquery", "hubdb"]            # multi-target
TARGETS = ["hubspot_crm"]                  # custom property changes
```

The runner's `ctx` provides a client for each declared target. For
hubspot_crm migrations: a wrapper that respects R1 (never modify uuid) +
detects existing properties before create.

### Bootstrap problem

`schema_migrations` itself has to be created somehow. Solution:
`_runner.py` checks for the table's existence on every run and creates
it inline before processing anything else. This is the only piece of
schema NOT in a migration file.

### Idempotency rules

- Every migration must be idempotent (CREATE TABLE IF NOT EXISTS, etc.)
- If re-applied, must produce no error and no duplicate state
- Tracking table is the source of truth for "what's been applied"
- Checksum on the file content surfaces accidental edits to already-applied
  migrations (alerts; doesn't auto-revert)

### Migrating from existing `scripts/create_*` files

We will NOT immediately convert all 6 existing scripts to migrations.
Instead:
1. New tables/schema changes always go through migrations.
2. As we touch each `create_*` script for any reason, we convert it.
3. ADR 0019 (future) will track when all 6 are migrated, at which point
   we can delete them.

### Versioning

Migration version numbers are 4-digit zero-padded (`0001`). Never reused,
never re-ordered. If a migration is wrong after being applied to prod,
write a new forward-only migration to correct it.

## Consequences

**Trade-offs accepted:**
- A bit of code (a few hundred LOC for `_runner.py` + helpers)
- Discipline: every schema change must go through this. No more
  `scripts/create_foo.py` one-off cowboy.
- Migrations apply serially on a single environment at a time. A run
  against prod takes ~30 seconds for typical BQ DDLs.

**What we gain:**
- Provable state: "is migration X applied?" is a SQL query
- Reversible (when down() is defined)
- Schema-as-code, reviewable in PRs
- Cross-system support: BQ, HubDB, HubSpot CRM all flow through one
  conceptual surface
- A pattern that scales as the data layer grows

## References

- ADR 0010 — Loop Event Bus (the first user of this pattern)
- `migrations/_runner.py` (implementation)
- `migrations/_common.py` (shared helpers)
