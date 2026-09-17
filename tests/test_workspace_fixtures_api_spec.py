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
    "create_brief": wc.CREATE_BRIEF, "media_plan_regenerate": REGENERATE_RESULT, "creative_upload": wc.CREATIVE_UPLOAD,
}
SPEC_BY_FIXTURE.update({"spend_sheet": wc.SPEND_SHEET, "spend_sheet_client": wc.SPEND_SHEET,
                        "profile": wc.PROFILE, "profile_client": wc.PROFILE, "profile_edit": wc.PROFILE_EDIT,
                        "profile_checkin": wc.PROFILE_CHECKIN, "suggestion_dismissed": wc.SUGGESTION_DISMISSED,
                        "profile_history": wc.PROFILE_HISTORY})
# Fixtures that hold a list of one shape under a key.
LIST_FIXTURES = {"approval_items": ("items", wc.ITEM)}
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


def test_decision_and_undo_items_match_item_spec():
    for name in ("decision", "undo"):
        data = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
        problems = wc.check(data["item"], wc.ITEM)
        assert not problems, f"{name}.json item breaks the item spec:\n  " + "\n  ".join(problems)


def test_every_preview_item_matches_item_spec():
    """Every item the preview serves, including the monthly Fair Housing review record."""
    import importlib.util
    root = pathlib.Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("preview_workspace", root / "scripts" / "preview_workspace.py")
    pw = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pw)
    problems = []
    for it in pw.all_items():
        problems += [f"{it['id']}: {p}" for p in wc.check(it, wc.ITEM)]
        if it.get("review"):
            problems += [f"{it['id']}.review: {p}" for p in wc.check(it["review"], wc.FAIR_HOUSING_REVIEW)]
    assert not problems, "\n  ".join(problems)


def test_preview_creative_upload_matches_api_spec():
    """The preview fakes storage but answers exactly as the API does."""
    import importlib.util
    import io
    root = pathlib.Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("preview_workspace_upload", root / "scripts" / "preview_workspace.py")
    pw = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pw)
    client = pw.app.test_client()
    resp = client.post("/api/workspace/creative/upload", content_type="multipart/form-data", data={
        "company_id": "18234410021",
        "files": [(io.BytesIO(b"jpg"), "Pool at dusk.jpg", "image/jpeg"), (io.BytesIO(b"mp4"), "tour.mp4", "video/mp4"),
                  (io.BytesIO(b"zip"), "plans.zip", "application/zip")]})
    assert resp.status_code == 201
    body = resp.get_json()
    assert not wc.check(body, wc.CREATIVE_UPLOAD), wc.check(body, wc.CREATIVE_UPLOAD)
    assert [u["asset_name"] for u in body["uploaded"]] == ["lyvbroadway-pool-at-dusk", "lyvbroadway-tour"]
    assert body["skipped"] == [{"filename": "plans.zip", "reason": "only photos and videos"}]
    assert client.post("/api/workspace/creative/upload", data={"company_id": "18234410021"}).status_code == 400
