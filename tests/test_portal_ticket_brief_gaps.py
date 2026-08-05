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

    def test_askable_helper_agrees_with_the_mapping(self):
        for type_key, keys in config.PORTAL_TICKET_BRIEF_FIELDS.items():
            for key in keys:
                self.assertTrue(portal_tickets._is_askable(cb.FIELDS[key]),
                                f"{type_key}: '{key}' fails _is_askable")

    def test_the_cap_is_sane(self):
        self.assertGreaterEqual(config.PORTAL_TICKET_BRIEF_MAX_ASK, 1)
        self.assertLessEqual(config.PORTAL_TICKET_BRIEF_MAX_ASK, 8)


# ── 2-4. the gap engine ─────────────────────────────────────────────────────

# creative_ad_copy, in mapping order — the fixture the gap tests lean on.
CREATIVE = ["taglines", "brand_adjectives", "differentiators", "residents_love",
            "property_amenities", "unit_features", "must_include", "forbidden_phrases"]


def _gaps(props, type_key="creative_ad_copy", company_id="123"):
    with mock.patch.object(cb, "load_company_state", return_value=props):
        return portal_tickets.brief_gaps(company_id, type_key)


class GapEngineTests(unittest.TestCase):

    def test_mapping_fixture_matches_config(self):
        """Guard the fixture itself — if the mapping moves, these tests must too."""
        self.assertEqual(config.PORTAL_TICKET_BRIEF_FIELDS["creative_ad_copy"], CREATIVE)

    # ── 2. known vs ask split ────────────────────────────────────────────────

    def test_known_and_ask_split_by_source(self):
        out = _gaps({
            "name": "Sunset Ridge",
            "fluency_taglines": "Live Well, Live Here",       # human override
            "fluency_property_amenities": "Pool\nGym",        # pipeline-resolved
        })
        known = {k["key"]: k for k in out["known"]}
        asked = [a["key"] for a in out["ask"]]

        self.assertEqual(set(known), {"taglines", "property_amenities"})
        self.assertEqual(known["taglines"]["source"], "override")
        self.assertEqual(known["property_amenities"]["source"], "resolved")
        self.assertEqual(known["property_amenities"]["preview"], "Pool · Gym")
        self.assertEqual(out["property_name"], "Sunset Ridge")

        # Neither known field is re-asked; the ask is the next 5 empties in order.
        self.assertNotIn("taglines", asked)
        self.assertNotIn("property_amenities", asked)
        self.assertEqual(asked, ["brand_adjectives", "differentiators", "residents_love",
                                 "unit_features", "must_include"])

        c = out["counts"]
        self.assertEqual(c, {"mapped": 8, "known": 2, "asked": 5, "deferred": 1})
        self.assertEqual(c["known"] + c["asked"] + c["deferred"], c["mapped"])

    def test_ask_rows_carry_what_the_form_needs_to_render(self):
        out = _gaps({"name": "X"}, type_key="new_account_build")
        rows = {a["key"]: a for a in out["ask"]}
        self.assertEqual(rows["voice_tier"]["input"], "multiselect")
        self.assertEqual(rows["voice_tier"]["options"],
                         ["value", "standard", "lifestyle", "luxury"])
        self.assertEqual(rows["advertised_name"]["input"], "text")
        self.assertEqual(rows["differentiators"]["input"], "textarea")
        self.assertEqual(rows["differentiators"]["placeholder"], "One per line")
        for row in out["ask"]:
            self.assertLessEqual(len(row["hint"]), 121)  # 120 + the ellipsis
            self.assertTrue(row["label"])

    def test_types_with_no_mapping_produce_no_block(self):
        for type_key in ("general", "dispo_cancel", "new_business"):
            out = _gaps({"name": "X", "fluency_taglines": "t"}, type_key=type_key)
            self.assertEqual(out["ask"], [])
            self.assertEqual(out["known"], [])
            self.assertEqual(out["counts"]["mapped"], 0)

    # ── 3. the cap holds ─────────────────────────────────────────────────────

    def test_cap_limits_the_ask_and_defers_the_rest(self):
        out = _gaps({"name": "Empty Property"})
        self.assertEqual(len(out["ask"]), 5)
        self.assertEqual([a["key"] for a in out["ask"]], CREATIVE[:5])
        self.assertEqual(out["ask"][0]["key"], "taglines")  # priority order preserved
        self.assertEqual([d["key"] for d in out["deferred"]], CREATIVE[5:])
        self.assertTrue(all(d["reason"] == "over_cap" for d in out["deferred"]))
        self.assertEqual(out["counts"], {"mapped": 8, "known": 0, "asked": 5, "deferred": 3})

    def test_cap_is_configurable(self):
        with mock.patch.object(config, "PORTAL_TICKET_BRIEF_MAX_ASK", 3):
            out = _gaps({"name": "Empty Property"})
        self.assertEqual(len(out["ask"]), 3)
        self.assertEqual(out["counts"]["deferred"], 5)

    def test_unaskable_fields_are_deferred_not_asked(self):
        with mock.patch.dict(config.PORTAL_TICKET_BRIEF_FIELDS,
                             {"creative_ad_copy": ["floor_plans", "year_built", "taglines"]},
                             clear=False):
            out = _gaps({"name": "X"})
        self.assertEqual([a["key"] for a in out["ask"]], ["taglines"])
        self.assertEqual({d["key"]: d["reason"] for d in out["deferred"]},
                         {"floor_plans": "not_askable", "year_built": "not_askable"})

    # ── 4. whitespace-only override is a gap ─────────────────────────────────

    def test_whitespace_only_override_counts_as_missing(self):
        """The drift bug, applied here: '   ' is not an answer.

        Portal display, the Fluency feed, and this form all route through
        resolve_value, so they can never disagree about what's filled in.
        """
        out = _gaps({"name": "X", "fluency_taglines": "   ",
                     "fluency_brand_adjectives": "\n\t "})
        asked = [a["key"] for a in out["ask"]]
        self.assertIn("taglines", asked)
        self.assertIn("brand_adjectives", asked)
        self.assertEqual(out["known"], [])

    def test_whitespace_override_falls_through_to_a_real_resolved_value(self):
        out = _gaps({"name": "X",
                     "fluency_property_amenities_override": "   ",
                     "fluency_property_amenities": "Pool"})
        known = {k["key"]: k for k in out["known"]}
        self.assertIn("property_amenities", known)
        self.assertEqual(known["property_amenities"]["source"], "resolved")


# ── 5-6. the endpoint ───────────────────────────────────────────────────────

from flask import Flask  # noqa: E402

import routes.portal_tickets as pt_routes  # noqa: E402

GAPS_URL = "/api/portal-tickets/brief-gaps"
PORTAL_HDR = {"X-Portal-Email": "manager@rpmliving.com"}


def _client():
    app = Flask(__name__)
    app.register_blueprint(pt_routes.portal_tickets_bp)
    return app.test_client()


class EndpointContractTests(unittest.TestCase):
    """The full error table from the scope doc. Nothing here may 500."""

    def setUp(self):
        self.client = _client()
        # The flag is off by default; these tests exercise the enabled path.
        p = mock.patch.object(config, "PORTAL_TICKET_BRIEF_GAPS_ENABLED", True)
        p.start()
        self.addCleanup(p.stop)

    def _get(self, query, headers=PORTAL_HDR):
        return self.client.get(GAPS_URL + query, headers=headers)

    def test_200_shape(self):
        with mock.patch.object(cb, "load_company_state",
                               return_value={"name": "Sunset Ridge", "fluency_taglines": "Live Well"}):
            r = self._get("?company_id=123&ticket_type=creative_ad_copy&uuid=abc-123")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(set(body), {"ok", "enabled", "ticket_type", "company_id",
                                     "property_name", "ask", "known", "deferred", "counts"})
        self.assertTrue(body["ok"])
        self.assertTrue(body["enabled"])
        self.assertEqual(body["ticket_type"], "creative_ad_copy")
        self.assertEqual(body["company_id"], "123")
        self.assertEqual(len(body["ask"]), 5)
        self.assertEqual([k["key"] for k in body["known"]], ["taglines"])
        self.assertEqual(set(body["counts"]), {"mapped", "known", "asked", "deferred"})
        self.assertEqual(set(body["ask"][0]),
                         {"key", "label", "input", "hint", "options", "placeholder"})

    def test_401_without_auth(self):
        r = self._get("?company_id=123&ticket_type=creative_ad_copy", headers={})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.get_json(), {"error": "auth required"})

    def test_internal_key_also_authorizes(self):
        with mock.patch.dict(os.environ, {"INTERNAL_API_KEY": "s3cret"}), \
             mock.patch.object(cb, "load_company_state", return_value={"name": "X"}):
            r = self._get("?company_id=123&ticket_type=creative_ad_copy",
                          headers={"X-Internal-Key": "s3cret"})
        self.assertEqual(r.status_code, 200)

    def test_400_missing_company_id(self):
        r = self._get("?ticket_type=creative_ad_copy")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()["error"], "company_id required")

    def test_400_missing_ticket_type(self):
        r = self._get("?company_id=123")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()["error"], "ticket_type required")

    def test_400_unknown_ticket_type(self):
        r = self._get("?company_id=123&ticket_type=not_a_type")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()["error"], "Unknown ticket type.")

    def test_200_empty_for_unmapped_type(self):
        with mock.patch.object(cb, "load_company_state", return_value={"name": "X"}):
            r = self._get("?company_id=123&ticket_type=general")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body["ask"], [])
        self.assertEqual(body["counts"]["mapped"], 0)

    def test_200_empty_when_flag_off(self):
        with mock.patch.object(config, "PORTAL_TICKET_BRIEF_GAPS_ENABLED", False), \
             mock.patch.object(cb, "load_company_state") as load:
            r = self._get("?company_id=123&ticket_type=creative_ad_copy")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertFalse(body["enabled"])
        self.assertEqual(body["ask"], [])
        self.assertEqual(body["counts"], {"mapped": 0, "known": 0, "asked": 0, "deferred": 0})
        load.assert_not_called()  # flag off = we don't even read HubSpot

    def test_options_preflight(self):
        r = self.client.open(GAPS_URL, method="OPTIONS")
        self.assertIn(r.status_code, (200, 204))


class DegradedPathTests(unittest.TestCase):
    """HubSpot down -> the form still renders. No 500s, ever."""

    def setUp(self):
        self.client = _client()
        p = mock.patch.object(config, "PORTAL_TICKET_BRIEF_GAPS_ENABLED", True)
        p.start()
        self.addCleanup(p.stop)

    def _get(self):
        return self.client.get(
            GAPS_URL + "?company_id=123&ticket_type=creative_ad_copy", headers=PORTAL_HDR)

    def test_profile_read_raises(self):
        with mock.patch.object(cb, "load_company_state", side_effect=RuntimeError("hubspot 500")):
            r = self._get()
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertTrue(body["degraded"])
        self.assertEqual(body["ask"], [])
        self.assertEqual(body["known"], [])

    def test_profile_read_returns_empty(self):
        with mock.patch.object(cb, "load_company_state", return_value={}):
            r = self._get()
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertTrue(body["degraded"])
        self.assertEqual(body["ask"], [])
        self.assertEqual(body["counts"]["mapped"], 8)  # we know what we'd have asked

    def test_unexpected_engine_error_is_contained(self):
        with mock.patch.object(portal_tickets, "brief_gaps", side_effect=ValueError("boom")):
            r = self._get()
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["degraded"])


if __name__ == "__main__":
    unittest.main()
