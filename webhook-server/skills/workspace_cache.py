"""Serve-stale caches for the portfolio-wide sources the workspace reads.

Phase 2 amendment 3. Three sources are expensive to build and shared by every
property: the spend sheet (every deal and line item in the portfolio), the AptIQ
daily export (~27 MB) and the AptIQ floor-plan export. Their own modules either
rebuild synchronously when the TTL lapses (`spend_sheet`, `portfolio`) or never
refresh at all (`apt_iq_csv_client` caches for the life of the process).

Here each one is read as:

* warm and fresh  → return it;
* warm but stale  → return the last good copy now and refresh on a background
                    thread (single-flight: one refresh per source at a time);
* cold            → build synchronously, because there is nothing to serve.

`warm()` builds all of them. `POST /api/internal/workspace/warm` calls it before
a demo and from cron, so no user request pays the cold build. Every payload
built from these carries an `as_of`, so staleness is visible, not hidden.

The source modules stay the single implementation of each build; this module
only decides when to call them and holds the last good copy.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Callable

from skills import workspace_common as wc

logger = logging.getLogger(__name__)

SPEND_TTL = float(os.environ.get("WORKSPACE_SPEND_TTL", "1800"))
APTIQ_TTL = float(os.environ.get("WORKSPACE_APTIQ_TTL", "21600"))
PORTFOLIO_TTL = float(os.environ.get("WORKSPACE_PORTFOLIO_TTL", "900"))


class SourceUnavailable(RuntimeError):
    """The source is not configured; there is nothing to build or serve."""


class _Entry:
    def __init__(self, name: str, ttl: float, build: Callable[[], Any]):
        self.name = name
        self.ttl = ttl
        self.build = build
        self.value: Any = None
        self.built_at: float | None = None
        self.refreshing = False
        self.lock = threading.Lock()
        # Held for the LENGTH of a build, where `lock` only guards the fields.
        # Without it every thread that arrives cold starts its own build.
        self.build_lock = threading.Lock()
        self.last_error: str | None = None

    def _refresh(self) -> None:
        try:
            value = self.build()
            with self.lock:
                self.value, self.built_at = value, time.time()
                self.last_error = None
        except Exception as exc:  # noqa: BLE001 — keep serving the last good copy
            logger.warning("workspace cache: %s refresh failed: %s", self.name, exc)
            with self.lock:
                self.last_error = f"{type(exc).__name__}: {str(exc)[:200]}"
        finally:
            with self.lock:
                self.refreshing = False

    def get(self) -> tuple[Any, str | None]:
        with self.lock:
            value, built_at = self.value, self.built_at
            stale = built_at is None or (time.time() - built_at) >= self.ttl
            start_bg = value is not None and stale and not self.refreshing
            if start_bg:
                self.refreshing = True
        if value is None:
            self._build_now()
            with self.lock:
                if self.value is None:
                    raise RuntimeError(self.last_error or f"{self.name} could not be built")
                return self.value, wc.to_iso_ts(self.built_at)
        if start_bg:
            threading.Thread(target=self._refresh, name=f"ws-cache-{self.name}", daemon=True).start()
        return value, wc.to_iso_ts(built_at)

    def _build_now(self) -> None:
        """Build once, however many threads arrive cold at the same moment.

        `_refresh()` runs outside `lock`, so a second thread used to find
        `value` still None and start a build of its own — up to 16 of them on
        waitress, each rebuilding the 27MB AptIQ CSV or the full spend sheet.
        Late arrivals now wait for the first build and read its result.
        """
        with self.build_lock:
            with self.lock:
                if self.value is not None:
                    return
                self.refreshing = True
            self._refresh()

    def warm(self) -> dict:
        t0 = time.time()
        # Same lock as the cold path: the boot warm and the first request that
        # beats it must not build the same source twice.
        with self.build_lock:
            with self.lock:
                self.refreshing = True
            self._refresh()
        return {"source": self.name, "ok": self.last_error is None and self.value is not None,
                "seconds": round(time.time() - t0, 2), "error": self.last_error,
                "as_of": wc.to_iso_ts(self.built_at)}

    def clear(self) -> None:
        with self.lock:
            self.value, self.built_at, self.refreshing, self.last_error = None, None, False, None


# ── builders (delegate to the source modules) ────────────────────────────────

def _build_spend() -> list:
    import spend_sheet
    return spend_sheet.get_spend_sheet_data(force=True)


def _build_aptiq_daily() -> dict:
    if not os.environ.get("APT_IQ_DAILY_SHEET_URL"):
        raise SourceUnavailable("APT_IQ_DAILY_SHEET_URL is not set")
    from services.fluency_ingestion import apt_iq_csv_client as csv
    rows = csv._load_csv()
    # Hand the fresh copy to the module too, so its other callers see it.
    with csv._lock:
        csv._cache, csv._cache_loaded_at = rows, time.time()
    return rows


def _build_aptiq_floor_plans() -> dict:
    if not os.environ.get("APT_IQ_FLOOR_PLAN_SHEET_URL"):
        raise SourceUnavailable("APT_IQ_FLOOR_PLAN_SHEET_URL is not set")
    from services.fluency_ingestion import apt_iq_csv_client as csv
    rows = csv._load_floor_plan_csv()
    with csv._fp_lock:
        csv._fp_cache, csv._fp_loaded_at = rows, time.time()
    return rows


def _build_portfolio() -> list:
    import portfolio
    portfolio._portfolio_cache.pop("all_properties", None)
    return portfolio.fetch_portfolio("workspace-cache", "marketing_rvp") or []


SPEND = _Entry("spend_sheet", SPEND_TTL, _build_spend)
APTIQ_DAILY = _Entry("aptiq_daily", APTIQ_TTL, _build_aptiq_daily)
APTIQ_FLOOR_PLANS = _Entry("aptiq_floor_plans", APTIQ_TTL, _build_aptiq_floor_plans)
PORTFOLIO = _Entry("portfolio", PORTFOLIO_TTL, _build_portfolio)
ENTRIES = (SPEND, APTIQ_DAILY, APTIQ_FLOOR_PLANS, PORTFOLIO)


def _get(entry: _Entry):
    try:
        return entry.get()
    except SourceUnavailable:
        raise
    except RuntimeError as exc:
        if entry.last_error and entry.last_error.startswith("SourceUnavailable"):
            raise SourceUnavailable(entry.last_error.split(": ", 1)[-1]) from exc
        raise


# ── public readers ───────────────────────────────────────────────────────────

def spend_rows() -> tuple[list, str | None]:
    return _get(SPEND)


def aptiq_daily() -> tuple[dict, str | None]:
    return _get(APTIQ_DAILY)


def aptiq_floor_plans() -> tuple[dict, str | None]:
    return _get(APTIQ_FLOOR_PLANS)


def portfolio_rows() -> tuple[list, str | None]:
    return _get(PORTFOLIO)


def monthly_spend(company_id: str) -> tuple[dict, str | None]:
    """spend_sheet.get_company_monthly_spend's result, from the cached rows.

    Same SKU columns and the same sum (tests pin the two against each other),
    plus `zero_skus`: line items present at $0, which Plan & Spend reports as
    ended channels.
    """
    import spend_sheet

    rows, as_of = spend_rows()
    cid = str(company_id)
    row = next((r for r in rows if str(r.get("company_id")) == cid), None)
    if not row:
        return {"company_id": cid, "total": 0.0, "by_sku": {}, "zero_skus": [],
                "deal_id": None, "deal_name": None}, as_of
    by_sku: dict = {}
    zero: list = []
    total = 0.0
    for key in spend_sheet._SPEND_COLUMN_KEYS:
        val = row.get(key)
        if val is None:
            continue
        try:
            amt = float(val)
        except (TypeError, ValueError):
            continue
        if amt > 0:
            by_sku[key] = amt
            total += amt
        elif amt == 0:
            zero.append(key)
    return {"company_id": cid, "total": round(total, 2), "by_sku": by_sku, "zero_skus": zero,
            "deal_id": row.get("deal_id"), "deal_name": row.get("deal_name")}, as_of


def warm() -> dict:
    """Build every cache now. Sources that are not configured are reported, not
    failed."""
    results = [e.warm() for e in ENTRIES]
    return {"warmed": results, "ok": all(r["ok"] or (r["error"] or "").startswith("SourceUnavailable")
                                         for r in results)}


def clear() -> None:
    for e in ENTRIES:
        e.clear()
