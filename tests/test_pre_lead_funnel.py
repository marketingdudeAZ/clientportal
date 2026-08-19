"""Tests for the pre-lead funnel skill.

The behaviours under test are the ones a wrong answer would be expensive for:
this report is read during a pricing negotiation, so "renders a plausible number
it should not have" is the failure that matters, not "raises".

No BigQuery is required — the query runner is injected.
"""

from __future__ import annotations

from datetime import date

import pytest

from skills.pre_lead_funnel.probe import ProbeResult
from skills.pre_lead_funnel.render import render_markdown
from skills.pre_lead_funnel.report import ReportContext, build_report
from skills.pre_lead_funnel.stats import Cohort, Rate


# --------------------------------------------------------------------------
# Rate arithmetic
# --------------------------------------------------------------------------

def test_zero_denominator_is_undefined_not_zero():
    """A month with no sessions has no conversion rate. Rendering it as 0%
    would tell the client traffic arrived and none of it converted."""
    r = Rate(numerator=0, denominator=0)
    assert r.value is None
    assert r.wilson_interval is None
    assert "undefined" in r.instability[0]


def test_rate_always_carries_numerator_and_denominator():
    d = Rate(numerator=41, denominator=1315).as_dict()
    assert d["numerator"] == 41
    assert d["denominator"] == 1315
    assert d["value"] == pytest.approx(41 / 1315)


def test_small_numerator_is_flagged_unstable():
    """29 leads on 900 sessions is 3.2% and is not a trendable number."""
    r = Rate(numerator=29, denominator=900)
    assert r.is_unstable
    assert any("< 30" in reason for reason in r.instability)


def test_healthy_volume_is_not_flagged():
    r = Rate(numerator=300, denominator=10_000)
    assert not r.is_unstable, r.instability


def test_wilson_interval_never_runs_below_zero():
    """The normal approximation produces a negative lower bound at these
    counts; Wilson is used precisely to avoid printing that to a client."""
    lo, hi = Rate(numerator=1, denominator=500).wilson_interval
    assert lo >= 0.0
    assert hi <= 1.0
    assert lo < 1 / 500 < hi


def test_cohort_average_is_pooled_not_mean_of_rates():
    """A 40-session property must not swing the cohort like a 4,000-session one."""
    cohort = Cohort(members=[("Small", Rate(4, 40)), ("Large", Rate(80, 4000))])
    # Mean of rates would be (10% + 2%) / 2 = 6%.
    assert cohort.average == pytest.approx(84 / 4040)
    assert cohort.average < 0.03
    assert cohort.n == 2
    assert cohort.range == pytest.approx((0.02, 0.10))


# --------------------------------------------------------------------------
# Availability behaviour
# --------------------------------------------------------------------------

def _probe(**caps) -> ProbeResult:
    p = ProbeResult(property_label="Test Property")
    for name, available in caps.items():
        p.add(name.replace("__", ":"), available, detail=f"{name} -> {available}")
    return p


def _ctx(**over) -> ReportContext:
    base = dict(
        property_label="Test Property",
        hyly_property_id="123",
        start=date(2026, 1, 1),
        end=date(2026, 8, 31),
        ga4_table="p.d.ga4_analytics_events",
        rollup_table=None,
        lead_events=("generate_lead",),
        page_patterns=["%/floorplans%"],
        cohort_property_ids=[],
    )
    base.update(over)
    return ReportContext(**base)


def _no_query(*a, **k):
    raise AssertionError("no query should run when a capability is blocked")


def test_blocked_capability_yields_unavailable_not_empty_rows():
    """The ADR 0022 failure mode: absent access rendering as absent traffic."""
    probe = _probe(bigquery=True, ga4_table=False)
    report = build_report(_ctx(), probe, _no_query)
    w1 = report["widgets"][0]
    assert w1["status"] == "unavailable"
    assert w1["rows"] == []
    assert w1["unavailable"]["reason"]


def test_missing_page_patterns_blocks_floorplan_widget_rather_than_guessing():
    probe = _probe(bigquery=True, ga4_table=True, session_key=True, page_location=True)
    report = build_report(_ctx(page_patterns=[]), probe, _no_query)
    w5 = next(w for w in report["widgets"] if w["widget"] == 5)
    assert w5["status"] == "unavailable"
    assert "site-specific" in w5["unavailable"]["reason"]


def test_average_position_is_reported_as_retired_not_substituted():
    """Google removed the metric in 2019. Silently swapping in top-impression
    share would violate the report's no-proxy rule."""
    probe = _probe(bigquery=True, google_ads=False)
    report = build_report(_ctx(), probe, _no_query)
    w7 = next(w for w in report["widgets"] if w["widget"] == 7)
    assert w7["status"] == "unavailable"
    joined = " ".join(w7["notes"])
    assert "2019-09-30" in joined
    assert "not applied silently" in joined


def test_uninstrumented_steps_are_declared_not_proxied():
    probe = _probe(
        bigquery=True,
        event__pricing_interaction=False,
        event__form_start=False,
        event__tour_request=False,
    )
    report = build_report(_ctx(), probe, _no_query)
    w6 = next(w for w in report["widgets"] if w["widget"] == 6)
    assert w6["status"] == "unavailable"
    assert all(r["instrumented"] is False for r in w6["rows"])
    assert any("No proxy metric" in n for n in w6["notes"])
    assert any("bounce or exit rate" in n for n in w6["notes"])


def test_cohort_reports_n_and_warns_when_too_small():
    probe = _probe(bigquery=True, ga4_table=True, session_key=True)
    calls = []

    def runner(sql, **params):
        calls.append(params["pid"])
        return [{"month": date(2026, 1, 1), "sessions": 1000, "lead_sessions": 35}]

    ctx = _ctx(cohort_property_ids=[("Comp A", "11"), ("Comp B", "22")])
    report = build_report(ctx, probe, runner)
    w3 = next(w for w in report["widgets"] if w["widget"] == 3)
    assert w3["rows"][0]["n_properties"] == 2
    assert any("too small" in n for n in w3["notes"])
    assert calls == ["11", "22"]


# --------------------------------------------------------------------------
# The widgets that carry the argument
# --------------------------------------------------------------------------

def test_unassigned_channel_traffic_is_flagged():
    probe = _probe(bigquery=True, ga4_table=True, session_key=True, default_channel_group=True)
    rows = [
        {"month": date(2026, 1, 1), "channel": "Paid Search", "sessions": 2000, "lead_sessions": 60},
        {"month": date(2026, 1, 1), "channel": "Unassigned", "sessions": 480, "lead_sessions": 2},
    ]
    report = build_report(_ctx(), probe, lambda sql, **k: rows)
    w4 = next(w for w in report["widgets"] if w["widget"] == 4)
    assert any("FLAG" in n and "480" in n for n in w4["notes"])


def test_price_exposure_splits_conversion_and_lead_share():
    probe = _probe(bigquery=True, ga4_table=True, session_key=True, page_location=True)
    rows = [
        {
            "month": date(2026, 1, 1),
            "segment": "saw_floorplan_or_pricing",
            "sessions": 800,
            "lead_sessions": 16,
        },
        {
            "month": date(2026, 1, 1),
            "segment": "never_saw_pricing",
            "sessions": 1200,
            "lead_sessions": 84,
        },
    ]
    # Dispatch on the query, so widget 5 (which shares these capabilities) gets
    # its own shape rather than being handed widget 9's columns.
    def runner(sql, **k):
        return rows if "segment" in sql else []

    report = build_report(_ctx(), probe, runner)
    w9 = next(w for w in report["widgets"] if w["widget"] == 9)
    row = w9["rows"][0]
    assert row["saw_floorplan_or_pricing"]["value"] == pytest.approx(0.02)
    assert row["never_saw_pricing"]["value"] == pytest.approx(0.07)
    # 84 of 100 leads never saw a price — the finding the whole report exists for.
    share = row["share_of_leads_that_never_saw_pricing"]
    assert (share["numerator"], share["denominator"]) == (84, 100)


def test_conversion_widget_shows_crm_leads_beside_ga4_never_instead_of():
    probe = _probe(bigquery=True, ga4_table=True, session_key=True, ga4_coverage=True,
                   hyly_rollup=True)

    def runner(sql, **k):
        if "hyly_daily_activity_v1" in sql:
            return [{"month": date(2026, 1, 1), "leads": 95}]
        return [{"month": date(2026, 1, 1), "sessions": 2000, "lead_sessions": 40}]

    report = build_report(_ctx(rollup_table="p.d.hyly_daily_activity_v1"), probe, runner)
    w2 = next(w for w in report["widgets"] if w["widget"] == 2)
    row = w2["rows"][0]
    assert row["numerator"] == 40 and row["denominator"] == 2000
    assert row["crm_leads"] == 95  # gap is visible, not reconciled away
    assert any("undercount" in n for n in w2["notes"])


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def test_render_shows_raw_counts_and_flags_and_never_prints_bare_blanks():
    probe = _probe(bigquery=True, ga4_table=True, session_key=True, ga4_coverage=True)
    rows = [{"month": date(2026, 1, 1), "sessions": 400, "lead_sessions": 6}]
    report = build_report(_ctx(), probe, lambda sql, **k: rows)
    md = render_markdown(report)
    assert "(6 / 400)" in md          # raw numerator/denominator present
    assert "UNSTABLE" in md            # 6 leads must be flagged
    assert "unavailable" in md.lower() # blocked widgets say so in words
    assert "| — |" not in md           # no bare dashes standing in for data
