"""CLI entry point.

Deliberately refuses to emit a report when the warehouse is unreachable. An
empty-but-well-formed report is the failure mode ADR 0022 was written about:
`hyly_client` swallowed every exception into `[]`, so a missing vendor table
read as "this property has no leads". A funnel analysis that renders zeros
because it could not authenticate would put that same error in front of a
client during a pricing decision.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "webhook-server"))

from skills.pre_lead_funnel import probe as probe_mod  # noqa: E402
from skills.pre_lead_funnel.render import render_markdown  # noqa: E402
from skills.pre_lead_funnel.report import ReportContext, build_report  # noqa: E402

DEFAULT_LEAD_EVENTS = ("generate_lead", "form_submit", "submit_lead", "contact_submit")


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def resolve_hyly_property_id(name: str) -> str | None:
    """Look up the property's hyly_property_id on its HubSpot company.

    R1: this reads `uuid` and platform ids, it never writes them.
    """
    try:
        import hubspot_client
    except Exception:
        return None
    try:
        matches = hubspot_client.search_companies(
            [{"filters": [{"propertyName": "name", "operator": "EQ", "value": name}]}],
            properties=["name", "uuid", "hyly_property_id", "google_ads_customer_id"],
        )
    except Exception as exc:
        print(f"HubSpot lookup failed: {exc}", file=sys.stderr)
        return None
    if not matches:
        print(f"No HubSpot company named {name!r}", file=sys.stderr)
        return None
    return (matches[0].get("properties") or {}).get("hyly_property_id")


def make_runner(client):
    from google.cloud import bigquery

    def run_query(sql: str, patterns: list[str] | None = None, **params):
        qp = []
        for k, v in params.items():
            if isinstance(v, date):
                qp.append(bigquery.ScalarQueryParameter(k, "DATE", v))
            else:
                qp.append(bigquery.ScalarQueryParameter(k, "STRING", str(v)))
        for i, p in enumerate(patterns or []):
            qp.append(bigquery.ScalarQueryParameter(f"p{i}", "STRING", p.lower()))
        cfg = bigquery.QueryJobConfig(query_parameters=qp)
        return [dict(r.items()) for r in client.query(sql, job_config=cfg).result()]

    return run_query


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="pre_lead_funnel")
    ap.add_argument("--property", required=True, help="HubSpot company name")
    ap.add_argument("--hyly-property-id", help="skip the HubSpot lookup")
    ap.add_argument("--start", type=_parse_date, required=True)
    ap.add_argument("--end", type=_parse_date, required=True)
    ap.add_argument(
        "--page-pattern",
        action="append",
        default=[],
        help="SQL LIKE pattern for floorplan/pricing/availability URLs. Repeatable. "
        "Required for widgets 5 and 9; they report unavailable without it.",
    )
    ap.add_argument("--lead-event", action="append", default=[])
    ap.add_argument(
        "--cohort",
        action="append",
        default=[],
        help="Comparison property as 'Name=hyly_property_id'. Repeatable.",
    )
    ap.add_argument("--ga4-table", default=probe_mod.GA4_TABLE_DEFAULT)
    ap.add_argument("--json", action="store_true", help="emit JSON instead of markdown")
    ap.add_argument("--out", help="write to this path instead of stdout")
    args = ap.parse_args(argv)

    pid = args.hyly_property_id or resolve_hyly_property_id(args.property)

    client = None
    try:
        client = probe_mod._bq_client()
    except Exception as exc:
        print(f"BigQuery client init failed: {exc}", file=sys.stderr)

    probe_result = probe_mod.probe(args.property, pid, ga4_table=args.ga4_table, client=client)

    if not probe_result.has("bigquery"):
        cap = probe_result.get("bigquery")
        print(
            "REFUSING TO RENDER A REPORT: the warehouse is unreachable "
            f"({cap.detail if cap else 'unknown'}).\n"
            "A report built without data access would render every widget as "
            "empty, which reads as 'no traffic' rather than 'no access'.",
            file=sys.stderr,
        )
        print(json.dumps(probe_result.as_dict(), indent=2))
        return 2

    rollup = None
    hp = os.environ.get("BIGQUERY_HYLY_PROJECT") or os.environ.get("BIGQUERY_PROJECT_ID")
    hd = os.environ.get("BIGQUERY_HYLY_DATASET")
    if hp and hd:
        rollup = f"{hp}.{hd}.hyly_daily_activity_v1"

    cohort = []
    for spec in args.cohort:
        name, _, cid = spec.partition("=")
        if cid:
            cohort.append((name, cid))

    ctx = ReportContext(
        property_label=args.property,
        hyly_property_id=pid,
        start=args.start,
        end=args.end,
        ga4_table=args.ga4_table,
        rollup_table=rollup,
        lead_events=tuple(args.lead_event) or DEFAULT_LEAD_EVENTS,
        page_patterns=args.page_pattern,
        cohort_property_ids=cohort,
    )
    report = build_report(ctx, probe_result, make_runner(client))
    text = json.dumps(report, indent=2, default=str) if args.json else render_markdown(report)
    if args.out:
        Path(args.out).write_text(text)
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
