"""Create apartments.com ILS performance tables + resolved view (ADR 0021).

Three objects:

  1. apartmentscom_ils_daily — raw per-listing daily summary rows from the
     Performance Summary API. One row per (record_date, costar_listing_id)
     per ingestion. Partitioned by record_date, clustered by
     costar_property_id. We append (never delete) and dedupe in the view,
     mirroring aptiq_snapshots + aptiq_snapshots_latest (0008) — this
     sidesteps the BQ streaming-buffer delete restriction.

  2. apartmentscom_listing_map — CoStar id → property uuid crosswalk.
     Populated from HubSpot company records (custom props
     apartmentscom_property_id / apartmentscom_listing_id). Kept OUT of the
     daily table so the identity mapping can be refreshed independently of
     the metrics history. NOTE (R1): this table stores uuid but code that
     writes it only ever RESOLVES uuid from HubSpot — it never mints one.

  3. apartmentscom_ils_resolved_v1 — the canonical read surface. Dedupes the
     daily table to the latest ingested row per (record_date,
     costar_listing_id) and LEFT JOINs the map so unmapped listings still
     appear (property_uuid = NULL) rather than being dropped.

Idempotent: CREATE TABLE IF NOT EXISTS + CREATE OR REPLACE VIEW.
"""

TARGETS = ["bigquery"]

DAILY_DDL = """
CREATE TABLE IF NOT EXISTS `{project}.{dataset}.apartmentscom_ils_daily` (
  record_date                 DATE   NOT NULL,   -- the day the metrics are for
  costar_property_id          STRING,            -- PropertyId (CoStar)
  costar_listing_id           STRING,            -- ListingId (CoStar)
  pmc                          STRING,            -- property mgmt company name
  property_name               STRING,
  address                      STRING,
  city                         STRING,
  state                        STRING,
  postal_code                  STRING,
  country                      STRING,
  ad_package                   STRING,            -- product/package level active
  -- impressions + views (Loop: Attract)
  search_result_impressions    INT64,
  details_page_impressions     INT64,
  total_impressions            INT64,
  total_media_views            INT64,
  hd_video_views               INT64,
  tour_3d_views                INT64,
  property_map_views           INT64,
  -- leads (Loop: Engage)
  total_leads                  INT64,
  phone_leads                  INT64,
  email_leads                  INT64,
  property_website_leads       INT64,
  request_to_tour_leads        INT64,
  request_to_apply_leads       INT64,
  unit_application_leads       INT64,
  ingested_at                  TIMESTAMP NOT NULL,
  raw_payload                  STRING             -- original item JSON
)
PARTITION BY record_date
CLUSTER BY costar_property_id
"""

MAP_DDL = """
CREATE TABLE IF NOT EXISTS `{project}.{dataset}.apartmentscom_listing_map` (
  costar_property_id   STRING,
  costar_listing_id    STRING,
  property_uuid        STRING,   -- resolved from HubSpot; never minted here (R1)
  hubspot_company_id   STRING,
  property_name        STRING,   -- convenience label from HubSpot
  updated_at           TIMESTAMP NOT NULL
)
CLUSTER BY costar_property_id
"""

# Resolved read surface. Dedup daily to latest ingest per
# (record_date, costar_listing_id); LEFT JOIN keeps unmapped listings.
# Map is deduped to newest crosswalk row per costar_listing_id.
RESOLVED_VIEW_DDL = """
CREATE OR REPLACE VIEW `{project}.{dataset}.apartmentscom_ils_resolved_v1` AS
WITH latest_daily AS (
  SELECT * EXCEPT(_rn) FROM (
    SELECT *, ROW_NUMBER() OVER (
      PARTITION BY record_date, costar_listing_id
      ORDER BY ingested_at DESC
    ) AS _rn
    FROM `{project}.{dataset}.apartmentscom_ils_daily`
  ) WHERE _rn = 1
),
latest_map AS (
  SELECT * EXCEPT(_rn) FROM (
    SELECT *, ROW_NUMBER() OVER (
      PARTITION BY costar_listing_id
      ORDER BY updated_at DESC
    ) AS _rn
    FROM `{project}.{dataset}.apartmentscom_listing_map`
  ) WHERE _rn = 1
)
SELECT
  d.record_date,
  m.property_uuid,
  m.hubspot_company_id,
  d.costar_property_id,
  d.costar_listing_id,
  d.pmc,
  d.property_name,
  d.address, d.city, d.state, d.postal_code, d.country,
  d.ad_package,
  d.search_result_impressions,
  d.details_page_impressions,
  d.total_impressions,
  d.total_media_views,
  d.hd_video_views,
  d.tour_3d_views,
  d.property_map_views,
  d.total_leads,
  d.phone_leads,
  d.email_leads,
  d.property_website_leads,
  d.request_to_tour_leads,
  d.request_to_apply_leads,
  d.unit_application_leads,
  d.ingested_at,
  (m.property_uuid IS NOT NULL) AS is_mapped
FROM latest_daily d
LEFT JOIN latest_map m
  ON d.costar_listing_id = m.costar_listing_id
"""


def up(ctx):
    ctx.run_bq(ctx.render(DAILY_DDL))
    ctx.log("Created apartmentscom_ils_daily table")
    ctx.run_bq(ctx.render(MAP_DDL))
    ctx.log("Created apartmentscom_listing_map table")
    ctx.run_bq(ctx.render(RESOLVED_VIEW_DDL))
    ctx.log("Created apartmentscom_ils_resolved_v1 view")


def down(ctx):
    ctx.run_bq(
        f"DROP VIEW IF EXISTS `{ctx.project}.{ctx.dataset}.apartmentscom_ils_resolved_v1`"
    )
    ctx.run_bq(
        f"DROP TABLE IF EXISTS `{ctx.project}.{ctx.dataset}.apartmentscom_listing_map`"
    )
    ctx.run_bq(
        f"DROP TABLE IF EXISTS `{ctx.project}.{ctx.dataset}.apartmentscom_ils_daily`"
    )
    ctx.log("Dropped apartmentscom ILS tables + view")
