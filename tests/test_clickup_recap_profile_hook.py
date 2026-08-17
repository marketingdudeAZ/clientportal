"""The ticket-completion → property-profile hook inside clickup_recap.

The recap path is production traffic that writes client-visible notes, so the
new profile-proposal step must be strictly additive:

  * it runs only after a ticket matched EXACTLY ONE uuid-bearing company
  * it is handed that company's uuid, not a re-derived one
  * a failure inside it never costs us the recap
  * dry_run previews proposals without storing them
  * with the flag off, nothing changes about the recap's behaviour

Every external call is mocked — no ClickUp, HubSpot, LLM, or BigQuery traffic.
"""

from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("HUBSPOT_API_KEY", "test-key")
os.environ.setdefault("CLICKUP_API_KEY", "test-key")

import pytest  # noqa: E402

import clickup_client  # noqa: E402
import clickup_recap  # noqa: E402
import ticket_profile_sync  # noqa: E402
import ticket_recap  # noqa: E402
import ticket_recap_writer  # noqa: E402

DONE_TASK = {
    "id": "task-9",
    "name": "Rebrand rollout",
    "list": {"name": "Rebrands"},
    "status": {"status": "complete", "type": "closed"},
    "tags": [],
    "custom_fields": [],
}
COMPANY = {"id": "cid-1", "properties": {"name": "The Maple", "uuid": "u-1"}}


@pytest.fixture
def wired(monkeypatch):
    """A completed ticket that matches one company and produces a recap."""
    monkeypatch.setattr(clickup_client, "get_task", lambda tid: dict(DONE_TASK))
    monkeypatch.setattr(clickup_client, "get_comments", lambda tid: [{"text": "done"}])
    monkeypatch.setattr(clickup_client, "add_tag", lambda tid, tag: True)
    monkeypatch.setattr(clickup_recap, "match_company_for_ticket",
                        lambda task: (COMPANY, "url:domain"))
    monkeypatch.setattr(ticket_recap, "generate_recap",
                        lambda *a, **k: {"note": "We refreshed your branding.",
                                         "needs_review": False, "review_reason": "",
                                         "flags": [], "attribution": "none"})
    monkeypatch.setattr(ticket_recap_writer, "post_recap_to_company",
                        lambda *a, **k: {"note_id": "n-1"})


def test_proposals_are_generated_with_the_matched_company_uuid(wired):
    with mock.patch.object(ticket_profile_sync, "propose_from_task",
                           return_value=[{"field_key": "advertised_name"}]) as prop:
        out = clickup_recap.process_completed_task("task-9")

    prop.assert_called_once()
    args, kwargs = prop.call_args
    assert args[1] == "cid-1"        # the matched company
    assert args[2] == "u-1"          # its uuid, straight off the matched record
    assert args[3] == "rebrand"      # inferred from the ClickUp list name
    assert kwargs["persist"] is True
    assert out["profile_proposals"] == 1


def test_a_proposal_failure_never_costs_us_the_recap(wired):
    with mock.patch.object(ticket_profile_sync, "propose_from_task",
                           side_effect=RuntimeError("bigquery down")):
        out = clickup_recap.process_completed_task("task-9")
    assert out["posted"] == {"note_id": "n-1"}
    assert out["profile_proposals"] == 0


def test_dry_run_previews_proposals_without_storing_them(wired):
    with mock.patch.object(ticket_profile_sync, "propose_from_task",
                           return_value=[{"field_key": "advertised_name"}]) as prop:
        out = clickup_recap.process_completed_task("task-9", dry_run=True)
    assert prop.call_args[1]["persist"] is False
    assert out["dry_run"] is True
    assert out["profile_proposals"] == [{"field_key": "advertised_name"}]


def test_flag_off_means_no_proposals_and_an_unchanged_recap(wired, monkeypatch):
    monkeypatch.delenv("TICKET_PROFILE_LOOP_ENABLED", raising=False)
    out = clickup_recap.process_completed_task("task-9")
    assert out["profile_proposals"] == 0
    assert out["posted"] == {"note_id": "n-1"}


def test_an_excluded_type_never_reaches_the_hook(monkeypatch):
    """dispo_cancel short-circuits before matching, as it always has."""
    dispo = dict(DONE_TASK, list={"name": "Dispo / Cancel"})
    monkeypatch.setattr(clickup_client, "get_task", lambda tid: dispo)
    with mock.patch.object(ticket_profile_sync, "propose_from_task") as prop:
        out = clickup_recap.process_completed_task("task-9")
    prop.assert_not_called()
    assert "excluded type" in out["skipped"]


def test_an_unmatched_ticket_never_reaches_the_hook(wired, monkeypatch):
    """No confident uuid match → skip. Proposals inherit that same bar."""
    monkeypatch.setattr(clickup_recap, "match_company_for_ticket",
                        lambda task: (None, "no confident uuid match"))
    with mock.patch.object(ticket_profile_sync, "propose_from_task") as prop:
        out = clickup_recap.process_completed_task("task-9")
    prop.assert_not_called()
    assert out["skipped"].startswith("no match")


def test_proposals_survive_an_empty_recap(wired, monkeypatch):
    """A ticket can change the property record even with no client-facing note."""
    monkeypatch.setattr(ticket_recap, "generate_recap",
                        lambda *a, **k: {"note": "", "review_reason": "LLM unavailable"})
    with mock.patch.object(ticket_profile_sync, "propose_from_task",
                           return_value=[{"field_key": "advertised_name"}]):
        out = clickup_recap.process_completed_task("task-9")
    assert out["skipped"] == "empty recap"
    assert out["profile_proposals"] == 1
