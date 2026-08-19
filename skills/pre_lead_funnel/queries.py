"""SQL for the pre-lead funnel report.

Everything here reads Hyly's GA4 export copy at session grain. The shape is the
standard GA4 BigQuery export — one row per event, `event_params` a repeated
key/value struct — enriched by Hyly with `property_id` and `default_channel`.

Two conventions hold throughout and are load-bearing:

1. A *session* is `user_pseudo_id` + the `ga_session_id` event_param. That is
   GA4's own session key. Counting distinct `user_pseudo_id` instead would
   count users and quietly inflate every conversion rate in the report.

2. A session is attributed to the month its events fall in, and a lead to the
   month the lead event fires. No cross-month attribution, no lookback window.
   For a "did traffic convert" question these must be the same window or the
   ratio means nothing.

`page_patterns` is a list of LIKE patterns for the property's floorplan,
pricing and availability URLs. It has no default: the pattern set is a property
of the specific website and guessing it would produce a confidently wrong
answer to the most important question in this report.
"""

from __future__ import annotations

# GA4's session key, as GA4 itself defines it.
_SESSION_KEY = (
    "CONCAT(user_pseudo_id, '-', CAST((SELECT value.int_value FROM "
    "UNNEST(event_params) WHERE key = 'ga_session_id') AS STRING))"
)

_PAGE_LOCATION = (
    "(SELECT value.string_value FROM UNNEST(event_params) WHERE key = 'page_location')"
)


def _pattern_clause(column: str, n_patterns: int) -> str:
    """`REGEXP_CONTAINS`-free OR-of-LIKEs, so patterns stay reviewable by a
    marketer rather than requiring a regex to be read."""
    if n_patterns == 0:
        return "FALSE"
    return " OR ".join(f"LOWER({column}) LIKE @p{i}" for i in range(n_patterns))


def sessions_by_month(ga4_table: str) -> str:
    """Widget 1 — session volume by month. Prior-year rows come back in the
    same result set so the caller can align them without a second scan."""
    return f"""
    SELECT
      DATE_TRUNC(PARSE_DATE('%Y%m%d', event_date), MONTH) AS month,
      COUNT(DISTINCT {_SESSION_KEY})                      AS sessions,
      COUNT(DISTINCT user_pseudo_id)                      AS users
    FROM `{ga4_table}`
    WHERE CAST(property_id AS STRING) = @pid
      AND PARSE_DATE('%Y%m%d', event_date) BETWEEN @start AND @end
    GROUP BY month
    ORDER BY month
    """


def sessions_and_leads_by_month(ga4_table: str, lead_events: tuple[str, ...]) -> str:
    """Widget 2 — sessions, leads and the ratio, both counted in GA4.

    Leads are counted as *sessions containing a lead event*, not as lead events.
    A prospect who submits twice in one visit is one converted session; counting
    events would make the site look like it converts better than it does.
    """
    events = ", ".join(f"'{e}'" for e in lead_events)
    return f"""
    WITH sessions AS (
      SELECT
        DATE_TRUNC(PARSE_DATE('%Y%m%d', event_date), MONTH) AS month,
        {_SESSION_KEY}                                      AS session_id,
        MAX(CASE WHEN event_name IN ({events}) THEN 1 ELSE 0 END) AS converted
      FROM `{ga4_table}`
      WHERE CAST(property_id AS STRING) = @pid
        AND PARSE_DATE('%Y%m%d', event_date) BETWEEN @start AND @end
      GROUP BY month, session_id
    )
    SELECT month,
           COUNT(*)       AS sessions,
           SUM(converted) AS lead_sessions
    FROM sessions
    GROUP BY month
    ORDER BY month
    """


def sessions_and_leads_by_channel(ga4_table: str, lead_events: tuple[str, ...]) -> str:
    """Widget 4 — the same ratio split by GA4's *default* channel group.

    `default_channel` is Hyly's carry-through of GA4's default grouping. No
    custom grouping is applied here, per the request. Unassigned and (not set)
    are deliberately NOT collapsed into Other — they are the finding.
    """
    events = ", ".join(f"'{e}'" for e in lead_events)
    return f"""
    WITH sessions AS (
      SELECT
        DATE_TRUNC(PARSE_DATE('%Y%m%d', event_date), MONTH) AS month,
        {_SESSION_KEY}                                      AS session_id,
        -- One channel per session: GA4 stamps every event in a session with
        -- the same default_channel, but ANY_VALUE guards a mixed session
        -- rather than fanning it across two channel rows and double-counting.
        ANY_VALUE(IFNULL(default_channel, '(not set)'))     AS channel,
        MAX(CASE WHEN event_name IN ({events}) THEN 1 ELSE 0 END) AS converted
      FROM `{ga4_table}`
      WHERE CAST(property_id AS STRING) = @pid
        AND PARSE_DATE('%Y%m%d', event_date) BETWEEN @start AND @end
      GROUP BY month, session_id
    )
    SELECT month, channel,
           COUNT(*)       AS sessions,
           SUM(converted) AS lead_sessions
    FROM sessions
    GROUP BY month, channel
    ORDER BY month, sessions DESC
    """


def floorplan_engagement_by_month(ga4_table: str, page_patterns: list[str]) -> str:
    """Widget 5 — floorplan / availability page engagement.

    Returns pageviews and, separately, the number of distinct sessions that
    reached such a page. The share-of-sessions figure the request asks for is
    built from the session count, not the pageview count: 400 pageviews across
    40 sessions is a very different story from 400 across 400, and dividing
    pageviews by sessions can exceed 100% and read as nonsense.
    """
    clause = _pattern_clause("page_location", len(page_patterns))
    return f"""
    WITH pv AS (
      SELECT
        DATE_TRUNC(PARSE_DATE('%Y%m%d', event_date), MONTH) AS month,
        {_SESSION_KEY}                                      AS session_id,
        {_PAGE_LOCATION}                                    AS page_location
      FROM `{ga4_table}`
      WHERE CAST(property_id AS STRING) = @pid
        AND event_name = 'page_view'
        AND PARSE_DATE('%Y%m%d', event_date) BETWEEN @start AND @end
    ),
    total AS (
      SELECT month, COUNT(DISTINCT session_id) AS sessions FROM pv GROUP BY month
    ),
    matched AS (
      SELECT month,
             COUNT(*)                   AS floorplan_pageviews,
             COUNT(DISTINCT session_id) AS floorplan_sessions
      FROM pv
      WHERE {clause}
      GROUP BY month
    )
    SELECT t.month, t.sessions,
           IFNULL(m.floorplan_pageviews, 0) AS floorplan_pageviews,
           IFNULL(m.floorplan_sessions, 0)  AS floorplan_sessions
    FROM total t LEFT JOIN matched m USING (month)
    ORDER BY t.month
    """


def price_exposure_segmentation(
    ga4_table: str, lead_events: tuple[str, ...], page_patterns: list[str]
) -> str:
    """The question the email is actually asking, expressed as one query.

    Splits every session into two segments — saw a floorplan/pricing page, or
    did not — and reports the lead rate of each, plus how many of the month's
    leads came from each. Three readings, three different conversations:

      * `saw_price` converts far *below* `no_price` -> price is visibly
        filtering people out at the moment of disclosure. Pricing is capping
        the funnel before the lead stage, which is the SVP's hypothesis.
      * `saw_price` converts at or above `no_price`, and the volume reaching
        price is small -> people are not getting to pricing at all. Traffic and
        on-site pathing, not price, is the constraint.
      * Most leads never saw a price -> the lead pool is systematically less
        price-qualified than Operations assumes, and lead volume is not
        comparable to a competitor whose site shows rent up front.

    This runs entirely inside GA4. It needs no Hyly join and no CRM join, which
    is what makes it the one part of this analysis that can ship first.
    """
    events = ", ".join(f"'{e}'" for e in lead_events)
    clause = _pattern_clause("page_location", len(page_patterns))
    return f"""
    WITH ev AS (
      SELECT
        DATE_TRUNC(PARSE_DATE('%Y%m%d', event_date), MONTH) AS month,
        {_SESSION_KEY}                                      AS session_id,
        event_name,
        {_PAGE_LOCATION}                                    AS page_location
      FROM `{ga4_table}`
      WHERE CAST(property_id AS STRING) = @pid
        AND PARSE_DATE('%Y%m%d', event_date) BETWEEN @start AND @end
    ),
    sess AS (
      SELECT
        month,
        session_id,
        MAX(CASE WHEN event_name = 'page_view' AND ({clause}) THEN 1 ELSE 0 END) AS saw_price,
        MAX(CASE WHEN event_name IN ({events}) THEN 1 ELSE 0 END)                AS converted
      FROM ev
      GROUP BY month, session_id
    )
    SELECT
      month,
      IF(saw_price = 1, 'saw_floorplan_or_pricing', 'never_saw_pricing') AS segment,
      COUNT(*)       AS sessions,
      SUM(converted) AS lead_sessions
    FROM sess
    GROUP BY month, segment
    ORDER BY month, segment
    """


def leads_by_month_hyly(rollup_table: str) -> str:
    """Lead counts as the CRM/Hyly side defines them.

    Reported alongside the GA4 lead count rather than instead of it. When the
    two disagree materially that gap is itself a finding — it means the site is
    not firing a lead event on every path that produces a CRM lead (call
    tracking, chat, ILS handoff), and any GA4-only conversion rate is an
    undercount.
    """
    return f"""
    SELECT DATE_TRUNC(activity_date, MONTH) AS month,
           SUM(leads) AS leads
    FROM `{rollup_table}`
    WHERE hyly_property_id = @pid
      AND activity_date BETWEEN @start AND @end
    GROUP BY month
    ORDER BY month
    """
