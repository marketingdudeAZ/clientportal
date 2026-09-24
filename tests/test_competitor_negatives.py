"""Tests for competitor_negatives.py — pure, no I/O.

Pins the two things that matter: the list names competitors (so wrong-community
queries stop matching), and it never emits a geographic or own-brand negative
(Fair Housing, and it would cut the property's own traffic).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

import competitor_negatives as cn  # noqa: E402

SELF = {"Property ID": "1", "Property": "Atwood at Rivulon", "City": "Gilbert"}
COHORT = [
    SELF,
    {"Property ID": "2", "Property": "Avana Gilbert Apartments", "City": "Gilbert"},
    {"Property ID": "3", "Property": "Vive Chandler", "City": "Chandler"},
    {"Property ID": "4", "Property": "The Gilbert Apartments", "City": "Gilbert"},
    {"Property ID": "5", "Property": "Rivulon Townhomes", "City": "Gilbert"},
    {"Property ID": "6", "Property": "The Rio", "City": "Mesa"},
    {"Property ID": "7", "Property": "Spring Meadow", "City": "Mesa"},
]


def build(**kw):
    return cn.build_negatives(SELF, COHORT, **kw)


def test_names_competitors_full_and_short_forms():
    kws = build()["keywords"]
    assert "avana gilbert apartments" in kws
    assert "avana gilbert" in kws
    assert "vive chandler" in kws
    assert "spring meadow" in kws


def test_excludes_self():
    kws = build()["keywords"]
    assert not any("atwood" in k for k in kws)
    assert build()["competitors"] == 6


def test_never_emits_bare_geography():
    kws = build()["keywords"]
    # "The Gilbert Apartments" reduces to the city name: a geographic negative.
    assert "gilbert" not in kws
    assert "the gilbert apartments" not in kws
    for place in ("gilbert", "chandler", "mesa", "az", "arizona"):
        assert place not in kws


def test_never_blocks_own_brand():
    # "Rivulon Townhomes" shares the property's own distinctive token.
    kws = build()["keywords"]
    assert "rivulon townhomes" not in kws
    assert "rivulon" not in kws


def test_short_names_go_to_manual_review_not_the_list():
    out = build()
    assert "rio" not in out["keywords"] and "the rio" not in out["keywords"]
    assert any(p == "the rio" for p, _ in out["skipped"])


def test_zip_is_refused():
    assert not cn.is_safe_negative("avana 85296", self_name="Atwood", place_words=frozenset())


def test_normalize():
    assert cn.normalize("  Harmon @ Ascent & Co's ") == "harmon ascent and cos"


def test_name_variants():
    assert cn.name_variants("The Avana Apartments") == ["the avana apartments", "avana"]
    assert cn.name_variants("Spring Meadow") == ["spring meadow"]
    assert cn.name_variants("") == []


def test_search_terms_rank_first_and_survive_cap():
    seen = cn.names_seen_in_search_terms(
        ["spring meadow apartments phone", "Spring Meadow mesa", "vive chandler"],
        [r["Property"] for r in COHORT])
    assert seen == ["Spring Meadow", "Vive Chandler"]
    out = build(seen_in_search_terms=seen, limit=2)
    assert out["keywords"] == ["spring meadow", "vive chandler"]
    assert out["truncated"] is True


def test_editor_rows():
    rows = cn.editor_csv_rows("Atwood - Competitor Negatives", ["vive chandler"])
    assert rows == [{"Shared set name": "Atwood - Competitor Negatives",
                     "Keyword": "vive chandler", "Criterion Type": "Negative Phrase"}]
