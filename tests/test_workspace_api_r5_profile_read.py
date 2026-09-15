"""Round 5 profile read: one classification source, internal fields stripped
server-side, provenance, staleness and completeness weighting.

Offline: HubSpot reads and the audit log are mocked; `requests` is disabled.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent / "webhook-server"))
sys.path.insert(0, str(TESTS))

from flask import Flask  # noqa: E402

import community_brief as cb  # noqa: E402
import feature_access  # noqa: E402
import workspace_contract as contract  # noqa: E402
from routes.workspace import workspace_bp  # noqa: E402
from skills import workspace_inbox as wi  # noqa: E402
from skills import workspace_profile as wpr  # noqa: E402

CID = "111"
INTERNAL = "dana.rogers@rpmliving.com"
CLIENT = "owner@acme.com"
NOW = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)

SPEC_AD_FACING = {
    "name", "address", "city", "state", "zip", "domain", "voice_tier", "unit_noun", "advertised_name",
    "short_name", "taglines", "brand_adjectives", "differentiators", "romance", "residents_love", "floor_plans",
    "property_amenities", "unit_features", "neighborhood", "nearby_neighborhoods", "landmarks",
    "neighborhood_highlights", "must_include", "forbidden_phrases",
}
SPEC_CONTEXT = {
    "former_property_name", "lifecycle_state", "year_built", "unit_level_details", "nearby_employers",
    "competitors", "goals", "initiatives", "onsite_developments", "local_partnerships", "onsite_events",
    "motivations_considerations", "tracking", "documents",
}

PROPS = {
    "name": "LYV Broadway", "city": "Carrollton", "state": "TX",
    "fluency_taglines": "Live where Broadway meets the lake.",
    "fluency_romance": "A courtyard community near the lake.",
    "fluency_property_amenities": "Pool\nFitness center",
    "fluency_property_amenities_override": "Rooftop pool\nFitness center\nDog park",
    "fluency_floor_plans_json": '[{"name": "A1", "beds": 1}]',
    "fluency_goals": "Reach 95% occupancy by spring",
    "fluency_residents_dislike": "Parking is tight.",
    "fluency_tracking_json": "[]",
}


def _ago(days):
    """As the HubDB audit table stores it."""
    return (NOW - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _iso(days):
    """As the API returns it."""
    return (NOW - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


AUDIT = [
    {"field_key": "romance", "edited_by": "dana.rogers@rpmliving.com", "edited_at": _ago(10),
     "old_value": "", "new_value": "A courtyard community near the lake."},
    {"field_key": "property_amenities", "edited_by": "marcus@rpmliving.com", "edited_at": _ago(20),
     "old_value": "", "new_value": "Rooftop pool"},
    {"field_key": "taglines", "edited_by": "owner@acme.com", "edited_at": _ago(120),
     "old_value": "", "new_value": "Live where Broadway meets the lake."},
]


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    import hubspot_client
    import property_brief_audit

    def _no_network(*a, **k):
        raise RuntimeError("network disabled in workspace tests")
    monkeypatch.setattr("requests.sessions.Session.request", _no_network)
    monkeypatch.setenv("WORKSPACE_ENABLED", "true")
    monkeypatch.setattr(property_brief_audit, "_table_id", lambda: "t-audit")
    monkeypatch.setattr(property_brief_audit, "recent_edits", lambda cid, limit=50: [dict(a) for a in AUDIT])
    monkeypatch.setattr(hubspot_client, "get_company", lambda cid, props=None: dict(PROPS))
    ctx = wi.PropertyContext(CID, "u-111", "LYV Broadway", {"uuid": "u-111"})
    monkeypatch.setattr(wi, "load_context", lambda cid: ctx)
    monkeypatch.setattr(feature_access, "_load_access_table", lambda: {
        CLIENT: {"role": "client", "beta_features": {"workspace"}, "companies": {CID}}})
    monkeypatch.setattr(feature_access, "_load_stage_table", lambda: {})
    feature_access.clear_cache()
    wpr.clear()
    yield
    feature_access.clear_cache()
    wpr.clear()


def _ctx():
    return wi.PropertyContext(CID, "u-111", "LYV Broadway", {"uuid": "u-111"})


def _fields(body):
    return {f["key"]: f for s in body["sections"] for f in s["fields"]}


class TestClassification:
    def test_one_source_matches_the_spec(self):
        internal = {k for k, f in cb.FIELDS.items() if f.internal}
        assert len(cb.FIELDS) == 55 and len(internal) == 17
        assert {k for k in cb.FIELDS if cb.is_ad_facing(k)} == SPEC_AD_FACING
        assert set(cb.FIELDS) - internal - SPEC_AD_FACING == SPEC_CONTEXT
        for key in internal:
            assert cb.used_in(key) == ["internal"]
        assert not (cb.VOICE_PACK_KEYS | cb.REPORT_KEYS) & internal
        assert set(cb.REPORT_KEYS) == {"goals", "initiatives", "lifecycle_state", "competitors"}
        for key in cb.FIELDS:
            assert set(cb.used_in(key)) <= set(cb.USED_IN_LABELS)

    def test_the_workspace_derives_rather_than_redefines(self):
        source = (TESTS.parent / "webhook-server" / "skills" / "workspace_profile.py").read_text()
        assert "AD_FACING_KEYS" not in source and "VOICE_PACK_KEYS" not in source


class TestBuild:
    def test_shape_and_receipts(self):
        body = wpr.build_profile(_ctx(), internal=True, now=NOW)
        contract.assert_shape(body, "profile")
        assert contract.numbers_without_source(body) == []
        assert [s["title"] for s in body["sections"]] == [t for t, _ in cb.SECTIONS]
        assert len(_fields(body)) == 55

    def test_provenance(self):
        f = _fields(wpr.build_profile(_ctx(), internal=True, now=NOW))
        amenities = f["property_amenities"]
        assert amenities["value"] == "Rooftop pool\nFitness center\nDog park"
        assert amenities["provenance"] == {"kind": "override", "by": "Marcus", "at": _iso(20),
                                           "source": "profile_edit", "overrides": "site_scrape"}
        assert f["floor_plans"]["provenance"]["kind"] == "resolved"
        assert f["floor_plans"]["provenance"]["source"] == "aptiq"
        assert f["name"]["provenance"]["source"] == "hubspot_company"
        assert f["romance"]["provenance"]["by"] == "Dana R."
        assert f["tracking"]["value"] is None and f["tracking"]["provenance"]["kind"] == "empty"
        assert f["unit_noun"]["used_in"] == ["ads"] and f["taglines"]["used_in"] == ["ads", "website_faq",
                                                                                     "ai_answers"]
        assert f["goals"]["used_in"] == ["reports"] and f["goals"]["ad_facing"] is False

    def test_staleness_from_audit_dates(self):
        body = wpr.build_profile(_ctx(), internal=True, now=NOW)
        f = _fields(body)
        assert f["taglines"]["stale"] is True and f["taglines"]["last_updated"] == _iso(120)
        assert f["romance"]["stale"] is False
        assert f["goals"]["stale"] is True and f["goals"]["last_updated"] is None     # value, no audit entry
        assert f["short_name"]["stale"] is False                                    # empty
        assert f["name"]["stale"] is False                                          # read-only
        assert f["floor_plans"]["stale"] is False                     # resolved by AptIQ, not a human value
        assert body["checkin"]["due"] is True
        assert set(body["checkin"]["stale_fields"]) == {"taglines", "goals", "residents_dislike"}

    def test_client_check_in_never_lists_internal_fields(self):
        body = wpr.build_profile(_ctx(), internal=False, now=NOW)
        assert set(body["checkin"]["stale_fields"]) == {"taglines", "goals"}

    def test_without_the_audit_table_staleness_is_unknown(self, monkeypatch):
        import property_brief_audit
        monkeypatch.setattr(property_brief_audit, "_table_id", lambda: "")
        internal = wpr.build_profile(_ctx(), internal=True, now=NOW)
        assert all(f["stale"] is None for f in _fields(internal).values())
        assert internal["checkin"] == {"due": False, "stale_fields": []}
        assert any(g.get("field") == "last_updated" for g in internal["gaps"])
        client = wpr.build_profile(_ctx(), internal=False, now=NOW)
        assert not any(g.get("field") == "last_updated" for g in client["gaps"])

    def test_reading_writes_nothing(self):
        with mock.patch("hubspot_client.patch_company") as patch_company, \
                mock.patch.object(cb, "write_field") as write_field:
            wpr.build_profile(_ctx(), internal=True, now=NOW)
        patch_company.assert_not_called()
        write_field.assert_not_called()


class TestCompleteness:
    def test_weighting(self):
        views = [
            {"key": "taglines", "label": "Taglines", "used_in": ["ads", "website_faq", "ai_answers"], "value": "x"},
            {"key": "short_name", "label": "Short Name", "used_in": ["ads"], "value": None},
            {"key": "goals", "label": "Goals", "used_in": ["reports"], "value": "x"},
            {"key": "tracking", "label": "Tracking", "used_in": [], "value": None},
        ]
        out = wpr.completeness(views)
        assert out["pct"] == round(100 * (3 + 1) / (3 + 3 + 1 + 1)) == 50
        assert out["weighted"] is True
        assert [m["key"] for m in out["top_missing"]] == ["short_name", "tracking"]

    def test_internal_fields_count_only_for_staff(self):
        client = wpr.build_profile(_ctx(), internal=False, now=NOW)["completeness"]
        staff = wpr.build_profile(_ctx(), internal=True, now=NOW)["completeness"]
        assert client["pct"] != staff["pct"]
        assert all(not cb.FIELDS[m["key"]].internal for m in client["top_missing"])
        assert all("ads" in m["used_in"] or "ai_answers" in m["used_in"] for m in client["top_missing"])


class TestRoute:
    @pytest.fixture
    def client(self):
        app = Flask(__name__)
        app.register_blueprint(workspace_bp)
        return app.test_client()

    def _get(self, client, email, preview=False):
        headers = {"X-Portal-Email": email}
        if preview:
            headers["X-Workspace-Preview-Role"] = "client"
        r = client.get(f"/api/workspace/profile?company_id={CID}", headers=headers)
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        contract.assert_shape(body, "profile")
        return body

    def _assert_client_view(self, body):
        fields = _fields(body)
        assert len(fields) == 38
        assert not any(cb.FIELDS[k].internal for k in fields)
        assert all("fair_housing_review" not in f for f in fields.values())
        assert "Parking is tight." not in str(body)

    def test_staff_see_all_55(self, client):
        fields = _fields(self._get(client, INTERNAL))
        assert len(fields) == 55 and "fair_housing_review" in fields["taglines"]

    def test_internal_fields_are_stripped_for_clients(self, client):
        self._assert_client_view(self._get(client, CLIENT))

    def test_and_in_preview_as_client(self, client):
        self._assert_client_view(self._get(client, INTERNAL, preview=True))

    def test_other_companies_are_refused(self, client):
        r = client.get("/api/workspace/profile?company_id=999", headers={"X-Portal-Email": CLIENT})
        assert r.status_code == 403
