"""White-labelled AI-search reports: published once, remembered, shown.

The fact that shapes every test here: the link Searchable hands back is PUBLIC
and unauthenticated. Anyone who has it can read the report with no sign-in.
That is what makes it shareable with a client and it is also the entire risk,
so publishing is never a side effect — not of rendering a screen, not of a
default argument, not of a rule.

The second rule: generate_report stays in WRITE_TOOLS and `call_tool` keeps
refusing it. One deliberate function with its own credential is the exception;
widening the allowlist would have let anything reach a write.
"""
from __future__ import annotations

import json
import os
import sys
from io import BytesIO

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "webhook-server"))
sys.path.insert(0, ROOT)

import searchable_mcp as sm  # noqa: E402
import skills  # noqa: E402
from skills import searchable_reports as sr  # noqa: E402

LINK = "https://app.searchable.com/r/pub_abc123"
PROJECT = {"id": "p-1", "name": "The Alcove", "domain": "thealcoveapartments.com",
           "data_available_from": "2026-09-18T00:00:00Z", "is_active": True}


class _Resp:
    def __init__(self, text):
        self._b = BytesIO(text.encode())

    def read(self):
        return self._b.read()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeHTTP:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.sent = []

    def __call__(self, req, timeout=None):
        self.sent.append({"headers": {k.lower(): v for k, v in req.header_items()},
                          "body": json.loads((req.data or b"{}").decode())})
        return _Resp(self.replies.pop(0) if self.replies else "{}")


def rpc(result):
    return json.dumps({"jsonrpc": "2.0", "id": 1, "result": result})


REPORT_OK = rpc({"structuredContent": {"url": LINK, "id": "rep-1",
                                       "title": "The Alcove — AI search visibility"}})


class _Identity:
    company_id = "555"
    name = "The Alcove"

    def to_dict(self):
        return {"uuid": "u-1", "company_id": "555", "name": "The Alcove",
                "domain": "thealcoveapartments.com"}


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for name in ("SEARCHABLE_API_TOKEN", "SEARCHABLE_API_TOKEN_WRITE",
                 "SEARCHABLE_MCP_REFRESH_TOKEN", "SEARCHABLE_MCP_CLIENT_ID"):
        monkeypatch.delenv(name, raising=False)
    sm.clear_cache()
    resolver = type("R", (), {"resolve": staticmethod(lambda cid, **kw: _Identity())})
    monkeypatch.setattr(skills, "property_resolver", resolver, raising=False)
    monkeypatch.setitem(sys.modules, "skills.property_resolver", resolver)
    monkeypatch.setattr(sm, "project_for_domain", lambda d: (PROJECT, None))
    yield
    sm.clear_cache()


# --- the credential --------------------------------------------------------

class TestWriteCredential:
    def test_the_read_token_cannot_publish(self, monkeypatch):
        """A read key must not be silently promoted: the answer is "not
        configured", not a 403 that reads like an outage."""
        monkeypatch.setenv("SEARCHABLE_API_TOKEN", "read-only-key")
        result, reason = sm.generate_report("p-1", confirm=True)
        assert result is None
        assert "SEARCHABLE_API_TOKEN_WRITE" in reason

    def test_the_write_token_is_the_one_sent(self, monkeypatch):
        monkeypatch.setenv("SEARCHABLE_API_TOKEN", "read-key")
        monkeypatch.setenv("SEARCHABLE_API_TOKEN_WRITE", "write-key")
        http = FakeHTTP(REPORT_OK)
        monkeypatch.setattr(sm.urllib.request, "urlopen", http)
        sm.generate_report("p-1", confirm=True)
        assert http.sent[0]["headers"]["authorization"] == "Bearer write-key"

    def test_has_write_credential_reports_presence_only(self, monkeypatch):
        assert sm.has_write_credential() is False
        monkeypatch.setenv("SEARCHABLE_API_TOKEN_WRITE", "k")
        assert sm.has_write_credential() is True


# --- publishing is deliberate ----------------------------------------------

class TestPublishingIsDeliberate:
    def test_the_default_is_a_dry_run(self, monkeypatch):
        """No `confirm` means the vendor is asked for a preview and nothing is
        published. A function that published by default would eventually be
        called by something that only meant to look."""
        monkeypatch.setenv("SEARCHABLE_API_TOKEN_WRITE", "write-key")
        http = FakeHTTP(rpc({"structuredContent": {"preview": True}}))
        monkeypatch.setattr(sm.urllib.request, "urlopen", http)
        sm.generate_report("p-1")
        args = http.sent[0]["body"]["params"]["arguments"]
        assert "confirm" not in args

    def test_confirm_is_passed_only_when_asked_for(self, monkeypatch):
        monkeypatch.setenv("SEARCHABLE_API_TOKEN_WRITE", "write-key")
        http = FakeHTTP(REPORT_OK)
        monkeypatch.setattr(sm.urllib.request, "urlopen", http)
        sm.generate_report("p-1", confirm=True)
        assert http.sent[0]["body"]["params"]["arguments"]["confirm"] is True

    def test_the_skill_refuses_to_publish_without_confirmation(self, monkeypatch):
        monkeypatch.setenv("SEARCHABLE_API_TOKEN_WRITE", "write-key")
        http = FakeHTTP(REPORT_OK)
        monkeypatch.setattr(sm.urllib.request, "urlopen", http)
        out = sr.publish("555")
        assert out["published"] is False
        assert "not confirmed" in out["reason"]
        assert http.sent == [], "an unconfirmed publish must not reach the vendor"

    def test_white_label_is_on_by_default(self, monkeypatch):
        """The whole point of showing it to a client."""
        monkeypatch.setenv("SEARCHABLE_API_TOKEN_WRITE", "write-key")
        http = FakeHTTP(REPORT_OK)
        monkeypatch.setattr(sm.urllib.request, "urlopen", http)
        sm.generate_report("p-1", confirm=True)
        assert http.sent[0]["body"]["params"]["arguments"]["whiteLabel"] is True

    def test_an_unknown_report_type_is_refused_before_the_call(self, monkeypatch):
        monkeypatch.setenv("SEARCHABLE_API_TOKEN_WRITE", "write-key")
        http = FakeHTTP(REPORT_OK)
        monkeypatch.setattr(sm.urllib.request, "urlopen", http)
        result, reason = sm.generate_report("p-1", report_type="everything", confirm=True)
        assert result is None and "Unknown report type" in reason
        assert http.sent == []


class TestErrorsAreLegible:
    def test_the_vendors_own_reason_comes_through(self, monkeypatch):
        """A missing scope and an outage look identical behind a generic
        message, and the fixes are nothing alike."""
        monkeypatch.setenv("SEARCHABLE_API_TOKEN_WRITE", "write-key")
        monkeypatch.setattr(sm.urllib.request, "urlopen", FakeHTTP(rpc({
            "isError": True,
            "content": [{"type": "text", "text": "missing required scope: write"}]})))
        result, reason = sm.generate_report("p-1", confirm=True)
        assert result is None
        assert "missing required scope: write" in reason

    def test_a_structured_error_is_unwrapped_too(self, monkeypatch):
        monkeypatch.setenv("SEARCHABLE_API_TOKEN_WRITE", "write-key")
        monkeypatch.setattr(sm.urllib.request, "urlopen", FakeHTTP(rpc({
            "isError": True,
            "structuredContent": {"message": "report quota exhausted"}})))
        _, reason = sm.generate_report("p-1", confirm=True)
        assert "report quota exhausted" in reason


class TestTheWriteGuardStillHolds:
    def test_generate_report_is_still_refused_through_call_tool(self, monkeypatch):
        """The deliberate function is the ONLY way in. If this ever stops
        raising, every rule and agent can publish public links."""
        monkeypatch.setenv("SEARCHABLE_API_TOKEN", "k")
        with pytest.raises(sm.WriteRefused):
            sm.call_tool("generate_report", {"projectId": "p-1", "confirm": True})

    def test_it_is_still_listed_as_a_write_tool(self):
        assert "generate_report" in sm.WRITE_TOOLS
        assert "generate_report" not in sm.READ_TOOLS


# --- remembering the link --------------------------------------------------

class TestRemembering:
    def test_a_published_link_is_written_to_the_loop_stream(self, monkeypatch):
        monkeypatch.setenv("SEARCHABLE_API_TOKEN_WRITE", "write-key")
        monkeypatch.setattr(sm.urllib.request, "urlopen", FakeHTTP(REPORT_OK))
        written = []
        import loop_writer
        monkeypatch.setattr(loop_writer, "record",
                            lambda stage, et, **kw: written.append(dict(kw, stage=stage, event_type=et)) or "e1")
        out = sr.publish("555", confirm=True)
        assert out["published"] is True and out["link"] == LINK
        assert written[0]["event_type"] == sr.EVENT_TYPE
        assert written[0]["payload"]["link"] == LINK
        assert written[0]["property_uuid"] == "u-1"

    def test_the_record_says_the_link_is_public(self, monkeypatch):
        """Anyone reading this row later should not have to work out how
        exposed the link is."""
        monkeypatch.setenv("SEARCHABLE_API_TOKEN_WRITE", "write-key")
        monkeypatch.setattr(sm.urllib.request, "urlopen", FakeHTTP(REPORT_OK))
        written = []
        import loop_writer
        monkeypatch.setattr(loop_writer, "record",
                            lambda stage, et, **kw: written.append(kw) or "e1")
        sr.publish("555", confirm=True)
        assert written[0]["payload"]["visibility"] == "public_unauthenticated"

    def test_a_failed_write_is_reported_not_swallowed(self, monkeypatch):
        """The report exists at the vendor either way; what is lost is the
        portal's ability to show it, and that must be visible."""
        monkeypatch.setenv("SEARCHABLE_API_TOKEN_WRITE", "write-key")
        monkeypatch.setattr(sm.urllib.request, "urlopen", FakeHTTP(REPORT_OK))
        import loop_writer
        monkeypatch.setattr(loop_writer, "record",
                            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("bq down")))
        out = sr.publish("555", confirm=True)
        assert out["link"] == LINK
        assert any("could not be recorded" in g["message"] for g in out["gaps"])

    def test_the_event_type_is_registered(self):
        import loop_writer
        assert sr.EVENT_TYPE in loop_writer.LOOP_EVENT_TYPES
        assert sr.LOOP_STAGE in loop_writer.LOOP_STAGES


class TestReadingBack:
    def _history(self, monkeypatch, events):
        fake = type("H", (), {"property_events": staticmethod(lambda ids, **kw: events)})
        monkeypatch.setattr(skills, "workspace_history", fake, raising=False)
        monkeypatch.setitem(sys.modules, "skills.workspace_history", fake)

    def test_the_newest_link_wins(self, monkeypatch):
        self._history(monkeypatch, {"u-1": [
            {"occurred_at": "2026-09-18T10:00:00Z",
             "payload": {"link": LINK, "published_at": "2026-09-18T10:00:00+00:00"}},
            {"occurred_at": "2026-09-01T10:00:00Z",
             "payload": {"link": "https://old", "published_at": "2026-09-01"}}]})
        latest, gaps = sr.latest_for_property("555", uuid="u-1")
        assert latest["link"] == LINK and gaps == []

    def test_an_unreadable_warehouse_is_a_gap_not_absence(self, monkeypatch):
        """"No report published" and "we could not look" are different answers."""
        self._history(monkeypatch, None)
        latest, gaps = sr.latest_for_property("555", uuid="u-1")
        assert latest is None and gaps

    def test_no_report_is_simply_none(self, monkeypatch):
        self._history(monkeypatch, {"u-1": []})
        latest, gaps = sr.latest_for_property("555", uuid="u-1")
        assert latest is None and gaps == []

    def test_a_screen_reads_every_property_in_one_query(self, monkeypatch):
        """One call for the whole table; a per-row read would be 98 queries."""
        asked = {}
        fake = type("H", (), {"property_events": staticmethod(
            lambda ids, **kw: asked.update(ids=ids) or {
                "u-1": [{"occurred_at": "2026-09-18T10:00:00Z",
                         "payload": {"link": LINK}}]})})
        monkeypatch.setattr(skills, "workspace_history", fake, raising=False)
        monkeypatch.setitem(sys.modules, "skills.workspace_history", fake)
        out = sr.links_for(["u-1", "u-2", None, "u-1"])
        assert asked["ids"] == ["u-1", "u-2"]        # deduped, no empties
        assert out["u-1"]["link"] == LINK and "u-2" not in out


def test_the_screen_warns_that_the_link_is_public():
    """A reader must not have to discover that a client report needs no
    sign-in. The page says so next to the links."""
    page = open(os.path.join(ROOT, "webhook-server", "portal_pages", "workspace.html"),
                encoding="utf-8").read()
    assert "public link" in page
    assert "no sign-in" in page
