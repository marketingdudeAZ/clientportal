"""Tests for the portal QA feedback widget.

The premise: a tester is using the portal for a week while the person who would
normally answer her questions is away. Every rule below exists because of what
happens if a finding is lost during that week.

The load-bearing behaviour is that this NEVER silently succeeds.
`clickup_client` is best-effort by design and returns None on failure; here that
must become a loud error, because a tester who is thanked for a report that went
nowhere will not file it twice.
"""

from __future__ import annotations

import os
import sys
import types
from unittest import mock

import pytest
from flask import Flask

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

import portal_feedback as fb  # noqa: E402


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setenv("CLICKUP_LIST_PORTAL_FEEDBACK", "901-qa")
    monkeypatch.setenv("CLICKUP_API_KEY", "test-key")


@pytest.fixture
def clickup(monkeypatch):
    """A fake ClickUp that records what it was asked to do."""
    calls = {"tasks": [], "attachments": []}

    def create_task(list_id, name, **kw):
        calls["tasks"].append({"list_id": list_id, "name": name, **kw})
        return {"id": "abc123", "url": "https://app.clickup.com/t/abc123"}

    def attach_file(task_id, filename, content, content_type="application/octet-stream"):
        calls["attachments"].append({"task_id": task_id, "filename": filename,
                                     "bytes": len(content), "type": content_type})
        return {"id": "att1"}

    monkeypatch.setitem(sys.modules, "clickup_client", types.SimpleNamespace(
        create_task=create_task, attach_file=attach_file))
    return calls


PNG = (b"\x89PNG\r\n\x1a\n", "image/png")


class TestNothingIsLost:
    def test_a_refused_task_raises_rather_than_returning_quietly(self, monkeypatch):
        """clickup_client returns None on failure. That must not read as success."""
        monkeypatch.setitem(sys.modules, "clickup_client", types.SimpleNamespace(
            create_task=lambda *a, **k: None, attach_file=lambda *a, **k: None))
        with pytest.raises(fb.FeedbackFailed) as exc:
            fb.submit(note="the chart is empty")
        assert "Nothing was saved" in str(exc.value)

    def test_a_task_with_no_id_is_also_a_failure(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "clickup_client", types.SimpleNamespace(
            create_task=lambda *a, **k: {"url": "x"}, attach_file=lambda *a, **k: None))
        with pytest.raises(fb.FeedbackFailed):
            fb.submit(note="something")

    def test_an_unconfigured_list_is_its_own_error(self, monkeypatch, clickup):
        monkeypatch.delenv("CLICKUP_LIST_PORTAL_FEEDBACK", raising=False)
        with pytest.raises(fb.FeedbackNotConfigured):
            fb.submit(note="something")
        assert not clickup["tasks"], "must not file into an unset list"

    def test_a_failed_screenshot_does_not_discard_the_report(self, monkeypatch):
        """The words are the report; the image is supporting evidence. Losing
        the whole finding because an upload failed is the wrong trade."""
        monkeypatch.setitem(sys.modules, "clickup_client", types.SimpleNamespace(
            create_task=lambda *a, **k: {"id": "t1", "url": "u"},
            attach_file=lambda *a, **k: None))
        out = fb.submit(note="broken", screenshots=[("s.png",) + PNG])
        assert out["task_id"] == "t1"
        assert out["attached"] == 0
        assert any("upload to ClickUp failed" in d for d in out["dropped"])

    def test_an_empty_report_is_rejected_before_clickup(self, clickup):
        with pytest.raises(ValueError):
            fb.submit(note="   ")
        assert not clickup["tasks"]

    def test_a_screenshot_alone_is_a_valid_report(self, clickup):
        """Sometimes the picture is the whole point."""
        out = fb.submit(note="", screenshots=[("shot.png",) + PNG])
        assert out["task_id"] == "abc123"
        assert out["attached"] == 1


class TestTheTaskIsReadable:
    def test_the_title_carries_the_finding(self, clickup):
        fb.submit(note="Ask returns nothing on the Henry")
        assert clickup["tasks"][0]["name"] == "Ask returns nothing on the Henry"

    def test_a_long_title_is_cut_on_a_word(self, clickup):
        fb.submit(note="x " * 90)
        name = clickup["tasks"][0]["name"]
        assert len(name) <= 82 and name.endswith("…")

    def test_only_the_first_line_becomes_the_title(self, clickup):
        fb.submit(note="Chart is blank\n\nSteps: open the page, wait")
        assert clickup["tasks"][0]["name"] == "Chart is blank"

    def test_blockers_are_flagged_in_the_title(self, clickup):
        fb.submit(note="cannot log in", severity="blocker")
        assert clickup["tasks"][0]["name"].startswith("[BLOCKER]")

    def test_a_screenshot_only_report_still_gets_a_title(self, clickup):
        fb.submit(note="", context={"page": "Performance"},
                  screenshots=[("s.png",) + PNG])
        assert "Performance" in clickup["tasks"][0]["name"]

    def test_context_is_captured_without_being_asked_for(self, clickup):
        fb.submit(note="odd number", reporter="dir@rpmliving.com", context={
            "page": "Ask", "property_name": "The Atwood at Rivulon",
            "url": "https://go.rpmliving.com/portal?uuid=309",
            "viewport": "1512x982", "user_agent": "Safari/17"})
        body = clickup["tasks"][0]["description"]
        for expected in ("Ask", "The Atwood at Rivulon", "1512x982",
                         "dir@rpmliving.com", "Safari/17"):
            assert expected in body, expected

    def test_severity_sets_priority_and_tag(self, clickup):
        fb.submit(note="a", severity="blocker")
        task = clickup["tasks"][0]
        assert task["priority"] == 1
        assert "blocker" in task["tags"] and "portal-feedback" in task["tags"]

    def test_an_unknown_severity_falls_back_rather_than_failing(self, clickup):
        fb.submit(note="a", severity="catastrophic")
        assert clickup["tasks"][0]["priority"] == fb.SEVERITY["bug"]["priority"]


class TestScreenshotRules:
    def test_an_oversized_image_is_dropped_and_named(self, clickup):
        big = b"x" * (fb.MAX_SHOT_BYTES + 1)
        out = fb.submit(note="a", screenshots=[("huge.png", big, "image/png")])
        assert out["attached"] == 0
        assert any("over the 10MB limit" in d for d in out["dropped"])

    def test_a_non_image_is_refused(self, clickup):
        out = fb.submit(note="a", screenshots=[("notes.pdf", b"%PDF", "application/pdf")])
        assert out["attached"] == 0
        assert any("not an image" in d for d in out["dropped"])

    def test_extra_screenshots_beyond_the_cap_are_reported(self, clickup):
        shots = [(f"s{i}.png",) + PNG for i in range(6)]
        out = fb.submit(note="a", screenshots=shots)
        assert out["attached"] == fb.MAX_SHOTS
        assert any("only 4 screenshots" in d for d in out["dropped"])

    def test_every_kept_screenshot_reaches_clickup(self, clickup):
        shots = [(f"s{i}.png",) + PNG for i in range(3)]
        out = fb.submit(note="a", screenshots=shots)
        assert out["attached"] == 3
        assert len(clickup["attachments"]) == 3
        assert all(a["task_id"] == "abc123" for a in clickup["attachments"])


class TestApi:
    @pytest.fixture
    def client(self, monkeypatch):
        from routes.feedback import feedback_bp
        app = Flask(__name__)
        app.register_blueprint(feedback_bp)
        monkeypatch.delenv("INTERNAL_API_KEY", raising=False)
        return app.test_client()

    @pytest.fixture
    def internal(self, monkeypatch):
        import feature_access
        monkeypatch.setattr(feature_access, "role_for", lambda e: "internal")
        return {"X-Portal-Email": "director@rpmliving.com"}

    def test_anonymous_cannot_file(self, client):
        assert client.post("/api/feedback", data={"note": "x"}).status_code == 401

    def test_a_client_user_cannot_file(self, client, monkeypatch):
        """The widget is a testing instrument, not a client feature."""
        import feature_access
        monkeypatch.setattr(feature_access, "role_for", lambda e: "client")
        r = client.post("/api/feedback", data={"note": "x"},
                        headers={"X-Portal-Email": "owner@acme.com"})
        assert r.status_code == 403

    def test_health_tells_the_widget_to_hide_when_unconfigured(self, client, internal,
                                                               monkeypatch):
        monkeypatch.delenv("CLICKUP_LIST_PORTAL_FEEDBACK", raising=False)
        body = client.get("/api/feedback/health", headers=internal).get_json()
        assert body["ready"] is False
        assert "CLICKUP_LIST_PORTAL_FEEDBACK" in body["reason"]

    def test_health_reports_ready_when_configured(self, client, internal):
        body = client.get("/api/feedback/health", headers=internal).get_json()
        assert body["ready"] is True
        assert {s["key"] for s in body["severities"]} == set(fb.SEVERITY)

    def test_a_submission_returns_the_clickup_link(self, client, internal, clickup):
        r = client.post("/api/feedback", headers=internal,
                        data={"note": "the tour chart is empty", "severity": "bug",
                              "page": "Performance"})
        assert r.status_code == 200
        assert r.get_json()["url"].endswith("/abc123")

    def test_an_unconfigured_list_is_503_not_500(self, client, internal, monkeypatch):
        monkeypatch.delenv("CLICKUP_LIST_PORTAL_FEEDBACK", raising=False)
        r = client.post("/api/feedback", headers=internal, data={"note": "x"})
        assert r.status_code == 503

    def test_a_clickup_outage_is_502_and_says_nothing_was_saved(self, client,
                                                                internal, monkeypatch):
        monkeypatch.setitem(sys.modules, "clickup_client", types.SimpleNamespace(
            create_task=lambda *a, **k: None, attach_file=lambda *a, **k: None))
        r = client.post("/api/feedback", headers=internal, data={"note": "x"})
        assert r.status_code == 502
        assert "Nothing was saved" in r.get_json()["error"]

    def test_an_empty_submission_is_400(self, client, internal, clickup):
        assert client.post("/api/feedback", headers=internal,
                           data={"note": ""}).status_code == 400

    def test_an_uploaded_screenshot_reaches_clickup(self, client, internal, clickup):
        import io
        r = client.post("/api/feedback", headers=internal,
                        content_type="multipart/form-data",
                        data={"note": "see this",
                              "screenshot": (io.BytesIO(PNG[0]), "shot.png", "image/png")})
        assert r.status_code == 200
        assert r.get_json()["attached"] == 1
        assert clickup["attachments"][0]["filename"] == "shot.png"
