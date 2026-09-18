"""Tests for the Google Ads IS-lost connector (google_ads_islost.py).

The structure is tested with a mocked API response — CID parsing, SEARCH
aggregation, and the company→CID→query→map path. The live _run_gaql is a
credential-gated seam (raises until configured), so the test mocks it.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("HUBSPOT_API_KEY", "test-key")

import pytest  # noqa: E402

import hubspot_client  # noqa: E402
import google_ads_islost as ga  # noqa: E402


# ── CID parsing ──────────────────────────────────────────────────────────────


def test_extract_property_cid_strips_dashes_and_mcc():
    assert ga.extract_property_cid("486-980-3719|123-456-7890") == "4869803719"


def test_extract_property_cid_empty():
    assert ga.extract_property_cid("") == ""


# ── parse/aggregate ──────────────────────────────────────────────────────────


def test_parse_islost_averages_search_campaigns():
    rows = [
        {"channel_type": "SEARCH", "budget_lost_is": 0.28},
        {"channel_type": "SEARCH", "budget_lost_is": 0.32},
        {"channel_type": "DISPLAY", "budget_lost_is": 0.9},  # ignored (not search)
    ]
    assert ga.parse_islost(rows) == {"paid_search": 0.30}


def test_parse_islost_ignores_nulls_and_empty():
    assert ga.parse_islost([{"channel_type": "SEARCH", "budget_lost_is": None}]) == {}
    assert ga.parse_islost([]) == {}


# ── full path ────────────────────────────────────────────────────────────────


def test_fetch_maps_company_cid_to_islost(monkeypatch):
    monkeypatch.setattr(hubspot_client, "get_company",
                        lambda cid, props: {"google_ads_customer_id": "4869803719|999"})
    monkeypatch.setattr(ga, "_run_gaql",
                        lambda cid, q: [{"channel_type": "SEARCH", "budget_lost_is": 0.25}])
    assert ga.fetch_islost_by_channel("c-1") == {"paid_search": 0.25}


def test_fetch_returns_empty_when_no_cid(monkeypatch):
    monkeypatch.setattr(hubspot_client, "get_company", lambda cid, props: {})
    # _run_gaql must NOT be called when there's no CID.
    monkeypatch.setattr(ga, "_run_gaql",
                        lambda *a: (_ for _ in ()).throw(AssertionError("should not run")))
    assert ga.fetch_islost_by_channel("c-1") == {}


def test_run_gaql_seam_raises_until_configured():
    with pytest.raises(ga.GoogleAdsNotConfigured):
        ga._run_gaql("123", ga._GAQL_BUDGET_LOST_IS)


# ── the live connector ───────────────────────────────────────────────────────
# The seam is now implemented. What matters is that a caller can always turn a
# failure into an honest gap: never a silent empty result, never a raw library
# traceback reaching a person.

class TestCredentials:
    """This connector reads its OWN credentials so a pre-existing GOOGLE_ADS_*
    set belonging to another integration is never repointed. Each credential
    answers to `_2`, `2` and the unsuffixed name, in that order."""

    CANONICAL = ("GOOGLE_ADS_DEVELOPER_TOKEN_2", "GOOGLE_ADS_CLIENT_ID_2",
                 "GOOGLE_ADS_CLIENT_SECRET_2", "GOOGLE_ADS_REFRESH_TOKEN_2",
                 "GOOGLE_ADS_LOGIN_CUSTOMER_ID_2")
    BASES = ("GOOGLE_ADS_DEVELOPER_TOKEN", "GOOGLE_ADS_CLIENT_ID",
             "GOOGLE_ADS_CLIENT_SECRET", "GOOGLE_ADS_REFRESH_TOKEN",
             "GOOGLE_ADS_LOGIN_CUSTOMER_ID")

    def _clear(self, monkeypatch):
        for base in self.BASES:
            for name in (base + "_2", base + "2", base):
                monkeypatch.delenv(name, raising=False)

    def _set_all(self, monkeypatch, suffix="_2"):
        self._clear(monkeypatch)
        for base in self.BASES:
            monkeypatch.setenv(base + suffix, "value-for-" + base)

    def test_nothing_configured_names_every_missing_value(self, monkeypatch):
        self._clear(monkeypatch)
        assert sorted(ga.missing_credentials()) == sorted(self.CANONICAL)
        assert ga.is_configured() is False

    def test_one_missing_value_is_named_on_its_own(self, monkeypatch):
        self._set_all(monkeypatch)
        monkeypatch.delenv("GOOGLE_ADS_REFRESH_TOKEN_2")
        assert ga.missing_credentials() == ["GOOGLE_ADS_REFRESH_TOKEN_2"]

    @pytest.mark.parametrize("suffix", ["_2", "2"])
    def test_both_suffix_spellings_are_accepted(self, monkeypatch, suffix):
        """A credential that silently does not apply because of an underscore is
        a bad half-hour, so both forms work."""
        self._set_all(monkeypatch, suffix=suffix)
        assert ga.missing_credentials() == []
        assert ga.credential_value("GOOGLE_ADS_DEVELOPER_TOKEN_2") == \
            "value-for-GOOGLE_ADS_DEVELOPER_TOKEN"

    def test_the_underscore_form_wins_when_both_are_set(self, monkeypatch):
        self._clear(monkeypatch)
        monkeypatch.setenv("GOOGLE_ADS_DEVELOPER_TOKEN2", "no-underscore")
        monkeypatch.setenv("GOOGLE_ADS_DEVELOPER_TOKEN_2", "underscore")
        assert ga.credential_value("GOOGLE_ADS_DEVELOPER_TOKEN_2") == "underscore"

    def test_the_error_tells_you_what_to_do(self, monkeypatch):
        self._clear(monkeypatch)
        with pytest.raises(ga.GoogleAdsNotConfigured) as err:
            ga._client()
        assert "google_ads_auth.py" in str(err.value)
        assert "GOOGLE_ADS_REFRESH_TOKEN_2" in str(err.value)

    def test_messages_never_ask_for_the_unsuffixed_name(self, monkeypatch):
        """That credential belongs to another integration."""
        self._clear(monkeypatch)
        message = str(ga.missing_credentials())
        for base in self.BASES:
            assert ("'%s'" % base) not in message

    def test_the_unsuffixed_names_still_satisfy_the_connector(self, monkeypatch):
        """Anything already running on the original set keeps working."""
        self._clear(monkeypatch)
        for base in self.BASES:
            monkeypatch.setenv(base, "legacy-" + base)
        assert ga.missing_credentials() == []
        assert ga.credential_sources()["GOOGLE_ADS_CLIENT_ID_2"] == "GOOGLE_ADS_CLIENT_ID"

    def test_sources_name_the_variable_that_actually_supplied_each_value(self, monkeypatch):
        """Sharing a credential with another integration is worth seeing."""
        self._clear(monkeypatch)
        monkeypatch.setenv("GOOGLE_ADS_DEVELOPER_TOKEN_2", "ours")
        monkeypatch.setenv("GOOGLE_ADS_CLIENT_ID", "theirs")
        sources = ga.credential_sources()
        assert sources["GOOGLE_ADS_DEVELOPER_TOKEN_2"] == "GOOGLE_ADS_DEVELOPER_TOKEN_2"
        assert sources["GOOGLE_ADS_CLIENT_ID_2"] == "GOOGLE_ADS_CLIENT_ID"
        assert sources["GOOGLE_ADS_CLIENT_SECRET_2"] is None

    def test_whitespace_only_counts_as_missing(self, monkeypatch):
        self._set_all(monkeypatch)
        monkeypatch.setenv("GOOGLE_ADS_DEVELOPER_TOKEN_2", "   ")
        assert ga.missing_credentials() == ["GOOGLE_ADS_DEVELOPER_TOKEN_2"]


class TestQueryReading:
    def test_the_select_clause_describes_what_to_read(self):
        fields = ga.selected_fields(
            "SELECT campaign.name, metrics.cost_micros, metrics.clicks "
            "FROM campaign WHERE segments.date DURING LAST_30_DAYS")
        assert fields == ["campaign.name", "metrics.cost_micros", "metrics.clicks"]

    def test_it_survives_odd_spacing_and_case(self):
        assert ga.selected_fields("select  a.b ,\n c.d  from x") == ["a.b", "c.d"]

    def test_enums_come_back_as_their_names(self):
        class _Enum:
            name = "SEARCH"

        class _Campaign:
            advertising_channel_type = _Enum()
            name = "Brand"

        class _Row:
            campaign = _Campaign()

        assert ga._read_path(_Row(), "campaign.advertising_channel_type") == "SEARCH"
        assert ga._read_path(_Row(), "campaign.name") == "Brand"

    def test_a_missing_field_reads_as_none_not_an_error(self):
        class _Row:
            pass

        assert ga._read_path(_Row(), "metrics.cost_micros") is None


class TestRunGaql:
    QUERY = ("SELECT campaign.advertising_channel_type, "
             "metrics.search_budget_lost_impression_share FROM campaign")

    def _client_returning(self, monkeypatch, rows):
        class _Service:
            def search_stream(self, customer_id, query):
                self.customer_id = customer_id
                return [type("Batch", (), {"results": rows})()]

        service = _Service()
        monkeypatch.setattr(ga, "_client", lambda: type(
            "Client", (), {"get_service": lambda self, name: service})())
        return service

    def test_rows_are_keyed_by_the_field_paths_the_caller_asked_for(self, monkeypatch):
        class _Row:
            class campaign:
                class advertising_channel_type:
                    name = "SEARCH"

            class metrics:
                search_budget_lost_impression_share = 0.31

        self._client_returning(monkeypatch, [_Row()])
        rows = ga._run_gaql("486-980-3719", self.QUERY)
        assert rows == [{"campaign.advertising_channel_type": "SEARCH",
                         "metrics.search_budget_lost_impression_share": 0.31}]

    def test_the_customer_id_is_sent_as_digits(self, monkeypatch):
        service = self._client_returning(monkeypatch, [])
        ga._run_gaql("486-980-3719", self.QUERY)
        assert service.customer_id == "4869803719"

    def test_an_empty_customer_id_never_calls_the_api(self, monkeypatch):
        def _boom():
            raise AssertionError("the client must not be built for an empty id")

        monkeypatch.setattr(ga, "_client", _boom)
        assert ga._run_gaql("", self.QUERY) == []
        assert ga._run_gaql("n/a", self.QUERY) == []


class TestFailuresAreLegible:
    """Each of these maps to something a person can act on, not a traceback."""

    @pytest.mark.parametrize("text,expected,fragment", [
        ("invalid_grant: Token has been expired or revoked",
         "GoogleAdsNotConfigured", "mint a new one"),
        ("DeveloperTokenError.DEVELOPER_TOKEN_NOT_APPROVED",
         "GoogleAdsNotConfigured", "Basic access"),
        ("AuthorizationError.USER_PERMISSION_DENIED",
         "GoogleAdsError", "does not have access"),
        ("CUSTOMER_NOT_ENABLED", "GoogleAdsError", "not reachable"),
        ("RESOURCE_EXHAUSTED: quota", "GoogleAdsError", "rate-limiting"),
        ("something else entirely", "GoogleAdsError", "request failed"),
    ])
    def test_translation(self, text, expected, fragment):
        out = ga._translate(RuntimeError(text), "4869803719")
        assert type(out).__name__ == expected
        assert fragment in str(out)

    def test_a_failure_mid_stream_is_translated_not_leaked(self, monkeypatch):
        class _Service:
            def search_stream(self, customer_id, query):
                raise RuntimeError("AuthorizationError.USER_PERMISSION_DENIED")

        monkeypatch.setattr(ga, "_client", lambda: type(
            "Client", (), {"get_service": lambda self, name: _Service()})())
        with pytest.raises(ga.GoogleAdsError) as err:
            ga._run_gaql("4869803719", "SELECT campaign.name FROM campaign")
        assert "4869803719" in str(err.value)
        assert "Traceback" not in str(err.value)
