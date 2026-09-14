"""Every workspace fixture matches the live API's contract.

The preview server and the UI are built on tests/fixtures/workspace. The API
branch encodes the shapes it actually returns in tests/workspace_contract.py.
This test runs `check(fixture, SPEC)` for every fixture, so the preview can't
drift from the live shapes again. A new fixture without a spec fails here.
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import workspace_contract as wc  # noqa: E402

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "workspace"

# POST /api/workspace/media-plan/regenerate (201), from skills/workspace_media_plan.regenerate.
REGENERATE_RESULT = {"work_item_id": wc.STR, "clickup_task_id": wc.STR, "status": wc.enum("draft_requested")}

SPEC_BY_FIXTURE = {
    "me": wc.ME, "portfolio": wc.PORTFOLIO, "work": wc.WORK, "item": wc.ITEM, "decision": wc.DECISION,
    "undo": wc.UNDO_RESULT, "property": wc.PROPERTY, "performance": wc.PERFORMANCE, "plan": wc.PLAN,
    "client_view": wc.CLIENT_VIEW, "signals": wc.SIGNALS, "request_draft": wc.DRAFT,
    "request_created": wc.FILED, "requests_recent": wc.RECENT, "search": wc.SEARCH,
    "dashboard": wc.DASHBOARD, "approvals": wc.APPROVALS, "approvals_empty": wc.APPROVALS,
    "property_overview": wc.PROPERTY_OVERVIEW, "media_plan": wc.MEDIA_PLAN,
    "visibility": wc.VISIBILITY_SCREEN, "content": wc.CONTENT, "creative": wc.CREATIVE, "value": wc.VALUE,
    "create_brief": wc.CREATE_BRIEF, "media_plan_regenerate": REGENERATE_RESULT,
}
# Round 4 shapes ("Round 4 — Kyle's review" in docs/handoffs/PORTAL_WORKSPACE_BUILD_PLAN.md). The API
# branch adds them to tests/workspace_contract.py in parallel; until they land here the Round 4 fixtures are
# checked against these, built from the contract's own pieces and following the spec exactly.
R4_CATEGORY = wc.enum("cost", "vendor", "content", "creative", "compliance")
R4_DASHBOARD = {
    **wc.DASHBOARD,
    "lens": wc.absent("lens"), "kpi_order": wc.absent("kpi_order"),
    "kpis": {**{k: wc.opt(wc.METRIC) for k in ("occupancy", "units_to_lease_90d", "leases_this_month", "cost_per_lease",
                                              "ai_visibility", "actions_taken", "waiting_on_you")},
             "identified_savings": wc.absent("identified_savings")},
    "properties": [{"company_id": wc.STR, "name": wc.opt(wc.STR), "units": wc.opt(wc.INT),
                    "occupancy": wc.opt(wc.METRIC), "to_lease_90d": wc.opt(wc.METRIC),
                    "leases_this_month": wc.opt(wc.METRIC), "health": wc.opt(wc.NUM), "band": wc.BAND,
                    "overspend_per_year": wc.absent("overspend_per_year")}],
    "waiting": [{"item_id": wc.STR, "title": wc.STR, "subtitle": wc.opt(wc.STR), "category": R4_CATEGORY}],
}
R4_APPROVALS = {
    **wc.APPROVALS,
    "interrupts": [{"id": wc.STR, "kind": wc.enum("compliance"), "title": wc.STR, "detail": wc.STR,
                    "company_id": wc.STR, "item_id": wc.opt(wc.STR), "primary_action": {"label": wc.STR},
                    "secondary_action": {"label": wc.STR}}],
    "batch": {"label": wc.STR, "rows": [{"item_id": wc.STR, "company_id": wc.STR, "property": wc.opt(wc.STR),
                                         "action": wc.STR, "category": R4_CATEGORY,
                                         "savings_per_year": wc.opt(wc.METRIC), "can_edit": wc.BOOL}]},
}
R4_ITEM = {
    **wc.ITEM,
    "source": wc.enum(*dict.fromkeys(wc.SOURCES.values + ("fair_housing_review",))),
    "why": wc.opt({"text": wc.STR, "receipts": [wc.RECEIPT]}),
    "for_whom": wc.opt({"text": wc.STR, "questions": [wc.STR]}),
    "approving_does": [{"label": wc.STR, "owner": wc.enum("RPM Digital", "vendor", "you"), "when": wc.opt(wc.STR)}],
}
R4_FAIR_HOUSING_REVIEW = {
    "property": {"company_id": wc.STR, "name": wc.opt(wc.STR)},
    "run_at": wc.STR, "next_run": wc.STR, "pages_checked": wc.INT, "assets_checked": wc.opt(wc.INT),
    "findings": [{"kind": wc.enum("copy", "image"), "location": wc.STR, "excerpt": wc.STR, "reason": wc.STR,
                  "severity": wc.STR, "suggested_fix": wc.STR}],
}
SPEC_BY_FIXTURE.update({"dashboard": R4_DASHBOARD, "approvals": R4_APPROVALS, "approvals_empty": R4_APPROVALS,
                        "item": R4_ITEM})
# Fixtures that hold a list of one shape under a key.
LIST_FIXTURES = {"approval_items": ("items", R4_ITEM)}
# Covered by their own contract tests (tests/test_ask.py, tests/test_workspace_report.py).
OWN_CONTRACT = ("ask_", "report_")
# The preview server's 35-property source book, not an API response;
# tests/test_workspace_preview_book.py checks it and every screen built from it.
PREVIEW_ONLY = {"preview_book"}


def _names():
    return sorted(p.stem for p in FIXTURES.glob("*.json"))


def test_every_workspace_fixture_has_an_api_spec():
    unmapped = [n for n in _names()
                if n not in SPEC_BY_FIXTURE and n not in LIST_FIXTURES and n not in PREVIEW_ONLY
                and not n.startswith(OWN_CONTRACT)]
    assert not unmapped, f"fixtures with no API spec to check against: {unmapped}"


@pytest.mark.parametrize("name", [n for n in _names() if n in SPEC_BY_FIXTURE])
def test_fixture_matches_api_spec(name):
    data = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    problems = wc.check(data, SPEC_BY_FIXTURE[name])
    assert not problems, f"{name}.json breaks the API contract:\n  " + "\n  ".join(problems)


@pytest.mark.parametrize("name", sorted(LIST_FIXTURES))
def test_list_fixture_matches_api_spec(name):
    key, spec = LIST_FIXTURES[name]
    data = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    problems = [f"{key}[{i}]{p[1:] if p.startswith('$') else '.' + p}" for i, row in enumerate(data[key])
                for p in wc.check(row, spec)]
    assert not problems, f"{name}.json breaks the API contract:\n  " + "\n  ".join(problems)


def test_decision_and_undo_items_match_round4_item_spec():
    for name in ("decision", "undo"):
        data = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
        problems = wc.check(data["item"], R4_ITEM)
        assert not problems, f"{name}.json item breaks the Round 4 item spec:\n  " + "\n  ".join(problems)


def test_every_preview_item_matches_round4_item_spec():
    """Every item the preview serves, including the monthly Fair Housing review record."""
    import importlib.util
    root = pathlib.Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("preview_workspace", root / "scripts" / "preview_workspace.py")
    pw = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pw)
    problems = []
    for it in pw.all_items():
        problems += [f"{it['id']}: {p}" for p in wc.check(it, R4_ITEM)]
        if it.get("review"):
            problems += [f"{it['id']}.review: {p}" for p in wc.check(it["review"], R4_FAIR_HOUSING_REVIEW)]
    assert not problems, "\n  ".join(problems)
