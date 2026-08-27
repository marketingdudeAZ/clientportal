"""Tests for the stored-filename convention.

A filename is the one piece of metadata that survives everywhere — the bucket,
Fluency, an ad platform's asset library, someone's download folder. Every
property has dozens of `landscape_1200x628.jpg` and they are
indistinguishable, so what the analyzer saw in the picture is folded in.

The two rules that must hold no matter what:

* the variant token survives verbatim — `asset_index` and the ad platforms
  identify a rendition by it, so truncation eats the description instead
* a missing or unusable description degrades to the old name rather than
  failing an upload, because a missing label is cosmetic and a lost file is not
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

import asset_naming as naming  # noqa: E402


class TestSlugify:
    @pytest.mark.parametrize("raw,expected", [
        ("Resort-style pool at dusk", "resort-style-pool-dusk"),
        ("Two-bedroom kitchen", "two-bedroom-kitchen"),
        ("  Rooftop   Lounge  ", "rooftop-lounge"),
    ])
    def test_plain_text_becomes_a_slug(self, raw, expected):
        assert naming.slugify(raw) == expected

    def test_accents_are_folded_not_dropped(self):
        """"Café Patio" losing a whole word would be worse than losing an accent."""
        assert naming.slugify("Café Patio") == "cafe-patio"

    def test_punctuation_and_emoji_do_not_survive(self):
        out = naming.slugify("Pool! 🏊 (renovated) — 2026")
        assert out and all(c.isalnum() or c == "-" for c in out)
        assert not out.startswith("-") and not out.endswith("-")

    def test_filler_words_are_dropped(self):
        assert naming.slugify("A photo of the pool with lounge chairs") == \
            "pool-lounge-chairs"

    def test_an_all_filler_description_keeps_its_words(self):
        """If stripping filler leaves nothing, the filler WAS the description."""
        assert naming.slugify("the view") == "the-view"

    def test_truncation_lands_on_a_word_boundary(self):
        out = naming.slugify("resort style swimming pool with cabanas and grills", 20)
        assert len(out) <= 20
        assert not out.endswith("-")
        assert "-".join(out.split("-")[:-1]) in out  # no half word

    def test_a_single_oversized_word_is_still_cut(self):
        assert naming.slugify("a" * 80, 10) == "a" * 10

    def test_empty_input_is_empty_output(self):
        assert naming.slugify("") == ""
        assert naming.slugify(None) == ""


class TestBuildFilename:
    def test_the_description_leads_and_the_variant_follows(self):
        out = naming.build_filename("landscape_1200x628.jpg",
                                    description="Resort pool at dusk")
        assert out == "resort-pool-dusk_landscape_1200x628.jpg"

    def test_the_subcategory_groups_related_assets(self):
        out = naming.build_filename("square_1200x1200.jpg",
                                    description="Resort pool at dusk",
                                    subcategory="Amenity")
        assert out.startswith("amenity-")
        assert out.endswith("_square_1200x1200.jpg")

    @pytest.mark.parametrize("variant", [
        "landscape_1200x628.jpg", "square_1200x1200.jpg",
        "portrait_1080x1350.jpg", "display_300x250.jpg",
        "display_728x90.jpg", "display_160x600.jpg",
    ])
    def test_every_variant_token_survives_verbatim(self, variant):
        """The pipeline identifies a rendition by this token. Mangling it
        breaks Fluency and the index."""
        stem = variant.rsplit(".", 1)[0]
        out = naming.build_filename(
            variant, description="An extremely long description of a resort "
                                 "style swimming pool with cabanas and grills",
            subcategory="Amenity")
        assert out.endswith(f"_{stem}.jpg")

    def test_a_long_description_is_truncated_not_the_variant(self):
        out = naming.build_filename(
            "landscape_1200x628.jpg",
            description="resort style swimming pool with cabanas grills and a "
                        "sun deck overlooking the courtyard fountain")
        assert out.endswith("_landscape_1200x628.jpg")
        assert len(out) <= naming.MAX_STEM + len(".jpg")

    def test_no_description_falls_back_to_the_bare_variant(self):
        assert naming.build_filename("landscape_1200x628.jpg") == \
            "landscape_1200x628.jpg"

    def test_an_unusable_description_falls_back_rather_than_producing_junk(self):
        """Emoji-only, punctuation-only — nothing sluggable. The upload must
        still succeed with the old name."""
        for junk in ("🏊🏊🏊", "!!!", "   ", "—"):
            assert naming.build_filename("square_1200x1200.jpg",
                                         description=junk) == \
                "square_1200x1200.jpg"

    def test_a_subcategory_alone_still_helps(self):
        out = naming.build_filename("square_1200x1200.jpg", subcategory="Aerial")
        assert out == "aerial_square_1200x1200.jpg"

    def test_a_description_matching_the_subcategory_is_not_repeated(self):
        out = naming.build_filename("square_1200x1200.jpg",
                                    description="Amenity", subcategory="Amenity")
        assert out == "amenity_square_1200x1200.jpg"

    def test_it_is_deterministic(self):
        """A retry must not litter the prefix with near-duplicate names."""
        args = dict(description="Resort pool at dusk", subcategory="Amenity")
        first = naming.build_filename("landscape_1200x628.jpg", **args)
        second = naming.build_filename("landscape_1200x628.jpg", **args)
        assert first == second

    def test_a_video_extension_is_preserved(self):
        out = naming.build_filename("original.mp4", description="Property tour")
        assert out.endswith(".mp4")

    def test_a_variant_with_no_extension_still_works(self):
        out = naming.build_filename("original", description="Property tour")
        assert "." not in out and out.endswith("_original")

    def test_the_result_is_safe_as_an_object_name(self):
        out = naming.build_filename(
            "landscape_1200x628.jpg",
            description="Café/Patio: 50% off! <script>", subcategory="Amenity")
        stem = out.rsplit(".", 1)[0]
        assert all(c.isalnum() or c in "-_" for c in stem), out
        assert "/" not in out


class TestDescribe:
    def test_it_reads_an_analyzer_result(self):
        desc, sub = naming.describe({
            "category": "Photography", "subcategory": "Amenity",
            "description": "Resort pool at dusk"})
        assert desc == "Resort pool at dusk" and sub == "Amenity"

    @pytest.mark.parametrize("bad", [None, {}, [], "text", {"description": None}])
    def test_a_partial_or_absent_result_never_raises(self, bad):
        """The analyzer is a model call and may return anything."""
        assert naming.describe(bad) == ("", "") or isinstance(naming.describe(bad), tuple)


class TestSubcategoryRepetition:
    """The analyzer usually repeats its own subcategory inside the description,
    which produced names like aerial-aerial-community-lake."""

    def test_a_leading_repeat_is_dropped(self):
        out = naming.build_filename("landscape_1200x628.jpg",
                                    description="Aerial of the community and lake",
                                    subcategory="Aerial")
        assert out == "aerial-community-lake_landscape_1200x628.jpg"

    def test_a_repeat_in_the_middle_is_dropped_too(self):
        out = naming.build_filename("landscape_1200x628.jpg",
                                    description="Building exterior at golden hour",
                                    subcategory="Exterior")
        assert out == "exterior-building-golden-hour_landscape_1200x628.jpg"

    def test_a_description_that_is_only_the_subcategory_still_names_the_file(self):
        out = naming.build_filename("landscape_1200x628.jpg",
                                    description="Amenity", subcategory="Amenity")
        assert out == "amenity_landscape_1200x628.jpg"

    def test_an_unrelated_description_is_untouched(self):
        out = naming.build_filename("landscape_1200x628.jpg",
                                    description="Quartz kitchen island",
                                    subcategory="Interior")
        assert out == "interior-quartz-kitchen-island_landscape_1200x628.jpg"
