"""Portfolio-wide batch runners — Red Light v2 PDFs + Loop forecasts.

The per-property endpoints (/api/red-light-v2/run, /api/loop/forecast/run)
existed but nothing swept the portfolio, so reports/forecasts only refreshed
when someone triggered them by hand (the pilot properties were manual runs).
These runners enumerate the in-scope portfolio and execute per-property runs
sequentially with throttling, dispatched on a background thread so the cron
HTTP call returns immediately (same async pattern as community-brief
capture-scan).

Scope: plestatus ∈ {RPM Managed, Onboarding} — the same scope the portal
views aligned on (triage/portfolio, 2026-07-06).

Skip logic (keeps monthly/daily crons cheap + idempotent):
  - red_light_v2: skip companies whose redlight_v2_run_date is within
    `max_age_days` (default 25 — a monthly cron re-runs everything, a
    retried cron doesn't double-run).
  - forecast: no skip by default (daily refresh is the point) but honors
    `limit` for testing.

Cron wiring (Render):
  red-light-v2-batch   0 6 1 * *   (monthly, 1st @ 6am UTC)
  forecast-batch       0 9 * * *   (daily 9am UTC — after AptIQ/tag syncs)
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
import time

import requests

logger = logging.getLogger(__name__)

HS_BASE = "https://api.hubapi.com"
PLE_INCLUDE = {"RPM Managed", "Onboarding"}

# One batch of each kind at a time — a cron retry must not double-run a
# portfolio sweep that's still going.
_locks = {"red_light_v2": threading.Lock(), "forecast": threading.Lock()}


def _headers() -> dict:
    from config import HUBSPOT_API_KEY
    return {"Authorization": f"Bearer {HUBSPOT_API_KEY}", "Content-Type": "application/json"}


def _enumerate_companies(extra_props: list) -> list[dict]:
    """LIST-endpoint enumeration (consistent counts), client-side PLE filter."""
    props = ["name", "plestatus", "uuid", "aptiq_property_id", "seo_tier"] + extra_props
    out: list[dict] = []
    after = None
    for _ in range(50):
        params = {"limit": 100, "properties": ",".join(sorted(set(props)))}
        if after:
            params["after"] = after
        r = requests.get(f"{HS_BASE}/crm/v3/objects/companies",
                         headers=_headers(), params=params, timeout=25)
        r.raise_for_status()
        d = r.json()
        for c in d.get("results", []):
            p = c.get("properties", {}) or {}
            if (p.get("plestatus") or "").strip() in PLE_INCLUDE:
                p["_company_id"] = c["id"]
                out.append(p)
        after = (d.get("paging") or {}).get("next", {}).get("after")
        if not after:
            break
    return out


# ── Red Light v2 batch ───────────────────────────────────────────────────────

def red_light_v2_batch(max_age_days: int = 25, limit: int = 0,
                       pause_seconds: float = 3.0) -> dict:
    """Enumerate scope and dispatch the sweep on a background thread.

    Only companies WITH an aptiq_property_id are eligible (the report is
    AptIQ-anchored). Companies with a run within max_age_days are skipped.
    """
    if _locks["red_light_v2"].locked():
        return {"status": "already_running"}

    companies = _enumerate_companies(["redlight_v2_run_date"])
    cutoff = (dt.date.today() - dt.timedelta(days=max_age_days)).isoformat()
    eligible = []
    skipped_fresh = 0
    skipped_no_aptiq = 0
    for p in companies:
        if not (p.get("aptiq_property_id") or "").strip():
            skipped_no_aptiq += 1
            continue
        run_date = (p.get("redlight_v2_run_date") or "")[:10]
        if run_date and run_date >= cutoff:
            skipped_fresh += 1
            continue
        eligible.append(p["_company_id"])
    if limit:
        eligible = eligible[:limit]

    def _sweep():
        with _locks["red_light_v2"]:
            import redlight_v2_run
            ok = fail = 0
            for i, cid in enumerate(eligible, 1):
                try:
                    redlight_v2_run.run(company_id=cid)
                    ok += 1
                except Exception as e:
                    fail += 1
                    logger.warning("red-light-v2-batch: %s failed: %s", cid, e)
                if i % 25 == 0:
                    logger.info("red-light-v2-batch: %d/%d (ok=%d fail=%d)",
                                i, len(eligible), ok, fail)
                time.sleep(pause_seconds)
            logger.info("red-light-v2-batch DONE: %d ok, %d failed of %d",
                        ok, fail, len(eligible))

    threading.Thread(target=_sweep, daemon=True).start()
    return {"status": "dispatched", "eligible": len(eligible),
            "skipped_fresh": skipped_fresh, "skipped_no_aptiq": skipped_no_aptiq,
            "scope_count": len(companies), "max_age_days": max_age_days}


# ── Forecast batch ───────────────────────────────────────────────────────────

def forecast_batch(limit: int = 0, pause_seconds: float = 1.0) -> dict:
    """Run a fresh forecast for every in-scope company with a uuid."""
    if _locks["forecast"].locked():
        return {"status": "already_running"}

    companies = _enumerate_companies([])
    eligible = [p for p in companies if (p.get("uuid") or "").strip()]
    skipped_no_uuid = len(companies) - len(eligible)
    if limit:
        eligible = eligible[:limit]

    def _sweep():
        with _locks["forecast"]:
            import forecasting
            ok = fail = 0
            for i, p in enumerate(eligible, 1):
                try:
                    forecasting.run_forecast(
                        p["uuid"].strip(),
                        seo_tier=(p.get("seo_tier") or "").strip() or None,
                    )
                    ok += 1
                except Exception as e:
                    fail += 1
                    logger.warning("forecast-batch: %s failed: %s", p.get("_company_id"), e)
                if i % 50 == 0:
                    logger.info("forecast-batch: %d/%d (ok=%d fail=%d)",
                                i, len(eligible), ok, fail)
                time.sleep(pause_seconds)
            logger.info("forecast-batch DONE: %d ok, %d failed of %d",
                        ok, fail, len(eligible))

    threading.Thread(target=_sweep, daemon=True).start()
    return {"status": "dispatched", "eligible": len(eligible),
            "skipped_no_uuid": skipped_no_uuid, "scope_count": len(companies)}
