"""Round 4: the monthly Fair Housing review.

Offline. The website fetch is either injected or runs through a transport
recorder that proves every request is a GET; HubSpot, HubDB, BigQuery and ClickUp
are mocked.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent / "webhook-server"))
sys.path.insert(0, str(TESTS))

from flask import Flask  # noqa: E402

import feature_access  # noqa: E402
import loop_writer  # noqa: E402
import workspace_contract as contract  # noqa: E402
from routes.workspace import workspace_bp  # noqa: E402
from skills import workspace_decisions as wd  # noqa: E402
from skills import workspace_fair_housing_review as fhr  # noqa: E402
from skills import workspace_inbox as wi  # noqa: E402

CID = "123"
INTERNAL = "dana@rpmliving.com"
NOW = datetime(2026, 9, 14, 6, 0, tzinfo=timezone.utc)
VERIFIED = {"portal.identity_verified": True}


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    def _no_network(*a, **k):
        raise RuntimeError("network disabled in workspace tests")
    monkeypatch.setattr("requests.sessions.Session.request", _no_network)
    for var in ("WORKSPACE_FH_AI_IMAGE_CHECK", "INTERNAL_API_KEY", "PORTAL_TICKETS_PILOT_EMAILS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("WORKSPACE_ENABLED", "true")
    monkeypatch.setattr(feature_access, "_load_access_table", lambda: {})
    monkeypatch.setattr(feature_access, "_load_stage_table", lambda: {})
    monkeypatch.setattr(loop_writer, "_bq", lambda: None)
    feature_access.clear_cache()
    fhr.clear()
    wd._recent.clear()
    yield
    fhr.clear()


@pytest.fixture
def events(monkeypatch):
    rec = mock.Mock(return_value="e")
    monkeypatch.setattr(loop_writer, "record", rec)
    return rec


@pytest.fixture
def ctx(monkeypatch):
    import hubspot_client
    c = wi.PropertyContext(CID, "u-123", "LYV Broadway", {"uuid": "u-123", "name": "LYV Broadway",
                                                          "domain": "https://lyvbroadway.com/"})
    monkeypatch.setattr(wi, "load_context", lambda cid: c)
    monkeypatch.setattr(hubspot_client, "get_company", lambda cid, props=None: {
        "fluency_taglines": "Perfect for young professionals.",
        "fluency_differentiators": "A single-car garage comes with every home.",
        "fluency_romance": "A courtyard community near the lake.",
        "fluency_residents_dislike": "No kids near the pool, please.",   # internal field: never checked
    })
    return c


SITE = {
    "https://lyvbroadway.com": "Welcome home. Adults only community with a rooftop pool.",
    "https://lyvbroadway.com/amenities": "Great for seniors who want quiet. Bright white kitchens.",
}


def _fetch(url):
    return SITE.get(url, "")


class TestCopyChecks:
    def test_high_and_low_severity_and_plain_words(self):
        high = fhr.copy_findings("Adults only community with a rooftop pool.", "Website: x")
        assert high[0]["severity"] == "high" and "Fair Housing rules" in high[0]["reason"]
        low = fhr.copy_findings("Great for seniors who want quiet.", "Website: x")
        assert low[0]["severity"] == "low" and "“seniors”" in low[0]["reason"]
        assert fhr.copy_findings("A single-car garage and bright white kitchens.", "Website: x") == []

    def test_next_run_is_the_same_day_next_month(self):
        assert fhr.next_run(datetime(2026, 1, 31, tzinfo=timezone.utc)).date().isoformat() == "2026-02-28"
        assert fhr.next_run(datetime(2026, 12, 5, tzinfo=timezone.utc)).date().isoformat() == "2027-01-05"


class TestRun:
    def test_record_shape_and_scope(self, ctx, events):
        record = fhr.run_property(CID, now=NOW, fetch=_fetch)
        contract.assert_shape(record, "fair_housing_review")
        assert record["run_at"] == "2026-09-14T06:00:00Z" and record["next_run"] == "2026-10-14T06:00:00Z"
        assert record["pages_checked"] == 2 and record["assets_checked"] is None
        locations = [f["location"] for f in record["findings"]]
        assert "Website: https://lyvbroadway.com" in locations
        assert "Website: https://lyvbroadway.com/amenities" in locations
        assert "Property profile: Taglines" in locations
        assert not any("Don't Love" in loc for loc in locations)       # internal fields are not client copy
        assert record["image_check"] == "disabled"
        args, kw = events.call_args
        assert args == ("ops", "workspace_fair_housing_review") and kw["payload"] is record
        assert loop_writer.is_known_event_type("workspace_fair_housing_review")

    def test_website_reads_are_get_only(self, ctx, events, monkeypatch):
        seen = []

        class _Resp:
            status_code, text, content = 200, "<p>Adults only living.</p>", b"x"

            def raise_for_status(self):
                return None

        def record(self, method, url, *a, **k):
            seen.append(str(method).upper())
            return _Resp()
        monkeypatch.setattr("requests.sessions.Session.request", record)
        out = fhr.run_property(CID, now=NOW)
        assert out["pages_checked"] == len(fhr.PAGE_PATHS)
        assert set(seen) == {"GET"}

    def test_image_rule_is_behind_the_flag(self, ctx, events, monkeypatch):
        from skills import workspace_creative
        monkeypatch.setattr(workspace_creative, "_asset_rows", lambda c, gaps: [
            {"id": 1, "asset_name": "pool-ai-edited", "file_type": "png", "file_url": "https://cdn/a.png"},
            {"id": 2, "asset_name": "lobby", "file_type": "jpg", "source": "photography"}])
        assert fhr.run_property(CID, now=NOW, fetch=_fetch)["assets_checked"] is None
        monkeypatch.setenv("WORKSPACE_FH_AI_IMAGE_CHECK", "true")
        record = fhr.run_property(CID, now=NOW, fetch=_fetch)
        images = [f for f in record["findings"] if f["kind"] == "image"]
        assert record["assets_checked"] == 2 and len(images) == 1
        assert images[0]["severity"] == "review" and "not confirmed" in images[0]["reason"]

    def test_image_rules_are_pluggable(self):
        assert fhr.ai_image_needs_disclosure_review in fhr.IMAGE_RULES


class TestItem:
    def test_findings_become_one_compliance_item(self, ctx, events):
        fhr.run_property(CID, now=NOW, fetch=_fetch)
        items, _ = wi.collect(ctx, sources=["fair_housing_review"], with_history=False)
        it = wi.view_item(items[0], internal=False)
        contract.assert_shape(it, "item")
        n = fhr._latest[CID]["findings_count"]
        assert it["title"] == f"Your monthly Fair Housing review for LYV Broadway found {n} item(s)"
        assert it["id"] == f"fair_housing_review:{CID}-2026-09-14" and it["actions"]["approve"] is True
        assert it["evidence"]["columns"] == ["Page or asset", "Excerpt", "Why flagged", "Suggested fix"]
        contract.assert_shape(it["review"], "fair_housing_review")
        from skills import workspace_dashboard
        assert workspace_dashboard.category_for(items[0]) == "compliance"

    def test_a_clean_review_is_not_an_approval(self, ctx, events, monkeypatch):
        fhr.run_property(CID, now=NOW, fetch=lambda url: "A courtyard community near the lake.")
        import hubspot_client
        monkeypatch.setattr(hubspot_client, "get_company", lambda cid, props=None: {})
        fhr.run_property(CID, now=NOW, fetch=lambda url: "A courtyard community near the lake.")
        assert fhr._latest[CID]["findings_count"] == 0
        items, _ = wi.collect(ctx, sources=["fair_housing_review"], with_history=False)
        assert items == []

    def test_approve_files_draft_fixes_and_publishes_nothing(self, ctx, events):
        import portal_tickets
        fhr.run_property(CID, now=NOW, fetch=_fetch)
        item_id = f"fair_housing_review:{CID}-2026-09-14"
        with mock.patch.object(portal_tickets, "create_ticket",
                               return_value=({"ok": True, "ticket": {"id": "cu8"}}, 201)) as create, \
                mock.patch("hubspot_client.patch_company") as patch_company:
            out = wd.decide(ctx, item_id, "approve", None, INTERNAL, internal=True)
        args, kw = create.call_args
        assert args == (CID, "general") and kw["internal"] is False
        assert "nothing publishes automatically" in kw["fields"]["Details"]
        assert "Adults only" in kw["fields"]["Details"]
        patch_company.assert_not_called()
        assert out["undo"]["available"] is False
        assert out["item"]["status"] == "in_motion"

    def test_not_now_records_only(self, ctx, events):
        import portal_tickets
        fhr.run_property(CID, now=NOW, fetch=_fetch)
        with mock.patch.object(portal_tickets, "create_ticket") as create:
            wd.decide(ctx, f"fair_housing_review:{CID}-2026-09-14", "not_now", "already_handled", INTERNAL,
                      internal=True)
        create.assert_not_called()


class TestRunRoute:
    @pytest.fixture
    def client(self):
        app = Flask(__name__)
        app.register_blueprint(workspace_bp)
        # Every caller here stands for a signed-in session (Clerk, or a verified
        # link). Internal reads now require a PROVEN identity, so a test client
        # that only asserts an email would be refused the staff view.
        c = app.test_client()
        c.environ_base["portal.identity_verified"] = True
        return c

    URL = "/api/internal/workspace/fair-housing-review/run"

    def test_internal_key_required_and_flag(self, client, monkeypatch):
        monkeypatch.setenv("INTERNAL_API_KEY", "k")
        assert client.post(self.URL, json={"company_id": CID}).status_code == 401
        monkeypatch.setenv("WORKSPACE_ENABLED", "false")
        assert client.post(self.URL, headers={"X-Internal-Key": "k"}, json={"company_id": CID}).status_code == 404

    def test_one_property(self, client, monkeypatch):
        monkeypatch.setenv("INTERNAL_API_KEY", "k")
        with mock.patch.object(fhr, "run_property", return_value={"run_at": "x"}) as run:
            r = client.post(self.URL, headers={"X-Internal-Key": "k"}, json={"company_id": CID})
        assert r.status_code == 200 and run.call_args.args == (CID,)

    def test_all_runs_in_the_background(self, client, monkeypatch):
        monkeypatch.setenv("INTERNAL_API_KEY", "k")
        with mock.patch.object(fhr, "run_all") as run_all:
            r = client.post(self.URL, headers={"X-Internal-Key": "k"}, json={"all": True, "limit": 5})
            for _ in range(50):
                if run_all.called:
                    break
                import time
                time.sleep(0.01)
        assert r.status_code == 202
        contract.assert_shape(r.get_json(), "fair_housing_run_all")
        run_all.assert_called_once_with(limit=5)
        assert client.post(self.URL, headers={"X-Internal-Key": "k"}, json={}).status_code == 400
