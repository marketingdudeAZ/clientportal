"""Capture the exact (system, user) prompt pair generate_recap sends.

Used by the golden generator and by test_ticket_recap_prompt.py. Intercepts
the real Anthropic call rather than re-implementing the composition, so the
golden reflects what the model actually receives — including any future change
to how ticket_recap assembles its prompt.
"""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class _Block:
    type = "text"
    text = ('{"note":"We completed the requested work.","surfaced_problem":false,'
            '"attribution":"none","needs_review":false,"review_reason":""}')


class _Msg:
    content = [_Block()]


def capture_prompt(fixture: dict) -> dict:
    """Return {"system": ..., "user": ...} for one fixture. No network."""
    import config
    import ticket_recap

    captured: dict = {}

    class _Messages:
        def create(self, **kwargs):
            captured["system"] = kwargs.get("system")
            msgs = kwargs.get("messages") or [{}]
            captured["user"] = msgs[0].get("content")
            return _Msg()

    class _FakeAnthropic:
        def __init__(self, *a, **kw):
            self.messages = _Messages()

    fake = types.ModuleType("anthropic")
    fake.Anthropic = _FakeAnthropic

    prev_anthropic = sys.modules.get("anthropic")
    prev_key = getattr(config, "ANTHROPIC_API_KEY", None)
    sys.modules["anthropic"] = fake
    config.ANTHROPIC_API_KEY = "test-key"
    try:
        ticket_recap.generate_recap(
            fixture["task"], fixture["comments"], fixture["ticket_type"])
    finally:
        if prev_anthropic is None:
            sys.modules.pop("anthropic", None)
        else:
            sys.modules["anthropic"] = prev_anthropic
        config.ANTHROPIC_API_KEY = prev_key

    if "system" not in captured:
        raise AssertionError(
            f"fixture {fixture['id']}: generate_recap never called the model")
    return {"system": captured["system"], "user": captured["user"]}
