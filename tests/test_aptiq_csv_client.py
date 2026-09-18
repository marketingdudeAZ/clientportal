"""The AptIQ CSV reader, which took the production service down on 18 Sept 2026.

The floor-plan export grew to 169 MB / 656,720 rows. Loading it the obvious way
held the file three times over (`r.content`, `r.text`, and StringIO's UCS-4
copy) and then retained a 30-column dict per row forever: a measured 2,488 MB
peak, which OOM-killed the Render instance on every restart and took the whole
portal with it — every property's page, not just floor plans.

This file had no tests at all before that, which is how it shipped. What these
pin is the shape of the fix, and the column projection in particular: dropping
a column a reader reads does NOT raise, it just hands that reader None, which
reads as "this property has no rent" rather than as a bug.
"""
from __future__ import annotations

import io
import os
import re
import sys

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "webhook-server"))
sys.path.insert(0, ROOT)

from services.fluency_ingestion import apt_iq_csv_client as c  # noqa: E402

FP_URL = "https://example.invalid/floorplans.csv"
DAILY_URL = "https://example.invalid/daily.csv"

# The real floor-plan export's header, as observed on 2026-09-18. The guard test
# at the bottom uses it to decide whether a reader reads a column we drop.
REAL_FP_HEADER = [
    "Report Generation Date", "Market Name", "Market ID", "Property",
    "Property ID", "Property URL", "Date Tracking Started", "Address", "City",
    "State", "Zip", "Latitude", "Longitude", "Unit Count", "Year Built",
    "Year Renovated", "Floor Plan Name", "Beds", "Baths", "Avg Sq Ft",
    "Unit Mix: Total Units", "Unit Mix", "Available Units", "Days on Market",
    "Avg Rent", "Avg Rent/Sq Ft", "Avg NER", "Avg NER/Sq Ft",
    "Rent Trends (Last  7d)", "Management Company",
]


class FakeResponse:
    """A streaming response. Touching .text or .content fails the test: holding
    the body whole is the bug this module was rewritten to avoid."""

    def __init__(self, body: bytes, *, chunk: int = 7, status: int = 200,
                 headers=None, close_early_after: int | None = None):
        self._body = body
        self._chunk = chunk
        self.status_code = status
        self.encoding = "utf-8"
        self.headers = headers if headers is not None else {
            "Content-Length": str(len(body))}
        self._close_early_after = close_early_after
        self.closed = False

    # What the code under test is allowed to use ---------------------------
    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError("HTTP %d" % self.status_code)

    def iter_content(self, size=None):
        size = self._chunk if not size else min(size, self._chunk)
        sent = 0
        for start in range(0, len(self._body), size):
            if (self._close_early_after is not None
                    and sent >= self._close_early_after):
                # What urllib3 does when the connection goes away.
                raise ValueError("I/O operation on closed file.")
            piece = self._body[start:start + size]
            sent += len(piece)
            yield piece

    def close(self):
        self.closed = True

    # What it must NOT use -------------------------------------------------
    @property
    def text(self):
        raise AssertionError("read .text — that holds the whole export in memory")

    @property
    def content(self):
        raise AssertionError("read .content — that holds the whole export in memory")

    @property
    def raw(self):
        raise AssertionError("used .raw — urllib3 closes it at body end mid-parse")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.setenv(c.FLOOR_PLAN_URL_ENV, FP_URL)
    monkeypatch.setenv(c.CSV_URL_ENV, DAILY_URL)
    c.invalidate_cache()
    c.invalidate_floor_plan_cache()
    yield
    c.invalidate_cache()
    c.invalidate_floor_plan_cache()


def serve(monkeypatch, body: str, **kw):
    resp = FakeResponse(body.encode("utf-8"), **kw)
    monkeypatch.setattr(c.requests, "get", lambda url, **kwargs: resp)
    return resp


def fp_csv(rows, header=None):
    header = header or ["Property ID", "Floor Plan Name", "Beds", "Baths",
                        "Avg Sq Ft", "Available Units", "Days on Market",
                        "Report Generation Date", "Avg Rent", "Market Name"]
    out = [",".join(header)]
    out.extend(",".join(str(v) for v in r) for r in rows)
    return "\r\n".join(out) + "\r\n"


# --- streaming -------------------------------------------------------------

class TestStreaming:
    def test_the_body_is_never_held_whole(self, monkeypatch):
        """FakeResponse raises on .text/.content/.raw, so this passing is the
        assertion: the 1.08 GB of copies is gone."""
        serve(monkeypatch, fp_csv([["p1", "A1", 1, 1, 700, 2, 40, "2026-09-17", 1500, "Phoenix"]]))
        out = c._load_floor_plan_csv()
        assert list(out) == ["p1"]

    def test_a_row_split_across_chunk_boundaries_survives(self, monkeypatch):
        """One byte at a time: if the reader assembled records by chunk instead
        of by CSV record, this would tear rows apart."""
        rows = [["p%d" % i, "Plan%d" % i, 1, 1, 700 + i, i, 30 + i,
                 "2026-09-17", 1500 + i, "Phoenix"] for i in range(12)]
        serve(monkeypatch, fp_csv(rows), chunk=1)
        out = c._load_floor_plan_csv()
        assert len(out) == 12
        assert out["p7"][0]["Floor Plan Name"] == "Plan7"

    def test_a_quoted_newline_stays_one_record(self, monkeypatch):
        """The reason for newline="": a plan name containing a line break is one
        row. Hand-splitting on lines would make it two, and the second would be
        garbage attributed to no property."""
        body = ('Property ID,Floor Plan Name,Beds,Baths,Avg Sq Ft,Available Units,'
                'Days on Market,Report Generation Date,Avg Rent\r\n'
                'p1,"The\nPiedmont",1,1,700,2,40,2026-09-17,1500\r\n')
        serve(monkeypatch, body, chunk=3)
        out = c._load_floor_plan_csv()
        assert out["p1"][0]["Floor Plan Name"] == "The\nPiedmont"
        assert len(out) == 1

    def test_the_connection_is_closed_when_the_parse_finishes(self, monkeypatch):
        resp = serve(monkeypatch, fp_csv([["p1", "A", 1, 1, 700, 1, 10, "2026-09-17", 1500, "Phoenix"]]))
        c._load_floor_plan_csv()
        assert resp.closed is True

    def test_a_short_body_is_loud_not_a_quiet_truncation(self, monkeypatch):
        """A dropped connection would otherwise parse as "fewer properties
        today", which nobody would notice."""
        body = fp_csv([["p%d" % i, "A", 1, 1, 700, 1, 10, "2026-09-17", 1500, "Phoenix"]
                       for i in range(50)])
        serve(monkeypatch, body, chunk=64, close_early_after=200)
        with pytest.raises(c.IncompleteExport) as exc:
            c._load_floor_plan_csv()
        assert "ended early" in str(exc.value)

    def test_an_unknown_length_cannot_be_checked_so_it_is_accepted(self, monkeypatch):
        """Chunked transfer has no Content-Length. Refusing those would make the
        reader depend on a header the server need not send."""
        body = fp_csv([["p1", "A", 1, 1, 700, 1, 10, "2026-09-17", 1500, "Phoenix"]])
        serve(monkeypatch, body, headers={})
        assert list(c._load_floor_plan_csv()) == ["p1"]

    def test_a_gzipped_bodys_length_header_is_not_used_as_a_row_check(self, monkeypatch):
        """iter_content decodes, so Content-Length describes other bytes; using
        it would fail every gzipped export."""
        body = fp_csv([["p1", "A", 1, 1, 700, 1, 10, "2026-09-17", 1500, "Phoenix"]])
        serve(monkeypatch, body, headers={"Content-Encoding": "gzip",
                                          "Content-Length": "17"})
        assert list(c._load_floor_plan_csv()) == ["p1"]

    def test_an_empty_export_is_empty_not_an_exception(self, monkeypatch):
        serve(monkeypatch, "")
        assert c._load_floor_plan_csv() == {}

    def test_no_url_configured_returns_nothing(self, monkeypatch):
        monkeypatch.delenv(c.FLOOR_PLAN_URL_ENV, raising=False)
        assert c._load_floor_plan_csv() == {}


# --- what is kept ----------------------------------------------------------

class TestProjection:
    def test_only_the_declared_columns_are_retained(self, monkeypatch):
        serve(monkeypatch, fp_csv([["p1", "A1", 1, 1, 700, 2, 40, "2026-09-17", 1500, "Phoenix"]]))
        row = c._load_floor_plan_csv()["p1"][0]
        assert set(row) == set(c.FLOOR_PLAN_COLUMNS)
        assert "Market Name" not in row          # 22 columns had no reader

    def test_the_values_are_unchanged_by_the_projection(self, monkeypatch):
        serve(monkeypatch, fp_csv([["p1", "The Piedmont", 0, 1.5, 792, 3, 61,
                                    "2026-09-17", 1495, "Phoenix"]]))
        row = c._load_floor_plan_csv()["p1"][0]
        assert row["Floor Plan Name"] == "The Piedmont"
        assert row["Beds"] == "0" and row["Baths"] == "1.5"
        assert row["Avg Sq Ft"] == "792" and row["Available Units"] == "3"
        assert row["Days on Market"] == "61" and row["Avg Rent"] == "1495"

    def test_a_column_the_export_stops_sending_does_not_crash_the_load(self, monkeypatch):
        header = ["Property ID", "Floor Plan Name", "Beds", "Baths",
                  "Avg Sq Ft", "Available Units", "Report Generation Date"]
        serve(monkeypatch, fp_csv([["p1", "A", 1, 1, 700, 2, "2026-09-17"]], header))
        row = c._load_floor_plan_csv()["p1"][0]
        assert "Days on Market" not in row
        assert row["Floor Plan Name"] == "A"

    def test_no_property_id_column_is_refused_rather_than_miskeyed(self, monkeypatch):
        serve(monkeypatch, "Floor Plan Name,Beds\r\nA,1\r\n")
        assert c._load_floor_plan_csv() == {}

    def test_ragged_rows_do_not_crash(self, monkeypatch):
        body = ("Property ID,Floor Plan Name,Beds,Baths,Avg Sq Ft,Available Units,"
                "Days on Market,Report Generation Date,Avg Rent\r\n"
                "p1,A,1\r\n"                                     # short
                "p2,B,2,2,900,4,30,2026-09-17,1700,extra,more\r\n")  # long
        serve(monkeypatch, body)
        out = c._load_floor_plan_csv()
        assert out["p1"][0]["Beds"] == "1" and out["p1"][0]["Avg Sq Ft"] == ""
        assert out["p2"][0]["Avg Rent"] == "1700"

    def test_rows_with_no_property_id_are_skipped(self, monkeypatch):
        serve(monkeypatch, fp_csv([["", "A", 1, 1, 700, 2, 40, "2026-09-17", 1500, "Phoenix"],
                                   ["p1", "B", 1, 1, 700, 2, 40, "2026-09-17", 1500, "Phoenix"]]))
        assert list(c._load_floor_plan_csv()) == ["p1"]


class TestDedupe:
    def test_a_plan_repeated_across_report_dates_is_kept_once(self, monkeypatch):
        """The export repeats each plan; 656,720 rows deduped to 264,269. Every
        consumer already dropped these, so this changes no output."""
        serve(monkeypatch, fp_csv([
            ["p1", "A1", 1, 1, 700, 2, 40, "2026-09-17", 1500, "Phoenix"],
            ["p1", "A1", 1, 1, 700, 9, 99, "2026-09-16", 1400, "Phoenix"],
            ["p1", "A2", 2, 2, 900, 1, 20, "2026-09-17", 1900, "Phoenix"],
        ]))
        plans = c._load_floor_plan_csv()["p1"]
        assert [p["Floor Plan Name"] for p in plans] == ["A1", "A2"]

    def test_the_first_row_wins_exactly_as_the_readers_do(self, monkeypatch):
        """workspace_views._floor_plans keeps the first of a repeated identity.
        Keeping the last instead would change published numbers."""
        serve(monkeypatch, fp_csv([
            ["p1", "A1", 1, 1, 700, 2, 40, "2026-09-17", 1500, "Phoenix"],
            ["p1", "A1", 1, 1, 700, 9, 99, "2026-09-16", 1400, "Phoenix"],
        ]))
        row = c._load_floor_plan_csv()["p1"][0]
        assert row["Available Units"] == "2" and row["Days on Market"] == "40"

    def test_plans_differing_only_in_size_are_two_plans(self, monkeypatch):
        serve(monkeypatch, fp_csv([
            ["p1", "A1", 1, 1, 700, 2, 40, "2026-09-17", 1500, "Phoenix"],
            ["p1", "A1", 1, 1, 750, 2, 40, "2026-09-17", 1550, "Phoenix"],
        ]))
        assert len(c._load_floor_plan_csv()["p1"]) == 2

    def test_dedupe_is_per_property_not_global(self, monkeypatch):
        """Two properties sharing a plan name is normal; collapsing across them
        would delete one property's inventory."""
        serve(monkeypatch, fp_csv([
            ["p1", "A1", 1, 1, 700, 2, 40, "2026-09-17", 1500, "Phoenix"],
            ["p2", "A1", 1, 1, 700, 5, 10, "2026-09-17", 1600, "Phoenix"],
        ]))
        out = c._load_floor_plan_csv()
        assert out["p1"][0]["Available Units"] == "2"
        assert out["p2"][0]["Available Units"] == "5"


class TestBudget:
    def test_an_export_past_the_budget_is_refused_not_absorbed(self, monkeypatch):
        """The whole point: a gap in one panel beats an OOM that takes every
        property's page down."""
        monkeypatch.setattr(c, "FLOOR_PLAN_ROW_BUDGET", 3)
        serve(monkeypatch, fp_csv([
            ["p%d" % i, "A", 1, 1, 700, 1, 10, "2026-09-17", 1500, "Phoenix"]
            for i in range(10)]))
        with pytest.raises(c.SourceTooLarge) as exc:
            c._load_floor_plan_csv()
        assert "refusing to hold it in memory" in str(exc.value)

    def test_the_budget_counts_unique_rows_not_raw_ones(self, monkeypatch):
        """Duplicates are free, so a chattier export does not trip the ceiling."""
        monkeypatch.setattr(c, "FLOOR_PLAN_ROW_BUDGET", 3)
        serve(monkeypatch, fp_csv([
            ["p1", "A1", 1, 1, 700, 2, 40, "2026-09-1%d" % (i % 9), 1500, "Phoenix"]
            for i in range(30)]))
        assert len(c._load_floor_plan_csv()["p1"]) == 1

    def test_the_real_export_fits_under_the_shipped_budget(self):
        """264,269 unique rows measured on 2026-09-18, with the ceiling at
        500,000. If this ever inverts, the ceiling is the bug."""
        assert c.FLOOR_PLAN_ROW_BUDGET >= 300000


class TestDailyExport:
    def test_it_keys_by_property_id_and_keeps_every_column(self, monkeypatch):
        """No projection here: the property snapshot is read all over the portal
        and there is no short list to project to."""
        body = ("Property ID,Occupancy,Exposure % (Next 30d),Market Name\r\n"
                "p1,0.94,0.07,Phoenix\r\np2,0.88,0.11,Dallas\r\n")
        serve(monkeypatch, body)
        rows = c._load_csv()
        assert set(rows) == {"p1", "p2"}
        assert rows["p1"]["Exposure % (Next 30d)"] == "0.07"
        assert rows["p2"]["Market Name"] == "Dallas"

    def test_the_header_is_published_for_callers(self, monkeypatch):
        serve(monkeypatch, "Property ID,Occupancy\r\np1,0.94\r\n")
        c._load_csv()
        c._cache = {"p1": {}}          # column_names() must not re-fetch
        assert c.column_names() == ["Property ID", "Occupancy"]

    def test_a_later_row_for_the_same_property_wins(self, monkeypatch):
        """One row per property is the contract; last-write-wins matches the
        previous DictReader behavior exactly."""
        serve(monkeypatch, "Property ID,Occupancy\r\np1,0.90\r\np1,0.95\r\n")
        assert c._load_csv()["p1"]["Occupancy"] == "0.95"

    def test_no_property_id_column_returns_nothing(self, monkeypatch):
        serve(monkeypatch, "Occupancy\r\n0.94\r\n")
        assert c._load_csv() == {}


class TestCaching:
    def test_the_export_is_fetched_once_per_process(self, monkeypatch):
        calls = []
        body = fp_csv([["p1", "A", 1, 1, 700, 1, 10, "2026-09-17", 1500, "Phoenix"]])

        def counted(url, **kwargs):
            calls.append(url)
            return FakeResponse(body.encode())

        monkeypatch.setattr(c.requests, "get", counted)
        c.get_floor_plan_rows("p1")
        c.get_floor_plan_rows("p1")
        c.get_floor_plan_rows("p2")
        assert len(calls) == 1

    def test_a_missing_property_is_an_empty_list_not_an_error(self, monkeypatch):
        serve(monkeypatch, fp_csv([["p1", "A", 1, 1, 700, 1, 10, "2026-09-17", 1500, "Phoenix"]]))
        assert c.get_floor_plan_rows("nope") == []

    def test_invalidating_forces_a_refetch(self, monkeypatch):
        calls = []
        body = fp_csv([["p1", "A", 1, 1, 700, 1, 10, "2026-09-17", 1500, "Phoenix"]])
        monkeypatch.setattr(c.requests, "get",
                            lambda url, **kw: calls.append(url) or FakeResponse(body.encode()))
        c.get_floor_plan_rows("p1")
        c.invalidate_floor_plan_cache()
        c.get_floor_plan_rows("p1")
        assert len(calls) == 2


# --- the guard -------------------------------------------------------------

READERS = ("skills/workspace_views.py", "skills/workspace_signals.py",
           "skills/reco_digital.py", "skills/mcp_context.py")


def test_every_column_a_reader_reads_is_kept():
    """The projection fails SILENTLY: a dropped column hands the reader None,
    which renders as "no rent for this property" rather than as an error.

    This scans the readers for quoted strings that are real export columns and
    asserts each one survives the projection. It caught `Avg Rent`, which
    reco_digital reads for asking_rent and the first version of this change
    dropped.
    """
    known = set(REAL_FP_HEADER)
    kept = set(c.FLOOR_PLAN_COLUMNS)
    missed = {}
    for rel in READERS:
        path = os.path.join(ROOT, "webhook-server", rel)
        if not os.path.exists(path):
            continue
        source = open(path, encoding="utf-8").read()
        for literal in set(re.findall(r'"([^"\n]{3,40})"', source)):
            if literal in known and literal not in kept:
                missed.setdefault(literal, []).append(rel)
    assert not missed, (
        "These readers read floor-plan columns the projection drops, so they "
        "silently get None:\n  "
        + "\n  ".join("%s  <- %s" % (col, ", ".join(files))
                      for col, files in sorted(missed.items()))
        + "\n\nAdd them to FLOOR_PLAN_COLUMNS in apt_iq_csv_client.py.")


def test_the_dedupe_identity_is_a_subset_of_the_kept_columns():
    """Deduping on a column we then throw away would be silently arbitrary."""
    assert set(c.FLOOR_PLAN_IDENTITY) <= set(c.FLOOR_PLAN_COLUMNS)
