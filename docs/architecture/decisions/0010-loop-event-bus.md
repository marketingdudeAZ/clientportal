# ADR 0010 — Loop Event Bus

**Status:** Accepted
**Date:** 2026-05-16
**Authors:** Kyle Shipp, Claude

## Context

ADR 0009 describes the 4-stage Multifamily Marketing Loop. Every stage
emits signals; the Optimize stage consumes those signals to forecast and
recommend. We need a single canonical event store so:

- Every stage writes through one path (no scattered writers)
- The portal can render a unified timeline
- The forecasting engine has one place to query
- Observability comes for free (every async job is just a Loop event)
- The audit trail is queryable from a single table

## Decision

A single BigQuery table `loop_events` with a Python writer skill
`webhook-server/loop_writer.py` is the canonical event store and writer.

### Schema

```sql
CREATE TABLE IF NOT EXISTS `{project}.{dataset}.loop_events` (
  event_id        STRING NOT NULL,          -- UUID v4
  property_uuid   STRING,                   -- HubSpot company UUID (R1 join key)
  company_id      STRING,                   -- HubSpot company id (denormalized for ease)
  stage           STRING NOT NULL,          -- 'attract'|'engage'|'convert'|'optimize'|'ops'
  event_type      STRING NOT NULL,          -- e.g. 'marquee_generated', 'lease_signed', 'forecast_run'
  occurred_at     TIMESTAMP NOT NULL,       -- when the event happened (not when written)
  recorded_at     TIMESTAMP NOT NULL,       -- when we wrote the row
  source          STRING,                   -- 'hubspot'|'aptiq'|'hyly'|'dataforseo'|'fluency'|'claude'|'ops'|'client'
  source_id       STRING,                   -- external id from source system if any
  trigger         STRING,                   -- 'cron'|'manual'|'webhook'|'api'|'client_action'
  magnitude       FLOAT64,                  -- optional numeric for ranking/aggregation
  payload         STRING,                   -- JSON string, capped at 60kb
  status          STRING,                   -- 'completed'|'failed'|'running'|'pending' (for job-style events)
  runtime_ms      INT64,                    -- duration for job-style events
  error_message   STRING,                   -- populated when status='failed'
  parent_event_id STRING                    -- chain related events (e.g., recommendation→approval→execution)
)
PARTITION BY DATE(occurred_at)
CLUSTER BY property_uuid, stage;
```

**Why this shape:**
- `property_uuid` first cluster key — every Loop query is property-scoped
- `stage` second cluster key — most queries filter by stage
- `DATE(occurred_at)` partition — cheap to query "last 30 days for property X"
- `payload` as JSON STRING (not native JSON type) — broad BQ-region compatibility
- `parent_event_id` — enables threading (a recommendation → its approval → its execution)
- `status` + `runtime_ms` + `error_message` collapse the separate "jobs" table
  into one event type (`stage='ops'`)

### Stage taxonomy (versioned)

```python
LOOP_STAGES = {
  "attract",   # Paid media + organic acquisition
  "engage",    # Property page + content + AEO + reviews
  "convert",   # Lead → tour → application → lease (Hyly + AptIQ)
  "optimize",  # AI engine — forecasts, recommendations, reconfigs
  "ops",       # Internal: cron runs, backfills, refreshes, errors
}
```

Adding a stage requires bumping a SCHEMA_VERSION constant and updating
the portal renderer. The 5th stage (Delight+Advocate combined, owned by
another team) is reserved as `"delight_advocate"` for future use.

### Event type naming convention

`{noun}_{past_verb}` — examples:
- `marquee_generated`, `ad_variant_published`, `keyword_rank_changed` (attract)
- `property_brief_published`, `aeo_content_generated`, `review_received` (engage)
- `lead_submitted`, `tour_scheduled`, `application_received`, `lease_signed` (convert)
- `forecast_run`, `recommendation_proposed`, `recommendation_approved`, `tier_changed` (optimize)
- `cron_started`, `cron_completed`, `backfill_completed` (ops)

A registry lives in `webhook-server/loop_writer.py` (constants module).

### The writer skill: `loop_writer.py`

One function (the only way to write events):

```python
def record(
    stage: str,
    event_type: str,
    *,
    property_uuid: str | None = None,
    company_id: str | None = None,
    source: str | None = None,
    source_id: str | None = None,
    trigger: str = "api",
    occurred_at: datetime | None = None,
    magnitude: float | None = None,
    payload: dict | None = None,
    status: str | None = None,
    runtime_ms: int | None = None,
    error_message: str | None = None,
    parent_event_id: str | None = None,
) -> str:
    """Write one Loop event row. Returns the event_id. Idempotent
    (re-running with same event_id is a no-op).
    Validation: stage must be in LOOP_STAGES; event_type must follow
    naming convention; payload serialized + truncated to 60kb.
    """
```

Plus a context manager for job-style events:

```python
with track_job(
    stage="ops",
    event_type="aptiq_history_backfill",
    property_uuid=uuid,
    payload={"months_back": 13},
) as event:
    # do the work
    event.set_result({"rows_written": 13})
# Auto-logs start, end, runtime_ms, status=completed; or failed with
# error_message on exception.
```

### Reader path: `routes/loop.py`

Single Flask blueprint exposing:

- `GET /api/loop/status?uuid=X` — current Loop stage health for one property
- `GET /api/loop/events?uuid=X&stage=*&limit=50&since=...` — paged timeline
- `GET /api/loop/forecast?uuid=X` — latest forecast row (created by Optimize stage)
- `GET /api/loop/recommendations?uuid=X&status=pending` — open recs
- `POST /api/loop/approve` — approve a recommendation → fires downstream action

The status endpoint aggregates events into the 4-stage health summary
the portal displays. Cached per-uuid with 60s TTL.

### Idempotency

- `event_id` is required and unique. If the same `event_id` is written
  twice, the second write is a no-op (we INSERT INTO with a check).
- For job-style events with auto-generated IDs, the context manager
  guarantees one ID per `with` block.
- For external webhook ingestion (HubSpot, etc.), the caller derives a
  deterministic event_id from the source's id + event_type so retries
  don't duplicate.

### Performance

- Streaming inserts (acceptable: events are tiny, BQ handles 10k+ writes/sec)
- 60s read cache on `/api/loop/status` per uuid
- Portal query: "events for uuid X in last 30 days" hits exactly one
  partition + one cluster — sub-100ms even at 10M rows total

### Observability collapses into Loop events

There is no separate `jobs` table. Every async job (cron, backfill,
report run) writes events with `stage='ops'` + `status` field. Querying
"what jobs failed in the last hour" is:

```sql
SELECT * FROM loop_events
WHERE stage='ops' AND status='failed'
  AND occurred_at > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 HOUR)
ORDER BY occurred_at DESC
```

The Muse SEO refresh hang (May 15) would have been visible in 5 minutes
with this in place.

## Consequences

**Trade-offs accepted:**
- All writes through one path = a slow `loop_writer.record()` slows
  everything. Mitigated by: pure BQ streaming insert, no joins, no
  heavy work on the write path.
- Schema migration becomes a bigger deal — `loop_events` table is the
  central nervous system. ADR 0011 covers the migration runner pattern.
- `payload` is a JSON STRING, not native JSON type. Queries that need to
  filter by payload field need JSON_EXTRACT. Acceptable; not a hot path.

**What we gain:**
- One table to query for the full property story
- One writer to maintain, one reader path
- Observability for free (jobs are just events)
- Forecasting engine has one input source
- Portal timeline is one query

## References

- ADR 0009 — Multifamily Loop architecture
- ADR 0011 — Schema migration pattern
- `webhook-server/loop_writer.py` (implementation)
- `webhook-server/routes/loop.py` (reader API)
