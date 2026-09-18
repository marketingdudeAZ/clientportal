"""Prove the Google Ads connection, one property at a time.

Run this the moment the five environment values are set, before trusting any
recommendation that depends on ad data. It reads only — no campaign, budget or
keyword is touched.

    python3 scripts/google_ads_smoke.py --company-id 30912193455
    python3 scripts/google_ads_smoke.py --property "The Atwood at Rivulon"
    python3 scripts/google_ads_smoke.py --coverage        # how many RPMI
                                                          # properties have an id

What it tells you, in order:
  1. whether the five credentials are present,
  2. whether the account answers at all,
  3. what it returned — campaigns, spend, impression share lost to budget,
  4. whether that is enough for the recommendation rules to fire.

A failure here is a configuration answer, not a stack trace: each one says which
value to fix and where it comes from.
"""
from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "webhook-server"))
sys.path.insert(0, os.path.dirname(HERE))


def _load_env() -> None:
    """Pick up a local .env the way the app does, so this works off a laptop."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for candidate in (os.path.join(os.path.dirname(HERE), ".env"),
                      os.path.expanduser("~/Client-Portal/.env")):
        if os.path.exists(candidate):
            load_dotenv(candidate, override=False)


def _money(value) -> str:
    try:
        return "${:,.2f}".format(float(value))
    except (TypeError, ValueError):
        return "—"


def check_credentials() -> int:
    import google_ads_islost as ads

    missing = ads.missing_credentials()
    if not missing:
        print("Credentials: all five present.")
        return 0
    print("Credentials: MISSING %d of 5\n" % len(missing))
    where = {
        "GOOGLE_ADS_DEVELOPER_TOKEN": "the manager account, Tools & Settings → "
                                      "Setup → API Center (must be Basic, not Test)",
        "GOOGLE_ADS_CLIENT_ID": "the OAuth client (Desktop app) in the GCP project",
        "GOOGLE_ADS_CLIENT_SECRET": "the same OAuth client",
        "GOOGLE_ADS_REFRESH_TOKEN": "run scripts/google_ads_auth.py once and sign "
                                    "in as the account that can see the manager",
        "GOOGLE_ADS_LOGIN_CUSTOMER_ID": "the manager account id, digits only",
    }
    for name in missing:
        print("  %-30s %s" % (name, where.get(name, "")))
    return 1


def coverage() -> int:
    """How many RPMI properties carry a Google Ads id at all."""
    import hubspot_client as hs

    rows, seen = [], set()
    for value in ("RPMI", "RPM Investments"):
        after = None
        while True:
            payload = {"filterGroups": [{"filters": [
                {"propertyName": "client", "operator": "EQ", "value": value}]}],
                "properties": ["name", "google_ads_customer_id", "plestatus"],
                "limit": 100}
            if after:
                payload["after"] = after
            body = hs._request("POST", "%s/search" % hs._COMPANIES, json=payload).json()
            for row in body.get("results", []):
                if row["id"] not in seen:
                    seen.add(row["id"])
                    rows.append(row)
            after = ((body.get("paging") or {}).get("next") or {}).get("after")
            if not after:
                break

    def prop(row, key):
        return ((row.get("properties") or {}).get(key) or "").strip()

    managed = [r for r in rows if prop(r, "plestatus") in ("RPM Managed", "Onboarding")]
    with_id = [r for r in managed if prop(r, "google_ads_customer_id")]
    print("RPMI managed properties: %d" % len(managed))
    print("  with a Google Ads account id: %d" % len(with_id))
    print("  without one (no rule can fire): %d" % (len(managed) - len(with_id)))
    for row in managed:
        if not prop(row, "google_ads_customer_id"):
            print("    - %s" % prop(row, "name"))
    return 0


def smoke(company_id: str) -> int:
    import google_ads_islost as ads
    from skills import property_resolver as pr

    if check_credentials():
        return 1

    try:
        identity = pr.resolve(company_id)
    except Exception as exc:  # noqa: BLE001
        print("Could not resolve %r: %s" % (company_id, exc))
        return 1
    cid = (identity.to_dict().get("google_ads_customer_id") or "").strip()
    print("Property: %s (company %s)" % (identity.name, identity.company_id))
    if not cid:
        print("  This property has no Google Ads account id, so no paid-media rule "
              "can fire for it. Nothing to test here — try --coverage.")
        return 1
    print("  Google Ads account: %s" % cid)

    query = ("SELECT campaign.name, campaign.advertising_channel_type, "
             "campaign.status, metrics.cost_micros, metrics.clicks, "
             "metrics.conversions, metrics.search_budget_lost_impression_share "
             "FROM campaign WHERE segments.date DURING LAST_30_DAYS")
    try:
        rows = ads._run_gaql(cid, query)
    except ads.GoogleAdsNotConfigured as exc:
        print("\nNOT CONFIGURED: %s" % exc)
        return 1
    except ads.GoogleAdsError as exc:
        print("\nREFUSED: %s" % exc)
        return 1

    if not rows:
        print("\nThe account answered, but reported no campaigns in the last 30 days.")
        print("That is a real answer: either the account is empty or nothing ran.")
        return 0

    print("\nLast 30 days — %d campaign rows" % len(rows))
    spend = sum(float(r.get("metrics.cost_micros") or 0) / 1e6 for r in rows)
    clicks = sum(float(r.get("metrics.clicks") or 0) for r in rows)
    conversions = sum(float(r.get("metrics.conversions") or 0) for r in rows)
    print("  spend %s · clicks %s · conversions %s"
          % (_money(spend), "{:,.0f}".format(clicks), "{:,.1f}".format(conversions)))
    for row in rows[:8]:
        print("    %-42s %-10s %10s  IS lost to budget: %s"
              % (str(row.get("campaign.name"))[:42],
                 row.get("campaign.advertising_channel_type"),
                 _money(float(row.get("metrics.cost_micros") or 0) / 1e6),
                 row.get("metrics.search_budget_lost_impression_share")))
    if len(rows) > 8:
        print("    … and %d more" % (len(rows) - 8))

    islost = ads.parse_islost([
        {"channel_type": r.get("campaign.advertising_channel_type"),
         "budget_lost_is": r.get("metrics.search_budget_lost_impression_share")}
        for r in rows])
    print("\nImpression share lost to budget (search): %s"
          % (islost.get("paid_search", "not reported")))
    print("\nThe connection works. The paid-media rules can read this property.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--company-id", help="HubSpot company id")
    parser.add_argument("--property", help="property name, resolved via HubSpot")
    parser.add_argument("--coverage", action="store_true",
                        help="count RPMI properties with a Google Ads id")
    parser.add_argument("--check", action="store_true",
                        help="check the five credentials and stop")
    args = parser.parse_args()

    _load_env()
    if args.check:
        return check_credentials()
    if args.coverage:
        return coverage()
    target = args.company_id or args.property
    if not target:
        parser.print_help()
        return 2
    return smoke(target)


if __name__ == "__main__":
    sys.exit(main())
