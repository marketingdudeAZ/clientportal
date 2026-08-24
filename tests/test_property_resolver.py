"""Tests for skills.property_resolver.

The resolver collapses ~11 inlined identity lookups into one. It had no unit
tests — it was verified once by hand against live HubSpot, which proves it
worked that afternoon and nothing about the next change.

These are offline: hubspot_client is stubbed.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

from skills import property_resolver as pr  # noqa: E402


class TestNameResolution:
    """A property name is what a person types. It is not an identifier —
    not unique, not stable, not always spelled the way HubSpot stores it —
    so resolution refuses rather than guessing when more than one survives."""

    def _co(self, name, cid="1"):
        return {"id": cid, "properties": {"name": name, "hs_object_id": cid,
                                          "uuid": cid}}

    def _hubspot(self, monkeypatch, by_query):
        """Stub search_companies, keyed on the filter's operator."""
        import types

        def search_companies(filters, props):
            f = filters[0]
            return by_query.get((f["propertyName"], f["operator"], f["value"]), [])

        monkeypatch.setitem(sys.modules, "hubspot_client", types.SimpleNamespace(
            search_companies=search_companies,
            get_company=lambda *a, **k: {},
        ))

    def test_exact_name_resolves(self, monkeypatch):
        self._hubspot(monkeypatch, {
            ("name", "EQ", "The Atwood at Rivulon"): [self._co("The Atwood at Rivulon", "30912193455")],
        })
        pr.clear_cache()
        assert pr.resolve("The Atwood at Rivulon", kind="name").company_id == "30912193455"

    def test_falls_back_to_a_token_search(self, monkeypatch):
        self._hubspot(monkeypatch, {
            ("name", "EQ", "Atwood at Rivulon"): [],
            ("name", "CONTAINS_TOKEN", "Atwood at Rivulon"): [
                self._co("The Atwood at Rivulon", "30912193455")],
        })
        pr.clear_cache()
        assert pr.resolve("Atwood at Rivulon", kind="name").company_id == "30912193455"

    def test_a_bare_string_is_inferred_as_a_name(self, monkeypatch):
        """Before this, an unrecognised string raised 'cannot infer identifier
        kind', which reads as 'no such property' to whoever typed it."""
        self._hubspot(monkeypatch, {
            ("name", "EQ", "Atwood at Rivulon"): [self._co("Atwood at Rivulon", "7")],
        })
        pr.clear_cache()
        assert pr.resolve("Atwood at Rivulon").company_id == "7"

    def test_several_matches_refuse_rather_than_pick_one(self, monkeypatch):
        self._hubspot(monkeypatch, {
            ("name", "EQ", "Atwood"): [],
            ("name", "CONTAINS_TOKEN", "Atwood"): [
                self._co("The Atwood at Rivulon", "1"), self._co("Atwood Park", "2")],
        })
        pr.clear_cache()
        with pytest.raises(pr.AmbiguousProperty) as exc:
            pr.resolve("Atwood", kind="name")
        assert "Atwood Park" in str(exc.value)
        assert "company_id" in str(exc.value), "must say how to disambiguate"

    def test_one_exact_hit_among_token_matches_is_not_ambiguous(self, monkeypatch):
        """'Atwood Park' matching both itself and 'Atwood Park North' has an
        obvious answer, and refusing there would be pedantic."""
        self._hubspot(monkeypatch, {
            ("name", "EQ", "Atwood Park"): [],
            ("name", "CONTAINS_TOKEN", "Atwood Park"): [
                self._co("Atwood Park North", "1"), self._co("atwood park", "2")],
        })
        pr.clear_cache()
        assert pr.resolve("Atwood Park", kind="name").company_id == "2"

    def test_no_match_is_not_found(self, monkeypatch):
        self._hubspot(monkeypatch, {})
        pr.clear_cache()
        with pytest.raises(pr.PropertyNotFound):
            pr.resolve("Nowhere Apartments", kind="name")
