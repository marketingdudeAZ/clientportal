"""STAGING-ONLY: CSV-based Apt IQ reader (replaces apartmentiq_client.py for tonight's pipeline).

Reads APT_IQ_DAILY_SHEET_URL (a daily CSV export), keys rows by `Property ID`,
caches the parsed result in-process so successive lookups are O(1). The CSV is
~27 MB so we pay the parse cost once per Render process lifetime.

Public API:
    get_property_row(property_id: str) -> dict | None
    get_all_rows() -> dict[str, dict]   # keyed by Property ID
    column_names() -> list[str]
    invalidate_cache()

    # Floor-plan report (report_type=floor_plan) — many rows per property:
    get_floor_plan_rows(property_id: str) -> list[dict]
    invalidate_floor_plan_cache()
"""

from __future__ import annotations

import csv
import io
import logging
import os
import threading
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

CSV_URL_ENV = "APT_IQ_DAILY_SHEET_URL"
# Floor-plan report export (report_type=floor_plan). One row PER floor plan,
# so many rows share a Property ID — grouped into lists, not a flat dict.
FLOOR_PLAN_URL_ENV = "APT_IQ_FLOOR_PLAN_SHEET_URL"
PROPERTY_ID_COL = "Property ID"
_FETCH_TIMEOUT = 120

# Module-level cache; thread-safe load.
_lock = threading.Lock()
_cache: dict[str, dict] | None = None
_cache_loaded_at: float = 0.0
_columns: list[str] = []

# Floor-plan cache: Property ID -> list of floor-plan rows.
_fp_lock = threading.Lock()
_fp_cache: dict[str, list[dict]] | None = None
_fp_loaded_at: float = 0.0


def _load_csv() -> dict[str, dict]:
    """Fetch + parse the daily CSV. Returns dict keyed by Property ID (str)."""
    url = os.environ.get(CSV_URL_ENV, "")
    if not url:
        logger.warning("apt_iq_csv_client: %s not set", CSV_URL_ENV)
        return {}

    t0 = time.time()
    r = requests.get(url, timeout=_FETCH_TIMEOUT, allow_redirects=True)
    r.raise_for_status()
    text = r.text
    logger.info("apt_iq_csv_client: fetched %d bytes in %.1fs",
                len(r.content), time.time() - t0)

    reader = csv.DictReader(io.StringIO(text))
    out: dict[str, dict] = {}
    cols: list[str] = []
    if reader.fieldnames:
        cols = list(reader.fieldnames)
    for row in reader:
        pid = (row.get(PROPERTY_ID_COL) or "").strip()
        if not pid:
            continue
        out[pid] = row
    global _columns
    _columns = cols
    logger.info("apt_iq_csv_client: parsed %d properties (%d columns)",
                len(out), len(cols))
    return out


def _ensure_loaded() -> dict[str, dict]:
    global _cache, _cache_loaded_at
    if _cache is not None:
        return _cache
    with _lock:
        if _cache is None:
            _cache = _load_csv()
            _cache_loaded_at = time.time()
    return _cache


def get_property_row(property_id: str) -> dict | None:
    """Return one CSV row by Property ID, or None if missing."""
    pid = (property_id or "").strip()
    if not pid:
        return None
    rows = _ensure_loaded()
    return rows.get(pid)


def get_all_rows() -> dict[str, dict]:
    """Return all rows (cached). Keyed by Property ID string."""
    return _ensure_loaded()


def column_names() -> list[str]:
    """Return CSV header column names (loads cache if not yet loaded)."""
    _ensure_loaded()
    return list(_columns)


def invalidate_cache() -> None:
    """Force the next call to re-fetch the CSV from APT_IQ_DAILY_SHEET_URL."""
    global _cache, _cache_loaded_at
    with _lock:
        _cache = None
        _cache_loaded_at = 0.0


# ── Floor-plan report (report_type=floor_plan) ──────────────────────────────


def _load_floor_plan_csv() -> dict[str, list[dict]]:
    """Fetch + parse the floor-plan CSV, grouping rows by Property ID.

    The floor-plan export carries one row per floor plan (Floor Plan Name,
    Beds, Baths, Avg Sq Ft, Unit Mix counts, ...). Many rows share a
    Property ID, so we return Property ID -> [rows].
    """
    url = os.environ.get(FLOOR_PLAN_URL_ENV, "")
    if not url:
        logger.warning("apt_iq_csv_client: %s not set", FLOOR_PLAN_URL_ENV)
        return {}

    t0 = time.time()
    r = requests.get(url, timeout=_FETCH_TIMEOUT, allow_redirects=True)
    r.raise_for_status()
    text = r.text
    logger.info("apt_iq_csv_client[floor_plan]: fetched %d bytes in %.1fs",
                len(r.content), time.time() - t0)

    reader = csv.DictReader(io.StringIO(text))
    out: dict[str, list[dict]] = {}
    for row in reader:
        pid = (row.get(PROPERTY_ID_COL) or "").strip()
        if not pid:
            continue
        out.setdefault(pid, []).append(row)
    logger.info("apt_iq_csv_client[floor_plan]: parsed %d properties (%d rows)",
                len(out), sum(len(v) for v in out.values()))
    return out


def _ensure_fp_loaded() -> dict[str, list[dict]]:
    global _fp_cache, _fp_loaded_at
    if _fp_cache is not None:
        return _fp_cache
    with _fp_lock:
        if _fp_cache is None:
            _fp_cache = _load_floor_plan_csv()
            _fp_loaded_at = time.time()
    return _fp_cache


def get_floor_plan_rows(property_id: str) -> list[dict]:
    """Return all floor-plan rows for a Property ID (empty list if none)."""
    pid = (property_id or "").strip()
    if not pid:
        return []
    return _ensure_fp_loaded().get(pid, [])


def invalidate_floor_plan_cache() -> None:
    """Force the next call to re-fetch the floor-plan CSV."""
    global _fp_cache, _fp_loaded_at
    with _fp_lock:
        _fp_cache = None
        _fp_loaded_at = 0.0
