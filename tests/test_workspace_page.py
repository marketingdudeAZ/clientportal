"""GET /workspace — the page route.

Defends three things: the route does not exist unless WORKSPACE_ENABLED is on,
it serves the page unchanged when it is on, and nothing about identity comes
from the query string (no ?email= injection, and the page never reads one).
"""

from __future__ import annotations

import os
import re
import sys

import pytest
from flask import Flask

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

from routes.portal_ui import _WORKSPACE, portal_ui_bp  # noqa: E402


@pytest.fixture
def client():
    app = Flask(__name__)
    app.register_blueprint(portal_ui_bp)
    return app.test_client()


@pytest.mark.parametrize("value", [None, "", "0", "false", "no", "off"])
def test_404_when_flag_off(client, value):
    if value is None:
        os.environ.pop("WORKSPACE_ENABLED", None)
    else:
        os.environ["WORKSPACE_ENABLED"] = value
    assert client.get("/workspace").status_code == 404


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes"])
def test_200_when_flag_on(client, value):
    os.environ["WORKSPACE_ENABLED"] = value
    resp = client.get("/workspace")
    assert resp.status_code == 200
    assert resp.mimetype == "text/html"
    assert resp.headers.get("Cache-Control") == "no-store"
    with open(_WORKSPACE, encoding="utf-8") as fh:
        assert resp.get_data(as_text=True) == fh.read()


def test_email_query_is_ignored(client):
    os.environ["WORKSPACE_ENABLED"] = "true"
    plain = client.get("/workspace").get_data(as_text=True)
    with_email = client.get("/workspace?email=someone@example.com").get_data(as_text=True)
    assert plain == with_email
    assert "someone@example.com" not in with_email
    assert "__PORTAL_EMAIL__" not in with_email


def test_page_never_reads_email_from_url():
    with open(_WORKSPACE, encoding="utf-8") as fh:
        page = fh.read()
    assert "?email" not in page
    assert not re.search(r"""\.get\(\s*['"]email['"]\s*\)""", page)
    assert "X-Portal-Email" not in page


def test_page_sends_signed_link_header_and_strips_token():
    with open(_WORKSPACE, encoding="utf-8") as fh:
        page = fh.read()
    assert "X-Workspace-Link" in page
    assert "sessionStorage" in page
    assert "history.replaceState" in page


def test_page_only_calls_same_origin_apis():
    with open(_WORKSPACE, encoding="utf-8") as fh:
        page = fh.read()
    for path in re.findall(r"""api\(\s*['"]([^'"]+)""", page):
        assert path.startswith("/api/"), path
    assert "onrender.com" not in page
