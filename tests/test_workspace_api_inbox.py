"""Workspace inbox, views and portfolio — the skills behind the API.

Offline: every reader is mocked and `requests` is disabled. These tests pin the
normalization rules rather than the HTTP edge (tests/test_workspace_api.py
covers that):

* ids are "<source>:<source_id>"; loop recommendation ids are a deterministic
  hash that changes with the forecast run;
* status, lens, steps and receipts come from what the source carries;
* LLM-authored text keeps only sentences whose numbers match the source record;
* Fair Housing: high severity hides copy from clients, low severity only flags;
* the gap shape {message, field?, source?};
* the brief paragraph fallback chain, portfolio views, client view.
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

from skills import workspace_cache as wcache  # noqa: E402
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
    wcache.clear()


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

    def test_loop_hash_is_deterministic_and_changes_with_run(self):
        rec = {"action": "shift_budget", "amount": 300}
        assert wi.loop_rec_hash("f1", rec) == wi.loop_rec_hash("f1", {"amount": 300, "action": "shift_budget"})
        assert wi.loop_rec_hash("f1", rec) != wi.loop_rec_hash("f2", rec)

    def test_every_source_has_an_adapter_lens_stage_and_generic_title(self):
        import loop_writer
        assert set(wi.ADAPTERS) == set(wi.SOURCES) == set(wi.LENS) == set(wi.GENERIC_TITLES)
        assert set(wi.STAGE.values()) <= loop_writer.LOOP_STAGES

    def test_items_carry_no_internal_only_flag(self):
        item = _item()
        assert "internal_only" not in item and item["client_visible"] is True
        assert item["notes"] == [] and item["evidence"] is None and item["sparkline"] is None


# ── helpers ──────────────────────────────────────────────────────────────────

class TestCommon:
    def test_gap_shape(self):
        assert wc.gap("occupied", "No value", source="aptiq") == \
            {"message": "No value", "field": "occupied", "source": "aptiq"}
        assert wc.gap("source:portal_ticket", "Down") == {"message": "Down", "source": "portal_ticket"}
        assert wc.gaps_for([wc.gap(None, "a", internal=True), wc.gap(None, "b"), wc.gap(None, "b")],
                           internal=False) == [{"message": "b"}]

    def test_verified_text_keeps_matched_numbers_and_drops_unmatched_sentences(self):
        allowed = wc.number_forms(["91.8", 390, "0.935"])
        text = "Occupancy is 91.8 percent. About 108 units sat for months. The property has 390 homes."
        out, removed = wc.verified_text(text, allowed)
        assert out == "Occupancy is 91.8 percent. The property has 390 homes."
        assert removed == 1
        assert wc.verified_text("Occupancy is 97 percent.", allowed)[0] is None
        assert wc.verified_text("Occupancy sits at 93.5%.", allowed)[0] == "Occupancy sits at 93.5%."
        assert wc.verified_text("No numbers here.", set()) == ("No numbers here.", 0)

    def test_dates(self):
        assert wc.to_iso_date(1757808000000) == "2025-09-14"
        assert wc.to_iso_ts("09/13/2026") == "2026-09-13T00:00:00Z"
        assert wc.month_end("2026-02") == "2026-02-28"
        assert wc.quarter_start(date(2026, 9, 14)) == date(2026, 7, 1)

    def test_ratio_and_metric(self):
        assert (wc.ratio(93.5), wc.ratio("97.3%"), wc.ratio(0.9715), wc.ratio(None)) == (0.935, 0.973, 0.9715, None)
        assert wc.metric(None, "aptiq", None) is None

    @pytest.mark.parametrize("text", [
        "A single-car garage comes with every home.",
        "Pick any color for the accent wall.",
        "Residents of any age enjoy the pool.",
        "Bright white kitchens.",
    ])
    def test_plain_words_are_low_severity_only(self, text):
        review = wc.fair_housing_review(text)
        assert review is None or review["severity"] == "low"

    @pytest.mark.parametrize("text", ["No kids allowed in the pool area.", "Adults only community.",
                                      "Perfect for young professionals."])
    def test_hard_patterns_are_high_severity(self, text):
        assert wc.fair_housing_review(text)["severity"] == "high"


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
    def test_statuses_steps_receipts_and_signature_flag(self, recs):
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
        assert [s["kind"] for s in items["a"]["steps"]] == ["auto", "queued", "person"]
        assert items["a"]["receipts"][0]["source"] == "red_light"
        assert items["a"]["_requires_signature"] is True and items["b"]["_requires_signature"] is False
        assert items["a"]["trail"][0]["visibility"] == "client"
        assert gaps == []

    def test_numbers_matching_the_company_record_are_shown(self, recs):
        recs.append({"rec_id": "a", "rec_type": "budget_change", "status": "pending",
                     "title": "Occupancy is 91.8% against a 95% target",
                     "body": "Occupancy is 91.8%. Spend $600 over 3 weeks to recover."})
        gaps = []
        item = wi._hubdb_recs(_ctx(occupancy__="91.8", target_occupancy="95"), gaps, TODAY)[0]
        assert item["title"] == "Occupancy is 91.8% against a 95% target"
        assert item["found"] == "Occupancy is 91.8%."
        assert "1 recommendation card" in gaps[0]["message"]
        assert item["_raw"]["body"].endswith("recover.")     # the handler still gets the source text

    def test_generic_title_is_the_last_resort(self, recs):
        recs.append({"rec_id": "a", "rec_type": "budget_change", "status": "pending",
                     "title": "108 of 165 units sat 90+ days", "body": ""})
        assert wi._hubdb_recs(_ctx(), [], TODAY)[0]["title"] == "Budget change recommendation"

    def test_needs_uuid_and_table(self, recs, monkeypatch):
        gaps = []
        assert wi._hubdb_recs(_ctx(uuid=""), gaps, TODAY) == []
        assert "uuid" in gaps[0]["message"]
        import config
        monkeypatch.setattr(config, "HUBDB_RECOMMENDATIONS_TABLE_ID", "", raising=False)
        gaps = []
        assert wi._hubdb_recs(_ctx(), gaps, TODAY) == []
        assert gaps[0]["_internal"] is True


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
        assert it["title"] == "Shift $300 from paid_social to paid_search"
        assert it["expect"] == "Forecast: +1.2 leases over 30 days"
        assert it["client_visible"] is True and it["_requires_signature"] is True


class TestCallPrep:
    def _ctx(self, recs, month="2026-09", **extra):
        payload = {"generated_at": "2026-09-02T00:00:00Z", "recommendations": recs,
                   "summary": {"changed": "Occupancy moved to 91.8 percent. Leads rose 40 percent.",
                               "working": "Search is steady."},
                   "questions": ["Any events planned?"]}
        return _ctx(callprep_cycle_month=month, callprep_data_json=json.dumps(payload),
                    occupancy__="91.8", **extra)

    def test_items_notes_and_internal_trail(self):
        ctx = self._ctx([
            {"rec_id": "r1", "title": "Refresh creative", "body": "Tired set", "channel": "paid_social",
             "status": "pending"},
            {"rec_id": "r2", "title": "Lift 1-bed spend", "status": "dismissed",
             "actioned_at": "2026-09-05T00:00:00Z", "actioned_by": "a@rpmliving.com"},
        ])
        gaps = []
        items = wi._call_prep(ctx, gaps, TODAY)
        assert [i["status"] for i in items] == ["to_do", "done"]
        assert items[0]["due"] == "2026-09-30" and items[0]["lens"] == "tailor"
        assert items[1]["title"] == "Call prep recommendation"
        notes = [n["text"] for n in items[0]["notes"]]
        assert notes == ["What changed: Occupancy moved to 91.8 percent.",
                         "What's working: Search is steady.", "Ask on the call: Any events planned?"]
        assert all(n["visibility"] == "internal" for n in items[0]["notes"])
        assert all(t["visibility"] == "internal" for t in items[1]["trail"])
        assert gaps and "removed" in gaps[0]["message"]

    def test_stale_month_is_a_gap_not_items(self):
        gaps = []
        assert wi._call_prep(self._ctx([{"rec_id": "r1", "title": "Old", "status": "pending"}], month="2026-08"),
                             gaps, TODAY) == []
        assert "2026-08" in gaps[0]["message"]


class TestOtherAdapters:
    def test_content_brief_tier_gate_and_statuses(self, monkeypatch):
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

    def test_video_failed_render_is_an_internal_note_not_a_hidden_item(self):
        ctx = _ctx(video_cycle_month="2026-09", video_variants_json=json.dumps([
            {"variant_id": "v1", "title": "Pool", "status": "pending_review"},
            {"variant_id": "v4", "title": "Roof", "status": "failed", "error": "render timeout"}]))
        items = {i["source_id"]: wi.finalize(i) for i in wi._video_variants(ctx, [], TODAY)}
        assert items["v1"]["actions"]["approve"] is True
        assert items["v4"]["client_visible"] is True and items["v4"]["actions"]["approve"] is False
        assert items["v4"]["notes"][0]["visibility"] == "internal"

    def test_onboarding_evidence_table(self, monkeypatch):
        import onboarding
        monkeypatch.setattr(onboarding, "list_onboarding", lambda: [
            {"company_id": "1", "checklist": {"brief": True, "budget": False}, "done": 1, "total": 2}])
        items = wi._onboarding_gaps(_ctx(plestatus="Onboarding"), [], TODAY)
        assert items[0]["evidence"] == {"columns": ["Onboarding check", "Complete"],
                                        "rows": [["Finish the property brief", True],
                                                 ["Set the marketing budget", False]], "more_count": 0}

    def test_portal_ticket_store_down_is_a_gap(self, monkeypatch):
        import portal_tickets
        monkeypatch.setattr(portal_tickets, "list_tickets", lambda *a, **k: [])
        monkeypatch.setattr(portal_tickets, "tracking_degraded", lambda: True)
        gaps = []
        assert wi._portal_tickets(_ctx(), gaps, TODAY) == []
        assert gaps[0]["source"] == "portal_ticket"

    def test_service_ticket_description_is_a_client_note(self, monkeypatch):
        import ticket_manager
        monkeypatch.setattr(ticket_manager, "list_tickets", lambda *a, **k: [
            {"id": "h1", "subject": "Broken", "stage_id": "2", "owner_name": "Your AM",
             "description": "The form fails", "created_at": "2026-09-01T00:00:00Z"}])
        item = wi._service_tickets(_ctx(), [], TODAY)[0]
        assert item["owner"] is None and item["notes"][0]["visibility"] == "client"


# ── collection and role views ────────────────────────────────────────────────

class TestCollectAndView:
    def test_a_dead_source_is_a_gap_not_an_empty_queue(self, monkeypatch):
        def boom(ctx, gaps, today):
            raise RuntimeError("ClickUp down")
        monkeypatch.setitem(wi.ADAPTERS, "portal_ticket", boom)
        monkeypatch.setitem(wi.ADAPTERS, "video_variant", lambda ctx, gaps, today: [
            wi._new_item("video_variant", "v1", "Pool", needs_approval=True)])
        items, gaps = wi.collect(_ctx(), sources=["portal_ticket", "video_variant"], today=TODAY,
                                 with_history=False)
        assert [i["id"] for i in items] == ["video_variant:v1"]
        assert gaps == [{"message": "Could not be read (RuntimeError)", "source": "portal_ticket"}]

    def test_high_severity_copy_is_replaced_for_clients_only(self, monkeypatch):
        monkeypatch.setitem(wi.ADAPTERS, "hubdb_rec", lambda ctx, gaps, today: [
            wi._new_item("hubdb_rec", "a", "Market it as adults only", found="No kids nearby.",
                         needs_approval=True, _generic_title="Strategy change recommendation")])
        items, gaps = wi.collect(_ctx(), sources=["hubdb_rec"], today=TODAY, with_history=False)
        internal = wi.view_item(items[0], internal=True)
        client = wi.view_item(items[0], internal=False)
        assert internal["title"] == "Market it as adults only"
        assert internal["fair_housing_review"]["severity"] == "high"
        assert client["title"] == "Strategy change recommendation" and client["found"] is None
        assert "fair_housing_review" not in client
        assert wc.gaps_for(gaps, internal=False) == []

    def test_low_severity_copy_is_shown_and_flagged_internally(self, monkeypatch):
        monkeypatch.setitem(wi.ADAPTERS, "hubdb_rec", lambda ctx, gaps, today: [
            wi._new_item("hubdb_rec", "a", "Add a single photo of the pool", needs_approval=True)])
        items, _ = wi.collect(_ctx(), sources=["hubdb_rec"], today=TODAY, with_history=False)
        client = wi.view_item(items[0], internal=False)
        assert client["title"] == "Add a single photo of the pool"
        assert wi.view_item(items[0], internal=True)["fair_housing_review"]["severity"] == "low"

    def test_view_filters_trail_and_notes_and_recounts(self):
        item = _item(trail=[wi.trail(None, "a", "Opened"), wi.trail(None, "b", "Internal", "internal")],
                     notes=[wi.note(None, "a", "private"), wi.note(None, "b", "shared", "client")])
        assert wi.view_item(item, True)["comments_count"] == 2
        client = wi.view_item(item, False)
        assert [t["text"] for t in client["trail"]] == ["Opened"]
        assert [n["text"] for n in client["notes"]] == ["shared"] and client["comments_count"] == 1
        assert not any(k.startswith("_") for k in client)

    def test_decision_overlay_honors_undo(self):
        a = wi._new_item("loop_rec", "a", "A", needs_approval=True)
        b = wi._new_item("content_brief", "b", "B", needs_approval=True)
        history = {
            "loop_rec:a": [{"at": "2026-09-12T10:00:00+00:00", "action": "approve", "actor": "x",
                            "undone": True, "undone_at": "2026-09-12T10:05:00+00:00"}],
            "content_brief:b": [{"at": "2026-09-12T10:00:00+00:00", "action": "not_now",
                                 "reason": "not_priority", "actor": "x"}],
        }
        wi.apply_decisions([a, b], history)
        wi.finalize(a), wi.finalize(b)
        assert a["status"] == "to_do" and a["actions"]["approve"] is True
        assert [t["text"] for t in a["trail"]] == ["Approved", "Decision undone"]
        assert a["trail"][1]["visibility"] == "internal"
        assert b["status"] == "done" and b["trail"][-1]["text"] == "Not now: Not a priority"

    def test_decision_history_pairs_undo_with_the_decision(self, monkeypatch):
        import loop_writer
        monkeypatch.setattr(loop_writer, "_bq", lambda: object())
        monkeypatch.setattr(loop_writer, "query_recent", lambda uuid, limit=500: [
            {"event_type": "workspace_decision_undone", "occurred_at": "2026-09-12T10:05:00+00:00",
             "payload": {"item_id": "loop_rec:a", "actor": "x"}},
            {"event_type": "workspace_decision", "occurred_at": "2026-09-12T10:00:00+00:00",
             "payload": {"item_id": "loop_rec:a", "action": "approve", "actor": "x"}},
            {"event_type": "recommendation_approved", "payload": {"item_id": "loop_rec:a"}}])
        out = wi.decision_history(_ctx(), [])
        assert len(out["loop_rec:a"]) == 1 and out["loop_rec:a"][0]["undone"] is True

    def test_decision_history_without_bigquery_is_none_with_internal_gap(self, monkeypatch):
        import loop_writer
        monkeypatch.setattr(loop_writer, "_bq", lambda: None)
        gaps = []
        assert wi.decision_history(_ctx(), gaps) is None
        assert gaps[0]["field"] == "trail" and gaps[0]["_internal"] is True


class TestWorkAndClientView:
    def test_grouping_and_badge(self):
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
        assert out["summary"] == {"open": 4, "late": 1, "needs_approval": 3,
                                  "next_deadline": (TODAY + timedelta(days=3)).isoformat()}

    def test_client_view_has_no_hidden_count_and_no_go_live_date(self):
        items = [
            _item("portal_ticket", "moving", status="in_motion", title="New photos"),
            _item("call_prep", "internal", status="in_motion", title="Call prep work"),
            _item("hubdb_rec", "approved", status="done", _closed="2026-08-20", title="Paid moved"),
            _item("hubdb_rec", "dismissed", status="done", _closed="2026-08-20", _closed_as="dismissed"),
            _item("video_variant", "lastq", status="done", _closed="2026-05-01"),
        ]
        out = wi.build_client_view(items, [], today=TODAY)
        assert "hidden_open_count" not in out
        assert [c["title"] for c in out["changing"]] == ["Call prep work", "New photos"]
        assert all(c["date"] is None for c in out["changing"])
        assert [d["title"] for d in out["done_this_quarter"]] == ["Paid moved"]


# ── views ────────────────────────────────────────────────────────────────────

class TestBriefChain:
    def _brief(self, monkeypatch, internal=True, **props):
        import property_brief_audit
        monkeypatch.setattr(property_brief_audit, "recent_edits", lambda cid, limit=50: [])
        gaps = []
        return wv._brief(_ctx(**props), gaps, internal), gaps

    def test_romance_wins(self, monkeypatch):
        out, _ = self._brief(monkeypatch, fluency_romance="A lakeside story.",
                             what_makes_this_property_unique_="Big dog park")
        assert out["text"] == "A lakeside story." and out["curated"] is True and out["source"] == "fluency_romance"

    def test_composed_from_fields_verbatim(self, monkeypatch):
        out, _ = self._brief(monkeypatch, what_makes_this_property_unique_="Big dog park.",
                             property_voice_and_tone="  ", additional_selling_points="Rooftop deck.")
        assert out == {"text": "Big dog park. Rooftop deck.", "curated": False, "edited_by": None,
                       "edited_at": None, "source": "composed"}

    def test_null_with_gap(self, monkeypatch):
        out, gaps = self._brief(monkeypatch)
        assert out["text"] is None and gaps[0]["field"] == "brief.text"

    def test_high_severity_brief_is_hidden_from_clients(self, monkeypatch):
        client, _ = self._brief(monkeypatch, internal=False, fluency_romance="Adults only living.")
        internal, _ = self._brief(monkeypatch, fluency_romance="Adults only living.")
        assert client["text"] is None and "fair_housing_review" not in client
        assert internal["text"] == "Adults only living." and internal["fair_housing_review"]["severity"] == "high"


class TestViews:
    def test_performance_without_aptiq_is_null_with_gaps(self, monkeypatch):
        monkeypatch.setattr(wcache, "monthly_spend", lambda cid: (
            {"total": 0.0, "by_sku": {}, "zero_skus": [], "deal_id": None}, None))
        out = wv.build_performance(_ctx(totalunits="200"))
        assert out["occupied"] is None and out["available_now"] is None and out["monthly_plan"] is None
        assert {"aptiq", "monthly_plan"} <= {g.get("field") for g in out["gaps"]}

    def test_channel_amounts_keep_fees_apart(self):
        assert wv.channel_amounts({"search": 100, "pmax": 50, "mgmt_fee": 25, "mystery": 5}) == \
            {"paid_search": 150.0, "fees": 30.0}

    def test_connections_distinguish_linked_from_connected(self):
        out = {c["name"]: c for c in wv._connections(_ctx(hyly_property_id="h1"), {"matched": True},
                                                     "2026-09-14T00:00:00Z", [])}
        assert out["ApartmentIQ"]["status"] == "connected"
        assert out["Hyly"]["status"] == "linked" and out["GA4"]["status"] == "not_connected"

    def test_monthly_spend_matches_the_spend_sheet(self, monkeypatch):
        import spend_sheet
        rows = [{"company_id": "1", "deal_id": "d", "deal_name": "IO", "search": 100.0, "seo": "250",
                 "tiktok": 0, "mgmt_fee": None}]
        monkeypatch.setattr(spend_sheet, "get_spend_sheet_data", lambda force=False: rows)
        monkeypatch.setattr(wcache, "spend_rows", lambda: (rows, "2026-09-14T00:00:00Z"))
        ours, _ = wcache.monthly_spend("1")
        theirs = spend_sheet.get_company_monthly_spend("1")
        assert {k: ours[k] for k in theirs} == theirs
        assert ours["zero_skus"] == ["tiktok"]


class TestPortfolio:
    def test_assignment_filter(self, monkeypatch):
        monkeypatch.setattr(wp, "managed_properties", lambda: [
            {"hubspot_company_id": "1", "marketing_manager_email": "Dana@RPMLiving.com"},
            {"hubspot_company_id": "2", "marketing_rvp_email": "dana@rpmliving.com"},
            {"hubspot_company_id": "3", "marketing_manager_email": "other@rpmliving.com"}])
        assert [p["hubspot_company_id"] for p in wp.assigned_properties("dana@rpmliving.com")] == ["1", "2"]

    def test_needs_me_ranks_and_counts_quiet(self, monkeypatch):
        props = [{"hubspot_company_id": str(i), "aptiq_property_id": f"a{i}",
                  "marketing_manager_email": "dana@rpmliving.com"} for i in (1, 2, 3, 4)]
        monkeypatch.setattr(wp, "managed_properties", lambda: props)
        monkeypatch.setattr(wcache, "aptiq_daily", lambda: ({
            "a1": {"Available Units": "10"}, "a2": {"Available Units": "40", "Report Generation Date": "09/13/2026"},
            "a4": {"Available Units": "99"}}, "2026-09-14T00:00:00Z"))
        per = {"1": 2, "2": 1, "3": 1, "4": 0}
        monkeypatch.setattr(wi, "load_context", lambda cid: wi.PropertyContext(cid, "", "", {}))
        monkeypatch.setattr(wi, "collect", lambda ctx, **kw: (
            [wi.finalize(wi._new_item("hubdb_rec", f"{n}", f"Item {n}", needs_approval=True))
             for n in range(per[ctx.company_id])], []))
        out = wp.build_portfolio("dana@rpmliving.com", today=TODAY)
        assert out["view"] == "needs_me"
        assert [r["company_id"] for r in out["properties"]] == ["2", "1", "3"]
        assert out["quiet_count"] == 1 and out["item_count"] == 4
        assert out["properties"][0]["units_at_risk"]["as_of"] == "2026-09-13T00:00:00Z"
        assert out["properties"][2]["units_at_risk"] is None
