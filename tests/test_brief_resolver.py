"""Tests for the canonical Community Brief override-wins resolver.

The precedence rule — override > resolved > empty, with a whitespace-only
override treated as empty — is the single contract that keeps the portal
display and the Fluency feed in agreement. These tests lock it, including the
historical drift bug where a whitespace-only override showed "Edited" in the
portal (`community_brief._effective`) but shipped empty to Fluency
(`fluency_feed._resolve`), so the client saw one brief and Fluency ran another.

Pure-function tests — no HubSpot / BigQuery / Anthropic I/O is exercised.
"""

from __future__ import annotations

import os
import sys

# Make webhook-server/ importable (same convention as test_property_brief.py).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("HUBSPOT_API_KEY", "test-key")

import community_brief as cb  # noqa: E402
import fluency_feed as ff  # noqa: E402


# ── resolve_value precedence ────────────────────────────────────────────────


def test_override_beats_resolved():
    assert cb.resolve_value({"r": "auto", "o": "human"}, "r", "o") == "human"


def test_resolved_when_no_override():
    assert cb.resolve_value({"r": "auto"}, "r", "o") == "auto"


def test_empty_when_neither():
    assert cb.resolve_value({}, "r", "o") == ""


def test_whitespace_override_falls_through_to_resolved():
    # The drift bug, encoded as a regression: "   " is not a real human edit.
    assert cb.resolve_value({"r": "auto", "o": "   "}, "r", "o") == "auto"


def test_whitespace_override_no_resolved_is_empty():
    assert cb.resolve_value({"o": "   "}, "r", "o") == ""


def test_none_keys_safe():
    assert cb.resolve_value({"o": "x"}, None, "o") == "x"
    assert cb.resolve_value({"r": "x"}, "r", None) == "x"
    assert cb.resolve_value({}, None, None) == ""


# ── _nonblank ───────────────────────────────────────────────────────────────


def test_nonblank():
    assert cb._nonblank("x") is True
    assert cb._nonblank("  x ") is True
    assert cb._nonblank("") is False
    assert cb._nonblank("   ") is False
    assert cb._nonblank(None) is False


# ── _effective delegates and matches a real field ──────────────────────────


def test_effective_uses_field_keys():
    field = cb.FIELDS["neighborhood"]  # has both hs_resolved + hs_override
    props = {field.hs_resolved: "Downtown", field.hs_override: "South Congress"}
    assert cb._effective(field, props) == "South Congress"
    # Whitespace override must fall through to the pipeline value.
    props2 = {field.hs_resolved: "Downtown", field.hs_override: "  "}
    assert cb._effective(field, props2) == "Downtown"


# ── parity: portal _effective() and feed _resolve() never disagree ──────────


def test_portal_and_feed_agree_on_whitespace_override():
    field = cb.FIELDS["property_amenities"]  # resolved + override, list-ish
    props = {field.hs_resolved: "Pool\nGym", field.hs_override: "   "}
    portal = cb._effective(field, props)  # raw
    feed = ff._resolve(props, field.hs_resolved, field.hs_override)  # normalized
    # Both fell through to the resolved value; neither picked the whitespace.
    assert portal == "Pool\nGym"
    assert feed == "Pool, Gym"  # _norm comma-joins the SAME source value
    assert "   " not in (portal, feed)


def test_feed_normalizes_but_shares_precedence():
    field = cb.FIELDS["property_amenities"]
    props = {field.hs_resolved: "auto", field.hs_override: "Pool\nGym\nSpa"}
    # Override wins on both surfaces; only the formatting differs.
    assert cb._effective(field, props) == "Pool\nGym\nSpa"
    assert ff._resolve(props, field.hs_resolved, field.hs_override) == "Pool, Gym, Spa"
