"""The Properties screen's two scopes.

The bug this fixes: Properties listed only the companies HubSpot names you on
(`ASSIGNMENT_FIELDS` — marketing manager, director or RVP), while the page said
"Every property you can see" and `/me` reported `portfolio_wide: true`. A
director with one test assignment saw one test property and had no way to the
other 751 without editing HubSpot records.

Two things are worth defending here, and only one of them is cosmetic:

  * Staff can reach every managed property, and land there by default.
  * A CLIENT is never offered the switch. Their scope IS their companies, so an
    "All properties" control would either do nothing or show them somebody
    else's portfolio. pytest sees the page only as a string, so the client case
    is checked by running the real function in node.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "webhook-server"))

PAGE = REPO / "webhook-server" / "portal_pages" / "workspace.html"
HARNESS = REPO / "tests" / "js" / "workspace_prop_scope_harness.js"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def run_harness(**scenario) -> dict:
    proc = subprocess.run(
        ["node", str(HARNESS), str(PAGE), json.dumps(scenario)],
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"harness failed:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


# ── what the page asks the server for ────────────────────────────────────────

class TestTheRequest:
    def test_all_scope_asks_the_portfolio_endpoint_for_every_property(self, page):
        """`view=all` is the difference between 752 properties and the caller's
        assignments; build_portfolio defaults to needs_me when you have any."""
        assert "'/api/workspace/portfolio?view=all&page='" in page

    def test_it_pages_rather_than_asking_for_everything_at_once(self, page):
        """Each page reads its properties' work items with a thread pool. One
        unpaged request would do that for all 752 inside one render."""
        assert "state.props.page" in page
        assert "next_page" in page

    def test_the_mine_scope_still_uses_the_dashboard(self, page):
        """Unchanged on purpose: the dashboard is what carries occupancy,
        leases, health and profile for a small set of properties."""
        assert "guard(api('/api/workspace/dashboard'), 'properties')" in page


# ── who gets the switch ──────────────────────────────────────────────────────

@needs_node
class TestWhoSeesTheSwitch:
    def test_staff_land_on_every_managed_property(self):
        """The actual complaint: a director opened Properties and saw one test
        property, because that was the only record naming their email."""
        out = run_harness(role="internal")
        assert out["scope"] == "all"
        assert out["switch_rendered"] is True

    def test_a_client_never_gets_the_switch(self):
        """Their scope IS their companies. An "All properties" control would
        either do nothing or show them a portfolio that is not theirs."""
        out = run_harness(role="client", companies=[{"company_id": "1"}])
        assert out["switch_rendered"] is False
        assert out["scope"] == "mine"

    def test_a_client_cannot_reach_the_all_scope_by_forcing_it(self):
        """Defence in depth for the same rule: even with the field set — a
        stale value, a console poke — a client resolves to their own scope.
        The server refuses too (`/portfolio` is internal-only); this keeps the
        page from ever asking."""
        out = run_harness(role="client", chosen="all")
        assert out["scope"] == "mine"

    def test_staff_can_switch_back_to_their_assignments(self):
        out = run_harness(role="internal", chosen="mine")
        assert out["scope"] == "mine"

    def test_the_active_scope_is_the_pressed_one(self):
        out = run_harness(role="internal")
        assert out["all_pressed_on_all_view"] is True
        assert out["mine_pressed_on_mine_view"] is True
        assert out["only_one_pressed"] == 1

    def test_switching_scope_resets_to_the_first_page(self, page):
        """Otherwise page 3 of All carries into Mine and shows nothing."""
        assert "state.props.page = 1;" in page
        assert "state.props.rows = [];" in page

    def test_the_empty_assignment_case_points_somewhere(self):
        """A staff member with no assignments would otherwise get a blank table
        and no hint that the portfolio is one click away."""
        out = run_harness(role="internal", note="No managed property names this email.")
        assert out["note_rendered"] is True


# ── honesty about the columns ────────────────────────────────────────────────

class TestTheColumns:
    def test_the_all_view_shows_only_columns_it_can_fill(self, page):
        """Occupancy and units at risk come from the AptIQ row the ranking
        already reads. Health, leases and profile would each need a full
        per-property read, so they are absent rather than a column of dashes."""
        block = page.split("function renderPropsAll()", 1)[1].split("function propertiesHtml", 1)[0]
        assert "Units at risk" in block
        assert "Next thing to do" in block
        for absent in ("Leases (month)", "Profile", "To lease (90d)"):
            assert absent not in block, f"{absent} cannot be filled for every property"

    def test_it_says_why_those_columns_are_missing(self, page):
        """A missing column with no explanation reads as a bug."""
        assert "filling those columns here would mean reading every managed property" in page

    def test_the_staff_lede_no_longer_claims_to_show_everything(self, page):
        """The old copy — "Every property you can see" — was true for a client
        and false for staff, which is what made the bug invisible."""
        assert "The properties HubSpot names you on" in page

    def test_missing_values_render_as_a_dash_not_a_zero(self, page):
        """A property with no AptIQ row has unknown occupancy, not 0%."""
        block = page.split("function renderPropsAll()", 1)[1].split("function propertiesHtml", 1)[0]
        assert block.count("empty-dash") >= 4


# ── the server side of the same rule ─────────────────────────────────────────

class TestTheServerAgrees:
    def test_the_portfolio_endpoint_is_internal_only(self):
        """The page not asking is not a control; this is."""
        source = (REPO / "webhook-server" / "routes" / "workspace.py").read_text(encoding="utf-8")
        block = source.split("def workspace_portfolio()", 1)[1].split("def ", 1)[0]
        assert "_is_internal()" in block
        assert "403" in block

    def test_view_all_means_every_managed_property(self):
        from skills import workspace_portfolio as wp
        assert "all" in wp.VIEWS and "needs_me" in wp.VIEWS

    def test_assignment_fields_are_what_scoped_it(self):
        """Named here so the next person reading this test knows which HubSpot
        fields decide "mine" — they are the reason the bug existed."""
        from skills import workspace_portfolio as wp
        assert wp.ASSIGNMENT_FIELDS == ("marketing_manager_email",
                                        "marketing_director_email",
                                        "marketing_rvp_email")
