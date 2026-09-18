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
import sys
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

# The floor-plan columns the portal reads. The readers are
# workspace_views._floor_plans, workspace_signals.stale_inventory,
# reco_digital._plans_from_rows and mcp_context._floor_plans; the export has 30
# columns and the other 22 were retained forever for no reader at all.
#
# ADD A COLUMN HERE THE MOMENT A READER READS IT. Dropping it does not raise —
# the reader just sees None and reports nothing, which looks like a property
# with no rent rather than a bug. tests/test_aptiq_csv_client.py guards this by
# scanning the readers for column literals.
FLOOR_PLAN_COLUMNS = (
    PROPERTY_ID_COL, "Floor Plan Name", "Beds", "Baths", "Avg Sq Ft",
    "Available Units", "Days on Market", "Report Generation Date",
    # reco_digital reads this for asking_rent. It prefers "Avg Asking Rent",
    # "Asking Rent" and "Effective Rent" first; none of those exist in the
    # export today, so this is the one that actually answers.
    "Avg Rent",
)

# What makes two floor-plan rows the same plan. Identical to the identity
# workspace_views._floor_plans dedupes on, so this drops rows that every
# consumer was already dropping — one report date later in the same file.
FLOOR_PLAN_IDENTITY = ("Floor Plan Name", "Beds", "Baths", "Avg Sq Ft")

# A ceiling with headroom, not a target. Measured 2026-09-18 against the real
# export: 656,720 rows in, 264,269 unique out across 16,274 properties, median
# 12 plans per property and a long tail to 216. Crossing this means the export
# changed shape, and a gap in one panel beats an OOM that takes every
# property's page down with it.
FLOOR_PLAN_ROW_BUDGET = int(os.environ.get("APT_IQ_FLOOR_PLAN_ROW_BUDGET")
                            or 500000)

# Module-level cache; thread-safe load.
_lock = threading.Lock()
_cache: dict[str, dict] | None = None
_cache_loaded_at: float = 0.0
_columns: list[str] = []

# Floor-plan cache: Property ID -> list of floor-plan rows.
_fp_lock = threading.Lock()
_fp_cache: dict[str, list[dict]] | None = None
_fp_loaded_at: float = 0.0


class IncompleteExport(RuntimeError):
    """The transfer ended before the length the server promised."""


class SourceTooLarge(RuntimeError):
    """The export outgrew what this process will hold in memory.

    Raised instead of parsing on. A missing source degrades to a gap in the
    portal; a source that eats the instance takes every other property's page
    down with it, which is strictly worse.
    """


class _ChunkReader(io.RawIOBase):
    """A file-like view over an iterator of byte chunks.

    Two reasons this exists instead of handing `resp.raw` straight to
    TextIOWrapper: urllib3 closes the raw stream once the body is finished and
    the wrapper then reads once more, which raises "I/O operation on closed
    file" in the middle of a perfectly good parse; and `iter_content` is what
    applies the content decoding. A closed or exhausted upstream is EOF here,
    which is what it actually means.
    """

    def __init__(self, chunks, *, expect_bytes: int | None = None,
                 label: str = "csv"):
        self._chunks = chunks
        self._buf = b""
        self._read = 0
        self._expect = expect_bytes
        self._label = label

    def readable(self) -> bool:
        return True

    def _eof(self) -> int:
        """EOF, unless the body was short — then say so instead of truncating.

        A connection dropped mid-transfer would otherwise parse as "this export
        has fewer properties today", which is invisible. Only checked when the
        length is known and the body is not encoded, since `iter_content`
        decodes and the header describes the encoded size.
        """
        if self._expect is not None and self._read < self._expect:
            raise IncompleteExport(
                "%s: the export ended early — read %d of %d bytes"
                % (self._label, self._read, self._expect))
        return 0

    def readinto(self, target) -> int:  # noqa: D102
        while not self._buf:
            try:
                self._buf = next(self._chunks)
            except StopIteration:
                return self._eof()
            except ValueError:
                # urllib3 closes the raw stream once the body is done; that is
                # the normal end, not a failure.
                return self._eof()
        take = min(len(target), len(self._buf))
        target[:take] = self._buf[:take]
        self._buf = self._buf[take:]
        self._read += take
        return take


def _stream_reader(url: str, *, label: str):
    """Yield (header, row_iterator) for a CSV, never holding the whole file.

    The old code held it THREE times — `r.content`, the decoded `r.text`, and
    the UCS-4 copy inside `io.StringIO` — which measured 1.08 GB of peak on the
    169 MB floor-plan export before a single row was parsed. Streaming pays for
    one buffer instead.

    `newline=""` is what the csv module requires, and it is what keeps a quoted
    field containing a newline as one record: splitting on lines by hand (e.g.
    `iter_lines`) would silently tear those records in half.
    """
    t0 = time.time()
    resp = requests.get(url, timeout=_FETCH_TIMEOUT, allow_redirects=True,
                        stream=True)
    try:
        resp.raise_for_status()
        # Only trust Content-Length when the body is not encoded: iter_content
        # decodes, so for a gzipped export the header describes other bytes.
        expect = None
        if not (resp.headers.get("Content-Encoding") or "").strip():
            try:
                expect = int(resp.headers["Content-Length"])
            except (KeyError, TypeError, ValueError):
                expect = None
        raw = io.BufferedReader(
            _ChunkReader(resp.iter_content(64 * 1024), expect_bytes=expect,
                         label="apt_iq_csv_client[%s]" % label),
            buffer_size=64 * 1024)
        stream = io.TextIOWrapper(raw, encoding=resp.encoding or "utf-8",
                                  errors="replace", newline="")
        reader = csv.reader(stream)
        try:
            header = next(reader)
        except StopIteration:
            logger.warning("apt_iq_csv_client[%s]: the export is empty", label)
            return [], iter(())

        def rows():
            try:
                count = 0
                for row in reader:
                    count += 1
                    yield row
                logger.info("apt_iq_csv_client[%s]: streamed %d rows in %.1fs",
                            label, count, time.time() - t0)
            finally:
                resp.close()

        return header, rows()
    except Exception:
        resp.close()
        raise


def _load_csv() -> dict[str, dict]:
    """Fetch + parse the daily CSV. Returns dict keyed by Property ID (str).

    One row per property and every column kept, because the property snapshot
    is read all over the portal and there is no short list of columns to
    project to. The saving here is the streaming read, not the shape.
    """
    url = os.environ.get(CSV_URL_ENV, "")
    if not url:
        logger.warning("apt_iq_csv_client: %s not set", CSV_URL_ENV)
        return {}

    header, rows = _stream_reader(url, label="daily")
    if not header:
        return {}
    try:
        pid_at = header.index(PROPERTY_ID_COL)
    except ValueError:
        logger.error("apt_iq_csv_client: no %r column in the daily export",
                     PROPERTY_ID_COL)
        return {}

    # The header strings are shared by every row's dict rather than re-created
    # per row, and repeated values are interned: the same market name appears
    # in thousands of rows and there is no reason to store it thousands of
    # times.
    keys = [sys.intern(h) for h in header]
    width = len(keys)
    out: dict[str, dict] = {}
    for row in rows:
        if len(row) != width:
            row = (row + [""] * width)[:width]
        pid = row[pid_at].strip()
        if not pid:
            continue
        out[pid] = {k: (sys.intern(v) if len(v) <= 64 else v)
                    for k, v in zip(keys, row)}
    global _columns
    _columns = list(header)
    logger.info("apt_iq_csv_client: parsed %d properties (%d columns)",
                len(out), len(keys))
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

    The export carries one row per floor plan per property, and it has grown to
    169 MB / 656,720 rows. Loading it the obvious way measured a 2,488 MB peak
    and was killing the Render instance on every restart, which took the whole
    portal down with it — not just floor plans.

    Three things keep it small, and none of them change what a caller sees:

    * Streamed, so the file is never held whole (see `_stream_reader`).
    * Projected to the columns the portal actually reads. Everything else was
      being retained forever for no reader.
    * Deduplicated on the identity its consumers already dedupe on — plan name,
      beds, baths, size — keeping the FIRST row, exactly as
      `workspace_views._floor_plans` does. The export repeats a plan across
      report dates; those repeats were 20x the data and zero information.

    Past `FLOOR_PLAN_ROW_BUDGET` kept rows it raises SourceTooLarge rather than
    parse on. A gap in one panel is recoverable; an OOM is not.
    """
    url = os.environ.get(FLOOR_PLAN_URL_ENV, "")
    if not url:
        logger.warning("apt_iq_csv_client: %s not set", FLOOR_PLAN_URL_ENV)
        return {}

    header, rows = _stream_reader(url, label="floor_plan")
    if not header:
        return {}
    index = {name: i for i, name in enumerate(header)}
    if PROPERTY_ID_COL not in index:
        logger.error("apt_iq_csv_client[floor_plan]: no %r column in the export",
                     PROPERTY_ID_COL)
        return {}
    # Keep what exists; a column the export drops must not crash the load.
    keep = [(sys.intern(name), index[name]) for name in FLOOR_PLAN_COLUMNS
            if name in index]
    missing = [name for name in FLOOR_PLAN_COLUMNS if name not in index]
    if missing:
        logger.warning("apt_iq_csv_client[floor_plan]: export is missing %s",
                       ", ".join(missing))
    pid_at = index[PROPERTY_ID_COL]
    id_at = [index[name] for name in FLOOR_PLAN_IDENTITY if name in index]
    width = len(header)

    out: dict[str, list[dict]] = {}
    seen: dict[str, set] = {}
    kept = skipped = 0
    for row in rows:
        if len(row) != width:
            row = (row + [""] * width)[:width]
        pid = row[pid_at].strip()
        if not pid:
            continue
        ident = tuple(row[i].strip() for i in id_at)
        marks = seen.setdefault(pid, set())
        if ident in marks:
            skipped += 1
            continue
        marks.add(ident)
        kept += 1
        if kept > FLOOR_PLAN_ROW_BUDGET:
            raise SourceTooLarge(
                "the AptIQ floor-plan export exceeded %d unique rows; refusing "
                "to hold it in memory" % FLOOR_PLAN_ROW_BUDGET)
        out.setdefault(pid, []).append(
            {name: (sys.intern(row[i]) if len(row[i]) <= 64 else row[i])
             for name, i in keep})
    logger.info("apt_iq_csv_client[floor_plan]: parsed %d properties, %d unique "
                "rows (%d duplicate rows skipped)", len(out), kept, skipped)
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
