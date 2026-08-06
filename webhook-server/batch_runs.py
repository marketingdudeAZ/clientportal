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

# Introspection: live progress of the current/last sweep of each kind.
# Written by the sweep threads, read by GET on the batch endpoints — the
# first wedged sweep was undebuggable without this.
BUILD = "batch-v3-states"
_status: dict = {"red_light_v2": {}, "forecast": {}}


def _headers() -> dict:
    from config import HUBSPOT_API_KEY
    return {"Authorization": f"Bearer {HUBSPOT_API_KEY}", "Content-Type": "application/json"}


_STATE_ABBR = {"arizona":"AZ","california":"CA","nevada":"NV","new mexico":"NM",
               "colorado":"CO","texas":"TX","florida":"FL","georgia":"GA","tennessee":"TN",
               "north carolina":"NC","south carolina":"SC","oklahoma":"OK","illinois":"IL",
               "washington":"WA","oregon":"OR","utah":"UT","idaho":"ID","kansas":"KS",
               "missouri":"MO","minnesota":"MN","wisconsin":"WI","ohio":"OH","indiana":"IN",
               "michigan":"MI","alabama":"AL","louisiana":"LA","virginia":"VA","maryland":"MD",
               "pennsylvania":"PA","new york":"NY","nebraska":"NE","wyoming":"WY"}

def _state_code(v) -> str:
    s = str(v or "").strip()
    if len(s) == 2:
        return s.upper()
    return _STATE_ABBR.get(s.lower(), s.upper())


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
                       pause_seconds: float = 3.0,
                       states: list | None = None) -> dict:
    """Enumerate scope and dispatch the sweep on a background thread.

    Only companies WITH an aptiq_property_id are eligible (the report is
    AptIQ-anchored). Companies with a run within max_age_days are skipped.
    """
    if _locks["red_light_v2"].locked():
        return {"status": "already_running"}

    companies = _enumerate_companies(["redlight_v2_run_date", "state"])
    if states:
        want = {str(x).strip().upper() for x in states}
        companies = [p for p in companies if _state_code(p.get("state")) in want]
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

    def _run_one_with_timeout(cid: str, timeout_s: int = 300) -> str:
        """Run one report in a worker thread with a hard timeout.

        A single property whose AptIQ bulk job never completes must not
        wedge the whole portfolio sweep (it did — the first full sweep
        produced 0 reports because company #1 hung). Timed-out threads are
        abandoned (daemon) and the sweep moves on.
        """
        import redlight_v2_run
        result = {"state": "timeout"}

        def _target():
            try:
                redlight_v2_run.run(company_id=cid)
                result["state"] = "ok"
            except Exception as e:
                result["state"] = f"fail: {e}"

        t = threading.Thread(target=_target, daemon=True)
        t.start()
        t.join(timeout_s)
        return result["state"]

    def _sweep():
        with _locks["red_light_v2"]:
            import datetime as _dt
            ok = fail = timed_out = 0
            st = _status["red_light_v2"]
            st.update({"build": BUILD, "started_at": _dt.datetime.utcnow().isoformat()+"Z",
                       "total": len(eligible), "i": 0, "ok": 0, "fail": 0,
                       "timeout": 0, "current": "", "done": False})
            logger.info("red-light-v2-batch: sweep starting, %d eligible", len(eligible))
            for i, cid in enumerate(eligible, 1):
                st.update({"i": i, "current": cid})
                if i <= 5 or i % 25 == 0:
                    logger.info("red-light-v2-batch: [%d/%d] running company %s",
                                i, len(eligible), cid)
                state = _run_one_with_timeout(cid)
                if state == "ok":
                    ok += 1
                elif state == "timeout":
                    timed_out += 1
                    logger.warning("red-light-v2-batch: %s TIMED OUT (300s) — skipped", cid)
                else:
                    fail += 1
                    logger.warning("red-light-v2-batch: %s %s", cid, state)
                st.update({"ok": ok, "fail": fail, "timeout": timed_out,
                           "last_result": state[:120]})
                time.sleep(pause_seconds)
            st["done"] = True
            logger.info("red-light-v2-batch DONE: %d ok, %d failed, %d timed out of %d",
                        ok, fail, timed_out, len(eligible))

    threading.Thread(target=_sweep, daemon=True).start()
    return {"status": "dispatched", "build": BUILD, "states": states or "all",
            "eligible": len(eligible),
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
            import datetime as _dt
            ok = fail = 0
            st = _status["forecast"]
            st.update({"build": BUILD, "started_at": _dt.datetime.utcnow().isoformat()+"Z",
                       "total": len(eligible), "i": 0, "done": False})
            for i, p in enumerate(eligible, 1):
                st.update({"i": i, "current": p.get("_company_id", "")})
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
            st.update({"ok": ok, "fail": fail, "done": True})
            logger.info("forecast-batch DONE: %d ok, %d failed of %d",
                        ok, fail, len(eligible))

    threading.Thread(target=_sweep, daemon=True).start()
    return {"status": "dispatched", "build": BUILD, "eligible": len(eligible),
            "skipped_no_uuid": skipped_no_uuid, "scope_count": len(companies)}
