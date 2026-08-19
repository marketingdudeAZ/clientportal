"""Capability probe — what can this report actually be filled with, today?

Run this BEFORE building the report. It answers, against live infrastructure
rather than against what the ADRs assumed, the only question that matters for a
pre-lead funnel analysis:

    Do we hold session-grain web data for this property at all?

The answer is not obvious. RPM has no GA4 connector of its own (docs/SPEC.md
lists one as Phase 0 work; nothing implements it). The only GA4-shaped data any
code in this repo can reach is Hyly's `ga4_analytics_events` copy, which (a)
covers the 15-property Hyly beta only, and (b) is a frozen 2026-08-11 snapshot
per ADR 0022, not a feed.

So a property can fail this probe in four distinct ways, and telling them apart
changes what you say to the client:

    no credentials      -> our problem, fixable this afternoon
    not in the beta     -> no session data exists for it anywhere we can read
    in beta, no rows    -> tagging or property_id mapping is broken
    rows but no events  -> the site does not fire the event being asked for

`hyly_client` used to collapse all four into an empty list, which is how a
missing vendor table came to look like "this property has no leads" (ADR 0022).
This module exists so that never happens inside a client-facing analysis.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Hyly's GA4 export copy. Declared in connectors/hyly/sources.json; repeated as
# a default here so the probe runs standalone.
GA4_TABLE_DEFAULT = "data-and-reporting-483421.hyly.ga4_analytics_events"

# GA4 event names that would let us answer widgets 2, 5 and 6. Presence is
# probed, never assumed — most multifamily templates fire page_view and a
# single generate_lead and nothing else, which is exactly the finding that
# decides whether widget 6 can exist.
INTENT_EVENTS = {
    "lead": ("generate_lead", "form_submit", "submit_lead", "contact_submit"),
    "form_start": ("form_start", "form_engagement"),
    "pricing_interaction": (
        "view_item",
        "check_availability",
        "view_pricing",
        "floorplan_click",
        "unit_select",
    ),
    "tour_request": ("schedule_tour", "book_tour", "tour_request"),
}


@dataclass
class Capability:
    """One probed fact, with the evidence attached."""

    name: str
    available: bool
    detail: str = ""
    evidence: Any = None

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "available": self.available,
            "detail": self.detail,
            "evidence": self.evidence,
        }


@dataclass
class ProbeResult:
    property_label: str
    capabilities: list[Capability] = field(default_factory=list)

    def add(self, name: str, available: bool, detail: str = "", evidence: Any = None) -> Capability:
        cap = Capability(name=name, available=available, detail=detail, evidence=evidence)
        self.capabilities.append(cap)
        return cap

    def get(self, name: str) -> Optional[Capability]:
        for cap in self.capabilities:
            if cap.name == name:
                return cap
        return None

    def has(self, name: str) -> bool:
        cap = self.get(name)
        return bool(cap and cap.available)

    def as_dict(self) -> dict:
        return {
            "property": self.property_label,
            "capabilities": [c.as_dict() for c in self.capabilities],
        }


def _bq_client():
    """BigQuery client from the same env contract hyly_client uses, or None."""
    project = os.environ.get("BIGQUERY_PROJECT_ID")
    sa = os.environ.get("BIGQUERY_SERVICE_ACCOUNT_JSON", "")
    if not (project and sa):
        return None
    import json

    from google.cloud import bigquery
    from google.oauth2 import service_account

    info = json.loads(sa) if sa.strip().startswith("{") else json.load(open(sa))
    creds = service_account.Credentials.from_service_account_info(info)
    return bigquery.Client(project=project, credentials=creds)


def _split_ref(table_ref: str) -> tuple[str, str, str]:
    project, dataset, table = table_ref.split(".", 2)
    return project, dataset, table


def probe(
    property_label: str,
    hyly_property_id: Optional[str],
    ga4_table: str = GA4_TABLE_DEFAULT,
    client: Any = None,
) -> ProbeResult:
    """Establish, empirically, which widgets can be filled for this property.

    `client` is injectable so the probe is testable without BigQuery. Passing
    None builds one from env; if env is unset every downstream capability is
    reported unavailable with that as the stated reason, rather than the probe
    raising and leaving the caller to guess.
    """
    result = ProbeResult(property_label=property_label)

    if client is None:
        try:
            client = _bq_client()
        except Exception as exc:  # credentials present but malformed
            result.add("bigquery", False, f"BigQuery client init failed: {exc}")
            return result

    if client is None:
        result.add(
            "bigquery",
            False,
            "BIGQUERY_PROJECT_ID / BIGQUERY_SERVICE_ACCOUNT_JSON are unset — "
            "no warehouse access from this environment",
        )
        return result
    result.add("bigquery", True, "warehouse reachable")

    if not hyly_property_id:
        result.add(
            "property_identity",
            False,
            f"No hyly_property_id on the HubSpot company for {property_label}. "
            "Hyly is a 15-property beta; a property outside it has no "
            "session-grain data in any source this repo can read.",
        )
        return result
    result.add("property_identity", True, f"hyly_property_id={hyly_property_id}")

    _probe_ga4_table(result, client, ga4_table, hyly_property_id)
    _probe_leads(result, client, hyly_property_id)
    _probe_paid_media(result)
    return result


def _probe_ga4_table(result: ProbeResult, client, ga4_table: str, property_id: str) -> None:
    project, dataset, table = _split_ref(ga4_table)

    try:
        cols = [
            r["column_name"]
            for r in client.query(
                f"SELECT column_name FROM `{project}.{dataset}`."
                "INFORMATION_SCHEMA.COLUMNS WHERE table_name = @t",
                job_config=_params(t=table),
            ).result()
        ]
    except Exception as exc:
        result.add("ga4_table", False, f"{ga4_table} unreadable: {exc}")
        return

    if not cols:
        result.add("ga4_table", False, f"{ga4_table} does not exist")
        return
    result.add("ga4_table", True, f"{len(cols)} columns", evidence=sorted(cols))

    # Sessions need a session key. The GA4 export carries it as
    # user_pseudo_id + the ga_session_id event_param; ADR 0022's column
    # contract does not promise either, so both are probed rather than assumed.
    has_pseudo = "user_pseudo_id" in cols
    has_params = "event_params" in cols
    result.add(
        "session_key",
        has_pseudo and has_params,
        "user_pseudo_id + event_params.ga_session_id present"
        if (has_pseudo and has_params)
        else f"missing {'user_pseudo_id ' if not has_pseudo else ''}"
        f"{'event_params' if not has_params else ''} — sessions cannot be "
        "counted at session grain from this table",
    )
    result.add(
        "default_channel_group",
        "default_channel" in cols,
        "GA4 default channel grouping column present"
        if "default_channel" in cols
        else "no default_channel column — widget 4 cannot use the GA4 default grouping",
    )
    result.add(
        "page_location",
        has_params,
        "page_location readable from event_params (floorplan/pricing URL matching)"
        if has_params
        else "no event_params — page-level URL matching impossible",
    )

    # Coverage: rows for this property, and the date window they span. A frozen
    # snapshot that stops in August cannot answer "through current".
    try:
        row = list(
            client.query(
                f"""
                SELECT
                  COUNT(*)            AS rows_,
                  MIN(event_date)     AS first_date,
                  MAX(event_date)     AS last_date
                FROM `{ga4_table}`
                WHERE CAST(property_id AS STRING) = @pid
                """,
                job_config=_params(pid=str(property_id)),
            ).result()
        )
    except Exception as exc:
        result.add("ga4_coverage", False, f"coverage query failed: {exc}")
        return

    rows = row[0]["rows_"] if row else 0
    if not rows:
        result.add(
            "ga4_coverage",
            False,
            f"0 GA4 rows for property_id={property_id}. The property is mapped "
            "but the export carries nothing for it — a tagging or "
            "property-mapping fault, not an absence of traffic.",
        )
        return
    first, last = row[0]["first_date"], row[0]["last_date"]
    result.add(
        "ga4_coverage",
        True,
        f"{rows:,} rows, {first} to {last}",
        evidence={"rows": rows, "first_date": str(first), "last_date": str(last)},
    )

    # Which intent events does this property's site actually fire? This single
    # answer decides whether widget 6 exists or is declared unavailable.
    try:
        names = [
            (r["event_name"], r["n"])
            for r in client.query(
                f"""
                SELECT event_name, COUNT(*) AS n
                FROM `{ga4_table}`
                WHERE CAST(property_id AS STRING) = @pid
                GROUP BY event_name
                ORDER BY n DESC
                """,
                job_config=_params(pid=str(property_id)),
            ).result()
        ]
    except Exception as exc:
        result.add("ga4_events", False, f"event enumeration failed: {exc}")
        return

    fired = {n for n, _ in names}
    result.add("ga4_events", True, f"{len(names)} distinct event names", evidence=names)
    for concept, candidates in INTENT_EVENTS.items():
        matched = sorted(fired.intersection(candidates))
        result.add(
            f"event:{concept}",
            bool(matched),
            f"fires {matched}"
            if matched
            else f"site fires none of {list(candidates)} — this funnel step is "
            "not instrumented and cannot be reported",
        )


def _probe_leads(result: ProbeResult, client, property_id: str) -> None:
    """Lead counts come from the Hyly rollup, not from GA4.

    Kept separate on purpose: a property can have GA4 sessions and no rollup
    row, and conflating the two produces a conversion rate with a denominator
    from one system and a numerator from another that never agreed on what a
    lead is. When both exist the report shows both lead definitions side by
    side rather than picking one silently.
    """
    project = os.environ.get("BIGQUERY_HYLY_PROJECT") or os.environ.get("BIGQUERY_PROJECT_ID")
    dataset = os.environ.get("BIGQUERY_HYLY_DATASET")
    if not (project and dataset):
        result.add(
            "hyly_rollup",
            False,
            "BIGQUERY_HYLY_PROJECT / BIGQUERY_HYLY_DATASET unset — "
            "hyly_daily_activity_v1 not addressable",
        )
        return
    table = f"{project}.{dataset}.hyly_daily_activity_v1"
    try:
        row = list(
            client.query(
                f"""
                SELECT SUM(leads) AS leads, MIN(activity_date) AS first_date,
                       MAX(activity_date) AS last_date
                FROM `{table}`
                WHERE hyly_property_id = @pid
                """,
                job_config=_params(pid=str(property_id)),
            ).result()
        )
    except Exception as exc:
        result.add("hyly_rollup", False, f"{table} unreadable: {exc}")
        return
    leads = (row[0]["leads"] if row else None) or 0
    result.add(
        "hyly_rollup",
        leads > 0,
        f"{leads:,} leads, {row[0]['first_date']} to {row[0]['last_date']}"
        if leads
        else f"no rollup rows for property_id={property_id}",
        evidence={"leads": int(leads)},
    )


def _probe_paid_media(result: ProbeResult) -> None:
    """Google Ads: impression share, cost, and search-term detail.

    `webhook-server/google_ads_islost.py` documents itself as a
    credential-gated seam — the GAQL and parsing are built and tested, but
    `_run_gaql` raises GoogleAdsNotConfigured because neither the `google-ads`
    library nor the OAuth credentials have landed. Widgets 7 and 8 stand or
    fall on that.
    """
    try:
        import google.ads.googleads  # noqa: F401

        lib = True
    except Exception:
        lib = False

    required = (
        "GOOGLE_ADS_DEVELOPER_TOKEN",
        "GOOGLE_ADS_CLIENT_ID",
        "GOOGLE_ADS_CLIENT_SECRET",
        "GOOGLE_ADS_REFRESH_TOKEN",
    )
    missing = [k for k in required if not os.environ.get(k)]
    result.add(
        "google_ads",
        lib and not missing,
        "google-ads client available"
        if (lib and not missing)
        else "not configured: "
        + ("`google-ads` library not installed" if not lib else "")
        + (f" missing env {missing}" if missing else ""),
    )


def _params(**kwargs):
    from google.cloud import bigquery

    return bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter(k, "STRING", v) for k, v in kwargs.items()
        ]
    )
