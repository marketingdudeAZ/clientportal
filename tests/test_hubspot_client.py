"""Tests for the central HubSpot client (hubspot_client.py).

Locks the four things the client exists to enforce once instead of across 39
scattered call sites: the R1 immutable-uuid guard, 401 reload-and-retry (the
fix for the "every webhook 401'd" outage), 429 backoff, and the read-through
cache with write-through invalidation. No real HubSpot I/O — a fake session
returns queued responses.
"""

from __future__ import annotations

import os
import sys

# Make webhook-server/ importable (same convention as test_brief_resolver.py).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("HUBSPOT_API_KEY", "test-key")

import pytest  # noqa: E402

import hubspot_client as hc  # noqa: E402


# ── fakes ────────────────────────────────────────────────────────────────────


class FakeResponse:
    def __init__(self, status_code: int, json_body: dict | None = None,
                 headers: dict | None = None):
        self.status_code = status_code
        self._json = json_body or {}
        self.headers = headers or {}
        self.text = str(json_body)

    def json(self) -> dict:
        return self._json


class FakeSession:
    """Returns queued responses in order and records each request's (method,url)."""

    def __init__(self, responses: list[FakeResponse]):
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def request(self, method, url, headers=None, **kwargs):
        self.calls.append((method, url))
        if not self._responses:
            raise AssertionError(f"unexpected extra request: {method} {url}")
        return self._responses.pop(0)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """Fresh cache + session per test; never really sleep on 429 backoff."""
    hc.clear_cache()
    hc._SESSION = None
    monkeypatch.setattr(hc.time, "sleep", lambda *_: None)
    yield
    hc.clear_cache()
    hc._SESSION = None


def _install(responses: list[FakeResponse]) -> FakeSession:
    fake = FakeSession(responses)
    hc._SESSION = fake
    return fake


# ── R1 guard ─────────────────────────────────────────────────────────────────


def test_patch_company_rejects_uuid():
    with pytest.raises(hc.R1Violation):
        hc.patch_company("123", {"uuid": "should-never-write", "name": "X"})


def test_create_company_rejects_uuid():
    with pytest.raises(hc.R1Violation):
        hc.create_company({"uuid": "nope", "name": "X"})


def test_batch_patch_rejects_uuid_in_any_item():
    with pytest.raises(hc.R1Violation):
        hc.batch_patch_companies([
            {"id": "1", "properties": {"name": "ok"}},
            {"id": "2", "properties": {"uuid": "nope"}},
        ])


def test_patch_company_without_uuid_succeeds():
    fake = _install([FakeResponse(200, {"id": "1", "properties": {"name": "X"}})])
    out = hc.patch_company("1", {"name": "X"})
    assert out["id"] == "1"
    assert fake.calls == [("PATCH", f"{hc._COMPANIES}/1")]


# ── read-through cache + write-through invalidation ─────────────────────────


def test_get_company_is_cached():
    fake = _install([FakeResponse(200, {"properties": {"name": "X"}})])
    a = hc.get_company("1", ["name"])
    b = hc.get_company("1", ["name"])
    assert a == b == {"name": "X"}
    assert len(fake.calls) == 1  # second read served from cache


def test_patch_invalidates_cache():
    fake = _install([
        FakeResponse(200, {"properties": {"name": "old"}}),   # first GET
        FakeResponse(200, {"id": "1", "properties": {}}),       # PATCH
        FakeResponse(200, {"properties": {"name": "new"}}),     # GET after invalidation
    ])
    assert hc.get_company("1", ["name"]) == {"name": "old"}
    hc.patch_company("1", {"name": "new"})
    assert hc.get_company("1", ["name"]) == {"name": "new"}
    assert len(fake.calls) == 3  # cache was busted by the write


def test_cache_respects_ttl(monkeypatch):
    fake = _install([
        FakeResponse(200, {"properties": {"name": "a"}}),
        FakeResponse(200, {"properties": {"name": "b"}}),
    ])
    t = {"now": 1000.0}
    monkeypatch.setattr(hc.time, "monotonic", lambda: t["now"])
    assert hc.get_company("1", ["name"]) == {"name": "a"}
    t["now"] += hc._CACHE_TTL + 1  # expire
    assert hc.get_company("1", ["name"]) == {"name": "b"}
    assert len(fake.calls) == 2


# ── 401 reload + retry once ──────────────────────────────────────────────────


def test_401_reloads_token_and_retries_once():
    fake = _install([
        FakeResponse(401),
        FakeResponse(200, {"properties": {"name": "X"}}),
    ])
    assert hc.get_company("1", ["name"]) == {"name": "X"}
    assert len(fake.calls) == 2  # one failure, one successful retry


def test_persistent_401_raises_auth_error():
    _install([FakeResponse(401), FakeResponse(401)])
    with pytest.raises(hc.HubSpotAuthError):
        hc.get_company("1", ["name"])


# ── 429 backoff ──────────────────────────────────────────────────────────────


def test_429_backs_off_then_succeeds(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(hc.time, "sleep", lambda s: slept.append(s))
    _install([
        FakeResponse(429, headers={"Retry-After": "0"}),
        FakeResponse(200, {"properties": {"name": "X"}}),
    ])
    assert hc.get_company("1", ["name"]) == {"name": "X"}
    assert slept == [0.0]  # honored Retry-After before retrying


def test_non_2xx_raises_hubspot_error():
    _install([FakeResponse(500, {"message": "boom"})])
    with pytest.raises(hc.HubSpotError):
        hc.get_company("1", ["name"])


# ── identity rollup + open-deal guard ────────────────────────────────────────


def test_get_company_identity_requests_join_keys():
    fake = _install([FakeResponse(200, {"properties": {
        "uuid": "u-1", "aptiq_property_id": "ap-1", "name": "X",
    }})])
    out = hc.get_company_identity("1")
    assert out["uuid"] == "u-1"
    assert out["aptiq_property_id"] == "ap-1"
    assert fake.calls == [("GET", f"{hc._COMPANIES}/1")]


def test_get_open_deals_filters_closed():
    _install([
        FakeResponse(200, {"results": [{"toObjectId": 11}, {"toObjectId": 22}]}),
        FakeResponse(200, {"results": [
            {"id": "11", "properties": {"dealstage": "appointmentscheduled"}},
            {"id": "22", "properties": {"dealstage": "closedwon"}},
        ]}),
    ])
    out = hc.get_open_deals_for_company("1")
    assert [d["id"] for d in out] == ["11"]  # closedwon dropped


def test_get_open_deals_empty_when_no_associations():
    _install([FakeResponse(200, {"results": []})])
    assert hc.get_open_deals_for_company("1") == []
