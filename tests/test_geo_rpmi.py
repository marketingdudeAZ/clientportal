"""The RPMI GEO/AEO roll-up.

The finding this screen exists to carry is COVERAGE. One Searchable project
exists across 98 RPMI websites, so a roll-up that listed only the measured
properties would render a tidy screen and hide the state of the programme.

The rule these tests defend, over and over: "not measured yet" and "scored
zero" are opposite findings. One means we have nothing; the other means the
assistants never name us. A screen that lets them look alike is worse than no
screen, because someone would act on it.
"""
from __future__ import annotations

import os
import sys

import pytest
from flask import Flask

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "webhook-server"))
sys.path.insert(0, ROOT)

import skills  # noqa: E402
from skills import geo_rpmi  # noqa: E402

ROSTER = [
    {"company_id": "1", "uuid": "u1", "name": "Vitri Apartments",
     "domain": "vitriapartments.com", "city": "Scottsdale", "state": "AZ", "units": 300},
    {"company_id": "2", "uuid": "u2", "name": "75 West",
     "domain": "live75west.com", "city": "Atlanta", "state": "GA", "units": 220},
    {"company_id": "3", "uuid": "u3", "name": "No Site LLC",
     "domain": None, "city": "Austin", "state": "TX", "units": 100},
]

PROJECT = {"id": "p-1", "name": "Vitriapartments", "domain": "vitriapartments.com",
           "data_available_from": "2026-09-14T18:36:03.268Z", "is_active": True}

VIS = {"score": 33.3, "score_change": 13.3, "change_period": "30d",
       "totals": {"responses": 90, "mentions": 499, "citations": 166, "sources": 1202},
       "engines": [
           {"engine": "chatgpt", "visibility_rate": 36.7, "responses": 30,
            "responses_with_brand": 11},
           {"engine": "google-ai-overview", "visibility_rate": 20, "responses": 30,
            "responses_with_brand": 6}],
       "topics": ["Apartment Amenities"], "trend": [{"d": 1}, {"d": 2}, {"d": 3}]}


def fake_vendor(monkeypatch, *, configured=True, projects=(PROJECT,),
                projects_reason=None, vis=VIS, vis_reason=None):
    import searchable_mcp as sm
    monkeypatch.setattr(sm, "is_configured", lambda: configured)
    monkeypatch.setattr(sm, "list_projects",
                        lambda: (list(projects) if projects is not None else None,
                                 projects_reason))
    monkeypatch.setattr(sm, "visibility", lambda pid, days=30: (vis, vis_reason))
    return sm


@pytest.fixture(autouse=True)
def roster(monkeypatch):
    fake = type("R", (), {"get_roster": staticmethod(lambda *a, **k: [dict(r) for r in ROSTER])})
    monkeypatch.setattr(skills, "rpmi_roster", fake, raising=False)
    monkeypatch.setitem(sys.modules, "skills.rpmi_roster", fake)
    return fake


class TestCoverageIsTheFinding:
    def test_every_property_is_listed_measured_or_not(self, monkeypatch):
        fake_vendor(monkeypatch)
        out = geo_rpmi.build()
        assert out["property_count"] == 3
        assert {r["name"] for r in out["properties"]} == {
            "Vitri Apartments", "75 West", "No Site LLC"}

    def test_the_unmeasured_count_is_reported_not_implied(self, monkeypatch):
        fake_vendor(monkeypatch)
        out = geo_rpmi.build()
        assert (out["measured_count"], out["unmeasured_count"]) == (1, 2)

    def test_an_unmeasured_property_has_no_score_rather_than_a_zero(self, monkeypatch):
        """The whole point. A zero would read as "the assistants never name us",
        which is a finding; the truth is that nobody has asked yet."""
        fake_vendor(monkeypatch)
        out = geo_rpmi.build()
        row = [r for r in out["properties"] if r["name"] == "75 West"][0]
        assert row["measured"] is False
        assert row.get("score") is None
        assert "No Searchable project" in row["reason"]

    def test_a_property_with_no_website_says_that_instead(self, monkeypatch):
        """A different reason, and a different fix: this one is a HubSpot gap,
        not a Searchable one."""
        fake_vendor(monkeypatch)
        out = geo_rpmi.build()
        row = [r for r in out["properties"] if r["name"] == "No Site LLC"][0]
        assert "No website" in row["reason"]

    def test_the_average_covers_only_measured_properties(self, monkeypatch):
        """Averaging unmeasured properties in as zeros would understate the
        portfolio and make the programme look worse the more we roll it out."""
        fake_vendor(monkeypatch)
        out = geo_rpmi.build()
        assert out["average_score"] == 33.3

    def test_no_projects_at_all_says_measurement_has_not_started(self, monkeypatch):
        fake_vendor(monkeypatch, projects=[])
        out = geo_rpmi.build()
        assert out["measured_count"] == 0
        assert out["average_score"] is None
        assert any("history does not backfill" in g["message"] for g in out["gaps"])


class TestMeasuredRows:
    def test_a_measured_property_carries_the_numbers_a_client_would_see(self, monkeypatch):
        fake_vendor(monkeypatch)
        row = [r for r in geo_rpmi.build()["properties"] if r["measured"]][0]
        assert row["score"] == 33.3
        assert row["score_change"] == 13.3 and row["change_period"] == "30d"
        assert row["totals"]["responses"] == 90
        assert [e["engine"] for e in row["engines"]] == ["chatgpt", "google-ai-overview"]

    def test_it_carries_when_measurement_started(self, monkeypatch):
        """A project created yesterday has a score and no trend. Without this a
        single point reads as a flat line."""
        fake_vendor(monkeypatch)
        row = [r for r in geo_rpmi.build()["properties"] if r["measured"]][0]
        assert row["data_available_from"] == "2026-09-14T18:36:03.268Z"
        assert row["trend_points"] == 3

    def test_a_project_with_no_readings_is_unmeasured_not_zero(self, monkeypatch):
        fake_vendor(monkeypatch, vis=None, vis_reason="no readings yet")
        out = geo_rpmi.build()
        row = [r for r in out["properties"] if r["domain"] == "vitriapartments.com"][0]
        assert row["measured"] is False and row.get("score") is None
        assert row["reason"] == "no readings yet"

    def test_the_weakest_measured_property_sorts_first(self, monkeypatch):
        """The screen opens on the properties with the most room to move, and
        unmeasured ones never outrank a real score."""
        import searchable_mcp as sm
        fake_vendor(monkeypatch)
        monkeypatch.setattr(sm, "list_projects", lambda: ([
            PROJECT, dict(PROJECT, id="p-2", domain="live75west.com")], None))
        scores = {"p-1": 60.0, "p-2": 10.0}
        monkeypatch.setattr(sm, "visibility",
                            lambda pid, days=30: (dict(VIS, score=scores[pid]), None))
        rows = geo_rpmi.build()["properties"]
        assert [r["name"] for r in rows[:2]] == ["75 West", "Vitri Apartments"]
        assert rows[-1]["measured"] is False


class TestFailures:
    def test_an_unconfigured_vendor_is_a_gap_not_an_empty_portfolio(self, monkeypatch):
        fake_vendor(monkeypatch, configured=False)
        out = geo_rpmi.build()
        assert out["property_count"] == 3          # properties still listed
        assert out["measured_count"] == 0
        assert any("not connected" in g["message"] for g in out["gaps"])

    def test_a_vendor_outage_does_not_blank_the_screen(self, monkeypatch):
        fake_vendor(monkeypatch, projects=None, projects_reason="Searchable is temporarily unreachable.")
        out = geo_rpmi.build()
        assert out["property_count"] == 3
        assert any("unreachable" in g["message"] for g in out["gaps"])
        row = out["properties"][0]
        assert row["measured"] is False and row.get("score") is None

    def test_a_dead_roster_is_reported_rather_than_shown_as_zero_properties(self, monkeypatch):
        broken = type("R", (), {"get_roster": staticmethod(
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("hubspot down")))})
        monkeypatch.setattr(skills, "rpmi_roster", broken, raising=False)
        monkeypatch.setitem(sys.modules, "skills.rpmi_roster", broken)
        fake_vendor(monkeypatch)
        out = geo_rpmi.build()
        assert out["property_count"] == 0
        assert any("not complete" in g["message"] for g in out["gaps"])

    def test_reads_are_capped_and_the_remainder_is_counted(self, monkeypatch):
        """Each measured property is a vendor round trip. The cap must never
        silently drop properties from the list."""
        import searchable_mcp as sm
        fake_vendor(monkeypatch)
        monkeypatch.setattr(sm, "list_projects", lambda: ([
            PROJECT, dict(PROJECT, id="p-2", domain="live75west.com")], None))
        out = geo_rpmi.build(max_reads=1)
        assert out["property_count"] == 3
        assert any("not read on this page" in g["message"] for g in out["gaps"])


class TestTheRoute:
    # A verified session: the app-level proof gate answers 401 before any of
    # this runs, and what these tests are about is what happens AFTER identity.
    VERIFIED = {"portal.identity_verified": True}

    @pytest.fixture
    def client(self, monkeypatch):
        monkeypatch.setenv("WORKSPACE_ENABLED", "true")
        monkeypatch.delenv("WORKSPACE_REQUIRE_PROOF", raising=False)
        from routes.workspace import workspace_bp
        app = Flask(__name__)
        app.register_blueprint(workspace_bp)
        app.config["TESTING"] = True
        return app.test_client()

    def test_it_is_internal_only(self, client, monkeypatch):
        """It reads the whole RPMI portfolio, so an asserted email and
        preview-as-client are both refused."""
        import routes.workspace as rw
        monkeypatch.setattr(rw, "require_access", lambda key: None)
        monkeypatch.setattr(rw, "_is_internal", lambda: False)
        assert client.get("/api/workspace/geo",
                          environ_overrides=self.VERIFIED).status_code == 403

    def test_days_is_validated(self, client, monkeypatch):
        import routes.workspace as rw
        monkeypatch.setattr(rw, "require_access", lambda key: None)
        monkeypatch.setattr(rw, "_is_internal", lambda: True)
        for bad in ("0", "999", "abc", "-5"):
            assert client.get("/api/workspace/geo?days=%s" % bad,
                              environ_overrides=self.VERIFIED).status_code == 400

    def test_it_returns_the_roll_up(self, client, monkeypatch):
        import routes.workspace as rw
        monkeypatch.setattr(rw, "require_access", lambda key: None)
        monkeypatch.setattr(rw, "_is_internal", lambda: True)
        fake_vendor(monkeypatch)
        body = client.get("/api/workspace/geo",
                          environ_overrides=self.VERIFIED).get_json()
        assert body["property_count"] == 3 and body["measured_count"] == 1


def test_the_screen_never_shows_an_unmeasured_property_as_a_zero():
    """The page-side half of the same rule, checked in the markup because the
    server can only offer the distinction — the screen has to keep it."""
    page = open(os.path.join(ROOT, "webhook-server", "portal_pages", "workspace.html"),
                encoding="utf-8").read()
    block = page.split("function renderGeo()", 1)[1].split("function screenProperties", 1)[0]
    assert "empty-dash" in block
    assert "is not a zero" in page
