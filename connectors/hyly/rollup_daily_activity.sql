-- hyly_daily_activity_v1 — RPM-owned rollup of Hyly's Convert funnel.
--
-- Source grain : one row per (contact, event) with contact-level milestone
--                timestamps denormalised onto every row.
-- Output grain : property x activity_date x channel.
--
-- Two modelling decisions are encoded here and documented in ADR 0022:
--   1. Attribution is FIRST-TOUCH — the earliest non-null source_mapped for a
--      contact. 23.4% of contacts carry more than one source_mapped, so this
--      is a real choice, not a formality. Switch the ORDER BY in `attribution`
--      to DESC for last-touch.
--   2. Milestones are counted on their OWN date (a lease closed in August is an
--      August lease even if the lead arrived in May), which is what makes
--      month-over-month funnel rates meaningful.
--
-- Params rendered by the migration: {src}, {dest}, {date_floor}

CREATE OR REPLACE TABLE `{dest}`
PARTITION BY activity_date
CLUSTER BY hyly_property_id
AS
WITH src AS (
  SELECT *
  FROM `{src}`
  WHERE property_id IS NOT NULL
    AND contact_id  IS NOT NULL
),

-- First-touch channel per contact. Hyly emits 314 distinct source_mapped
-- values with heavy near-duplication, so we normalise by pattern rather than
-- by enumerating values — new vendor values land in a sensible bucket instead
-- of silently becoming a new channel in the client's chart.
attribution AS (
  SELECT
    contact_id,
    property_id,
    ARRAY_AGG(source_mapped IGNORE NULLS ORDER BY event_datetime ASC)[SAFE_OFFSET(0)] AS source_raw
  FROM src
  GROUP BY contact_id, property_id
),

milestones AS (
  SELECT
    contact_id,
    property_id,
    ANY_VALUE(property_name)          AS property_name,
    MIN(h_cc_event_datetime)          AS m_lead,
    MIN(h_fts_event_datetime)         AS m_tour_scheduled,
    MIN(h_ft_event_datetime)          AS m_toured,
    MIN(h_app_event_datetime)         AS m_applied,
    MIN(h_lease_event_datetime)       AS m_leased
  FROM src
  GROUP BY contact_id, property_id
),

joined AS (
  SELECT m.*, a.source_raw
  FROM milestones m
  LEFT JOIN attribution a USING (contact_id, property_id)
),

-- One row per (contact, milestone reached, date it was reached).
events AS (
  SELECT contact_id, property_id, property_name, source_raw, 'lead'           AS milestone, DATE(m_lead)           AS activity_date FROM joined WHERE m_lead           IS NOT NULL
  UNION ALL
  SELECT contact_id, property_id, property_name, source_raw, 'tour_scheduled', DATE(m_tour_scheduled) FROM joined WHERE m_tour_scheduled IS NOT NULL
  UNION ALL
  SELECT contact_id, property_id, property_name, source_raw, 'toured',         DATE(m_toured)         FROM joined WHERE m_toured         IS NOT NULL
  UNION ALL
  SELECT contact_id, property_id, property_name, source_raw, 'applied',        DATE(m_applied)        FROM joined WHERE m_applied        IS NOT NULL
  UNION ALL
  SELECT contact_id, property_id, property_name, source_raw, 'leased',         DATE(m_leased)         FROM joined WHERE m_leased         IS NOT NULL
),

normalised AS (
  SELECT
    activity_date,
    CAST(property_id AS STRING) AS hyly_property_id,
    property_name,
    contact_id,
    milestone,
    source_raw,
    CASE
      WHEN source_raw IS NULL THEN 'Unattributed'
      -- paid first: 'Google PayPerClick (PPC)' must not fall into organic Google
      WHEN REGEXP_CONTAINS(LOWER(source_raw), r'ppc|paypercl|paid ad|socialads|displayads|programmatic') THEN 'Paid Media'
      WHEN REGEXP_CONTAINS(LOWER(source_raw), r'property website|corporate site|rpm corporate')          THEN 'Owned Web'
      WHEN REGEXP_CONTAINS(LOWER(source_raw), r'apartmentlist')                                          THEN 'ILS - ApartmentList'
      WHEN REGEXP_CONTAINS(LOWER(source_raw), r'zillow')                                                 THEN 'ILS - Zillow'
      WHEN REGEXP_CONTAINS(LOWER(source_raw), r'costar|apartments\.com|apts')                            THEN 'ILS - Apartments.com/CoStar'
      WHEN REGEXP_CONTAINS(LOWER(source_raw), r'rentcafe')                                               THEN 'ILS - RentCafe'
      WHEN REGEXP_CONTAINS(LOWER(source_raw), r'apartmentguide|apartment guide|rentpath')                THEN 'ILS - ApartmentGuide'
      WHEN REGEXP_CONTAINS(LOWER(source_raw), r'craigslist')                                             THEN 'ILS - Craigslist'
      WHEN REGEXP_CONTAINS(LOWER(source_raw), r'google|gmb')                                             THEN 'Organic - Google'
      WHEN REGEXP_CONTAINS(LOWER(source_raw), r'bing|yelp|apple maps')                                   THEN 'Organic - Other'
      WHEN REGEXP_CONTAINS(LOWER(source_raw), r'locator|realtor|referral')                               THEN 'Referral'
      WHEN REGEXP_CONTAINS(LOWER(source_raw), r'walking|driving')                                        THEN 'Walk-in / Drive-by'
      WHEN REGEXP_CONTAINS(LOWER(source_raw), r'transfer')                                               THEN 'Transfer'
      ELSE 'Other'
    END AS channel
  FROM events
  -- Hyly carries sentinel dates as far back as 2002-10-26. Without this floor
  -- every portal chart renders a 24-year x-axis.
  WHERE activity_date >= DATE('{date_floor}')
    AND activity_date <= CURRENT_DATE()
)

SELECT
  activity_date,
  hyly_property_id,
  ANY_VALUE(property_name) AS property_name,
  channel,
  COUNT(DISTINCT IF(milestone = 'lead',           contact_id, NULL)) AS leads,
  COUNT(DISTINCT IF(milestone = 'tour_scheduled', contact_id, NULL)) AS tours_scheduled,
  COUNT(DISTINCT IF(milestone = 'toured',         contact_id, NULL)) AS tours_completed,
  COUNT(DISTINCT IF(milestone = 'applied',        contact_id, NULL)) AS applications,
  COUNT(DISTINCT IF(milestone = 'leased',         contact_id, NULL)) AS leases
FROM normalised
GROUP BY activity_date, hyly_property_id, channel
