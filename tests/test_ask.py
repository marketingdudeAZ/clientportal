"""Ask — the preset-question surface (registry / context / engine / routes).

Offline only. Every warehouse call, every HubSpot call and the model call are
mocked; nothing here touches a credential.

What these tests are actually defending, in priority order:

1. A DARK SOURCE IS A CAVEAT, NOT A GAP. One pull raising must not cost the
   client the other four, and its absence has to arrive as a named sentence in
   the answer. This is the `hyly_client` post-mortem (ADR 0022) written as a
   test: the failure mode is a shorter answer that looks complete.

2. EVERY CLAIM CARRIES ITS NUMERATOR, DENOMINATOR AND SOURCE. The prompt hands
   the model pre-formatted evidence lines, and a finding citing no index is
   dropped. Both halves are asserted, because either one alone lets a bare
   percentage through.

3. THE SURFACE IS PRESET-ONLY. An unknown key is a 404, never a best-effort
   answer, and the manifest says `free_text: false` out loud.

4. THE ATWOOD SHAPE IS VISIBLE. Leads 170 → 107 → 114 is *up* month over
   month. The collapse only exists against May, and against rising sessions.
   `analyze_trend` has to emit both halves or `whats_not_working` is blind.
"""

from __future__ import annotations

import copy
import json
import os
import sys
from unittest import mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("HUBSPOT_API_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from flask import Flask                                          # noqa: E402

from skills import ask_context, ask_engine, question_registry    # noqa: E402
from skills.property_resolver import PropertyIdentity            # noqa: E402
import routes.ask as ask_routes                                  # noqa: E402

NC = ask_context.NC_SOURCE


# ── shared fixtures ────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _no_bq_param_builders(monkeypatch):
    """`google.cloud.bigquery` can only be initialized once in this interpreter
    (PyO3), and query-parameter construction is not what any of these tests are
    about. Stub the two builders; the SQL and the row handling stay real."""
    monkeypatch.setattr(ask_context, "_string_param", lambda k, v: (k, "STRING", str(v)))
    monkeypatch.setattr(ask_context, "_int_param", lambda k, v: (k, "INT64", int(v)))


@pytest.fixture(autouse=True)
def _no_durable_cache(monkeypatch):
    """Never write the answer cache to HubSpot from a test, and start clean."""
    monkeypatch.setattr(ask_engine, "CACHE_TO_HUBSPOT", False)
    ask_engine.clear_cache()
    yield
    ask_engine.clear_cache()


def _identity(**over):
    base = dict(company_id="12345", uuid="uuid-atwood", name="Atwood at Rivulon",
                market="Phoenix", unit_count="330", occupancy="Stable",
                ninjacat_id="99001", aptiq_property_id="apt-1",
                aptiq_market_id="mkt-1", hyly_property_id=None)
    base.update(over)
    return PropertyIdentity(**base)


# Atwood at Rivulon, as investigated by hand 2026-08-19..24. These are the
# numbers the trend analyzer has to make legible.
ATWOOD_MONTHS = [
    {"nid": "99001", "month": "2026-04", "sessions": 3810, "leads": 158,
     "spend": 4200.0, "clicks": 1100, "impressions": 44000},
    {"nid": "99001", "month": "2026-05", "sessions": 4085, "leads": 170,
     "spend": 4300.0, "clicks": 1180, "impressions": 46000},
    {"nid": "99001", "month": "2026-06", "sessions": 5626, "leads": 107,
     "spend": 4800.0, "clicks": 1520, "impressions": 61000},
    {"nid": "99001", "month": "2026-07", "sessions": 6285, "leads": 114,
     "spend": 4850.0, "clicks": 1610, "impressions": 63000},
]


def _trend_pull(rows=None):
    rows = copy.deepcopy(rows if rows is not None else ATWOOD_MONTHS)
    signals = ask_context.analyze_trend(rows)
    return ask_context.Pull(
        name="performance_trend", source=NC, available=True,
        data={"months": rows, "window": "Apr 2026 to Jul 2026"},
        evidence=[s["evidence"] for s in signals], signals=signals)


# ══ 1. registry ════════════════════════════════════════════════════════════

def test_all_five_launch_questions_are_registered_in_order():
    assert question_registry.keys() == [
        "whats_working", "whats_not_working", "opportunities",
        "lead_sources", "tour_sources"]


def test_manifest_shape():
    m = question_registry.manifest()
    # Published as an explicit False so a client reads a decision, not a gap.
    assert m["free_text"] is False
    assert len(m["questions"]) == 5
    for q in m["questions"]:
        assert set(q) == {"key", "label", "blurb", "inputs", "viz", "order"}
        assert q["label"] and q["blurb"]
        assert q["inputs"], "a question with no inputs cannot be answered"
        for inp in q["inputs"]:
            assert set(inp) == {"name", "required"}
            assert inp["name"] in ask_context.PULLS
            assert isinstance(inp["required"], bool)
        assert any(i["required"] for i in q["inputs"]), \
            "a question with nothing required can 'succeed' on no data at all"
        if q["viz"]:
            assert q["viz"]["kind"] in ("line", "bar", "stat")
            assert q["viz"]["pull"] in [i["name"] for i in q["inputs"]]
    assert [q["order"] for q in m["questions"]] == sorted(
        q["order"] for q in m["questions"])


def test_manifest_never_leaks_the_prompt():
    """`instruction` is internal. Publishing it hands a client the jailbreak."""
    blob = json.dumps(question_registry.manifest())
    for q in question_registry.ordered():
        assert q.instruction not in blob


@pytest.mark.parametrize("bad,fragment", [
    (dict(pulls=("performance_trend", "no_such_pull"),
          required=("performance_trend",)), "unknown pull"),
    (dict(pulls=("performance_trend",), required=("occupancy",)),
     "required pull"),
    (dict(pulls=("performance_trend",), required=("performance_trend",),
          focus="sideways"), "unknown focus"),
    (dict(pulls=("performance_trend",), required=("performance_trend",),
          viz=question_registry.Viz(kind="bar", pull="occupancy", x="m",
                                    series=("leads",), title="t")), "viz reads pull"),
])
def test_validate_catches_a_malformed_entry(bad, fragment):
    entry = dict(key="_bogus", label="l", blurb="b", pulls=(), required=(),
                 focus=question_registry.FOCUS_ALL, instruction="i")
    entry.update(bad)
    q = question_registry.Question(**entry)
    question_registry.QUESTIONS[q.key] = q
    try:
        with pytest.raises(ValueError) as err:
            question_registry.validate()
        assert fragment in str(err.value)
        assert "_bogus" in str(err.value)
    finally:
        question_registry.QUESTIONS.pop(q.key, None)
    question_registry.validate()          # the real registry is still sound


@pytest.mark.parametrize("key", ["", None, "  ", "whats_working ; drop table",
                                 "free_text", "WHATS_WORKING"])
def test_unknown_key_raises(key):
    with pytest.raises(question_registry.UnknownQuestion):
        question_registry.get(key)


def test_get_tolerates_surrounding_whitespace():
    assert question_registry.get("  whats_working  ").key == "whats_working"


# ══ 2. evidence formatting — the numerator/denominator/source contract ═════

def test_change_evidence_carries_both_raw_values_and_the_source():
    e = ask_context.change_evidence("leads", 170, 114, "2026-05", "2026-07", NC)
    assert e == "leads 170 → 114 (−32.9%) from May 2026 to Jul 2026 [" + NC + "]"


def test_pct_change_is_none_not_zero_when_it_cannot_be_computed():
    # "we cannot compute this" and "it did not move" are different facts.
    assert ask_context.pct_change(0, 5) is None
    assert ask_context.pct_change(None, 5) is None
    assert ask_context.pct_change(100, 100) == 0.0
    assert ask_context.fmt_signed_pct(None) == "n/a"


def test_signed_pct_uses_a_real_minus_sign():
    assert ask_context.fmt_signed_pct(-32.9).startswith("−")   # U+2212, not "-"


def test_rate_and_share_evidence_state_the_fraction():
    r = ask_context.rate_evidence("conversion rate", 114, 6285, "leads",
                                  "sessions", "2026-07", NC)
    assert "114 leads / 6,285 sessions = 1.81%" in r and NC in r
    s = ask_context.share_evidence("Google Ads", 62, 114, "leads", "2026-07", NC)
    assert "62 of 114 leads (54.4%)" in s and "Jul 2026" in s


def test_share_evidence_says_share_na_rather_than_dividing_by_zero():
    assert "share n/a" in ask_context.share_evidence("X", 0, 0, "leads", "2026-07", NC)


# ══ 3. analyze_trend — the Atwood shape ═══════════════════════════════════

def test_month_over_month_alone_would_call_the_atwood_collapse_a_recovery():
    """The premise. If this ever fails the rest of the section is moot."""
    sigs = {s["key"]: s for s in ask_context.analyze_trend(copy.deepcopy(ATWOOD_MONTHS))}
    assert sigs["mom_leads"]["direction"] == "up"        # Jun 107 → Jul 114


def test_peak_decline_finds_the_collapse_the_latest_month_hides():
    sigs = {s["key"]: s for s in ask_context.analyze_trend(copy.deepcopy(ATWOOD_MONTHS))}
    peak = sigs["peak_decline_leads"]
    assert peak["from_period"] == "2026-05" and peak["from_value"] == 170
    assert peak["to_period"] == "2026-07" and peak["to_value"] == 114
    assert peak["pct_change"] == -32.9
    assert peak["sentiment"] == "negative"
    # Both halves ship in one string: "more visitors, fewer inquiries".
    assert "leads 170 → 114" in peak["evidence"]
    assert "while sessions 4,085 → 6,285 (+53.9%)" in peak["evidence"]


def test_largest_month_over_month_drop_is_may_to_june():
    sigs = {s["key"]: s for s in ask_context.analyze_trend(copy.deepcopy(ATWOOD_MONTHS))}
    worst = sigs["largest_mom_drop"]
    assert (worst["from_period"], worst["to_period"]) == ("2026-05", "2026-06")
    assert (worst["from_value"], worst["to_value"]) == (170, 107)
    assert "while sessions" in worst["evidence"]


def test_every_signal_carries_its_source_and_conversion_rate_shows_its_fraction():
    sigs = ask_context.analyze_trend(copy.deepcopy(ATWOOD_MONTHS))
    assert sigs, "a four-month window must produce signals"
    for s in sigs:
        assert "[%s]" % NC in s["evidence"], s
        assert s["sentiment"] in ("positive", "negative", "neutral")
    rates = [s for s in sigs if s["key"].startswith("conversion_rate_")]
    assert len(rates) == 2
    assert "114 leads / 6,285 sessions" in rates[-1]["evidence"] or \
           any("114 leads / 6,285 sessions" in r["evidence"] for r in rates)


def test_rising_spend_is_never_scored_as_a_win():
    sigs = {s["key"]: s for s in ask_context.analyze_trend(copy.deepcopy(ATWOOD_MONTHS))}
    assert sigs["mom_spend"]["sentiment"] == "neutral"


def test_a_single_month_produces_no_signals_rather_than_a_fake_trend():
    assert ask_context.analyze_trend([ATWOOD_MONTHS[0]]) == []
    assert ask_context.analyze_trend([]) == []


# ══ 4. context assembly — a dark source is a caveat, never a gap ══════════

def test_a_pull_that_raises_does_not_kill_the_other_pulls():
    def boom(_identity):
        raise RuntimeError("BigQuery 403: caller lacks bigquery.jobs.create")

    with mock.patch.dict(ask_context.PULLS, {
            "performance_trend": lambda i: _trend_pull(),
            "lead_sources": boom,
            "occupancy": lambda i: ask_context.Pull(
                name="occupancy", source="ApartmentIQ", available=True,
                evidence=["occupancy: 94% as of today [ApartmentIQ API]"])}):
        ctx = ask_context.assemble(_identity(),
                                   ["performance_trend", "lead_sources", "occupancy"])

    assert set(ctx.pulls) == {"performance_trend", "lead_sources", "occupancy"}
    assert set(ctx.available()) == {"performance_trend", "occupancy"}
    # The surviving pulls kept every one of their receipts.
    assert any("leads 170" in e for e in ctx.evidence())
    assert any("occupancy: 94%" in e for e in ctx.evidence())


def test_a_pull_that_raises_surfaces_as_a_named_caveat_not_a_silent_gap():
    def boom(_identity):
        raise RuntimeError("BigQuery 403: caller lacks bigquery.jobs.create")

    with mock.patch.dict(ask_context.PULLS, {"lead_sources": boom}):
        ctx = ask_context.assemble(_identity(), ["lead_sources"])

    missing = ctx.missing_inputs()
    assert [m["input"] for m in missing] == ["lead_sources"]
    reason = missing[0]["reason"]
    assert "failed to load" in reason
    assert "bigquery.jobs.create" in reason, \
        "the operator-readable cause has to survive to the reader"
    assert ctx.pulls["lead_sources"].available is False


def test_an_unregistered_pull_name_is_recorded_rather_than_crashing():
    ctx = ask_context.assemble(_identity(), ["performance_trend_typo"])
    assert ctx.pulls["performance_trend_typo"].available is False
    assert "No pull named" in ctx.pulls["performance_trend_typo"].missing_reason


def test_assemble_survives_an_identity_that_cannot_serialize_itself():
    class Hostile:
        company_id = "777"

        def to_dict(self):
            raise TypeError("nope")

    ctx = ask_context.assemble(Hostile(), [])
    assert ctx.identity == {"company_id": "777"}


# — the hyly_client lesson, concretely —

def test_hyly_unmapped_property_names_the_beta_instead_of_charting_zero():
    pull = ask_context.pull_tour_sources(_identity(hyly_property_id=None))
    assert pull.available is False
    assert "hyly_property_id" in pull.missing_reason
    assert "15-property beta" in pull.missing_reason
    # The specific failure that started this: substituting leads for tours.
    assert "cannot be answered from lead data alone" in pull.missing_reason


def test_hyly_configured_but_empty_is_a_stated_absence_not_an_empty_chart():
    fake = mock.Mock()
    fake.is_configured.return_value = True
    fake.get_channel_summary.return_value = {}          # the `hyly_client` [] case
    fake.get_data_freshness.return_value = {"is_stale": False}
    with mock.patch.dict(sys.modules, {"hyly_client": fake}):
        pull = ask_context.pull_tour_sources(_identity(hyly_property_id="h-9"))
    assert pull.available is False
    assert "no tour activity" in pull.missing_reason
    assert pull.evidence == []


def test_hyly_available_reports_lead_to_tour_as_a_count_over_a_count():
    fake = mock.Mock()
    fake.is_configured.return_value = True
    fake.get_channel_summary.return_value = {
        "_total": {"tours_completed": 40, "leads": 200},
        "Paid Search": {"tours_completed": 25, "leads": 100},
        "Organic": {"tours_completed": 15, "leads": 100},
    }
    fake.get_data_freshness.return_value = {
        "is_stale": True, "days_behind": 3, "data_through": "2026-08-21"}
    with mock.patch.dict(sys.modules, {"hyly_client": fake}):
        pull = ask_context.pull_tour_sources(_identity(hyly_property_id="h-9"))
    assert pull.available is True
    joined = " || ".join(pull.evidence)
    assert "Paid Search: 25 of 40 completed tours (62.5%)" in joined
    assert "Paid Search lead→tour: 25 tours / 100 leads = 25.00%" in joined
    assert "overall lead→tour: 40 tours / 200 leads = 20.00%" in joined
    assert "3 day(s) behind" in pull.caveat


def test_reputation_is_declared_dark_rather_than_quietly_omitted():
    """SOCi has no connector. 'What's working' must say so out loud."""
    pull = ask_context.pull_reputation(_identity())
    assert pull.available is False
    assert "SOCi" in pull.source
    assert "no connector" in pull.missing_reason
    assert "reputation" in question_registry.get("whats_working").pulls[-1] or \
        "reputation" in question_registry.get("whats_working").pulls


def test_performance_trend_without_a_ninjacat_id_names_the_missing_join_key():
    pull = ask_context.pull_performance_trend(_identity(ninjacat_id=None))
    assert pull.available is False
    assert "ninjacat_system_id" in pull.missing_reason


def test_lead_sources_ranks_by_leads_and_states_cost_per_lead_only_where_spend_exists():
    rows = [
        {"month": "2026-07", "channel": "Paid Search", "source": "Google Ads",
         "sessions": 2100, "clicks": 900, "leads": 62, "spend": 4200.0},
        {"month": "2026-07", "channel": "Paid Social", "source": "Meta",
         "sessions": 3922, "clicks": 480, "leads": 5, "spend": 500.0},
        {"month": "2026-07", "channel": "Organic", "source": "GA4",
         "sessions": 1800, "clicks": None, "leads": 47, "spend": None},
        {"month": "2026-06", "channel": "Paid Search", "source": "Google Ads",
         "sessions": 2000, "clicks": 880, "leads": 55, "spend": 4100.0},
    ]
    with mock.patch.object(ask_context, "_bq_ready", return_value=None), \
         mock.patch.dict(sys.modules, {"bigquery_client": mock.Mock(query=lambda *a, **k: rows)}):
        pull = ask_context.pull_lead_sources(_identity())

    assert pull.available is True
    assert pull.data["month"] == "2026-07"
    assert [r["channel"] for r in pull.data["rows"]] == ["Paid Search", "Organic", "Paid Social"]
    joined = " || ".join(pull.evidence)
    assert "Paid Search / Google Ads: 62 of 114 leads (54.4%)" in joined
    assert "Paid Social / Meta: 5 of 114 leads (4.4%)" in joined
    assert "Paid Social / Meta cost per lead: $500 spend / 5 leads = $100" in joined
    # Organic has no spend, so it gets no cost-per-lead line at all rather than $0.
    assert "Organic / GA4 cost per lead" not in joined
    assert "Paid Search / Google Ads leads 55 → 62" in joined


def test_lead_sources_states_each_channel_share_of_traffic_and_its_own_conversion():
    """The Atwood paid-social finding, expressed as evidence.

    The published finding is not "Meta sent 4% of leads". It is "Meta sent the
    single largest block of traffic on the property and converted almost none
    of it". Making that claim needs the SESSION denominator on the same line as
    the lead count — and the system prompt forbids the model from computing it,
    so if it is not formatted here it cannot be said at all.
    """
    rows = [
        {"month": "2026-07", "channel": "Paid Search", "source": "Google Ads",
         "sessions": 1300, "clicks": 920, "leads": 66, "spend": 3800.0},
        {"month": "2026-07", "channel": "Paid Social", "source": "Meta",
         "sessions": 3922, "clicks": 3922, "leads": 5, "spend": 500.0},
        {"month": "2026-07", "channel": "Organic Search", "source": "GA4",
         "sessions": 620, "clicks": None, "leads": 31, "spend": None},
        {"month": "2026-07", "channel": "ILS", "source": "Apartments.com",
         "sessions": 443, "clicks": None, "leads": 12, "spend": 550.0},
        {"month": "2026-06", "channel": "Paid Search", "source": "Google Ads",
         "sessions": 1250, "clicks": 900, "leads": 62, "spend": 3800.0},
        {"month": "2026-06", "channel": "Organic Search", "source": "GA4",
         "sessions": 900, "clicks": None, "leads": 28, "spend": None},
    ]
    with mock.patch.object(ask_context, "_bq_ready", return_value=None), \
         mock.patch.dict(sys.modules,
                         {"bigquery_client": mock.Mock(query=lambda *a, **k: rows)}):
        pull = ask_context.pull_lead_sources(_identity())
    joined = " || ".join(pull.evidence)

    # Share of traffic, beside share of leads.
    assert "Paid Social / Meta: 3,922 of 6,285 sessions (62.4%) in Jul 2026" in joined
    assert "Paid Social / Meta: 5 of 114 leads (4.4%) in Jul 2026" in joined
    # Its own conversion rate, as a count over a count.
    assert "Paid Social / Meta conversion rate: 5 leads / 3,922 sessions = 0.13%" in joined
    # And a channel to compare it against, so 0.13% means something.
    assert "Paid Search / Google Ads conversion rate: 66 leads / 1,300 sessions = 5.08%" in joined
    assert pull.data["total_sessions"] == 6285


def test_a_channel_with_no_prior_month_row_is_reported_as_a_launch():
    """'Spend appeared and leads did not follow' is not 'a channel got worse'."""
    rows = [
        {"month": "2026-07", "channel": "Paid Social", "source": "Meta",
         "sessions": 3922, "clicks": 3922, "leads": 5, "spend": 500.0},
        {"month": "2026-07", "channel": "Paid Search", "source": "Google Ads",
         "sessions": 1300, "clicks": 920, "leads": 66, "spend": 3800.0},
        {"month": "2026-06", "channel": "Paid Search", "source": "Google Ads",
         "sessions": 1250, "clicks": 900, "leads": 62, "spend": 3800.0},
    ]
    with mock.patch.object(ask_context, "_bq_ready", return_value=None), \
         mock.patch.dict(sys.modules,
                         {"bigquery_client": mock.Mock(query=lambda *a, **k: rows)}):
        pull = ask_context.pull_lead_sources(_identity())
    joined = " || ".join(pull.evidence)
    assert ("Paid Social / Meta is new in Jul 2026: no rows at all in Jun 2026, "
            "then 3,922 sessions and 5 leads in Jul 2026") in joined
    # An existing channel gets a change line, not a launch line.
    assert "Paid Search / Google Ads is new in" not in joined
    assert "Paid Search / Google Ads leads 62 → 66" in joined


def test_a_channel_with_no_sessions_gets_no_conversion_rate_rather_than_zero():
    rows = [{"month": "2026-07", "channel": "Referral", "source": "Direct",
             "sessions": None, "clicks": None, "leads": 9, "spend": None}]
    with mock.patch.object(ask_context, "_bq_ready", return_value=None), \
         mock.patch.dict(sys.modules,
                         {"bigquery_client": mock.Mock(query=lambda *a, **k: rows)}):
        pull = ask_context.pull_lead_sources(_identity())
    joined = " || ".join(pull.evidence)
    assert "Referral / Direct conversion rate" not in joined
    assert "Referral / Direct: 9 of 9 leads (100.0%)" in joined


def test_lead_sources_with_zero_total_leads_refuses_to_rank_nothing():
    rows = [{"month": "2026-07", "channel": "Paid Search", "source": "Google Ads",
             "sessions": 2100, "clicks": 900, "leads": 0, "spend": 4200.0}]
    with mock.patch.object(ask_context, "_bq_ready", return_value=None), \
         mock.patch.dict(sys.modules, {"bigquery_client": mock.Mock(query=lambda *a, **k: rows)}):
        pull = ask_context.pull_lead_sources(_identity())
    assert pull.available is False
    assert "No source recorded a single lead" in pull.missing_reason


def test_data_quality_caveat_rides_along_on_the_trend_pull():
    """A caveat the reader can see beats a clean number they cannot check."""
    dirty = copy.deepcopy(ATWOOD_MONTHS) + [dict(ATWOOD_MONTHS[-1])]   # duplicate row
    bq = mock.Mock()
    bq.query.return_value = [
        {"month": r["month"], "sessions": r["sessions"], "impressions": r["impressions"],
         "clicks": r["clicks"], "leads": r["leads"], "spend": r["spend"]} for r in dirty]
    with mock.patch.object(ask_context, "_bq_ready", return_value=None), \
         mock.patch.dict(sys.modules, {"bigquery_client": bq}):
        pull = ask_context.pull_performance_trend(_identity())
    assert pull.available is True
    assert pull.caveat and "duplicate row(s) collapsed" in pull.caveat
    assert pull.quality["duplicates_removed"] == 1
    assert len(pull.data["months"]) == 4


# ══ 5. engine — the prompt, and what survives it ═══════════════════════════

def _ctx_with(pulls):
    return ask_context.AskContext(identity=_identity().to_dict(), pulls=pulls)


def test_prompt_carries_numerator_denominator_and_source_for_every_claim():
    q = question_registry.get("whats_not_working")
    ctx = _ctx_with({
        "performance_trend": _trend_pull(),
        "tour_sources": ask_context._missing(
            "tour_sources", "BigQuery hyly_daily_activity_v1 (Hyly)",
            "This property has no hyly_property_id, so tours are dark."),
    })
    evidence = ctx.evidence()
    prompt = ask_engine.build_prompt(q, ctx, evidence)

    assert "EVIDENCE (the only numbers that exist)" in prompt
    for i, line in enumerate(evidence):
        assert "  [%d] %s" % (i, line) in prompt
        assert "[" in line and "]" in line, "no evidence line without a source"
        assert NC in line
        # Every line states an actual pair of values, not a lone percentage.
        assert ("→" in line) or (" / " in line), line
    # The dark input is named in its own section, with what it would have told us.
    assert "INPUTS THAT WERE NOT AVAILABLE" in prompt
    assert "tour_sources" in prompt and "no hyly_property_id" in prompt
    # The question's own instruction rides with it.
    assert q.instruction in prompt
    assert q.label in prompt


def test_prompt_says_so_explicitly_when_there_is_nothing_to_cite():
    q = question_registry.get("whats_working")
    prompt = ask_engine.build_prompt(q, _ctx_with({}), [])
    assert "(none — every input for this question was unavailable)" in prompt


def test_prompt_carries_data_quality_caveats_to_the_reader():
    p = _trend_pull()
    p.caveat = "2 row(s) excluded as implausible."
    prompt = ask_engine.build_prompt(question_registry.get("whats_not_working"),
                                     _ctx_with({"performance_trend": p}), p.evidence)
    assert "DATA-QUALITY CAVEATS (must reach the reader)" in prompt
    assert "2 row(s) excluded as implausible." in prompt


def test_system_prompt_forbids_inventing_numbers_and_demands_an_index():
    s = ask_engine.SYSTEM_PROMPT
    assert "Never state a number, percentage or direction that is not in the" in s
    assert "Do not compute new figures" in s
    assert "Never write a bare percentage" in s
    assert "Every finding must cite at least one evidence index" in s
    assert "do not imply the data is zero" in s


@pytest.mark.parametrize("findings", [
    [{"title": "Leads collapsed", "detail": "d"}],                    # no key at all
    [{"title": "Leads collapsed", "detail": "d", "evidence": []}],     # empty
    [{"title": "Leads collapsed", "detail": "d", "evidence": [99]}],   # out of range
    [{"title": "Leads collapsed", "detail": "d", "evidence": ["x"]}],  # not an index
    [{"title": "Leads collapsed", "detail": "d", "evidence": [-1]}],   # negative
    ["a bare string"],
])
def test_a_finding_that_cites_no_real_evidence_index_is_dropped(findings):
    assert ask_engine._coerce_findings(findings, ["e0", "e1"]) == []


def test_a_supported_finding_is_rewritten_to_carry_its_evidence_verbatim():
    out = ask_engine._coerce_findings(
        [{"title": "Leads fell", "detail": "d", "evidence": [1, 1, 0]}], ["e0", "e1"])
    assert out == [{"title": "Leads fell", "detail": "d", "evidence": ["e0", "e1"]}]


@pytest.mark.parametrize("raw", [
    "not json at all",
    "```json\n{\"headline\": \"h\"}\n```",
    '{"headline": "h", "findings": []}',
])
def test_parse_json_is_tolerant_of_fences_and_strict_about_garbage(raw):
    parsed = ask_engine._parse_json(raw)
    assert parsed is None or isinstance(parsed, dict)


# — end-to-end _generate, model mocked —

def _generate(question_key, pulls, model_json=None, llm=True):
    q = question_registry.get(question_key)
    ctx = _ctx_with(pulls)
    with mock.patch.object(ask_engine.ask_context, "assemble", return_value=ctx), \
         mock.patch.object(ask_engine, "_llm_available", return_value=llm), \
         mock.patch.object(ask_engine, "_complete",
                           return_value=model_json or "") as comp:
        out = ask_engine._generate(q, _identity())
    return out, comp


def test_a_model_answer_that_cites_evidence_is_kept_and_labelled_claude():
    pulls = {"performance_trend": _trend_pull()}
    ev = pulls["performance_trend"].evidence
    body = json.dumps({
        "headline": "Leads fell while traffic rose.",
        "summary": "s",
        "findings": [{"title": "Leads down a third", "detail": "d", "evidence": [0]}],
        "next_step": "Audit the form.",
        "not_evidenced": [],
    })
    out, comp = _generate("whats_not_working", pulls, body)
    assert out["answered"] is True and out["narrator"] == "claude"
    assert out["findings"][0]["evidence"] == [ev[0]]
    assert out["next_step"] == "Audit the form."
    assert comp.call_count == 1


def test_a_model_answer_where_nothing_is_supported_falls_back_to_arithmetic():
    body = json.dumps({"headline": "Everything is great!", "summary": "s",
                       "findings": [{"title": "Vibes", "detail": "trust me",
                                     "evidence": []}]})
    out, _ = _generate("whats_not_working", {"performance_trend": _trend_pull()}, body)
    assert out["narrator"] == "rules"
    assert out["headline"] != "Everything is great!"
    for f in out["findings"]:
        assert f["evidence"], "the rules narrator must cite too"
        assert NC in f["evidence"][0]


def test_an_llm_exception_still_produces_an_answer():
    q = question_registry.get("whats_not_working")
    ctx = _ctx_with({"performance_trend": _trend_pull()})
    with mock.patch.object(ask_engine.ask_context, "assemble", return_value=ctx), \
         mock.patch.object(ask_engine, "_llm_available", return_value=True), \
         mock.patch.object(ask_engine, "_complete", side_effect=RuntimeError("529")):
        out = ask_engine._generate(q, _identity())
    assert out["answered"] is True and out["narrator"] == "rules"
    assert out["findings"]


def test_every_dark_input_reaches_not_evidenced_whatever_the_model_said():
    pulls = {
        "performance_trend": _trend_pull(),
        "reputation": ask_context.pull_reputation(_identity()),
        "tour_sources": ask_context._missing("tour_sources", "Hyly", "beta only."),
    }
    body = json.dumps({"headline": "h", "summary": "s",
                       "findings": [{"title": "t", "detail": "d", "evidence": [0]}],
                       "not_evidenced": []})
    out, _ = _generate("whats_not_working", pulls, body)
    joined = " || ".join(out["not_evidenced"])
    assert "reputation:" in joined and "SOCi" in joined
    assert "tour_sources:" in joined
    assert {m["input"] for m in out["missing_inputs"]} == {"reputation", "tour_sources"}
    assert out["inputs"]["reputation"]["available"] is False


def test_a_missing_required_input_refuses_to_answer_rather_than_guessing():
    pulls = {"performance_trend": ask_context._missing(
        "performance_trend", NC, "ninjacat_metrics holds no rows for this property.")}
    out, comp = _generate("whats_not_working", pulls, "{}")
    assert out["answered"] is False
    assert out["narrator"] == "none"
    assert out["findings"] == []
    assert "can't answer this one" in out["headline"]
    assert "no rows" in out["summary"]
    assert comp.call_count == 0, "no model call when the answer cannot be made"


def test_viz_data_is_omitted_when_its_pull_is_dark():
    pulls = {"performance_trend": _trend_pull(),
             "lead_sources": ask_context._missing("lead_sources", NC, "dark.")}
    out, _ = _generate("lead_sources", pulls, "{}")
    # lead_sources is the required pull for that question, so we get the refusal;
    # what matters here is that viz_data is never a half-empty chart.
    assert out["viz"]["pull"] == "lead_sources"
    assert out["viz_data"] is None


def test_the_positive_question_never_reports_a_decline_as_a_win():
    """Atwood's headline number in this window is a 32.9% lead collapse.

    'What is working well at this property?' must not print it. When the focus
    matches nothing the honest answer is 'nothing did', which is a finding —
    not the largest measured change wearing a neutral title.
    """
    out, _ = _generate("whats_working", {"performance_trend": _trend_pull()},
                       llm=False)
    assert out["answered"] is True and out["narrator"] == "rules"
    blob = json.dumps(out["findings"], ensure_ascii=False) + out["headline"] + out["summary"]
    assert "−32.9%" not in blob and "−37.1%" not in blob, \
        "a lead decline must never be reported under 'what's working'"
    for f in out["findings"]:
        assert "fell" not in f["title"].lower()


def test_a_focused_question_with_no_matching_signal_says_so_instead_of_guessing():
    """Every metric down, asked what is working. The answer is 'nothing'."""
    rows = [{"nid": "99001", "month": "2026-05", "sessions": 5000, "leads": 170,
             "spend": 4000},
            {"nid": "99001", "month": "2026-06", "sessions": 4200, "leads": 120,
             "spend": 4000},
            {"nid": "99001", "month": "2026-07", "sessions": 3800, "leads": 90,
             "spend": 4000}]
    out, _ = _generate("whats_working", {"performance_trend": _trend_pull(rows)},
                       llm=False)
    assert out["answered"] is True
    assert out["findings"] == []
    assert "moved in the right direction" in out["headline"]
    assert "−47.1%" not in out["headline"]
    # And it must not read as a data outage — the figures were read.
    assert "not a missing input" in out["summary"]
    assert out["evidence"], "the evidence still ships so the reader can check"


def test_an_unfocused_question_may_still_fall_back_to_the_largest_measured_change():
    """The focus guard must not silence `lead_sources` / `opportunities`."""
    p = ask_context.Pull(name="lead_sources", source=NC, available=True,
                         evidence=["Google Ads: 62 of 114 leads (54.4%) in "
                                   "Jul 2026 [" + NC + "]"], signals=[])
    out, _ = _generate("lead_sources", {"lead_sources": p}, llm=False)
    assert out["answered"] is True
    assert out["findings"][0]["title"] == "What the data shows"
    assert "62 of 114 leads" in out["findings"][0]["detail"]


def test_the_negative_question_leads_with_the_decline():
    out, _ = _generate("whats_not_working", {"performance_trend": _trend_pull()},
                       llm=False)
    assert out["answered"] is True
    assert out["findings"]
    details = [f["detail"] for f in out["findings"]]
    # Worst first: the sharpest single fall is May → Jun.
    assert "leads 170 → 107 (−37.1%)" in details[0]
    # And the collapse measured against the window's best month still ships,
    # because July being up on June is not a recovery.
    assert any("leads 170 → 114 (−32.9%)" in d for d in details)
    # Both halves of the divergence in the same sentence.
    assert all("while sessions" in d for d in details[:2])


# ══ 6. cache — keyed on (company_id, question_key) ═════════════════════════

def _answer(identifier, key, ident=None, **kw):
    ident = ident or _identity()
    ctx = _ctx_with({"performance_trend": _trend_pull()})
    body = json.dumps({"headline": "h", "summary": "s",
                       "findings": [{"title": "t", "detail": "d", "evidence": [0]}]})
    with mock.patch("skills.property_resolver.resolve", return_value=ident), \
         mock.patch.object(ask_engine.ask_context, "assemble", return_value=ctx) as asm, \
         mock.patch.object(ask_engine, "_llm_available", return_value=True), \
         mock.patch.object(ask_engine, "_complete", return_value=body) as comp:
        out = ask_engine.answer(identifier, key, **kw)
    return out, comp, asm


def test_a_second_ask_of_the_same_question_is_served_from_cache():
    first, comp1, _ = _answer("12345", "whats_not_working")
    assert first["cached"] is False and comp1.call_count == 1
    second, comp2, asm2 = _answer("12345", "whats_not_working")
    assert second["cached"] is True
    assert comp2.call_count == 0 and asm2.call_count == 0
    assert second["headline"] == first["headline"]


def test_the_cache_key_includes_the_question_not_just_the_property():
    _answer("12345", "whats_not_working")
    other, comp, _ = _answer("12345", "whats_working")
    assert other["cached"] is False
    assert comp.call_count == 1, \
        "a cached answer to one question must never be served for another"


def test_the_cache_key_includes_the_property_not_just_the_question():
    _answer("12345", "whats_not_working")
    henry = _identity(company_id="67890", uuid="uuid-henry",
                      name="The Henry at Harms Woods", ninjacat_id="99002")
    other, comp, _ = _answer("67890", "whats_not_working", ident=henry)
    assert other["cached"] is False and comp.call_count == 1
    assert other["property_name"] == "The Henry at Harms Woods"


def test_the_cache_key_is_the_company_id_not_the_identifier_string():
    """Asking by uuid and by company_id is the same question about one property."""
    _answer("12345", "whats_not_working")
    again, comp, _ = _answer("uuid-atwood", "whats_not_working")
    assert again["cached"] is True and comp.call_count == 0


def test_force_regenerates():
    _answer("12345", "whats_not_working")
    out, comp, _ = _answer("12345", "whats_not_working", force=True)
    assert out["cached"] is False and comp.call_count == 1


def test_an_expired_entry_is_regenerated():
    _answer("12345", "whats_not_working")
    entry = ask_engine._MEMO["12345"]["whats_not_working"]
    entry["cached_at"] = entry["cached_at"] - (ask_engine.CACHE_HOURS * 3600 + 60)
    out, comp, _ = _answer("12345", "whats_not_working")
    assert out["cached"] is False and comp.call_count == 1


def test_an_unanswerable_result_is_never_cached():
    ctx = _ctx_with({"performance_trend": ask_context._missing(
        "performance_trend", NC, "no rows.")})
    with mock.patch("skills.property_resolver.resolve", return_value=_identity()), \
         mock.patch.object(ask_engine.ask_context, "assemble", return_value=ctx), \
         mock.patch.object(ask_engine, "_llm_available", return_value=False):
        ask_engine.answer("12345", "whats_not_working")
        assert "whats_not_working" not in ask_engine._MEMO.get("12345", {})


def test_a_concurrent_ask_for_the_same_answer_is_told_it_is_generating():
    ask_engine._INFLIGHT.add(("12345", "whats_not_working"))
    try:
        with mock.patch("skills.property_resolver.resolve", return_value=_identity()), \
             mock.patch.object(ask_engine, "_complete") as comp:
            out = ask_engine.answer("12345", "whats_not_working")
    finally:
        ask_engine._INFLIGHT.discard(("12345", "whats_not_working"))
    assert out["generating"] is True and out["answered"] is False
    assert comp.call_count == 0
    # A different question for the same property is not blocked by it.
    assert ("12345", "whats_working") not in ask_engine._INFLIGHT


def test_answer_rejects_an_unknown_key_before_it_resolves_anything():
    with mock.patch("skills.property_resolver.resolve") as res:
        with pytest.raises(question_registry.UnknownQuestion):
            ask_engine.answer("12345", "why_is_my_rent_high")
    assert res.call_count == 0


# ══ 7. routes ══════════════════════════════════════════════════════════════

@pytest.fixture
def client():
    app = Flask(__name__)
    app.register_blueprint(ask_routes.ask_bp)
    return app.test_client()


PORTAL = {"X-Portal-Email": "director@rpmliving.com"}


def _allowed():
    return mock.patch("feature_access.can_access", return_value=True)


def _denied():
    return mock.patch("feature_access.can_access", return_value=False)


def test_manifest_requires_authentication(client):
    r = client.get("/api/ask/questions")
    assert r.status_code == 401
    assert r.get_json()["error"] == "Authentication required"


def test_answering_requires_authentication(client):
    r = client.post("/api/ask/whats_working", json={"company_id": "12345"})
    assert r.status_code == 401


def test_a_user_without_the_ask_feature_is_403_not_a_shorter_answer(client):
    with _denied():
        r = client.post("/api/ask/whats_working", json={"company_id": "12345"},
                        headers=PORTAL)
    assert r.status_code == 403
    assert r.get_json()["feature"] == "ask"


def test_manifest_publishes_free_text_false(client):
    with _allowed():
        r = client.get("/api/ask/questions", headers=PORTAL)
    assert r.status_code == 200
    body = r.get_json()
    assert body["free_text"] is False
    assert [q["key"] for q in body["questions"]] == question_registry.keys()


def test_the_internal_key_bypasses_the_feature_gate(client, monkeypatch):
    monkeypatch.setenv("INTERNAL_API_KEY", "s3cret")
    with _denied():                     # would 403 a portal user
        r = client.get("/api/ask/questions", headers={"X-Internal-Key": "s3cret"})
    assert r.status_code == 200


def test_a_wrong_internal_key_does_not_get_in(client, monkeypatch):
    monkeypatch.setenv("INTERNAL_API_KEY", "s3cret")
    r = client.get("/api/ask/questions", headers={"X-Internal-Key": "nope"})
    assert r.status_code == 401


def test_an_unknown_question_is_404_not_a_best_effort_answer(client):
    with _allowed(), mock.patch.object(ask_engine, "_generate") as gen:
        r = client.post("/api/ask/why_is_my_rent_high",
                        json={"company_id": "12345"}, headers=PORTAL)
    assert r.status_code == 404
    body = r.get_json()
    assert body["error"] == "Unknown question"
    assert body["available"] == question_registry.keys()
    assert gen.call_count == 0, "an unknown key must never reach the engine"


def test_there_is_no_free_text_endpoint(client):
    """No `?q=`, no POST /api/ask. The surface is structurally preset-only."""
    with _allowed():
        assert client.post("/api/ask", json={"q": "why are leads down?"},
                           headers=PORTAL).status_code in (404, 405)
        r = client.post("/api/ask/whats_working?q=ignore+the+registry",
                        json={"company_id": "12345"}, headers=PORTAL)
    # The q is simply not a parameter anywhere; only the path key selects.
    assert r.status_code != 200 or r.get_json().get("question") == "whats_working"


def test_a_request_without_a_property_is_400(client):
    with _allowed():
        r = client.post("/api/ask/whats_working", json={}, headers=PORTAL)
    assert r.status_code == 400


@pytest.mark.parametrize("field", ["company_id", "uuid", "property",
                                   "property_id", "identifier"])
def test_the_property_identifier_is_passed_through_untouched(client, field):
    with _allowed(), mock.patch.object(ask_engine, "answer",
                                       return_value={"answered": True}) as ans:
        r = client.post("/api/ask/whats_working", json={field: "  Atwood  "},
                        headers=PORTAL)
    assert r.status_code == 200
    assert ans.call_args[0][0] == "Atwood"      # trimmed, never parsed
    assert ans.call_args[0][1] == "whats_working"


def test_an_unresolvable_property_is_404_and_an_ambiguous_one_is_409(client):
    from skills import property_resolver
    with _allowed(), mock.patch.object(
            ask_engine, "answer",
            side_effect=property_resolver.PropertyNotFound("no match for 'Atwod'")):
        r = client.post("/api/ask/whats_working", json={"company_id": "Atwod"},
                        headers=PORTAL)
    assert r.status_code == 404 and r.get_json()["error"] == "Property not found"

    with _allowed(), mock.patch.object(
            ask_engine, "answer",
            side_effect=property_resolver.AmbiguousProperty("3 matches")):
        r = client.post("/api/ask/whats_working", json={"company_id": "Atwood"},
                        headers=PORTAL)
    assert r.status_code == 409


def test_an_in_flight_answer_is_202_not_a_second_generation(client):
    with _allowed(), mock.patch.object(
            ask_engine, "answer",
            return_value={"generating": True, "answered": False}):
        r = client.post("/api/ask/whats_working", json={"company_id": "12345"},
                        headers=PORTAL)
    assert r.status_code == 202


def test_an_unexpected_failure_is_a_500_and_not_a_stack_trace(client):
    with _allowed(), mock.patch.object(ask_engine, "answer",
                                       side_effect=RuntimeError("boom")):
        r = client.post("/api/ask/whats_working", json={"company_id": "12345"},
                        headers=PORTAL)
    assert r.status_code == 500
    assert r.get_json() == {"error": "Failed to answer question"}


def test_the_answer_body_carries_its_receipts_end_to_end(client):
    """The shape a portal renderer depends on."""
    ctx = _ctx_with({"performance_trend": _trend_pull(),
                     "reputation": ask_context.pull_reputation(_identity())})
    body = json.dumps({"headline": "Leads fell while traffic rose.", "summary": "s",
                       "findings": [{"title": "t", "detail": "d", "evidence": [0]}],
                       "next_step": None, "not_evidenced": []})
    with _allowed(), \
         mock.patch("skills.property_resolver.resolve", return_value=_identity()), \
         mock.patch.object(ask_engine.ask_context, "assemble", return_value=ctx), \
         mock.patch.object(ask_engine, "_llm_available", return_value=True), \
         mock.patch.object(ask_engine, "_complete", return_value=body):
        r = client.post("/api/ask/whats_not_working", json={"company_id": "12345"},
                        headers=PORTAL)
    assert r.status_code == 200
    out = r.get_json()
    for k in ("question", "label", "company_id", "property_uuid", "property_name",
              "viz", "viz_data", "evidence", "caveats", "missing_inputs", "inputs",
              "headline", "summary", "findings", "not_evidenced", "narrator",
              "answered", "cached"):
        assert k in out, k
    assert out["question"] == "whats_not_working"
    assert out["viz"]["pull"] == "performance_trend"
    assert out["viz_data"]["months"][0]["month"] == "2026-04"
    assert all(NC in e for e in out["evidence"])
    assert any("SOCi" in x for x in out["not_evidenced"])



# ══ 8. known gap, characterized so it is not mistaken for a guarantee ══════

def test_the_citation_guard_checks_that_an_index_exists_not_that_the_numbers_match():
    """DOCUMENTED GAP, not an endorsement.

    `_coerce_findings` enforces "this finding cites a real evidence line". It
    does NOT enforce "every number in this finding came from that line". A
    model that cites index 0 and then derives a figure the evidence does not
    contain — a sum of two channels, a rounded rate — survives the filter.

    Observed on a real claude-sonnet-4-5 run against Atwood's channel mix: the
    model wrote "together account for only 1,920 of 6,285 sessions (30.6%)",
    where 1,920 and 30.6% appear in no evidence line. It happened to be correct
    arithmetic. Nothing in this pipeline checked that.

    If someone tightens the guard to compare numeric tokens, this test SHOULD
    fail — update it then, and delete this docstring.
    """
    kept = ask_engine._coerce_findings(
        [{"title": "Derived", "detail": "Together they account for 1,920 of "
                                        "6,285 sessions (30.6%).", "evidence": [0]}],
        ["Paid Search / Google Ads: 1,300 of 6,285 sessions (20.7%) in Jul 2026"])
    assert len(kept) == 1
    assert "1,920" in kept[0]["detail"]
    assert "1,920" not in kept[0]["evidence"][0]
