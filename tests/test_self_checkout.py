"""Tests for the Loop 1 self-checkout route (routes/self_checkout.py).

Every HubSpot / deal_creator / quote_generator call is mocked — no real deals,
quotes, or API traffic. Locks the guardrails (feature flag, auth, idempotency,
open-deal TOCTOU, per-day cap), the launch-date wiring (incl. the new-channel
buffer), and that the deal lands via deal_creator (test pipeline), never live.
"""

from __future__ import annotations

import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("HUBSPOT_API_KEY", "test-key")

import pytest  # noqa: E402
from flask import Flask  # noqa: E402

import hubspot_client  # noqa: E402
import deal_creator  # noqa: E402
import quote_generator  # noqa: E402
import loop_terminal_events  # noqa: E402
import launch_policy  # noqa: E402
from routes import self_checkout as sc  # noqa: E402

MON = date(2026, 6, 29)


@pytest.fixture(autouse=True)
def _mock_everything(monkeypatch):
    """Default-happy stubs; individual tests override as needed."""
    monkeypatch.setattr(hubspot_client, "search_deals", lambda *a, **k: [])
    monkeypatch.setattr(hubspot_client, "get_open_deals_for_company", lambda *a, **k: [])
    monkeypatch.setattr(hubspot_client, "get_company", lambda *a, **k: {"name": "The Glenn"})
    patched = {"launch": None, "deal": None}
    monkeypatch.setattr(deal_creator, "create_deal_with_line_items",
                        lambda *a, **k: "deal-1")
    monkeypatch.setattr(hubspot_client, "patch_deal",
                        lambda did, props: patched.update(launch=props.get(sc.LAUNCH_DATE_PROPERTY), deal=did) or {})
    monkeypatch.setattr(quote_generator, "generate_and_send_quote", lambda *a, **k: "quote-1")
    events = []
    monkeypatch.setattr(loop_terminal_events, "record_self_checkout_submitted",
                        lambda *a, **k: events.append(("submitted", k)))
    monkeypatch.setattr(loop_terminal_events, "record_deal_created",
                        lambda *a, **k: events.append(("created", k)))
    monkeypatch.setattr(sc, "_daily_counts", {})
    sc._captured = patched
    sc._events = events
    return patched, events


def _payload(**kw):
    base = dict(company_id="c-1", property_uuid="u-1", recommendation_id="r-1",
                channel="paid_search", recommended_budget=1700,
                change_type=launch_policy.ACTIVE_CHANNEL_INCREASE, launch_mode="asap")
    base.update(kw)
    return base


# ── happy path ───────────────────────────────────────────────────────────────


def test_creates_deal_quote_and_sets_launch_date():
    out = sc.process_self_checkout(_payload(), actor="pm@x.com", today=MON)
    assert out["deal_id"] == "deal-1"
    assert out["quote_id"] == "quote-1"
    assert out["idempotent"] is False
    assert out["launch_date"] == "2026-06-29"           # ASAP active increase = today
    assert sc._captured["launch"] == "2026-06-29"       # patched onto the deal
    kinds = [e[0] for e in sc._events]
    assert kinds == ["submitted", "created"]            # both funnel events fired


def test_new_channel_asap_gets_build_buffer():
    out = sc.process_self_checkout(
        _payload(change_type=launch_policy.NEW_CHANNEL_ACTIVATION), actor="pm@x.com", today=MON)
    assert out["launch_date"] == "2026-07-06"           # today + 5 business days
    assert sc._captured["launch"] == "2026-07-06"


# ── guardrails ───────────────────────────────────────────────────────────────


def test_idempotent_returns_existing_deal(monkeypatch):
    monkeypatch.setattr(hubspot_client, "search_deals",
                        lambda *a, **k: [{"id": "deal-existing", "properties": {}}])
    called = {"created": False}
    monkeypatch.setattr(deal_creator, "create_deal_with_line_items",
                        lambda *a, **k: called.update(created=True) or "nope")
    out = sc.process_self_checkout(_payload(), actor="pm@x.com", today=MON)
    assert out["idempotent"] is True
    assert out["deal_id"] == "deal-existing"
    assert called["created"] is False                   # no second deal created


def test_open_deal_for_channel_conflicts(monkeypatch):
    monkeypatch.setattr(hubspot_client, "get_open_deals_for_company",
                        lambda *a, **k: [{"properties": {"channel": "paid_search"}}])
    with pytest.raises(sc.CheckoutError) as e:
        sc.process_self_checkout(_payload(), actor="pm@x.com", today=MON)
    assert e.value.status == 409


def test_per_day_cap_trips(monkeypatch):
    monkeypatch.setattr(sc, "PER_DAY_CAP", 1)
    sc.process_self_checkout(_payload(recommendation_id="r-1"), actor="pm@x.com", today=MON)
    with pytest.raises(sc.CheckoutError) as e:
        sc.process_self_checkout(_payload(recommendation_id="r-2"), actor="pm@x.com", today=MON)
    assert e.value.status == 429


def test_missing_fields_rejected():
    with pytest.raises(sc.CheckoutError) as e:
        sc.process_self_checkout({"company_id": "c-1"}, actor="pm@x.com", today=MON)
    assert e.value.status == 400


def test_scheduled_requires_date():
    with pytest.raises(sc.CheckoutError) as e:
        sc.process_self_checkout(_payload(launch_mode="scheduled"), actor="pm@x.com", today=MON)
    assert e.value.status == 400


# ── route-level: flag + auth ─────────────────────────────────────────────────


def _client():
    app = Flask(__name__)
    app.register_blueprint(sc.self_checkout_bp)
    return app.test_client()


def test_route_404_when_disabled(monkeypatch):
    monkeypatch.delenv("SELF_CHECKOUT_ENABLED", raising=False)
    resp = _client().post("/api/self-checkout", json=_payload())
    assert resp.status_code == 404


def test_route_401_without_portal_email(monkeypatch):
    monkeypatch.setenv("SELF_CHECKOUT_ENABLED", "true")
    resp = _client().post("/api/self-checkout", json=_payload())
    assert resp.status_code == 401


def test_route_happy_with_auth(monkeypatch):
    monkeypatch.setenv("SELF_CHECKOUT_ENABLED", "true")
    resp = _client().post("/api/self-checkout", json=_payload(),
                          headers={"X-Portal-Email": "pm@x.com"})
    assert resp.status_code == 200
    assert resp.get_json()["deal_id"] == "deal-1"


# ── GET /recommendations + cron rearm endpoint ───────────────────────────────


def test_recommendations_404_when_disabled(monkeypatch):
    monkeypatch.delenv("SELF_CHECKOUT_ENABLED", raising=False)
    resp = _client().get("/api/self-checkout/recommendations?company_id=c-1",
                         headers={"X-Portal-Email": "pm@x.com"})
    assert resp.status_code == 404


def test_recommendations_empty_when_google_ads_unconfigured(monkeypatch):
    import google_ads_islost
    monkeypatch.setenv("SELF_CHECKOUT_ENABLED", "true")
    monkeypatch.setattr(hubspot_client, "get_company", lambda *a, **k: {"redlight_status": "RED"})
    monkeypatch.setattr(google_ads_islost, "fetch_islost_by_channel",
                        lambda cid: (_ for _ in ()).throw(google_ads_islost.GoogleAdsNotConfigured("x")))
    resp = _client().get("/api/self-checkout/recommendations?company_id=c-1",
                         headers={"X-Portal-Email": "pm@x.com"})
    body = resp.get_json()
    assert resp.status_code == 200
    assert body["cards"] == []
    assert body["reason"] == "google_ads_not_connected"


def test_recommendations_returns_cards(monkeypatch):
    import google_ads_islost
    import spend_sheet
    monkeypatch.setenv("SELF_CHECKOUT_ENABLED", "true")
    monkeypatch.setattr(hubspot_client, "get_company", lambda *a, **k: {"redlight_status": "RED"})
    monkeypatch.setattr(google_ads_islost, "fetch_islost_by_channel",
                        lambda cid: {"paid_search": 0.28})
    monkeypatch.setattr(spend_sheet, "get_company_monthly_spend",
                        lambda cid: {"by_sku": {"paid_search": 1500.0}})
    resp = _client().get("/api/self-checkout/recommendations?company_id=c-1&uuid=u-1",
                         headers={"X-Portal-Email": "pm@x.com"})
    body = resp.get_json()
    assert resp.status_code == 200
    assert len(body["cards"]) == 1
    card = body["cards"][0]
    assert card["channel"] == "paid_search"
    assert card["recommended_budget"] == 2083


def test_rearm_requires_internal_key(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_KEY", "secret")
    resp = _client().post("/api/internal/self-checkout/rearm")
    assert resp.status_code == 401


def test_rearm_runs_sweep(monkeypatch):
    import launch_rearm
    monkeypatch.setenv("INTERNAL_API_KEY", "secret")
    monkeypatch.setattr(launch_rearm, "rearm_stranded_deals",
                        lambda *a, **k: [{"deal_id": "d1"}])
    resp = _client().post("/api/internal/self-checkout/rearm",
                          headers={"X-Internal-Key": "secret"})
    assert resp.status_code == 200
    assert resp.get_json()["rearmed"] == 1
