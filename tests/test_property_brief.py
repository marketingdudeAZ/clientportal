"""Tests for the property-brief automation.

The automation spans four modules — `clickup_client`, `property_brief_store`,
`property_brief`, and `routes.property_brief`. These tests cover:

  - parsing custom fields out of a ClickUp task
  - the trigger gate (creation vs flagged-update)
  - selections/totals coercion
  - the in-memory brief store (single-use tokens, expiry, revisions)
  - the commercial path (company match → deal → quote)
  - the brief path (LLM call mocked; approval URL posted to ClickUp)
  - the approval portal (approve + needs-edits HTTP flows)
  - the quote-signed handler

External I/O — HubSpot, ClickUp, and Anthropic — is fully mocked.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
import unittest
from unittest import mock

# Make webhook-server/ importable. Insert it FIRST so its config.py wins
# over the older root-level config.py used by other parts of the repo.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Make sure config sees a HUBSPOT_API_KEY so deal_creator's module-level
# header dict isn't None.
os.environ.setdefault("HUBSPOT_API_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("CLICKUP_API_KEY", "test-key")
os.environ.setdefault("CLICKUP_WEBHOOK_SECRET", "test-clickup-secret")
os.environ.setdefault("HUBSPOT_QUOTE_WEBHOOK_SECRET", "test-hubspot-secret")
os.environ.setdefault("PROPERTY_BRIEF_PUBLIC_URL", "https://portal.example.com")

import clickup_client  # noqa: E402
import property_brief  # noqa: E402
import property_brief_store as store  # noqa: E402


# ── Fixtures ───────────────────────────────────────────────────────────────

def _task(**overrides):
    base = {
        "id":   "abc123",
        "name": "Maple Court — New Property Brief",
        "url":  "https://app.clickup.com/t/abc123",
        "description": "New property launching Q3 in Austin.",
        "assignees": [
            {"id": 999, "username": "Test AM", "email": "am@rpmliving.com"},
        ],
        "custom_fields": [
            {"name": "Property Name",    "value": "Maple Court"},
            {"name": "Property Domain",  "value": "https://maplecourtaustin.com"},
            {"name": "Submitter Email",  "value": "submitter@rpmliving.com"},
            {"name": "Submitter ClickUp ID", "value": "12345"},
            {"name": "RM Email",         "value": "rm@rpmliving.com"},
            {"name": "Selections",       "value": json.dumps({
                "seo": {"tier": "Standard", "monthly": 800, "setup": 0},
                "paid_search": {"tier": "Google Ads", "monthly": 3500, "setup": 500},
            })},
        ],
    }
    base.update(overrides)
    return base


# ── 1. Parsing ─────────────────────────────────────────────────────────────

class TestParseTicket(unittest.TestCase):
    def test_extracts_required_fields(self):
        parsed = property_brief.parse_ticket(_task())
        self.assertEqual(parsed["ticket_id"], "abc123")
        self.assertEqual(parsed["property_name"], "Maple Court")
        self.assertEqual(parsed["submitter_email"], "submitter@rpmliving.com")
        self.assertEqual(parsed["rm_email"], "rm@rpmliving.com")
        self.assertEqual(parsed["selections"]["seo"]["tier"], "Standard")
        self.assertEqual(parsed["selections"]["seo"]["monthly"], 800.0)
        self.assertEqual(parsed["totals"]["monthly"], 4300.0)
        self.assertEqual(parsed["totals"]["setup"], 500.0)

    def test_missing_required_field_raises(self):
        task = _task()
        task["custom_fields"] = [
            f for f in task["custom_fields"] if f["name"] != "RM Email"
        ]
        with self.assertRaises(property_brief.TicketParseError) as ctx:
            property_brief.parse_ticket(task)
        self.assertIn("rm_email", str(ctx.exception))

    def test_selections_accepts_list_form(self):
        task = _task()
        for f in task["custom_fields"]:
            if f["name"] == "Selections":
                f["value"] = json.dumps([
                    {"channel": "seo", "tier": "Premium", "monthly": "1,300", "setup": "$0"},
                ])
        parsed = property_brief.parse_ticket(task)
        self.assertEqual(parsed["selections"]["seo"]["monthly"], 1300.0)

    def test_empty_selections_raises(self):
        task = _task()
        for f in task["custom_fields"]:
            if f["name"] == "Selections":
                f["value"] = ""
        with self.assertRaises(property_brief.TicketParseError):
            property_brief.parse_ticket(task)


def _rpm_dropdown(name, selected_label, options=None):
    """Build a fake ClickUp drop_down field where `selected_label` is chosen."""
    options = options or [
        {"id": "opt-none",     "name": "None",     "orderindex": 0},
        {"id": "opt-standard", "name": "Standard", "orderindex": 1},
        {"id": "opt-premium",  "name": "Premium",  "orderindex": 2},
    ]
    chosen_id = None
    for o in options:
        if o["name"].lower() == selected_label.lower():
            chosen_id = o["id"]
            break
    return {
        "name": name,
        "type": "drop_down",
        "value": chosen_id,
        "type_config": {"options": options},
    }


def _rpm_currency(name, amount):
    return {"name": name, "type": "currency", "value": amount}


def _rpm_task():
    """A task shaped like the live RPM "New Account Build" intake list."""
    return {
        "id":   "rpm-task-1",
        "name": "AXIS Crossroads",
        "url":  "https://app.clickup.com/t/rpm-task-1",
        "description": "New build going live in May 2026.",
        "custom_fields": [
            {"name": "Property URL",     "type": "url",         "value": "https://axiscrossroads.com"},
            {"name": "Requester Email",  "type": "email",       "value": "kyle@rpmliving.com"},
            {"name": "RM's Email",       "type": "email",       "value": "rm@rpmliving.com"},
            _rpm_currency("Paid Search",  3500),
            _rpm_dropdown("Paid Search",  "Standard"),
            _rpm_currency("Paid Social",  0),
            _rpm_dropdown("Paid Social",  "None"),
            _rpm_currency("PMax",         2000),
            _rpm_dropdown("P Max",        "Premium"),
            _rpm_dropdown("SEO - Onboard","Standard"),
            _rpm_dropdown("Organic Social","None"),
        ],
    }


class TestParseTicketRPMShape(unittest.TestCase):
    """The RPM intake lists don't have a 'Selections' JSON field — selections
    come from per-channel currency + tier dropdowns. parse_ticket must fall
    back to the RPM extractor when explicit Selections is absent."""

    def test_parses_property_name_from_task_title(self):
        parsed = property_brief.parse_ticket(_rpm_task())
        self.assertEqual(parsed["property_name"], "AXIS Crossroads")

    def test_parses_domain_from_property_url_field(self):
        parsed = property_brief.parse_ticket(_rpm_task())
        self.assertEqual(parsed["property_domain"], "https://axiscrossroads.com")

    def test_parses_submitter_from_requester_email(self):
        parsed = property_brief.parse_ticket(_rpm_task())
        self.assertEqual(parsed["submitter_email"], "kyle@rpmliving.com")

    def test_parses_rm_from_rm_apostrophe_email(self):
        parsed = property_brief.parse_ticket(_rpm_task())
        self.assertEqual(parsed["rm_email"], "rm@rpmliving.com")

    def test_parses_assignee_email_from_first_assignee(self):
        task = _rpm_task()
        task["assignees"] = [
            {"id": 1, "username": "Jane Doe", "email": "jane@rpmliving.com"},
            {"id": 2, "username": "Other", "email": "other@rpmliving.com"},
        ]
        parsed = property_brief.parse_ticket(task)
        self.assertEqual(parsed["assignee_email"], "jane@rpmliving.com")
        self.assertEqual(parsed["assignee_name"], "Jane Doe")

    def test_assignee_email_empty_when_no_assignees(self):
        task = _rpm_task()
        task["assignees"] = []
        parsed = property_brief.parse_ticket(task)
        self.assertEqual(parsed["assignee_email"], "")

    def test_extracts_paid_channels_with_currency_and_tier(self):
        parsed = property_brief.parse_ticket(_rpm_task())
        self.assertIn("paid_search", parsed["selections"])
        self.assertEqual(parsed["selections"]["paid_search"]["monthly"], 3500.0)
        self.assertEqual(parsed["selections"]["paid_search"]["tier"], "Standard")
        self.assertIn("pmax", parsed["selections"])
        self.assertEqual(parsed["selections"]["pmax"]["monthly"], 2000.0)
        self.assertEqual(parsed["selections"]["pmax"]["tier"], "Premium")

    def test_extracts_tier_only_channels_with_zero_monthly(self):
        # SEO has no currency field on the RPM form — line item still gets
        # created so the brief mentions it; pricing comes from elsewhere.
        parsed = property_brief.parse_ticket(_rpm_task())
        self.assertIn("seo", parsed["selections"])
        self.assertEqual(parsed["selections"]["seo"]["tier"], "Standard")
        self.assertEqual(parsed["selections"]["seo"]["monthly"], 0.0)

    def test_skips_channels_with_no_amount_and_no_tier(self):
        # Paid Social has $0 + tier="None" → skip
        # Organic Social has no currency + tier="None" → skip
        parsed = property_brief.parse_ticket(_rpm_task())
        self.assertNotIn("paid_social", parsed["selections"])
        self.assertNotIn("social_posting", parsed["selections"])

    def test_totals_sum_only_included_channels(self):
        parsed = property_brief.parse_ticket(_rpm_task())
        # paid_search 3500 + pmax 2000 + seo 0 = 5500
        self.assertEqual(parsed["totals"]["monthly"], 5500.0)
        self.assertEqual(parsed["totals"]["setup"], 0.0)

    def test_explicit_selections_takes_priority_over_rpm_shape(self):
        # If a task has BOTH an explicit Selections JSON AND RPM-shape
        # fields, the explicit JSON wins (lets us migrate gradually).
        task = _rpm_task()
        task["custom_fields"].append({
            "name": "Selections", "type": "text",
            "value": json.dumps({"reputation": {"tier": "Plus", "monthly": 250, "setup": 0}}),
        })
        parsed = property_brief.parse_ticket(task)
        self.assertIn("reputation", parsed["selections"])
        self.assertNotIn("paid_search", parsed["selections"])


class TestTypedCustomFieldLookup(unittest.TestCase):
    """The typed-field helper is what disambiguates 'Paid Search' currency
    vs 'Paid Search' drop_down on the same task."""

    def test_currency_returns_float_not_dropdown(self):
        task = {
            "custom_fields": [
                _rpm_dropdown("Paid Search", "Standard"),
                _rpm_currency("Paid Search", 3500),
            ],
        }
        self.assertEqual(
            clickup_client.custom_field_value_typed(task, "Paid Search", of_type="currency"),
            3500.0,
        )

    def test_dropdown_returns_resolved_option_name(self):
        task = {
            "custom_fields": [
                _rpm_dropdown("Paid Search", "Standard"),
                _rpm_currency("Paid Search", 3500),
            ],
        }
        self.assertEqual(
            clickup_client.custom_field_value_typed(task, "Paid Search", of_type="drop_down"),
            "Standard",
        )

    def test_returns_none_when_type_filter_excludes_all(self):
        task = {"custom_fields": [_rpm_currency("Paid Search", 3500)]}
        self.assertIsNone(
            clickup_client.custom_field_value_typed(task, "Paid Search", of_type="drop_down")
        )


class TestShouldFire(unittest.TestCase):
    def test_creation_always_fires(self):
        self.assertTrue(property_brief.should_fire({"event": "taskCreated"}, _task()))

    def test_update_without_flag_does_not_fire(self):
        self.assertFalse(property_brief.should_fire({"event": "taskUpdated"}, _task()))

    def test_update_with_reprocess_flag_fires(self):
        task = _task()
        task["custom_fields"].append({"name": "rpm_brief_reprocess", "value": True})
        self.assertTrue(property_brief.should_fire({"event": "taskUpdated"}, task))


# ── 2. Brief store ─────────────────────────────────────────────────────────

class TestBriefStore(unittest.TestCase):
    def setUp(self):
        store.set_backend(store._MemoryBackend())

    def test_create_returns_unique_token(self):
        a = store.create(ticket_id="t1", company_id="c1", deal_id="d1",
                         submitter_email="a@x", rm_email="r@x", brief_markdown="A")
        b = store.create(ticket_id="t2", company_id="c2", deal_id="d2",
                         submitter_email="b@x", rm_email="r@x", brief_markdown="B")
        self.assertNotEqual(a["token"], b["token"])
        self.assertEqual(a["status"], store.STATUS_PENDING)

    def test_get_returns_pending_record(self):
        rec = store.create(ticket_id="t1", company_id="c1", deal_id=None,
                           submitter_email="a@x", rm_email="r@x", brief_markdown="A")
        fetched = store.get(rec["token"])
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["brief_markdown"], "A")

    def test_consume_marks_token_singleuse(self):
        rec = store.create(ticket_id="t1", company_id="c1", deal_id=None,
                           submitter_email="a@x", rm_email="r@x", brief_markdown="A")
        consumed = store.consume(rec["token"], decision=store.STATUS_APPROVED, decided_by="a@x")
        self.assertEqual(consumed["status"], store.STATUS_APPROVED)
        # Second attempt is rejected.
        self.assertIsNone(store.consume(rec["token"], decision=store.STATUS_APPROVED, decided_by="a@x"))
        # Read after consume is None too.
        self.assertIsNone(store.get(rec["token"]))

    def test_needs_edits_records_feedback(self):
        rec = store.create(ticket_id="t1", company_id="c1", deal_id=None,
                           submitter_email="a@x", rm_email="r@x", brief_markdown="A")
        consumed = store.consume(
            rec["token"],
            decision=store.STATUS_NEEDS_EDITS,
            decided_by="a@x",
            feedback="Use less jargon.",
        )
        self.assertEqual(consumed["feedback_history"], ["Use less jargon."])

    def test_attach_revision_increments_count_and_history(self):
        first = store.create(ticket_id="t1", company_id="c1", deal_id=None,
                             submitter_email="a@x", rm_email="r@x", brief_markdown="v1")
        store.consume(first["token"], decision=store.STATUS_NEEDS_EDITS,
                      decided_by="a@x", feedback="Make it sharper")
        # Read the consumed record (via memory backend internals — store.get
        # would refuse since it's already decided).
        decided = store._backend()._rows[first["token"]]  # noqa: SLF001 — test access
        second = store.attach_revision(previous=decided, brief_markdown="v2", feedback="")
        self.assertEqual(second["revision_count"], 1)
        self.assertEqual(second["feedback_history"], ["Make it sharper"])
        self.assertNotEqual(second["token"], first["token"])

    def test_expired_token_returns_none(self):
        rec = store.create(ticket_id="t1", company_id="c1", deal_id=None,
                           submitter_email="a@x", rm_email="r@x", brief_markdown="A")
        # Force the row to be expired.
        rec_internal = store._backend()._rows[rec["token"]]  # noqa: SLF001
        rec_internal["expires_at_ms"] = int(time.time() * 1000) - 1000
        self.assertIsNone(store.get(rec["token"]))


# ── 3. Commercial path ─────────────────────────────────────────────────────

class TestCommercialPath(unittest.TestCase):
    def setUp(self):
        # Patch the lazy module imports inside property_brief.
        self.deal_creator = mock.MagicMock()
        self.deal_creator.create_deal_with_line_items.return_value = "deal-42"
        self.quote_generator = mock.MagicMock()
        self.quote_generator.generate_and_send_quote.return_value = "quote-99"
        self.drafter = mock.MagicMock()
        self.drafter.normalize_domain.side_effect = lambda x: (x or "").lower().replace("https://", "").replace("www.", "").split("/")[0]
        self.drafter.resolve_company_by_domain.return_value = {
            "id": "company-7", "name": "Maple Court", "domain": "maplecourtaustin.com",
        }

        def _import(name):
            return {
                "deal_creator":     self.deal_creator,
                "quote_generator":  self.quote_generator,
                "brief_ai_drafter": self.drafter,
            }[name]

        self._patcher = mock.patch.object(property_brief, "_import", side_effect=_import)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    def test_run_commercial_path_happy(self):
        parsed = property_brief.parse_ticket(_task())
        result = property_brief.run_commercial_path(parsed)
        self.assertEqual(result["company_id"], "company-7")
        self.assertEqual(result["deal_id"], "deal-42")
        self.assertEqual(result["quote_id"], "quote-99")

        # Deal creator received the parsed selections + totals.
        call = self.deal_creator.create_deal_with_line_items.call_args
        self.assertEqual(call.kwargs["company_id"], "company-7")
        self.assertEqual(call.kwargs["totals"]["monthly"], 4300.0)

    def test_company_match_falls_through_to_name_search(self):
        self.drafter.resolve_company_by_domain.return_value = None
        with mock.patch.object(property_brief, "_search_companies_by_name",
                               return_value=[{"id": "c-9", "name": "Maple Court"}]):
            parsed = property_brief.parse_ticket(_task())
            result = property_brief.run_commercial_path(parsed)
            self.assertEqual(result["company_id"], "c-9")

    def test_ambiguous_name_match_raises(self):
        self.drafter.resolve_company_by_domain.return_value = None
        with mock.patch.object(property_brief, "_search_companies_by_name",
                               return_value=[{"id": "c-9"}, {"id": "c-10"}]):
            parsed = property_brief.parse_ticket(_task())
            with self.assertRaises(property_brief.CompanyMatchAmbiguous):
                property_brief.run_commercial_path(parsed)

    def test_no_match_creates_company(self):
        self.drafter.resolve_company_by_domain.return_value = None
        with mock.patch.object(property_brief, "_search_companies_by_name", return_value=[]), \
             mock.patch.object(property_brief, "_create_company",
                               return_value={"id": "new-1", "name": "Maple Court", "domain": "maplecourtaustin.com"}) as create:
            parsed = property_brief.parse_ticket(_task())
            result = property_brief.run_commercial_path(parsed)
            self.assertEqual(result["company_id"], "new-1")
            create.assert_called_once()

    def test_create_company_does_not_write_uuid(self):
        """R1: code MUST NOT write uuid. A HubSpot workflow copies
        Record ID -> uuid once a deal is associated. Setting uuid here
        would race or stomp that workflow."""
        captured = {}

        def fake_post(url, headers=None, json=None, timeout=None):
            captured["url"] = url
            captured["body"] = json
            resp = mock.MagicMock()
            resp.status_code = 201
            resp.json.return_value = {"id": "co-100"}
            resp.raise_for_status = mock.MagicMock()
            return resp

        with mock.patch("requests.post", side_effect=fake_post):
            result = property_brief._create_company(name="Maple Court", domain="maplecourtaustin.com")

        self.assertEqual(captured["url"], "https://api.hubapi.com/crm/v3/objects/companies")
        props = captured["body"]["properties"]
        self.assertEqual(props["name"], "Maple Court")
        self.assertEqual(props["domain"], "maplecourtaustin.com")
        self.assertNotIn("uuid", props, "R1: uuid must NOT be set by code; HubSpot workflow owns it")
        # Result also doesn't pretend to know a uuid the workflow hasn't set yet.
        self.assertNotIn("uuid", result)


# ── 4. Brief path ──────────────────────────────────────────────────────────

class TestBriefPath(unittest.TestCase):
    def setUp(self):
        store.set_backend(store._MemoryBackend())

    def test_run_brief_path_persists_record_and_posts_url(self):
        parsed = property_brief.parse_ticket(_task())
        commercial = {"company_id": "company-7", "deal_id": "deal-42"}

        with mock.patch.object(property_brief, "generate_brief", return_value="# Brief\n\nHello."), \
             mock.patch.object(clickup_client, "tag_user_in_comment", return_value=True) as tag, \
             mock.patch.object(clickup_client, "update_status", return_value=True) as status:
            record = property_brief.run_brief_path(parsed, commercial)

        self.assertEqual(record["brief_markdown"], "# Brief\n\nHello.")
        self.assertEqual(record["status"], store.STATUS_PENDING)
        self.assertIn(record["token"], tag.call_args.args[2])  # URL is in comment text
        status.assert_called()  # status update fired

    def test_approval_url_uses_public_base(self):
        with mock.patch.object(property_brief, "PROPERTY_BRIEF_PUBLIC_URL",
                               "https://portal.example.com"):
            url = property_brief.approval_url("xyz")
        self.assertEqual(url, "https://portal.example.com/property-brief/approve/xyz")


# ── 5. Decision handlers ───────────────────────────────────────────────────

class TestDecisionHandlers(unittest.TestCase):
    def setUp(self):
        store.set_backend(store._MemoryBackend())

    def _make_record(self, *, revision=0):
        rec = store.create(
            ticket_id="t-99", company_id="c-7", deal_id="d-42",
            submitter_email="s@x", rm_email="r@x",
            brief_markdown="initial brief",
        )
        rec["revision_count"] = revision
        store._backend().put(rec)
        consumed = store.consume(rec["token"], decision=store.STATUS_APPROVED, decided_by="s@x")
        return consumed

    def test_handle_approval_writes_company_and_comments(self):
        rec = self._make_record()
        with mock.patch.object(property_brief, "_hs_update_company") as hs, \
             mock.patch.object(property_brief, "generate_brief_doc", return_value="https://docs/brief"), \
             mock.patch.object(property_brief, "update_spend_sheet_row") as sheet, \
             mock.patch.object(clickup_client, "post_comment", return_value=True) as comment, \
             mock.patch.object(clickup_client, "update_status", return_value=True):
            result = property_brief.handle_approval(rec)

        self.assertEqual(result["brief_url"], "https://docs/brief")
        # Company received both the brief content and the URL.
        prop_writes = [c.args[1] for c in hs.call_args_list]
        merged = {k: v for d in prop_writes for k, v in d.items()}
        self.assertEqual(merged["rpm_brief_url"], "https://docs/brief")
        self.assertIn("rpm_brief_content", merged)
        sheet.assert_called_once()
        comment.assert_called_once()

    def test_handle_needs_edits_creates_new_token(self):
        # Build a 'consumed needs-edits' record manually.
        rec = store.create(
            ticket_id="t-99", company_id="c-7", deal_id="d-42",
            submitter_email="s@x", rm_email="r@x", brief_markdown="v1",
        )
        consumed = store.consume(
            rec["token"], decision=store.STATUS_NEEDS_EDITS,
            decided_by="s@x", feedback="Add tone of voice.",
        )

        fake_task = _task()
        with mock.patch.object(clickup_client, "get_task", return_value=fake_task), \
             mock.patch.object(property_brief, "generate_brief", return_value="v2"), \
             mock.patch.object(clickup_client, "tag_user_in_comment", return_value=True), \
             mock.patch.object(clickup_client, "update_status", return_value=True):
            result = property_brief.handle_needs_edits(consumed)

        self.assertNotEqual(result["new_token"], rec["token"])
        self.assertEqual(result["revision_count"], 1)
        # Feedback history carried forward.
        new_record = store._backend()._rows[result["new_token"]]  # noqa: SLF001
        self.assertEqual(new_record["feedback_history"], ["Add tone of voice."])

    def test_handle_needs_edits_escalates_after_max(self):
        rec = store.create(
            ticket_id="t-99", company_id="c-7", deal_id="d-42",
            submitter_email="s@x", rm_email="r@x", brief_markdown="v1",
        )
        rec["revision_count"] = 99  # over the cap regardless of config
        store._backend().put(rec)
        consumed = store.consume(rec["token"], decision=store.STATUS_NEEDS_EDITS,
                                 decided_by="s@x", feedback="No good.")

        with mock.patch.object(clickup_client, "post_comment", return_value=True) as comment, \
             mock.patch.object(clickup_client, "update_status", return_value=True):
            result = property_brief.handle_needs_edits(consumed)

        self.assertTrue(result["escalated"])
        comment.assert_called_once()


# ── 6. Quote-signed handler ────────────────────────────────────────────────

class TestQuoteSigned(unittest.TestCase):
    def setUp(self):
        store.set_backend(store._MemoryBackend())

    def test_handle_quote_signed_posts_to_clickup(self):
        rec = store.create(
            ticket_id="t-77", company_id="c-7", deal_id="d-42",
            submitter_email="s@x", rm_email="r@x", brief_markdown="...",
        )
        with mock.patch.object(clickup_client, "post_comment", return_value=True) as comment, \
             mock.patch.object(clickup_client, "update_status", return_value=True) as status:
            result = property_brief.handle_quote_signed("d-42")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["ticket_id"], "t-77")
        comment.assert_called_once()
        status.assert_called_once()
        # Silence unused-var.
        _ = rec

    def test_handle_quote_signed_unknown_deal_is_ignored(self):
        result = property_brief.handle_quote_signed("d-nothing")
        self.assertEqual(result["status"], "ignored")


# ── 7. ClickUp client ──────────────────────────────────────────────────────

class TestClickUpClient(unittest.TestCase):
    def test_custom_field_value_case_insensitive(self):
        task = {"custom_fields": [{"name": "Property Name", "value": "Maple Court"}]}
        self.assertEqual(clickup_client.custom_field_value(task, "property name"), "Maple Court")
        self.assertIsNone(clickup_client.custom_field_value(task, "missing"))

    def test_post_comment_returns_false_without_auth(self):
        with mock.patch.object(clickup_client, "CLICKUP_API_KEY", ""):
            self.assertFalse(clickup_client.post_comment("t1", "hi"))


# ── 8. Approval portal HTTP ────────────────────────────────────────────────

class TestApprovalPortalHTTP(unittest.TestCase):
    def setUp(self):
        store.set_backend(store._MemoryBackend())
        from flask import Flask
        from routes.property_brief import property_brief_bp
        self.app = Flask(__name__)
        self.app.register_blueprint(property_brief_bp)
        self.client = self.app.test_client()

    def test_get_renders_brief_for_valid_token(self):
        rec = store.create(
            ticket_id="t1", company_id="c1", deal_id="d1",
            submitter_email="s@x", rm_email="r@x",
            brief_markdown="# My Brief\n\nThe content.",
        )
        resp = self.client.get(f"/property-brief/approve/{rec['token']}")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"My Brief", resp.data)
        self.assertIn(b"Approve", resp.data)
        self.assertIn(b"Needs edits", resp.data)

    def test_get_unknown_token_returns_410(self):
        resp = self.client.get("/property-brief/approve/nope")
        self.assertEqual(resp.status_code, 410)
        self.assertIn(b"no longer valid", resp.data)

    def test_post_approve_runs_handler(self):
        rec = store.create(
            ticket_id="t1", company_id="c1", deal_id="d1",
            submitter_email="s@x", rm_email="r@x",
            brief_markdown="content",
        )
        with mock.patch.object(property_brief, "handle_approval",
                               return_value={"brief_url": "u", "approver": "s@x"}) as h:
            resp = self.client.post(
                f"/api/property-brief/approve/{rec['token']}",
                data={"decision": "approved", "decided_by": "s@x"},
            )
        self.assertEqual(resp.status_code, 200)
        h.assert_called_once()

    def test_post_needs_edits_runs_handler(self):
        rec = store.create(
            ticket_id="t1", company_id="c1", deal_id="d1",
            submitter_email="s@x", rm_email="r@x",
            brief_markdown="content",
        )
        with mock.patch.object(property_brief, "handle_needs_edits",
                               return_value={"new_token": "x", "revision_count": 1}) as h:
            resp = self.client.post(
                f"/api/property-brief/approve/{rec['token']}",
                data={"decision": "needs_edits", "feedback": "Try again"},
            )
        self.assertEqual(resp.status_code, 200)
        h.assert_called_once()
        # Decision and feedback are preserved on the consumed record.
        consumed = store._backend()._rows[rec["token"]]  # noqa: SLF001
        self.assertEqual(consumed["status"], store.STATUS_NEEDS_EDITS)
        self.assertEqual(consumed["feedback_history"], ["Try again"])

    def test_post_invalid_decision_returns_400(self):
        rec = store.create(
            ticket_id="t1", company_id="c1", deal_id="d1",
            submitter_email="s@x", rm_email="r@x",
            brief_markdown="content",
        )
        resp = self.client.post(
            f"/api/property-brief/approve/{rec['token']}",
            data={"decision": "maybe"},
        )
        self.assertEqual(resp.status_code, 400)


# ── 9. ClickUp webhook ────────────────────────────────────────────────────

class TestClickUpWebhook(unittest.TestCase):
    SECRET = "test-clickup-secret"

    def setUp(self):
        store.set_backend(store._MemoryBackend())
        from flask import Flask
        from routes import property_brief as routes_pb
        self.app = Flask(__name__)
        self.app.register_blueprint(routes_pb.property_brief_bp)
        self.client = self.app.test_client()
        # Patch the module-level secret rather than relying on import-time env
        # vars — under full test discovery, config.py is imported before
        # this file runs, so env-var setdefault wouldn't take effect.
        self._secret_patch = mock.patch.object(routes_pb, "CLICKUP_WEBHOOK_SECRET", self.SECRET)
        self._secret_patch.start()
        # Clear in-flight mutex between tests so cross-test contamination
        # doesn't cause false "in_flight" skips.
        routes_pb._in_flight.clear()

    def tearDown(self):
        self._secret_patch.stop()
        from routes import property_brief as routes_pb
        routes_pb._in_flight.clear()

    def _signed(self, payload: bytes) -> dict:
        sig = hmac.new(self.SECRET.encode(), payload, hashlib.sha256).hexdigest()
        return {"X-Signature": sig, "Content-Type": "application/json"}

    def test_unsigned_request_rejected(self):
        resp = self.client.post(
            "/webhooks/clickup/property-brief",
            data=json.dumps({"event": "taskCreated", "task_id": "abc123"}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(resp.status_code, 401)

    def test_signed_creation_dispatches_async_pipeline(self):
        # The whole pipeline (Path A + Path B) runs in a daemon thread so
        # ClickUp gets a 200 in under a second. The handler should
        # return status="dispatched" and not have called the pipeline
        # synchronously yet.
        from routes import property_brief as routes_pb
        body = json.dumps({"event": "taskCreated", "task_id": "abc123"}).encode()
        store.reset_for_tests()
        with mock.patch.object(clickup_client, "get_task", return_value=_task()), \
             mock.patch.object(routes_pb, "_run_pipeline_async") as bg:
            resp = self.client.post(
                "/webhooks/clickup/property-brief", data=body, headers=self._signed(body),
            )
        self.assertEqual(resp.status_code, 200)
        body_json = resp.get_json()
        self.assertEqual(body_json["status"], "dispatched")
        self.assertEqual(body_json["ticket_id"], "abc123")
        bg.assert_called_once()

    def test_pipeline_skipped_when_brief_already_exists(self):
        # Cross-process idempotency: a brief record from a prior webhook
        # delivery means the pipeline already ran. Don't re-dispatch.
        from routes import property_brief as routes_pb
        store.reset_for_tests()
        store.create(
            ticket_id="abc123", company_id="c-7", deal_id="d-42",
            submitter_email="x@y.com", rm_email="rm@y.com",
            brief_markdown="prior brief",
        )
        body = json.dumps({"event": "taskCreated", "task_id": "abc123"}).encode()
        with mock.patch.object(clickup_client, "get_task", return_value=_task()), \
             mock.patch.object(routes_pb, "_run_pipeline_async") as bg:
            resp = self.client.post(
                "/webhooks/clickup/property-brief", data=body, headers=self._signed(body),
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["status"], "skipped")
        self.assertEqual(resp.get_json()["reason"], "already_processed")
        bg.assert_not_called()

    def test_pipeline_skipped_when_in_flight(self):
        # In-process mutex: a retry that arrives while the daemon thread
        # is still running for this ticket should be 200'd without
        # re-dispatching.
        from routes import property_brief as routes_pb
        store.reset_for_tests()
        # Manually claim the ticket as if a daemon thread is mid-pipeline
        routes_pb._in_flight.add("abc123")
        try:
            body = json.dumps({"event": "taskCreated", "task_id": "abc123"}).encode()
            with mock.patch.object(clickup_client, "get_task", return_value=_task()), \
                 mock.patch.object(routes_pb, "_run_pipeline_async") as bg:
                resp = self.client.post(
                    "/webhooks/clickup/property-brief", data=body, headers=self._signed(body),
                )
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.get_json()["reason"], "in_flight")
            bg.assert_not_called()
        finally:
            routes_pb._in_flight.discard("abc123")

    def test_ambiguous_match_handled_inside_daemon(self):
        # The handler dispatches the pipeline async and returns 200
        # immediately. The CompanyMatchAmbiguous error gets caught
        # inside _run_pipeline_async, which posts a ClickUp comment
        # and bails. Run the pipeline synchronously here (skip the
        # threading) so we can assert on the resulting comment.
        from routes import property_brief as routes_pb
        body = json.dumps({"event": "taskCreated", "task_id": "abc123"}).encode()
        with mock.patch.object(clickup_client, "get_task", return_value=_task()), \
             mock.patch.object(property_brief, "run_commercial_path",
                               side_effect=property_brief.CompanyMatchAmbiguous("2 matches")), \
             mock.patch.object(clickup_client, "post_comment", return_value=True) as comment, \
             mock.patch("threading.Thread") as Thread:
            # Make the "Thread" instance run synchronously so we can
            # observe the daemon's side effects.
            Thread.side_effect = lambda target, args=(), **kw: type(
                "_FakeThread", (), {"start": lambda self: target(*args)}
            )()
            resp = self.client.post(
                "/webhooks/clickup/property-brief", data=body, headers=self._signed(body),
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["status"], "dispatched")
        # The daemon called post_comment with the ambiguous-match message.
        comment.assert_called_once()
        self.assertIn("2 matches", comment.call_args.args[1])

    def test_signature_verifies_against_any_secret_in_csv(self):
        # ClickUp generates a unique secret per webhook. We support a
        # comma-separated CLICKUP_WEBHOOK_SECRET so multiple webhooks
        # (one per list) can verify against the same env var.
        from routes import property_brief as routes_pb
        third = "third-list-secret"
        store.reset_for_tests()
        with mock.patch.object(routes_pb, "CLICKUP_WEBHOOK_SECRET",
                               f"first-secret , {self.SECRET} , {third}"):
            body = json.dumps({"event": "taskCreated", "task_id": "abc123"}).encode()
            sig = hmac.new(third.encode(), body, hashlib.sha256).hexdigest()
            with mock.patch.object(clickup_client, "get_task", return_value=_task()), \
                 mock.patch.object(routes_pb, "_run_pipeline_async"):
                resp = self.client.post(
                    "/webhooks/clickup/property-brief", data=body,
                    headers={"X-Signature": sig, "Content-Type": "application/json"},
                )
            self.assertEqual(resp.status_code, 200)

    def test_signature_rejected_when_no_secret_matches(self):
        from routes import property_brief as routes_pb
        with mock.patch.object(routes_pb, "CLICKUP_WEBHOOK_SECRET", "a,b,c"):
            body = json.dumps({"event": "taskCreated", "task_id": "x"}).encode()
            sig = hmac.new(b"unrelated-secret", body, hashlib.sha256).hexdigest()
            resp = self.client.post(
                "/webhooks/clickup/property-brief", data=body,
                headers={"X-Signature": sig, "Content-Type": "application/json"},
            )
            self.assertEqual(resp.status_code, 401)

    def test_missing_field_comments_and_blocks(self):
        # Strip RM Email so parse_ticket raises.
        broken = _task()
        broken["custom_fields"] = [f for f in broken["custom_fields"] if f["name"] != "RM Email"]
        body = json.dumps({"event": "taskCreated", "task_id": "abc123"}).encode()
        with mock.patch.object(clickup_client, "get_task", return_value=broken), \
             mock.patch.object(clickup_client, "post_comment", return_value=True) as comment:
            resp = self.client.post(
                "/webhooks/clickup/property-brief", data=body, headers=self._signed(body),
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["status"], "blocked")
        # Comment text mentions the missing field.
        self.assertIn("rm_email", comment.call_args.args[1].lower())


if __name__ == "__main__":
    unittest.main()
