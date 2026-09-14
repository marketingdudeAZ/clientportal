"""Workspace inbox, views and portfolio — the skills behind the API.

Offline: every reader is mocked and `requests` is disabled. These tests pin the
normalization rules rather than the HTTP edge (tests/test_workspace_api.py
covers that):

* ids are "<source>:<source_id>"; loop recommendation ids are a deterministic
  hash that changes with the forecast run;
* status, lens, steps and receipts come from what the source carries;
* LLM-written text with a digit in it is withheld and the withholding is a gap;
* a dead source is a gap, never an empty queue;
* grouping, the decision overlay, role scoping and the Fair Housing hold.
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent / "webhook-server"))
sys.path.insert(0, str(TESTS))

from skills import workspace_common as wc  # noqa: E402
from skills import workspace_inbox as wi  # noqa: E402
from skills import workspace_portfolio as wp  # noqa: E402
from skills import workspace_views as wv  # noqa: E402

TODAY = date(2026, 9, 14)


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    def _no_network(*a, **k):
        raise RuntimeError("network disabled in workspace tests")
    monkeypatch.setattr("requests.sessions.Session.request", _no_network)
    wi._onboarding_cache.clear()


def _ctx(**props) -> wi.PropertyContext:
    base = {"uuid": "u-1", "name": "Prop"}
    base.update(props)
    return wi.PropertyContext("1", base["uuid"], base["name"], base)


def _item(source="hubdb_rec", sid="1", **kw):
    it = wi._new_item(source, sid, kw.pop("title", f"{source} {sid}"), **kw)
    return wi.finalize(it)


# ── ids and registry ─────────────────────────────────────────────────────────

class TestIds:
    def test_item_id_round_trip(self):
        assert wi.parse_item_id(wi.item_id("call_prep", "abc")) == ("call_prep", "abc")

    @pytest.mark.parametrize("bad", ["", "hubdb_rec", "hubdb_rec:", "nope:1", ":1"])
    def test_rejects_bad_ids(self, bad):
        with pytest.raises(ValueError):
            wi.parse_item_id(bad)

    def test_source_id_may_contain_colons(self):
        assert wi.parse_item_id("service_ticket:a:b") == ("service_ticket", "a:b")

    def test_loop_hash_is_deterministic_and_key_order_free(self):
        a = wi.loop_rec_hash("f1", {"action": "shift_budget", "amount": 300})
        b = wi.loop_rec_hash("f1", {"amount": 300, "action": "shift_budget"})
        assert a == b and len(a) == 16

    def test_loop_hash_changes_with_run_and_content(self):
        rec = {"action": "shift_budget", "amount": 300}
        assert wi.loop_rec_hash("f1", rec) != wi.loop_rec_hash("f2", rec)
        assert wi.loop_rec_hash("f1", rec) != wi.loop_rec_hash("f1", dict(rec, amount=301))

    def test_every_source_has_an_adapter_lens_and_stage(self):
        import loop_writer
        assert set(wi.ADAPTERS) == set(wi.SOURCES)
        assert set(wi.LENS.values()) <= {"express", "tailor", "amplify", "evolve"}
        assert set(wi.LENS) == set(wi.SOURCES)
        assert set(wi.STAGE.values()) <= loop_writer.LOOP_STAGES


# ── helpers ──────────────────────────────────────────────────────────────────

class TestCommon:
    def test_llm_text_withholds_digits(self):
        assert wc.llm_text("Refresh the creative") == "Refresh the creative"
        assert wc.llm_text("Seven units open") == "Seven units open"
        assert wc.llm_text("7 units open") is None
        assert wc.llm_text("  ") is None

    @pytest.mark.parametrize("raw,expected", [(93.5, 0.935), ("97.3%", 0.973), (0.9715, 0.9715), (None, None)])
    def test_ratio_accepts_both_scales(self, raw, expected):
        assert wc.ratio(raw) == expected

    def test_dates(self):
        assert wc.to_iso_date(1757808000000) == "2025-09-14"
        assert wc.to_iso_ts("2026-09-10T00:00:00+00:00") == "2026-09-10T00:00:00Z"
        assert wc.month_end("2026-02") == "2026-02-28"
        # AptIQ's "Report Generation Date" format, taken from the live export.
        assert wc.to_iso_ts("09/13/2026") == "2026-09-13T00:00:00Z"
        assert wc.to_iso_date("13/09/2026") is None
        assert wc.quarter_start(date(2026, 9, 14)) == date(2026, 7, 1)

    def test_metric_is_null_when_unknown(self):
        assert wc.metric(None, "aptiq", None) is None
        assert wc.metric(0, "aptiq", None) == {"value": 0, "source": "aptiq", "as_of": None}

    def test_fair_housing_flags(self):
        assert wc.fair_housing_flags("Great for families with children") != []
        assert wc.fair_housing_flags("A courtyard near the lake") == []


# ── adapters ─────────────────────────────────────────────────────────────────

@pytest.fixture
def recs(monkeypatch):
    import config
    import hubdb_helpers
    monkeypatch.setattr(config, "HUBDB_RECOMMENDATIONS_TABLE_ID", "t-recs", raising=False)
    rows = []
    monkeypatch.setattr(hubdb_helpers, "read_rows", lambda t, filters=None, limit=500: [dict(r) for r in rows])
    return rows


class TestHubdbRecs:
    def test_statuses_steps_and_receipts(self, recs):
        recs += [
            {"rec_id": "a", "rec_type": "budget_change", "title": "Shift paid", "body": "Units open soon",
             "status": "pending", "created_date": 1757808000000, "source": "red_light"},
            {"rec_id": "b", "rec_type": "strategy_change", "title": "Refresh SEO", "status": "approved"},
            {"rec_id": "c", "rec_type": "strategy_change", "title": "Old idea", "status": "dismissed"},
        ]
        gaps = []
        items = {i["source_id"]: wi.finalize(i) for i in wi._hubdb_recs(_ctx(), gaps, TODAY)}
        assert [items[k]["status"] for k in "abc"] == ["to_do", "in_motion", "done"]
        assert items["a"]["actions"] == {"approve": True, "not_now": True}
        assert items["b"]["actions"] == {"approve": False, "not_now": False}
        assert [s["kind"] for s in items["a"]["steps"]] == ["auto", "queued", "person"]
        assert items["a"]["receipts"] == [{"label": "Red Light report finding", "source": "red_light",
                                           "as_of": "2025-09-14T00:00:00Z"}]
        assert items["a"]["lens"] == "evolve" and items["c"]["_closed_as"] == "dismissed"
        assert gaps == []

    def test_llm_numbers_are_withheld_with_a_gap(self, recs):
        recs.append({"rec_id": "a", "rec_type": "budget_change", "title": "108 of 165 units sat 90+ days",
                     "body": "Spend $600 over 3 weeks", "status": "pending"})
        gaps = []
        item = wi._hubdb_recs(_ctx(), gaps, TODAY)[0]
        assert item["title"] == "Budget change recommendation"
        assert item["found"] is None
        assert "withheld on 1 item" in gaps[0]["reason"]
        # the raw text is kept for the handler, never shown
        assert item["_raw"]["title"].startswith("108")

    def test_needs_uuid_and_table(self, recs, monkeypatch):
        gaps = []
        assert wi._hubdb_recs(_ctx(uuid=""), gaps, TODAY) == []
        assert "uuid" in gaps[0]["reason"]
        import config
        monkeypatch.setattr(config, "HUBDB_RECOMMENDATIONS_TABLE_ID", "", raising=False)
        gaps = []
        assert wi._hubdb_recs(_ctx(), gaps, TODAY) == []
        assert "not configured" in gaps[0]["reason"]


class TestLoopRecs:
    def test_actionable_recs_only(self, monkeypatch):
        import forecasting
        rec = {"action": "shift_budget", "from_channel": "paid_social", "to_channel": "paid_search",
               "amount": 300.0, "reason": "paid_search converts cheaper", "forecast_impact": 1.2}
        monkeypatch.setattr(forecasting, "get_latest_forecast", lambda uuid: {
            "forecast_id": "f1", "run_at": "2026-09-10T00:00:00+00:00", "horizon_days": 30,
            "recommendations": [rec, {"action": "hold"}, {"action": "expand_inputs"}]})
        items = wi._loop_recs(_ctx(), [], TODAY)
        assert len(items) == 1
        it = items[0]
        assert it["id"] == "loop_rec:" + wi.loop_rec_hash("f1", rec)
        assert it["title"] == "Shift $300 from paid_social to paid_search"
        assert it["expect"] == "Forecast: +1.2 leases over 30 days"
        assert it["channels"] == ["paid_social", "paid_search"]
        assert it["internal_only"] is True and it["client_visible"] is False
        assert it["_raw"]["recommendation"] == rec

    def test_no_forecast_is_no_items(self, monkeypatch):
        import forecasting
        monkeypatch.setattr(forecasting, "get_latest_forecast", lambda uuid: None)
        assert wi._loop_recs(_ctx(), [], TODAY) == []


class TestCallPrep:
    def _payload(self, recs):
        return json.dumps({"generated_at": "2026-09-02T00:00:00Z", "recommendations": recs})

    def test_current_month_items(self):
        ctx = _ctx(callprep_cycle_month="2026-09", callprep_data_json=self._payload([
            {"rec_id": "r1", "title": "Refresh creative", "body": "Tired set", "channel": "paid_social",
             "status": "pending"},
            {"rec_id": "r2", "title": "Lift 1-bed spend", "status": "dismissed",
             "actioned_at": "2026-09-05T00:00:00Z", "actioned_by": "a@rpmliving.com"},
        ]))
        gaps = []
        items = wi._call_prep(ctx, gaps, TODAY)
        assert [i["status"] for i in items] == ["to_do", "done"]
        assert items[0]["due"] == "2026-09-30" and items[0]["lens"] == "tailor"
        assert items[1]["title"] == "Call prep recommendation"
        assert items[1]["trail"][-1]["actor"] == "a@rpmliving.com"
        assert "withheld" in gaps[0]["reason"]

    def test_stale_month_is_a_gap_not_items(self):
        ctx = _ctx(callprep_cycle_month="2026-08", callprep_data_json=self._payload([
            {"rec_id": "r1", "title": "Old", "status": "pending"}]))
        gaps = []
        assert wi._call_prep(ctx, gaps, TODAY) == []
        assert "2026-08" in gaps[0]["reason"]


class TestContentBriefs:
    def test_tier_gate_and_statuses(self, monkeypatch):
        import config
        import hubdb_helpers
        import seo_entitlement
        monkeypatch.setattr(config, "HUBDB_CONTENT_BRIEFS_TABLE_ID", "t-b", raising=False)
        monkeypatch.setattr(hubdb_helpers, "read_rows", lambda t, filters=None, limit=500: [
            {"brief_id": "b1", "hub_keyword": "apartments in tampa", "status": "generated", "h1": "Tampa living"},
            {"brief_id": "b2", "hub_keyword": "tampa lofts", "status": "published"}])
        monkeypatch.setattr(seo_entitlement, "has_feature", lambda tier, f: False)
        assert wi._content_briefs(_ctx(), [], TODAY) == []
        monkeypatch.setattr(seo_entitlement, "has_feature", lambda tier, f: True)
        items = wi._content_briefs(_ctx(), [], TODAY)
        assert [i["status"] for i in items] == ["to_do", "done"]
        assert items[0]["title"] == "Content brief: Tampa living"
        assert items[1]["title"] == "Content brief: tampa lofts"


class TestVideoVariants:
    def test_statuses(self):
        ctx = _ctx(video_cycle_month="2026-09", video_variants_json=json.dumps([
            {"variant_id": "v1", "title": "Pool", "status": "pending_review"},
            {"variant_id": "v2", "title": "Gym", "status": "approved", "approved_at": 1757808000000},
            {"variant_id": "v3", "title": "Lobby", "status": "running"},
            {"variant_id": "v4", "title": "Roof", "status": "failed"},
        ]))
        items = {i["source_id"]: wi.finalize(i) for i in wi._video_variants(ctx, [], TODAY)}
        assert items["v1"]["actions"]["approve"] is True
        assert (items["v2"]["status"], items["v3"]["status"]) == ("done", "in_motion")
        assert items["v4"]["internal_only"] is True and items["v4"]["actions"]["approve"] is False


class TestTicketProfile:
    def test_disabled_is_empty(self, monkeypatch):
        import ticket_profile_sync
        monkeypatch.setattr(ticket_profile_sync, "enabled", lambda: False)
        assert wi._ticket_profile(_ctx(), [], TODAY) == []

    def test_ai_proposals_with_numbers_are_withheld(self, monkeypatch):
        import ticket_profile_sync
        monkeypatch.setattr(ticket_profile_sync, "enabled", lambda: True)
        monkeypatch.setattr(ticket_profile_sync, "list_proposals", lambda *a, **k: [
            {"proposal_id": "p1", "task_id": "t1", "field_label": "Pet fee", "proposed_value": "$35 a month",
             "current_value": "$25", "extractor": "ai", "status": "proposed", "conflicts_with_override": True},
            {"proposal_id": "p2", "task_id": "t2", "field_label": "Parking", "proposed_value": "Garage",
             "extractor": "field_map", "status": "rejected"}])
        gaps = []
        items = wi._ticket_profile(_ctx(), gaps, TODAY)
        assert items[0]["found"] is None and items[0]["if_skip"] == "The profile keeps: $25"
        assert len(items[0]["receipts"]) == 2
        assert items[1]["found"] == "A completed ticket suggests: Garage"
        assert (items[1]["status"], items[1]["_closed_as"]) == ("done", "rejected")
        assert "withheld" in gaps[0]["reason"]


class TestOnboarding:
    def test_one_item_per_missing_check(self, monkeypatch):
        import onboarding
        calls = []
        monkeypatch.setattr(onboarding, "list_onboarding", lambda: calls.append(1) or [
            {"company_id": "1", "checklist": {"brief": True, "budget": False, "creative": False, "gbp": True},
             "done": 2, "total": 4}])
        assert wi._onboarding_gaps(_ctx(plestatus="RPM Managed"), [], TODAY) == []
        items = wi._onboarding_gaps(_ctx(plestatus="Onboarding"), [], TODAY)
        assert [i["id"] for i in items] == ["onboarding_gap:1-budget", "onboarding_gap:1-creative"]
        assert items[0]["found"] == "Onboarding checklist: 2 of 4 complete."
        assert items[0]["internal_only"] is True
        wi._onboarding_gaps(_ctx(plestatus="Onboarding"), [], TODAY)
        assert len(calls) == 1          # the portfolio-wide scan is cached


class TestTickets:
    def test_unreadable_ticket_store_is_a_gap(self, monkeypatch):
        import portal_tickets
        monkeypatch.setattr(portal_tickets, "list_tickets", lambda *a, **k: [])
        monkeypatch.setattr(portal_tickets, "tracking_degraded", lambda: True)
        gaps = []
        assert wi._portal_tickets(_ctx(), gaps, TODAY) == []
        assert "could not be read" in gaps[0]["reason"]

    def test_portal_ticket_statuses(self, monkeypatch):
        import portal_tickets
        monkeypatch.setattr(portal_tickets, "tracking_degraded", lambda: False)
        monkeypatch.setattr(portal_tickets, "list_tickets", lambda *a, **k: [
            {"id": "a", "subject": "A", "status": "Needs your approval"},
            {"id": "b", "subject": "B", "status": "Done"},
            {"id": "c", "subject": "C", "status": "Status unavailable", "unresolved": True}])
        gaps = []
        items = wi._portal_tickets(_ctx(), gaps, TODAY)
        assert [i["status"] for i in items] == ["to_do", "done", "in_motion"]
        assert all(wi.finalize(i)["actions"] == {"approve": False, "not_now": False} for i in items)
        assert "1 request" in gaps[0]["reason"]

    def test_service_ticket_placeholder_owner_is_not_a_name(self, monkeypatch):
        import ticket_manager
        monkeypatch.setattr(ticket_manager, "list_tickets", lambda *a, **k: [
            {"id": "h1", "subject": "Broken", "stage_id": "2", "stage_label": "In Progress", "owner_name": "Your AM"},
            {"id": "h2", "subject": "Fixed", "stage_id": "4", "owner_name": "Marcus Jennings",
             "updated_at": "2026-09-02T00:00:00Z"}])
        items = wi._service_tickets(_ctx(), [], TODAY)
        assert items[0]["owner"] is None and items[0]["status"] == "in_motion"
        assert items[1]["owner"] == "Marcus Jennings" and items[1]["status"] == "done"


# ── collection ───────────────────────────────────────────────────────────────

class TestCollect:
    def test_a_dead_source_is_a_gap_not_an_empty_queue(self, monkeypatch):
        def boom(ctx, gaps, today):
            raise RuntimeError("ClickUp down")
        monkeypatch.setitem(wi.ADAPTERS, "portal_ticket", boom)
        monkeypatch.setitem(wi.ADAPTERS, "video_variant", lambda ctx, gaps, today: [
            wi._new_item("video_variant", "v1", "Pool", needs_approval=True)])
        items, gaps = wi.collect(_ctx(), sources=["portal_ticket", "video_variant"], today=TODAY,
                                 with_history=False)
        assert [i["id"] for i in items] == ["video_variant:v1"]
        assert gaps == [{"field": "source:portal_ticket", "reason": "Could not be read (RuntimeError)",
                         "_source": "portal_ticket"}]

    def test_client_scope_drops_internal_items_and_their_gaps(self, monkeypatch):
        monkeypatch.setitem(wi.ADAPTERS, "call_prep", lambda ctx, gaps, today: (
            gaps.append(wc.gap("source:call_prep", "stale", source="call_prep")) or
            [wi._new_item("call_prep", "r1", "Internal")]))
        monkeypatch.setitem(wi.ADAPTERS, "portal_ticket", lambda ctx, gaps, today: [
            wi._new_item("portal_ticket", "t1", "Client request")])
        items, gaps = wi.collect(_ctx(), sources=["call_prep", "portal_ticket"], today=TODAY,
                                 internal=False, with_history=False)
        assert [i["id"] for i in items] == ["portal_ticket:t1"]
        assert gaps == []

    def test_fair_housing_hold(self, monkeypatch):
        monkeypatch.setitem(wi.ADAPTERS, "hubdb_rec", lambda ctx, gaps, today: [
            wi._new_item("hubdb_rec", "a", "Target families with children", needs_approval=True)])
        items, gaps = wi.collect(_ctx(), sources=["hubdb_rec"], today=TODAY, with_history=False)
        assert items[0]["internal_only"] is True and items[0]["client_visible"] is False
        assert gaps[0]["field"] == "item:hubdb_rec:a" and "Fair Housing" in gaps[0]["reason"]
        items, _ = wi.collect(_ctx(), sources=["hubdb_rec"], today=TODAY, internal=False, with_history=False)
        assert items == []

    def test_decision_overlay(self):
        a = wi._new_item("loop_rec", "a", "A", needs_approval=True)
        b = wi._new_item("content_brief", "b", "B", needs_approval=True)
        c = wi._new_item("video_variant", "c", "C", needs_approval=True)
        history = {
            "loop_rec:a": [{"at": "2026-09-12T10:00:00+00:00", "action": "approve", "actor": "x@rpmliving.com"}],
            "content_brief:b": [{"at": "2026-09-12T10:00:00+00:00", "action": "not_now",
                                 "reason": "not_priority", "actor": "x@rpmliving.com"}],
            "video_variant:c": [{"at": "2026-09-12T10:00:00+00:00", "action": "approve", "outcome": "failed"}],
        }
        wi.apply_decisions([a, b, c], history)
        for it in (a, b, c):
            wi.finalize(it)
        assert a["status"] == "in_motion" and a["actions"]["approve"] is False
        assert b["status"] == "done" and b["trail"][-1]["text"] == "Not now: Not a priority"
        assert c["status"] == "to_do" and c["actions"]["approve"] is True

    def test_decision_history_reads_workspace_events_only(self, monkeypatch):
        import loop_writer
        monkeypatch.setattr(loop_writer, "_bq", lambda: object())
        monkeypatch.setattr(loop_writer, "query_recent", lambda uuid, limit=500: [
            {"event_type": "workspace_decision", "occurred_at": "2026-09-12T10:00:00+00:00",
             "payload": {"item_id": "loop_rec:a", "action": "approve", "actor": "x"}},
            {"event_type": "recommendation_approved", "payload": {"item_id": "loop_rec:a"}}])
        out = wi.decision_history(_ctx(), [])
        assert list(out) == ["loop_rec:a"] and len(out["loop_rec:a"]) == 1

    def test_decision_history_without_bigquery_is_a_gap(self, monkeypatch):
        import loop_writer
        monkeypatch.setattr(loop_writer, "_bq", lambda: None)
        gaps = []
        assert wi.decision_history(_ctx(), gaps) == {}
        assert gaps[0]["field"] == "trail"


class TestGrouping:
    def test_late_this_week_later(self):
        items = [
            _item("call_prep", "late", due=(TODAY - timedelta(days=1)).isoformat(), needs_approval=True),
            _item("call_prep", "soon", due=(TODAY + timedelta(days=3)).isoformat(), needs_approval=True),
            _item("call_prep", "far", due=(TODAY + timedelta(days=20)).isoformat(), needs_approval=True),
            _item("portal_ticket", "undated", status="in_motion"),
            _item("service_ticket", "closed", status="done", due=(TODAY - timedelta(days=9)).isoformat()),
        ]
        out = wi.build_work(items, [], status="all", today=TODAY)
        assert [i["source_id"] for i in out["groups"]["late"]] == ["late"]
        assert [i["source_id"] for i in out["groups"]["this_week"]] == ["closed", "soon", "undated"]
        assert out["groups"]["later"] == {"count": 1, "titles": ["call_prep far"]}
        assert out["summary"] == {"open": 4, "late": 1, "next_deadline": (TODAY + timedelta(days=3)).isoformat()}
        assert out["counts"] == {"to_do": 3, "in_motion": 1, "done": 1}
        assert out["hidden_count"] == 0

    def test_status_filter_and_hidden_count(self):
        items = [_item("call_prep", "a", needs_approval=True), _item("portal_ticket", "b", status="in_motion")]
        out = wi.build_work(items, [], status="in_motion", today=TODAY)
        assert [i["source_id"] for i in out["groups"]["this_week"]] == ["b"]
        assert out["hidden_count"] == 1

    def test_internal_keys_never_leave(self):
        out = wi.build_work([_item()], [wc.gap("x", "y", source="hubdb_rec")], status="all", today=TODAY)
        assert not any(k.startswith("_") for k in out["groups"]["this_week"][0])
        assert out["gaps"] == [{"field": "x", "reason": "y"}]


class TestClientView:
    def test_committed_and_completed_only(self):
        items = [
            _item("portal_ticket", "moving", status="in_motion", title="New photos"),
            _item("hubdb_rec", "approved", status="done", _closed="2026-08-20", title="Paid moved"),
            _item("hubdb_rec", "dismissed", status="done", _closed="2026-08-20", _closed_as="dismissed"),
            _item("content_brief", "notnow", status="done", _closed="2026-08-20", _closed_as="not_now"),
            _item("video_variant", "lastq", status="done", _closed="2026-05-01"),
            _item("video_variant", "undated", status="done"),
            _item("call_prep", "internal", needs_approval=True, internal_only=True, client_visible=False),
            _item("hubdb_rec", "todo", needs_approval=True),
        ]
        out = wi.build_client_view(items, [], today=TODAY)
        assert [c["title"] for c in out["changing"]] == ["New photos"]
        assert [d["title"] for d in out["done_this_quarter"]] == ["Paid moved"]
        assert out["done_count"] == 1
        assert out["hidden_open_count"] == 1
        assert out["gaps"][0]["field"] == "done_this_quarter"


# ── views ────────────────────────────────────────────────────────────────────

@pytest.fixture
def aptiq(monkeypatch):
    from services.fluency_ingestion import apt_iq_csv_client, apt_iq_reader
    monkeypatch.setenv("APT_IQ_DAILY_SHEET_URL", "https://example.invalid/d.csv")
    state = {"row": {"matched": True, "occupancy_pct": 93.5, "available_units": 12}}
    monkeypatch.setattr(apt_iq_reader, "read_property", lambda company: state["row"])
    monkeypatch.setattr(apt_iq_csv_client, "_cache_loaded_at", 1757830000.0)
    return state


class TestViews:
    def test_performance_without_aptiq_is_null_with_gaps(self, monkeypatch):
        import spend_sheet
        monkeypatch.setattr(spend_sheet, "get_company_monthly_spend", lambda cid: {"total": 0.0, "by_sku": {},
                                                                                   "deal_id": None})
        out = wv.build_performance(_ctx(totalunits="200"))
        assert out["occupied"] is None and out["available_now"] is None and out["monthly_plan"] is None
        fields = [g["field"] for g in out["gaps"]]
        assert "aptiq" in fields and "monthly_plan" in fields

    def test_performance_scales_and_derives(self, aptiq, monkeypatch):
        import spend_sheet
        monkeypatch.setattr(spend_sheet, "get_company_monthly_spend", lambda cid: {
            "total": 1000.0, "by_sku": {"search": 600.0, "seo": 400.0}, "deal_id": "d"})
        monkeypatch.setattr(spend_sheet, "_cache", {})
        out = wv.build_performance(_ctx(aptiq_property_id="ap", totalunits="200", target_occupancy="94"))
        assert out["occupied"]["value"] == 0.935 and out["occupied"]["units"] == 187
        assert out["occupied"]["target"] == 0.94 and out["occupied"]["source"] == "aptiq"
        assert out["monthly_plan"]["by_channel"] == {"paid_search": 600.0, "seo": 400.0}

    def test_channel_amounts_keep_fees_apart(self):
        assert wv.channel_amounts({"search": 100, "pmax": 50, "mgmt_fee": 25, "mystery": 5}) == \
            {"paid_search": 150.0, "fees": 30.0}

    def test_brief_withheld_from_clients_on_fair_housing_hit(self, monkeypatch):
        import property_brief_audit
        monkeypatch.setattr(property_brief_audit, "recent_edits", lambda cid, limit=50: [])
        ctx = _ctx(fluency_romance="Perfect for families with children")
        gaps = []
        assert wv._brief(ctx, gaps, internal=True)["text"] == "Perfect for families with children"
        gaps = []
        client = wv._brief(ctx, gaps, internal=False)
        assert client["text"] is None and client["curated"] is True
        assert any(g["field"] == "brief.text" for g in gaps)

    def test_connections_distinguish_linked_from_connected(self, aptiq):
        ctx = _ctx(aptiq_property_id="ap", hyly_property_id="h1")
        out = {c["name"]: c for c in wv._connections(ctx, {"matched": True}, "2026-09-14T00:00:00Z", [])}
        assert out["ApartmentIQ"]["status"] == "connected"
        assert out["Hyly"]["status"] == "linked" and out["GA4"]["status"] == "not_connected"

    def test_people_prefer_names_and_skip_blank(self, monkeypatch):
        import portal_tickets
        monkeypatch.setattr(portal_tickets, "_owner_name", lambda oid: "")
        gaps = []
        people = wv._people(_ctx(hubspot_owner_id="9", marketing_manager_email="mm@rpmliving.com"), gaps)
        assert people == [{"name": "mm@rpmliving.com", "role": "Property marketing manager",
                           "email": "mm@rpmliving.com"}]
        assert gaps[0]["field"] == "people"


class TestPortfolio:
    def test_assignment_filter(self, monkeypatch):
        import portfolio
        monkeypatch.setattr(portfolio, "fetch_portfolio", lambda email, role: [
            {"hubspot_company_id": "1", "marketing_manager_email": "Dana@RPMLiving.com"},
            {"hubspot_company_id": "2", "marketing_rvp_email": "dana@rpmliving.com"},
            {"hubspot_company_id": "3", "marketing_manager_email": "other@rpmliving.com"}])
        assert [p["hubspot_company_id"] for p in wp.assigned_properties("dana@rpmliving.com")] == ["1", "2"]

    def test_ranking_and_quiet(self, monkeypatch):
        from services.fluency_ingestion import apt_iq_csv_client
        monkeypatch.setattr(wp, "assigned_properties", lambda email: [
            {"hubspot_company_id": "1", "aptiq_property_id": "a1"},
            {"hubspot_company_id": "2", "aptiq_property_id": "a2"},
            {"hubspot_company_id": "3", "aptiq_property_id": ""},
            {"hubspot_company_id": "4", "aptiq_property_id": "a4"}])
        monkeypatch.setattr(apt_iq_csv_client, "get_all_rows", lambda: {
            "a1": {"Available Units": "10", "Occupancy %": "90"},
            "a2": {"Available Units": "40", "Occupancy %": "80", "Report Generation Date": "09/13/2026"},
            "a4": {"Available Units": "99"}})
        monkeypatch.setattr(apt_iq_csv_client, "_cache_loaded_at", 1757830000.0)
        monkeypatch.setattr(wi, "load_context", lambda cid: _ctx())
        per = {"1": 2, "2": 1, "3": 1, "4": 0}

        def collect(ctx, **kw):
            return [wi.finalize(wi._new_item("hubdb_rec", f"{n}", f"Item {n}", needs_approval=True))
                    for n in range(per[collect.cid.pop(0)])], []
        collect.cid = ["1", "2", "3", "4"]
        monkeypatch.setattr(wp, "_MAX_WORKERS", 1)
        monkeypatch.setattr(wi, "collect", collect)
        out = wp.build_portfolio("dana@rpmliving.com", today=TODAY)
        assert [r["company_id"] for r in out["properties"]] == ["2", "1", "3"]
        assert out["quiet_count"] == 1 and out["item_count"] == 4
        assert out["properties"][1]["more_items"] == 1
        assert out["properties"][2]["units_at_risk"] is None
        assert out["properties"][0]["units_at_risk"]["as_of"] == "2026-09-13T00:00:00Z"
