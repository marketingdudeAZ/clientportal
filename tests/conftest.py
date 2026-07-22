"""Pytest bootstrap shared by the whole suite.

Loaded before any test module is imported, so the env vars set here are in
place when `config.py` is first imported. Several modules bind config values at
import time (e.g. `from config import CLICKUP_API_KEY`), which freezes them for
the process. Without this, whichever test module imported config first decided
those frozen values — making the full-suite run order-dependent (green in
isolation, red in CI). See tests/test_creative_transition.py and
tests/test_portal_tickets.py for the affected modules.

Only fills gaps with setdefault — a real value in the environment always wins.
"""

import os

import pytest

_TEST_ENV_DEFAULTS = {
    "HUBSPOT_API_KEY": "test-key",
    "CLICKUP_API_KEY": "test-key",
    "CLICKUP_LIST_CREATIVE_TRANSITIONS": "900100200300",
    "ANTHROPIC_API_KEY": "test-key",
}

for _k, _v in _TEST_ENV_DEFAULTS.items():
    os.environ.setdefault(_k, _v)


@pytest.fixture(autouse=True)
def _restore_environ():
    """Revert any os.environ mutation a test makes, so run-time env changes
    (e.g. several tests set INTERNAL_API_KEY) can't leak into later tests and
    make outcomes depend on collection order."""
    snapshot = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(snapshot)
