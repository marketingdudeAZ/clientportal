"""STAGING-ONLY: CSV-based Apt IQ reader (replaces apartmentiq_client.py for tonight's pipeline).

Reads APT_IQ_DAILY_SHEET_URL (a daily CSV export), keys rows by `Property ID`,
caches the parsed result in-process so successive lookups are O(1). The CSV is
~27 MB so we pay the parse cost once per Render process lifetime.

Public API:
    get_property_row(property_id: str) -> dict | None
    get_all_rows() -> dict[str, dict]   # keyed by Property ID
    column_names() -> list[str]
    invalidate_cache()
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
PROPERTY_ID_COL = "Property ID"
_FETCH_TIMEOUT = 60

# Module-level cache; thread-safe load.
_lock = threading.Lock()
_cache: dict[str, dict] | None = None
_cache_loaded_at: float = 0.0
_columns: list[str] = []


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
