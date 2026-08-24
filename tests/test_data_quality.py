"""Tests for skills.data_quality.

Fixtures are real rows from the warehouse, not invented ones. Every case here
corresponds to something that reached a conclusion before being caught during
the Atwood/Henry investigations (2026-08-19..24). If one of these regresses,
a wrong number ships.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

from skills import data_quality as dq  # noqa: E402


# ── real rows ───────────────────────────────────────────────────────────────

# The five property-months carrying 22,941 leads that cannot exist.
IMPOSSIBLE = [
    {"nid": "prose_cartersville", "month": "2026-04", "sessions": 4_169, "leads": 6_590},
    {"nid": "courtney_isles", "month": "2026-05", "sessions": 2_971, "leads": 6_430},
    {"nid": "340_grand", "month": "2026-04", "sessions": 10_692, "leads": 4_363},
    {"nid": "clara_broadstone", "month": "2026-04", "sessions": 6_360, "leads": 2_699},
    {"nid": "clara_broadstone", "month": "2026-05", "sessions": 6_913, "leads": 2_859},
]

# The Atwood's real series — all plausible, must survive untouched.
ATWOOD = [
    {"nid": "10320682", "month": "2026-01", "sessions": 3_585, "leads": 76},
    {"nid": "10320682", "month": "2026-02", "sessions": 3_454, "leads": 84},
    {"nid": "10320682", "month": "2026-03", "sessions": 4_024, "leads": 114},
    {"nid": "10320682", "month": "2026-04", "sessions": 3_917, "leads": 136},
    {"nid": "10320682", "month": "2026-05", "sessions": 4_085, "leads": 170},
    {"nid": "10320682", "month": "2026-06", "sessions": 5_626, "leads": 107},
    {"nid": "10320682", "month": "2026-07", "sessions": 6_285, "leads": 114},
]

# The Emerson: 468,835 sessions in July against 4,885 in June, engaged FALLING.
EMERSON = [
    {"nid": "10281730", "month": "2026-02", "sessions": 5_456, "leads": 255, "engaged_sessions": 3_400},
    {"nid": "10281730", "month": "2026-03", "sessions": 5_991, "leads": 307, "engaged_sessions": 3_700},
    {"nid": "10281730", "month": "2026-04", "sessions": 6_546, "leads": 246, "engaged_sessions": 4_100},
    {"nid": "10281730", "month": "2026-05", "sessions": 4_596, "leads": 186, "engaged_sessions": 2_900},
    {"nid": "10281730", "month": "2026-06", "sessions": 4_885, "leads": 215, "engaged_sessions": 3_100},
    {"nid": "10281730", "month": "2026-07", "sessions": 468_835, "leads": 156, "engaged_sessions": 2_800},
]

# Sessions present, conversions never recorded — a third state, not zero.
NULL_LEADS = [
    {"nid": "300_hickory", "month": "2026-04", "sessions": 2_195, "leads": None},
    {"nid": "flats_lansdale", "month": "2026-04", "sessions": 1_851, "leads": None},
]


class TestDedupe:
    def test_collapses_exact_duplicates(self):
        rows = ATWOOD + [dict(ATWOOD[0]), dict(ATWOOD[3])]
        out, removed = dq.dedupe(rows, ("nid", "month"))
        assert removed == 2
        assert len(out) == len(ATWOOD)

    def test_clean_data_is_untouched(self):
        out, removed = dq.dedupe(ATWOOD, ("nid", "month"))
        assert removed == 0
        assert out == list(ATWOOD)

    def test_same_month_different_property_is_not_a_duplicate(self):
        rows = [
            {"nid": "a", "month": "2026-01", "sessions": 10, "leads": 1},
            {"nid": "b", "month": "2026-01", "sessions": 20, "leads": 2},
        ]
        out, removed = dq.dedupe(rows, ("nid", "month"))
        assert removed == 0 and len(out) == 2


class TestPlausibility:
    def test_excludes_all_five_impossible_rows(self):
        res = dq.check_plausible(IMPOSSIBLE)
        assert res.rows == []
        assert len(res.exceptions) == 5

    def test_names_the_over_100_percent_cases(self):
        res = dq.check_plausible(IMPOSSIBLE)
        over = [e for e in res.exceptions if "exceeds 100%" in e.detail]
        # Prose Cartersville (158%) and Courtney Isles (216%).
        assert len(over) == 2

    def test_keeps_every_real_atwood_month(self):
        res = dq.check_plausible(ATWOOD)
        assert len(res.rows) == 7
        assert res.exceptions == []
        assert res.caveat() is None

    def test_null_leads_are_their_own_reason_not_zero(self):
        res = dq.check_plausible(NULL_LEADS)
        assert res.rows == []
        assert {e.reason for e in res.exceptions} == {"conversions not recorded"}

    def test_zero_leads_is_legitimate_and_kept(self):
        rows = [{"nid": "x", "month": "2026-01", "sessions": 500, "leads": 0}]
        res = dq.check_plausible(rows)
        assert len(res.rows) == 1, "zero conversions is a real result, not an error"

    def test_zero_sessions_is_excluded(self):
        rows = [{"nid": "x", "month": "2026-01", "sessions": 0, "leads": 0}]
        res = dq.check_plausible(rows)
        assert res.rows == [] and res.exceptions[0].reason == "no denominator"

    def test_boundary_rate_is_kept(self):
        rows = [{"nid": "x", "month": "2026-01", "sessions": 100, "leads": 20}]
        assert len(dq.check_plausible(rows).rows) == 1


class TestOutliers:
    def test_flags_the_emerson(self):
        out = dq.find_volume_outliers(EMERSON)
        assert len(out) == 1
        assert out[0].key == "10281730/2026-07"

    def test_notes_engagement_did_not_follow(self):
        out = dq.find_volume_outliers(EMERSON)
        assert "engagement did not follow" in out[0].reason, (
            "the tell was 96x sessions with engaged sessions falling"
        )

    def test_atwoods_real_june_surge_is_not_flagged(self):
        # Atwood genuinely went 4,085 -> 5,626 sessions in June. That is a real
        # campaign launch, not corruption, and must not be quarantined.
        assert dq.find_volume_outliers(ATWOOD) == []

    def test_needs_history_before_judging(self):
        assert dq.find_volume_outliers(EMERSON[:2]) == []


class TestPipeline:
    def test_end_to_end_separates_clean_from_quarantined(self):
        res = dq.clean(ATWOOD + IMPOSSIBLE + [dict(ATWOOD[0])])
        assert res.duplicates_removed == 1
        assert len(res.rows) == 7
        assert len(res.exceptions) == 5

    def test_caveat_names_counts(self):
        res = dq.clean(ATWOOD + IMPOSSIBLE)
        caveat = res.caveat()
        assert "5 row(s) excluded" in caveat

    def test_outliers_are_reported_but_not_dropped_by_default(self):
        res = dq.clean(EMERSON)
        assert len(res.rows) == 6, "judgment belongs to the caller"
        assert any("outlier" in e.reason for e in res.exceptions)

    def test_outliers_can_be_excluded_explicitly(self):
        res = dq.clean(EMERSON, exclude_outliers=True)
        assert len(res.rows) == 5
        assert all(r["month"] != "2026-07" for r in res.rows)

    def test_summary_is_serialisable(self):
        import json
        json.dumps(dq.clean(ATWOOD + IMPOSSIBLE).summary())

    def test_clean_data_reports_no_caveat(self):
        res = dq.clean(ATWOOD)
        assert not res.had_issues and res.caveat() is None


class TestRegressionAgainstPublishedNumbers:
    """The July portfolio reversal, reproduced from the two rows that caused it."""

    def test_emerson_removal_flips_the_direction(self):
        june = 1_588_941
        july_with = 1_925_066
        emerson_june, emerson_july = 4_885, 468_835

        assert (july_with / june - 1) > 0.20, "with Emerson, July looks like a rise"

        july_without = july_with - emerson_july
        june_without = june - emerson_june
        assert (july_without / june_without - 1) < -0.05, (
            "without Emerson, July is a decline — this single row inverted the "
            "portfolio-level conclusion"
        )


class TestNonPropertyRows:
    """Rollup accounts are the wrong grain, not corrupt data."""

    ROLLUPS = [
        # "RPM Living - Corp" — a portfolio aggregate, ~26% apparent conversion.
        {"nid": "10263647", "month": "2026-01", "sessions": 126_613, "leads": 33_219},
        # "RPM Living - Summer Club" — identical figures, same rollup.
        {"nid": "10296595", "month": "2026-01", "sessions": 126_613, "leads": 33_219},
    ]

    def test_rollups_are_held_back_when_known_set_supplied(self):
        rows = ATWOOD + self.ROLLUPS
        res = dq.clean(rows, known_entities=["10320682"])
        assert len(res.rows) == 7
        assert {e.reason for e in res.exceptions} == {"not a managed property"}

    def test_rollups_are_named_as_grain_not_corruption(self):
        _, held = dq.split_non_property_rows(self.ROLLUPS, known_entities=[])
        assert all(e.reason == "not a managed property" for e in held), (
            "a rollup is not bad data; reporting it as 'impossible rate' sends "
            "someone to debug a property that is fine"
        )

    def test_without_a_known_set_rollups_surface_as_implausible(self):
        # The fallback that actually found them: 26% conversion is impossible
        # for a property, so the plausibility rule catches rollups too.
        res = dq.clean(self.ROLLUPS)
        assert len(res.rows) == 0
        assert any("impossible" in e.reason for e in res.exceptions)

    def test_known_properties_are_unaffected(self):
        res = dq.clean(ATWOOD, known_entities=["10320682"])
        assert len(res.rows) == 7 and not res.exceptions


class TestDimensionFreshness:
    """rpm_properties is the dimension every market rollup joins against.

    The sync is a TRUNCATE + INSERT, so a healthy table has one timestamp
    shared by all rows, refreshed nightly. When it stops running nothing
    errors — new properties are just absent from every rollup, and renamed or
    re-marketed ones report their old values. That silence is the bug.
    """

    def _now(self):
        from datetime import datetime, timezone
        return datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)

    def _with_rows(self, monkeypatch, rows):
        import types
        fake = types.SimpleNamespace(
            BIGQUERY_PROJECT_ID="p",
            _dataset=lambda: "d",
            query=lambda sql: rows,
        )
        monkeypatch.setitem(sys.modules, "bigquery_client", fake)

    def test_fresh_table_returns_none(self, monkeypatch):
        from datetime import datetime, timezone
        self._with_rows(monkeypatch, [{"freshest": datetime(2026, 8, 24, 6, 0,
                                                            tzinfo=timezone.utc),
                                       "n": 840}])
        assert dq.check_dimension_freshness(_now=self._now()) is None

    def test_stale_table_is_reported_with_its_age(self, monkeypatch):
        from datetime import datetime, timezone
        # The real observed state: last refreshed 2026-08-18.
        self._with_rows(monkeypatch, [{"freshest": datetime(2026, 8, 18, 18, 9, 19,
                                                            tzinfo=timezone.utc),
                                       "n": 840}])
        exc = dq.check_dimension_freshness(_now=self._now())
        assert exc is not None
        assert exc.reason == "dimension_stale"
        assert "137h" in exc.detail or "138h" in exc.detail
        assert "market rollup" in exc.detail

    def test_empty_table_is_its_own_reason(self, monkeypatch):
        self._with_rows(monkeypatch, [{"freshest": None, "n": 0}])
        exc = dq.check_dimension_freshness(_now=self._now())
        assert exc is not None and exc.reason == "dimension_empty"

    def test_null_timestamps_are_not_treated_as_fresh(self, monkeypatch):
        self._with_rows(monkeypatch, [{"freshest": None, "n": 840}])
        exc = dq.check_dimension_freshness(_now=self._now())
        assert exc is not None and exc.reason == "freshness_unknown"

    def test_naive_timestamps_do_not_crash(self, monkeypatch):
        """BigQuery can hand back a naive datetime depending on the client."""
        from datetime import datetime
        self._with_rows(monkeypatch, [{"freshest": datetime(2026, 8, 24, 6, 0),
                                       "n": 840}])
        assert dq.check_dimension_freshness(_now=self._now()) is None

    def test_an_unreadable_table_is_not_silently_fresh(self, monkeypatch):
        """The dangerous default. A failed check must never read as 'fine'."""
        import types
        def boom(sql):
            raise RuntimeError("permission denied")
        monkeypatch.setitem(sys.modules, "bigquery_client", types.SimpleNamespace(
            BIGQUERY_PROJECT_ID="p", _dataset=lambda: "d", query=boom))
        exc = dq.check_dimension_freshness(_now=self._now())
        assert exc is not None and exc.reason == "freshness_unknown"
