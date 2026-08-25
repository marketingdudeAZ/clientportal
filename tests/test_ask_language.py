"""Nothing internal reaches a page a leasing director reads.

The audience knows what a tour is, what Google Ads is, and what their occupancy
is. They have never heard of `hyly_property_id` or `ninjacat_metrics`, and the
version of this surface that shipped first told them to "add the google-ads
library + GOOGLE_ADS_DEVELOPER_TOKEN / OAuth" — at which point it had stopped
being a product and become a stack trace.

The sweep test at the bottom is the one that matters: it walks a real answer
payload and fails on any forbidden token, so the next feature that leaks a
table name breaks the build rather than a client conversation.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

from skills import ask_language as lang  # noqa: E402


class TestLabels:
    @pytest.mark.parametrize("key,expected", [
        ("tour_sources", "Tours by source"),
        ("impression_share_lost", "Paid search visibility"),
        ("performance_trend", "Traffic and leads"),
        ("reputation", "Reviews and ratings"),
        ("market_position", "Competitor pricing"),
    ])
    def test_internal_keys_become_plain_names(self, key, expected):
        assert lang.label(key) == expected

    def test_an_unmapped_key_still_reads_as_words(self):
        """A key we forgot should look plain, not broken."""
        assert lang.label("some_new_thing") == "Some new thing"
        assert "_" not in lang.label("some_new_thing")

    def test_an_empty_key_does_not_render_blank(self):
        assert lang.label("") == "This data"


class TestSources:
    def test_the_warehouse_table_becomes_something_checkable(self):
        """They can open Google Analytics. They cannot open a BigQuery table,
        and naming it buys nothing except distance."""
        assert lang.source("BigQuery ninjacat_metrics") == "Google Analytics + Google Ads"

    def test_vendor_names_they_do_not_know_are_replaced(self):
        assert "Hyly" not in lang.source("BigQuery hyly_daily_activity_v1 (Hyly)")
        assert lang.source("SOCi") == "Reviews"

    def test_an_unmapped_source_is_stripped_of_warehouse_words(self):
        out = lang.source("BigQuery some_new_table_v2")
        assert "BigQuery" not in out and "_" not in out


class TestReasons:
    def test_the_config_instruction_never_reaches_the_reader(self):
        """The worst offender: a leasing director told to add an OAuth token."""
        internal = ("Google Ads is not configured for this property (Google Ads "
                    "API not configured: add the `google-ads` library + "
                    "GOOGLE_ADS_DEVELOPER_TOKEN / OAuth / login-customer-id, "
                    "then implement _run_gaql.)")
        out = lang.reason(internal)
        for token in ("OAuth", "google-ads", "DEVELOPER_TOKEN", "_run_gaql"):
            assert token.lower() not in out.lower()
        assert "Google Ads account isn't connected" in out

    def test_the_tour_gap_says_what_is_missing_not_which_field(self):
        internal = ("This property has no hyly_property_id on its HubSpot "
                    "company record. Hyly is a 15-property beta, so tour-stage "
                    "attribution is not available here.")
        out = lang.reason(internal)
        assert "hyly" not in out.lower()
        assert "HubSpot company record" not in out
        assert "tours" in out.lower()

    def test_the_reviews_gap_does_not_name_a_vendor(self):
        out = lang.reason("SOCi has no connector on this platform yet.")
        assert "soci" not in out.lower()
        assert "review" in out.lower()

    def test_our_outage_is_owned_rather_than_blamed_on_the_property(self):
        out = lang.reason("BigQuery is not configured on this environment "
                          "(BIGQUERY_PROJECT_ID unset).")
        assert "bigquery" not in out.lower()
        assert "on us" in out.lower() or "our " in out.lower()

    def test_an_unanticipated_reason_is_passed_through_unchanged(self):
        """Inventing a friendly sentence for a failure we did not anticipate
        would be worse than showing the real one. The sweep test is what stops
        an unanticipated one from carrying jargon."""
        assert lang.reason("Something specific and new happened.") == \
            "Something specific and new happened."

    def test_empty_stays_empty(self):
        assert lang.reason("") == ""


class TestDescribeGap:
    def test_a_gap_carries_a_label_a_source_and_a_reason(self):
        out = lang.describe_gap(
            "tour_sources", "BigQuery hyly_daily_activity_v1 (Hyly)",
            "This property has no hyly_property_id on its HubSpot company record.")
        assert out["label"] == "Tours by source"
        assert "Hyly" not in out["source"]
        assert "hyly" not in out["reason"].lower()
        assert out["input"] == "tour_sources", "the key stays, for the UI to match on"


class TestTheSweep:
    """The test that actually protects the client conversation."""

    def _walk(self, node, path="payload"):
        """Yield (path, text) for every string in a nested payload."""
        if isinstance(node, str):
            yield path, node
        elif isinstance(node, dict):
            for k, v in node.items():
                # Keys are matched on by the UI and are not rendered.
                if k in ("input", "key", "pull", "name"):
                    continue
                yield from self._walk(v, f"{path}.{k}")
        elif isinstance(node, (list, tuple)):
            for i, v in enumerate(node):
                yield from self._walk(v, f"{path}[{i}]")

    def test_a_real_answer_payload_carries_no_internal_vocabulary(self):
        """Walks the captured production response for The Atwood at Rivulon.

        If this fails, something in the pipeline started printing a table name,
        a HubSpot field, a vendor nobody outside this team knows, or a
        configuration instruction — onto a page a client's leasing director
        reads.
        """
        fixture = os.path.join(os.path.dirname(__file__), "fixtures",
                               "ask_live_answer.json")
        if not os.path.exists(fixture):
            pytest.skip("no captured answer fixture")
        payload = json.load(open(fixture))

        leaks = []
        for path, text in self._walk(payload):
            for bad in lang.leaks(text):
                leaks.append(f"{path}: {bad!r} in {text[:90]!r}")
        assert not leaks, (
            "internal vocabulary reached a reader-facing string:\n  "
            + "\n  ".join(leaks[:12]))

    def test_the_forbidden_list_covers_what_actually_leaked(self):
        """Regression guard on the guard: these are the specific tokens seen on
        the page in the first build."""
        for token in ("bigquery", "ninjacat", "hyly", "soci",
                      "property_id", "oauth", "developer_token"):
            assert token in lang.FORBIDDEN_ON_PAGE

    def test_a_channel_name_is_not_mistaken_for_a_vendor(self):
        """"soci" is forbidden as a vendor but is a substring of "paid_social".
        A check that flagged the channel would train everyone to ignore it."""
        assert lang.leaks("paid_social: $500 of $10,700 monthly spend") == []
        assert "soci" in lang.leaks("SOCi has no connector")
