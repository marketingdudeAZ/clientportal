#!/usr/bin/env python3
"""Run the site crawl that feeds the page-level SEO rules.

    # what it would crawl, and nothing else — no DataForSEO calls at all
    python3 scripts/run_seo_crawl.py --rpmi --dry-run
    python3 scripts/run_seo_crawl.py --rpmi --limit 5 --dry-run

    # one property
    python3 scripts/run_seo_crawl.py --company-id 12345678

    # the roster, five sites at a time
    python3 scripts/run_seo_crawl.py --rpmi --limit 5

Each crawl appends one row per readable page to the BigQuery audit table
(`seo_onpage_audit`), which `webhook-server/seo_crawl.stored_pages()` reads
back and `skills/reco_seo.gather()` hands to the four page-level rules.

A crawl costs money per page, so:
  * `--max-pages` caps each site (default from SEO_CRAWL_MAX_PAGES).
  * a site already stored for today is skipped; `--force` overrides that.
  * `--dry-run` is the default habit — it prints the plan and stops.
  * every run prints the cost DataForSEO reported, per site and in total.
    Nothing is estimated: the dry run reports the page cap, not a price.

HubSpot is read only — the property resolver and the RPMI roster both read.
Nothing in this path writes a HubSpot property, so R1 cannot be touched.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "webhook-server"))

import seo_crawl  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Crawl property websites into the SEO audit table.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    scope = p.add_mutually_exclusive_group(required=True)
    scope.add_argument("--company-id", help="crawl the one site on this HubSpot company")
    scope.add_argument("--rpmi", action="store_true",
                       help="crawl the RPMI roster, one crawl per site")
    p.add_argument("--limit", type=int, default=None,
                   help="stop after N sites (roster order)")
    p.add_argument("--max-pages", type=int, default=None,
                   help="pages per site (default SEO_CRAWL_MAX_PAGES=%d)"
                        % seo_crawl.SEO_CRAWL_MAX_PAGES)
    p.add_argument("--jsonld-pages", type=int, default=None,
                   help="pages per site to read raw HTML for, for JSON-LD types "
                        "(default SEO_CRAWL_JSONLD_PAGES=%d); 0 turns it off"
                        % seo_crawl.SEO_CRAWL_JSONLD_PAGES)
    p.add_argument("--crawl-date", default=None,
                   help="the crawl date the rows are stored under (default today)")
    p.add_argument("--force", action="store_true",
                   help="crawl again even if this site already has rows for the date")
    p.add_argument("--no-store", action="store_true",
                   help="crawl but do not write to BigQuery (prints the rows)")
    p.add_argument("--dry-run", action="store_true",
                   help="print what would be crawled and stop; calls no API")
    p.add_argument("--json", action="store_true", help="print the raw result as JSON")
    p.add_argument("--verbose", "-v", action="store_true")
    return p


def _print_plan(plan: dict) -> None:
    print("Crawl plan for %s" % plan["crawl_date"])
    print("  targets            %d" % plan["targets"])
    print("  would crawl        %d" % plan["would_crawl"])
    print("  already crawled    %d%s" % (
        plan["already_crawled"],
        "" if plan["stored_sites_known"] else
        "   (unknown — BigQuery could not be read, so nothing is assumed stored)"))
    print("  unreadable sites   %d" % plan["unreadable"])
    print("  duplicate targets  %d" % plan["duplicates"])
    print("  page cap per site  %d" % plan["max_pages_per_site"])
    print("  pages at most      %d  (cost is whatever DataForSEO reports per page;"
          " nothing is estimated here)" % plan["max_pages_total"])
    print("")
    width = max([len(r["site"] or "(no site)") for r in plan["rows"]] + [8])
    for row in plan["rows"]:
        print("  %-*s  %-26s %s" % (width, row["site"] or "(no site)", row["state"],
                                    row["property_name"] or row["company_id"] or ""))


def _print_run(summary: dict) -> None:
    print("Crawl %s" % summary["crawl_date"])
    print("  sites                  %d" % summary["sites"])
    print("  crawled and stored     %d" % summary["crawled"])
    print("  skipped (already done) %d" % summary["skipped_already_crawled"])
    print("  pages stored           %d rows" % summary["rows_stored"])
    print("  pages read             %d" % summary["pages"])
    print("  partial link graphs    %d  (inbound link counts not stored for these)"
          % summary["incomplete_link_graphs"])
    print("  cost reported          $%.4f total" % summary["cost_usd"])
    if summary["cost_per_site_usd"] is not None:
        print("  cost per site          $%.4f" % summary["cost_per_site_usd"])
    print("")
    for result in summary["results"]:
        print("  %-34s %-18s %4d pages  $%.4f%s" % (
            result.get("site") or "(no site)", result.get("status"),
            result.get("pages") or 0, result.get("cost_usd") or 0.0,
            "  complete=False" if result.get("crawl_complete") is False else ""))
        for err in result.get("errors") or []:
            print("      ! %s" % err)
        for gap in result.get("gaps") or []:
            print("      - %s" % gap["message"])
    if summary["failures"]:
        print("\n%d site(s) produced nothing; the rest of the run continued."
              % len(summary["failures"]))


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    day = args.crawl_date or date.today().isoformat()

    if args.rpmi:
        try:
            targets = seo_crawl.rpmi_targets(args.limit)
        except Exception as exc:  # noqa: BLE001 — a HubSpot outage is not a traceback
            print("Could not read the RPMI roster: %s" % exc, file=sys.stderr)
            return 2
    else:
        targets = [{"site": None, "company_id": args.company_id}]

    if args.dry_run:
        if not args.rpmi:
            # Resolve the one company so the plan names the real site rather
            # than "(no site)". Read-only.
            try:
                from skills import property_resolver as pr
                identity = pr.resolve(str(args.company_id)).to_dict()
                targets = [{"site": identity.get("domain") or identity.get("website"),
                            "company_id": str(args.company_id),
                            "name": identity.get("name")}]
            except Exception as exc:  # noqa: BLE001
                print("Could not resolve company %s: %s" % (args.company_id, exc),
                      file=sys.stderr)
                return 2
        plan = seo_crawl.plan(targets, max_pages=args.max_pages, crawl_date=day)
        if args.json:
            print(json.dumps(plan, indent=2, default=str))
        else:
            _print_plan(plan)
        return 0

    options = {"max_pages": args.max_pages, "jsonld_pages": args.jsonld_pages,
               "store": not args.no_store}
    if args.rpmi:
        summary = seo_crawl.crawl_sites(targets, crawl_date=day, force=args.force,
                                        **options)
    else:
        result = seo_crawl.crawl_property(args.company_id, crawl_date=day,
                                          force=args.force, **options)
        summary = {"crawl_date": day, "sites": 1,
                   "crawled": 1 if result.get("status") == "stored" else 0,
                   "skipped_already_crawled":
                       1 if result.get("status") == "already_crawled" else 0,
                   "incomplete_link_graphs":
                       1 if result.get("crawl_complete") is False else 0,
                   "pages": result.get("pages") or 0,
                   "rows_stored": result.get("stored") or 0,
                   "cost_usd": round(float(result.get("cost_usd") or 0.0), 6),
                   "cost_per_site_usd": round(float(result.get("cost_usd") or 0.0), 6),
                   "failures": [] if result.get("status") in ("stored", "already_crawled")
                               else [{"site": result.get("site"),
                                      "status": result.get("status"),
                                      "errors": result.get("errors") or []}],
                   "results": [result]}

    if args.json:
        printable = dict(summary)
        printable["results"] = [{k: v for k, v in r.items() if k != "rows"}
                                for r in summary["results"]]
        if args.no_store:
            printable["rows"] = [row for r in summary["results"]
                                 for row in (r.get("rows") or [])]
        print(json.dumps(printable, indent=2, default=str))
    else:
        _print_run(summary)
        if args.no_store:
            print("\n--no-store: %d row(s) built and discarded."
                  % sum(len(r.get("rows") or []) for r in summary["results"]))
    return 1 if summary["failures"] and not summary["crawled"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
