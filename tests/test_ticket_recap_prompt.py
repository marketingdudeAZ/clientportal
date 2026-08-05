"""T1 — the ticket-recap prompt is byte-identical after the CLIENT_VOICE_RULES
extraction.

This is the license for the whole refactor. The shared house rules move out of
ticket_recap.SYSTEM_PROMPT into rec_voice.CLIENT_VOICE_RULES, and the recap
composes them back via rec_voice.build_system_prompt. Nothing the model
receives may change — not a character, not a blank line.

Goldens in tests/golden/ticket_recap_prompts.json were captured from the
pre-extraction code by intercepting the real Anthropic call. Regenerating them
to make a failure go away defeats the entire purpose: if this test fails, the
refactor changed the prompt and the diff is the bug.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import pytest  # noqa: E402

from recap_prompt_capture import capture_prompt  # noqa: E402
from recap_prompt_fixtures import FIXTURES  # noqa: E402

GOLDEN_PATH = os.path.join(os.path.dirname(__file__), "golden",
                           "ticket_recap_prompts.json")

with open(GOLDEN_PATH, encoding="utf-8") as fh:
    GOLDEN = json.load(fh)

# The budget/new-account exception to the shared NUMBERS rule. Appended for
# these ticket types only, never folded into the shared rules.
NUMBERS_EXCEPTION_TYPES = {"new_account_build", "budget_update",
                           "budget_update_currency_detail"}
NUMBERS_EXCEPTION_MARKER = "DO state the specific per-channel budget figures"


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda f: f["id"])
def test_t1_system_prompt_is_byte_identical(fixture):
    got = capture_prompt(fixture)
    expected = GOLDEN[fixture["id"]]["system"]
    assert got["system"] == expected, (
        f"system prompt changed for {fixture['id']}; the extraction was not "
        f"behavior-preserving")


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda f: f["id"])
def test_t1_user_message_is_byte_identical(fixture):
    got = capture_prompt(fixture)
    expected = GOLDEN[fixture["id"]]["user"]
    assert got["user"] == expected, (
        f"user message changed for {fixture['id']}; the extraction was not "
        f"behavior-preserving")


def test_t1_covers_every_framing_type():
    """Every TYPE_FRAMING key has a fixture, so no type is silently unpinned."""
    import ticket_recap
    covered = {f["ticket_type"] for f in FIXTURES}
    assert set(ticket_recap.TYPE_FRAMING) <= covered
    assert len(FIXTURES) == 7


# ── the budget numbers exception ────────────────────────────────────────────

@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda f: f["id"])
def test_numbers_exception_appears_only_for_budget_types(fixture):
    """The exception narrows the shared NUMBERS rule, so it must be scoped.

    It is appended to the user turn, which places it after the shared NUMBERS
    rule in the system prompt — an override, never folded in — and closest to
    the turn the model is answering, where instruction-following is strongest.
    """
    got = capture_prompt(fixture)
    whole = f"{got['system']}\n{got['user']}"
    if fixture["id"] in NUMBERS_EXCEPTION_TYPES:
        assert NUMBERS_EXCEPTION_MARKER in whole
        # Ordering: the exception must come after the rule it narrows.
        assert whole.index("NUMBERS: never state a specific dollar amount") < \
            whole.index(NUMBERS_EXCEPTION_MARKER)
    else:
        assert NUMBERS_EXCEPTION_MARKER not in whole


def test_shared_numbers_rule_is_present_for_every_type():
    for fixture in FIXTURES:
        got = capture_prompt(fixture)
        assert "never state a specific dollar amount" in got["system"], fixture["id"]
