"""What the left rail offers.

The rail used to carry an "RPM only" section — Signals, RPMI and AI Search.
They came out on 2026-09-22: each asks a question a screen above it already
asks (AI Search is the vendor's answer to what Visibility answers from our own
tables), and a staff-only block sitting on a client's portal reads as something
withheld rather than something internal.

The screens themselves were not deleted. They still open by URL and the server
still refuses them to anyone who is not staff — `/api/workspace/signals`,
`/rpmi` and `/geo` demand a proven internal identity, which
tests/test_workspace_api.py pins.
"""

from __future__ import annotations

from pathlib import Path

import pytest

PAGE = Path(__file__).resolve().parent.parent / "webhook-server" / "portal_pages" / "workspace.html"


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_the_rail_has_no_rpm_only_section(page):
    """The markup, not the word: the comment explaining the removal says it."""
    assert 'class="nav-label"' not in page
    assert ".nav-label{" not in page


@pytest.mark.parametrize("label", ["Signals", "RPMI", "AI Search"])
def test_no_nav_link_to_the_internal_screens(page, label):
    assert f"label: '{label}'" not in page


@pytest.mark.parametrize("route", ["#/signals", "#/rpmi", "#/geo"])
def test_the_screens_still_exist_and_still_route(page, route):
    assert route.lstrip("#/") in page
