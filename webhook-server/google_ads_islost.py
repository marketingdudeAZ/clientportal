"""Google Ads impression-share-lost-to-budget connector (Loop 1 #3b).

Supplies the recommendation engine's magnitude input: how much search impression
share a property is losing to budget. NinjaCat used to carry this; it's
deprecating, so this pulls it straight from the Google Ads API.

Join key: the company's `google_ads_customer_id` property. The docstring here
used to claim the format is `{property_cid}|{mcc_cid}`; measured against the
live records, NONE carry the pipe — all 79 RPMI values are bare 10-digit
customer ids. Both forms parse, and the manager account comes from
GOOGLE_ADS_LOGIN_CUSTOMER_ID rather than from the record.

`_run_gaql` is live when five environment values are present:

    GOOGLE_ADS_DEVELOPER_TOKEN      from the MCC's API Center (needs Basic
                                    access; Test access only reaches test
                                    accounts)
    GOOGLE_ADS_CLIENT_ID            OAuth client (Desktop app)
    GOOGLE_ADS_CLIENT_SECRET
    GOOGLE_ADS_REFRESH_TOKEN        minted by scripts/google_ads_auth.py
    GOOGLE_ADS_LOGIN_CUSTOMER_ID    the manager account, digits only

With any of them missing it raises GoogleAdsNotConfigured, which every caller
already turns into a named gap rather than a failure. The API does not accept
the portal service account: service-account auth needs domain-wide delegation
on a Google Workspace domain, and this business runs Microsoft 365.

Channel vocabulary: Google Ads `search_budget_lost_impression_share` is a Search
metric, so it maps to the recommendation engine's `paid_search` channel.
"""

from __future__ import annotations

import logging
import os

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


class GoogleAdsError(RuntimeError):
    """The API was reachable but refused or failed this request."""


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


_REQUIRED_ENV = (
    "GOOGLE_ADS_DEVELOPER_TOKEN",
    "GOOGLE_ADS_CLIENT_ID",
    "GOOGLE_ADS_CLIENT_SECRET",
    "GOOGLE_ADS_REFRESH_TOKEN",
    "GOOGLE_ADS_LOGIN_CUSTOMER_ID",
)


def missing_credentials() -> list:
    """Which of the five values are absent. Empty means the connector can run."""
    return [name for name in _REQUIRED_ENV if not (os.environ.get(name) or "").strip()]


def is_configured() -> bool:
    return not missing_credentials()


def _digits(value: str) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _client():
    """A Google Ads client built from the environment.

    Deliberately not cached: the library holds a refresh token and rebuilds
    cheaply, and a cached client hides a credential rotation until a restart.
    """
    missing = missing_credentials()
    if missing:
        raise GoogleAdsNotConfigured(
            "Google Ads API is not configured; missing: %s. Mint a refresh token "
            "with scripts/google_ads_auth.py, then set these on the service."
            % ", ".join(missing))
    try:
        from google.ads.googleads.client import GoogleAdsClient
    except ImportError as exc:
        raise GoogleAdsNotConfigured(
            "The google-ads library is not installed in this environment."
        ) from exc
    return GoogleAdsClient.load_from_dict({
        "developer_token": os.environ["GOOGLE_ADS_DEVELOPER_TOKEN"].strip(),
        "client_id": os.environ["GOOGLE_ADS_CLIENT_ID"].strip(),
        "client_secret": os.environ["GOOGLE_ADS_CLIENT_SECRET"].strip(),
        "refresh_token": os.environ["GOOGLE_ADS_REFRESH_TOKEN"].strip(),
        "login_customer_id": _digits(os.environ["GOOGLE_ADS_LOGIN_CUSTOMER_ID"]),
        "use_proto_plus": True,
    })


def selected_fields(query: str) -> list:
    """The field paths a GAQL SELECT asks for, in order.

    Rows come back as protobuf objects, so the query itself is the only
    description of what to read off them.
    """
    lowered = query.lower()
    start = lowered.index("select") + len("select")
    end = lowered.index(" from ", start)
    return [f.strip() for f in query[start:end].split(",") if f.strip()]


def _read_path(row, path: str):
    """`metrics.cost_micros` off a protobuf row, enums as their names."""
    value = row
    for part in path.split("."):
        value = getattr(value, part, None)
        if value is None:
            return None
    name = getattr(value, "name", None)          # enum wrapper
    if name is not None and not isinstance(value, (str, bytes)):
        return name
    if isinstance(value, (list, tuple)) or hasattr(value, "__iter__") and not isinstance(
            value, (str, bytes)):
        try:
            return [str(v) for v in value]
        except TypeError:
            return value
    return value


def _run_gaql(customer_id: str, query: str) -> list:
    """Run a GAQL query for one customer, return library-agnostic row dicts.

    Keys are the dotted field paths from the SELECT, which is what the callers'
    alias maps expect. Errors are translated rather than raised raw: a caller
    should be able to turn any failure into a gap naming the property.
    """
    cid = _digits(customer_id)
    if not cid:
        return []
    client = _client()
    service = client.get_service("GoogleAdsService")
    fields = selected_fields(query)
    rows = []
    try:
        stream = service.search_stream(customer_id=cid, query=query)
        for batch in stream:
            for row in batch.results:
                rows.append({field: _read_path(row, field) for field in fields})
    except Exception as exc:  # noqa: BLE001 — the library raises its own type
        raise _translate(exc, cid) from exc
    return rows


def _translate(exc: Exception, customer_id: str) -> Exception:
    """Turn a library error into something a caller can report honestly."""
    text = str(exc)
    lowered = text.lower()
    if "invalid_grant" in lowered or "refresh" in lowered and "token" in lowered:
        return GoogleAdsNotConfigured(
            "The Google Ads refresh token is no longer valid; mint a new one with "
            "scripts/google_ads_auth.py.")
    if "developer_token" in lowered or "developertoken" in lowered:
        return GoogleAdsNotConfigured(
            "The developer token was refused. Test-level tokens only reach test "
            "accounts; Basic access is required for live data.")
    if "user_permission_denied" in lowered or "not have permission" in lowered:
        return GoogleAdsError(
            "This login does not have access to account %s." % customer_id)
    if "customer_not_enabled" in lowered or "not_found" in lowered:
        return GoogleAdsError("Account %s is not reachable or not enabled." % customer_id)
    if "resource_exhausted" in lowered or "rate" in lowered and "limit" in lowered:
        return GoogleAdsError("Google Ads is rate-limiting this request; try later.")
    return GoogleAdsError("Google Ads request failed (%s)." % type(exc).__name__)


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
