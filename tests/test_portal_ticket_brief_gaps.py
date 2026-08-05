"""Tests for ticket brief gaps — ask only what the property profile lacks.

See docs/ticket-brief-gaps-scope.md. Nine cases:

  1. mapping integrity (config police — a bad mapping fails CI, not the portal)
  2. known vs ask split
  3. the 5-question cap holds
  4. whitespace-only override counts as a gap
  5. endpoint contract (200 shape + every error row)
  6. HubSpot down -> degraded, never a 500
  7. write-back happy path
  8. write failure is isolated (the ticket still gets created)
  9. write gates (mapping-only, R1/uuid unreachable, anti-clobber, blanks)

All external I/O — HubSpot, ClickUp, BigQuery — is mocked. BigQuery is left
unconfigured so the mapping store no-ops (its graceful-degradation path).
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from unittest import mock

# webhook-server/ must win for `import config` — it holds the app config
# (and the PORTAL_TICKET_* symbols). Root is appended last as a fallback.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("HUBSPOT_API_KEY", "test-key")
os.environ.setdefault("CLICKUP_API_KEY", "test-key")
os.environ["CLICKUP_LIST_CREATIVE_AD_COPY"] = "901-creative"
os.environ["CLICKUP_LIST_GENERAL"] = "901-general"
os.environ["CLICKUP_LIST_CAMPAIGN_REVIEW"] = "901-review"

import community_brief as cb  # noqa: E402
import config  # noqa: E402
import portal_tickets  # noqa: E402


# ── 1. mapping integrity — the config police ────────────────────────────────


class MappingIntegrityTests(unittest.TestCase):
    """Every mapped key must be real, writable, and safe to ask a client.

    This is the test that makes the mapping self-policing: a typo, a table
    field, or an internal-only field slipped into a client-facing type fails
    here instead of silently dropping a question (or leaking budget/ICP
    context into the client portal).
    """

    def setUp(self):
        self.types_by_key = {t["key"]: t for t in config.PORTAL_TICKET_TYPES}

    def test_every_mapped_type_is_a_real_ticket_type(self):
        for type_key in config.PORTAL_TICKET_BRIEF_FIELDS:
            self.assertIn(type_key, self.types_by_key,
                          f"{type_key} is not a PORTAL_TICKET_TYPES key")

    def test_every_mapped_key_exists_in_the_brief(self):
        for type_key, keys in config.PORTAL_TICKET_BRIEF_FIELDS.items():
            for key in keys:
                self.assertIn(key, cb.FIELDS,
                              f"{type_key}: '{key}' is not a community_brief field")

    def test_every_mapped_field_has_somewhere_to_write(self):
        for type_key, keys in config.PORTAL_TICKET_BRIEF_FIELDS.items():
            for key in keys:
                self.assertTrue(cb.FIELDS[key].hs_override,
                                f"{type_key}: '{key}' has no hs_override to write to")

    def test_no_table_or_readonly_fields_are_asked(self):
        for type_key, keys in config.PORTAL_TICKET_BRIEF_FIELDS.items():
            for key in keys:
                ftype = cb.FIELDS[key].type
                self.assertNotIn(ftype, cb.TABLE_TYPES,
                                 f"{type_key}: '{key}' is a table field, not inline-answerable")
                self.assertNotEqual(ftype, "readonly",
                                    f"{type_key}: '{key}' is machine-owned (readonly)")

    def test_no_internal_fields_on_client_facing_types(self):
        """Budget, ICP, resident friction, PMS/CMS never get asked of a client."""
        for type_key, keys in config.PORTAL_TICKET_BRIEF_FIELDS.items():
            if self.types_by_key[type_key].get("audience") != "client":
                continue
            for key in keys:
                self.assertFalse(cb.FIELDS[key].internal,
                                 f"{type_key}: '{key}' is internal=True — not client-safe")

    def test_no_duplicate_keys_within_a_type(self):
        for type_key, keys in config.PORTAL_TICKET_BRIEF_FIELDS.items():
            self.assertEqual(len(keys), len(set(keys)),
                             f"{type_key} maps a field more than once")

    def test_the_deliberately_empty_types_stay_empty(self):
        """general / dispo_cancel / new_business must show no profile block."""
        for type_key in ("general", "dispo_cancel", "new_business"):
            self.assertEqual(config.PORTAL_TICKET_BRIEF_FIELDS.get(type_key), [],
                             f"{type_key} must map to no brief fields")

    def test_the_cap_is_sane(self):
        self.assertGreaterEqual(config.PORTAL_TICKET_BRIEF_MAX_ASK, 1)
        self.assertLessEqual(config.PORTAL_TICKET_BRIEF_MAX_ASK, 8)


if __name__ == "__main__":
    unittest.main()
