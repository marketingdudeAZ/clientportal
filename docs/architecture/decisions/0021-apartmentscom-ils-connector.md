# ADR 0021 — Apartments.com ILS Performance connector + portal page

**Status:** Proposed
**Date:** 2026-07-23
**Authors:** Kyle Shipp, Claude

## Context

Apartments.com (CoStar) issued RPM a Performance Summary API key. The API
exposes daily **listing performance** (impressions, media/video/tour/map
views) and **lead data** (phone, email, website, request-to-tour,
request-to-apply, unit-application) per authorized listing. We want this in
the portal on a daily cadence.

Key facts about the API (from the vendor docs, 2026-07-14):

- **One endpoint, account-scoped:** `POST /routes/mkt/vendor/analytics/
  daily-property-summary`, auth via `X-PMC-API-KEY`. A single call returns
  **every listing authorized for the key** in one `items[]` array — no
  per-property fan-out.
- **Daily granularity.** No `date` → yesterday. Optional `date`
  (YYYY-MM-DD) must be a past date, **no older than 3 months**.
- **Rate limit: 5 requests/hour per requested summary date.** Trivial for a
  daily cron; a 90-day backfill hits 90 *distinct* dates, so the per-date
  limit is never approached.
- Per-listing rows carry CoStar `PropertyId` + `ListingId`, descriptive
  fields (name/address/city/state/package), and the metric set above.

This is a Layer 1 data source. Per the platform rule, applications never
call it directly — a connector feeds a Layer 2 skill that lands data in
BigQuery, and the portal reads the warehouse.

## Decision

### Layering

```
apartments.com API
   │  (X-PMC-API-KEY)
   ▼
apartmentscom_client.py        L1 connector — fetch + normalize, no side effects
   ▼
apartmentscom_ingestion.py     L2 skill — land in BQ + emit Loop event
   ▼
BigQuery: apartmentscom_ils_daily  ──►  apartmentscom_ils_resolved_v1 (view)
   ▼
routes/ils.py  (/api/ils/*)    app API — uuid-scoped reads
   ▼
client-portal-ils.html         dedicated ILS Performance portal page
```

### Warehouse (migration 0013)

- `apartmentscom_ils_daily` — raw per-listing daily rows, partitioned by
  `record_date`, clustered by `costar_property_id`. **Append-only**; we
  dedupe in the view (latest ingest per date × listing), mirroring
  `aptiq_snapshots` / `aptiq_snapshots_latest` (ADR 0011 pattern) to avoid
  the BQ streaming-buffer delete restriction.
- `apartmentscom_listing_map` — CoStar-id → `uuid` crosswalk, populated
  from HubSpot. Kept separate so identity mapping evolves independently of
  metrics history.
- `apartmentscom_ils_resolved_v1` — canonical read surface. **LEFT** joins
  the map so **unmapped listings are never dropped** (they carry
  `property_uuid = NULL`, `is_mapped = false`), per the "land all raw"
  decision. They surface once their CoStar id is mapped onto HubSpot.

### Identity (R1)

Two HubSpot company custom properties hold the CoStar ids:
`apartmentscom_property_id`, `apartmentscom_listing_id`
(created by `scripts/create_apartmentscom_property.py`). **Code never writes
`uuid`** — it only reads uuid off the company and pairs it with these ids in
the map table. Populating the CoStar ids onto companies is a human-in-the-loop
pass, aided by `scripts/suggest_apartmentscom_mapping.py`: it fuzzy-matches the
API's authorized-listing roster (`PropertyName`/`Address`/`City`/`State`) to
HubSpot companies — reusing the AptIQ backfill's name/address normalization —
and emits a review CSV (dry-run default). `--commit` writes
`apartmentscom_property_id` only for the high-confidence exact-name/address
tiers after a typed confirmation; fuzzy matches are always review-only.

### Cadence

- **Daily cron** (Render Cron Job → `apartmentscom_refresh_cron.py` →
  `POST /api/internal/apartmentscom-sync`) pulls yesterday. The ingestion is
  quick and synchronous (one API call + one BQ insert), so — unlike the
  Fluency refresh — the endpoint returns the run summary directly rather than
  using daemon threads. Suggested schedule: `0 13 * * *` UTC (~8 AM Central).
- **One-time 90-day backfill** (`scripts/backfill_apartmentscom.py`) walks
  the max window newest→oldest, respecting the per-date rate limit and
  emitting a single Loop `backfill_completed` event.

### Loop Event Bus (ADR 0010)

Each ingestion emits **one** `ops`/`job` Loop event summarizing the run
(listings, impressions, leads). We deliberately do **not** fan out per-listing
events (700+/day would flood the timeline); per-property metrics live in BQ
and are read by the ILS page directly. Per-property Loop rollups (Attract =
impressions, Engage = leads) are a possible follow-up.

### Portal surface

A **dedicated ILS Performance page** (`client-portal-ils.html` +
`js/ils.js`), separate from the Loop subpage (ADR 0018) per Kyle's
preference — same non-destructive, standalone-HubSpot-template pattern.
Reads `/api/ils/summary` and `/api/ils/trend`, both uuid-scoped. Shows
Attract KPIs, Engage KPIs (both with period-over-period delta), and a daily
impressions + leads trend.

## Consequences

- **Portfolio-wide from day one** without per-property calls — the single
  account-scoped endpoint returns all authorized listings.
- Metrics land even before identity is wired; the resolved view simply shows
  them as unmapped until the CoStar ids are backfilled onto HubSpot.
- New operational dependencies: `APARTMENTSCOM_API_KEY` on the web service +
  cron; a Render Cron Job for the daily refresh; a one-time mapping pass.
- 3-month history ceiling: anything older than 90 days is unavailable from
  the API, so the initial backfill is the only source of pre-launch history.

## Alternatives considered

- **Fold into the Loop subpage** — rejected; ILS performance is a distinct
  concern and Kyle wanted its own page.
- **Only ingest listings that resolve to a uuid** — rejected; silently loses
  data for not-yet-mapped properties. We land all raw and resolve via join.
- **Per-listing Loop events** — rejected for volume; run-level summary event
  instead.
