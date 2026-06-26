"""Google Ads impression-share-lost-to-budget connector (Loop 1 #3b).

Supplies the recommendation engine's magnitude input: how much search impression
share a property is losing to budget. NinjaCat used to carry this; it's
deprecating, so this pulls it straight from the Google Ads API.

Join key: the company's `google_ads_customer_id` property (format
`{property_cid}|{mcc_cid}`, populated by scripts/backfill_google_ads_cid.py).

STRUCTURE is built and tested here (CID parsing, the GAQL, the parse/map). The
one live piece — `_run_gaql` — is a credential-gated SEAM: it needs the
`google-ads` library (not yet in requirements.txt) and API credentials
(GOOGLE_ADS_DEVELOPER_TOKEN + OAuth client/refresh + login-customer-id). Until
those land it raises GoogleAdsNotConfigured; everything else is unit-tested with
a mocked response.

Channel vocabulary: Google Ads `search_budget_lost_impression_share` is a Search
metric, so it maps to the recommendation engine's `paid_search` channel.
"""

from __future__ import annotations

import logging

import hubspot_client

logger = logging.getLogger(__name__)

GOOGLE_ADS_CID_PROPERTY = "google_ads_customer_id"

# Budget-lost impression share is a 30-day Search-campaign metric (0.0–1.0).
_GAQL_BUDGET_LOST_IS = (
    "SELECT campaign.advertising_channel_type, "
    "metrics.search_budget_lost_impression_share "
    "FROM campaign WHERE segments.date DURING LAST_30_DAYS"
)


class GoogleAdsNotConfigured(RuntimeError):
    """The google-ads library or API credentials are not available yet."""


def extract_property_cid(piped_value: str) -> str:
    """`'486-980-3719|123-456'` → `'4869803719'` (property CID, dashes stripped)."""
    if not piped_value:
        return ""
    head = piped_value.split("|", 1)[0]
    return head.replace("-", "").strip()


def parse_islost(rows: list[dict]) -> dict[str, float]:
    """Aggregate SEARCH-campaign budget-lost-IS → {'paid_search': avg}.

    `rows` are library-agnostic dicts: {'channel_type': 'SEARCH',
    'budget_lost_is': 0.28}. Non-search rows and nulls are ignored.
    """
    search = [
        r["budget_lost_is"] for r in rows
        if r.get("channel_type") == "SEARCH" and r.get("budget_lost_is") is not None
    ]
    if not search:
        return {}
    return {"paid_search": round(sum(search) / len(search), 4)}


def _run_gaql(customer_id: str, query: str) -> list[dict]:
    """SEAM (credential-gated): run a GAQL query, return library-agnostic rows.

    Real impl (added when credentials + the google-ads lib land):
      client = GoogleAdsClient.load_from_env()
      stream = client.get_service("GoogleAdsService").search_stream(
          customer_id=customer_id, query=query)
      return [{"channel_type": row.campaign.advertising_channel_type.name,
               "budget_lost_is": row.metrics.search_budget_lost_impression_share}
              for batch in stream for row in batch.results]
    """
    raise GoogleAdsNotConfigured(
        "Google Ads API not configured: add the `google-ads` library + "
        "GOOGLE_ADS_DEVELOPER_TOKEN / OAuth / login-customer-id, then implement _run_gaql."
    )


def fetch_islost_by_channel(company_id: str) -> dict[str, float]:
    """Impression-share-lost-to-budget per channel for one property.

    Returns {} when the company has no Google Ads CID (e.g. no paid search) —
    the recommendation engine then simply produces no card for that channel.
    """
    company = hubspot_client.get_company(company_id, [GOOGLE_ADS_CID_PROPERTY])
    cid = extract_property_cid(company.get(GOOGLE_ADS_CID_PROPERTY) or "")
    if not cid:
        logger.info("no %s for company %s — no IS-lost", GOOGLE_ADS_CID_PROPERTY, company_id)
        return {}
    rows = _run_gaql(cid, _GAQL_BUDGET_LOST_IS)
    return parse_islost(rows)
