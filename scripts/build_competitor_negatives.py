"""Build a competitor negative keyword list for one property (read-only).

Reads the Apt IQ daily CSV (APT_IQ_DAILY_SHEET_URL), takes every other property
in the same Market ID, and writes a Google Ads Editor import for a shared
negative list (phrase match). Nothing is uploaded — a paid specialist reviews
the file and applies it.

    python3 scripts/build_competitor_negatives.py --aptiq-id 100516126
    python3 scripts/build_competitor_negatives.py --aptiq-id 100516126 \\
        --search-terms atwood_search_terms.csv --out atwood_negatives.csv

--search-terms is an optional search-term report export (one term per row in
a "Search term" column). Competitors found there sort first so they survive
the 5,000-keyword cap.

Fair Housing: the list holds competitor NAMES only. Bare city, state and ZIP
phrases are refused by competitor_negatives.is_safe_negative.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "webhook-server"))
sys.path.insert(0, os.path.dirname(HERE))

import competitor_negatives as cn  # noqa: E402


def _read_terms(path: str) -> list[str]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return [r.get("Search term") or "" for r in csv.DictReader(f)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--aptiq-id", required=True)
    ap.add_argument("--search-terms")
    ap.add_argument("--out")
    a = ap.parse_args()

    from services.fluency_ingestion import apt_iq_csv_client
    rows = apt_iq_csv_client.get_all_rows()
    self_row = rows.get(str(a.aptiq_id))
    if not self_row:
        print(f"Apt IQ id {a.aptiq_id} is not in the daily CSV.", file=sys.stderr)
        return 1
    market = (self_row.get("Market ID") or "").strip()
    cohort = [r for r in rows.values() if (r.get("Market ID") or "").strip() == market]

    seen = []
    if a.search_terms:
        seen = cn.names_seen_in_search_terms(_read_terms(a.search_terms),
                                             [r.get("Property") or "" for r in cohort])
    out = cn.build_negatives(self_row, cohort, seen_in_search_terms=seen)

    name = self_row.get("Property") or a.aptiq_id
    list_name = f"{name} - Competitor Negatives"
    path = a.out or f"competitor_negatives_{a.aptiq_id}.csv"
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["Shared set name", "Keyword", "Criterion Type"])
        w.writeheader()
        w.writerows(cn.editor_csv_rows(list_name, out["keywords"]))

    print(f"{name} (market {self_row.get('Market Name') or market}): "
          f"{out['competitors']} competitors -> {len(out['keywords'])} negatives -> {path}")
    if seen:
        print(f"  seen in search terms ({len(seen)}): {', '.join(seen[:15])}")
    if out["truncated"]:
        print(f"  TRUNCATED at {cn.SHARED_LIST_LIMIT}; split into a second list.")
    if out["skipped"]:
        print(f"  skipped {len(out['skipped'])} generic/geographic/own-brand phrases "
              f"(review by hand): {', '.join(p for p, _ in out['skipped'][:15])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
