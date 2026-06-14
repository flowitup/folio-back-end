"""Unit tests for app.domain.value_objects.supplier_slug.slugify."""

from __future__ import annotations

from app.domain.value_objects.supplier_slug import slugify


class TestSlugify:
    def test_simple_two_word_name(self):
        assert slugify("Leroy Merlin") == "leroy-merlin"

    def test_accent_stripped(self):
        assert slugify("Castorama Éco") == "castorama-eco"

    def test_multiple_accents(self):
        assert slugify("Briçon & Côté") == "bricon-cote"

    def test_ampersand_collapses_to_single_dash(self):
        assert slugify("M&S Supplies") == "m-s-supplies"

    def test_punctuation_collapsed(self):
        assert slugify("A---B!!!C") == "a-b-c"

    def test_trailing_punctuation_stripped(self):
        assert slugify("Supplier!") == "supplier"

    def test_leading_punctuation_stripped(self):
        assert slugify("!Supplier") == "supplier"

    def test_empty_string_returns_fallback(self):
        assert slugify("") == "supplier"

    def test_whitespace_only_returns_fallback(self):
        assert slugify("   ") == "supplier"

    def test_all_non_alnum_returns_fallback(self):
        assert slugify("!!! ###") == "supplier"

    def test_length_cap_100(self):
        long_name = "A" * 200
        result = slugify(long_name)
        assert len(result) <= 100

    def test_truncation_no_trailing_dash(self):
        # Construct a name that when slugified ends up longer than 100 chars with a
        # word boundary exactly at position 100 after a dash.
        name = "a" * 99 + " b" * 10
        result = slugify(name)
        assert len(result) <= 100
        assert not result.endswith("-")

    def test_lowercase(self):
        assert slugify("HELLO WORLD") == "hello-world"

    def test_numbers_preserved(self):
        assert slugify("Supplier 2000") == "supplier-2000"

    def test_underscore_becomes_dash(self):
        assert slugify("my_supplier") == "my-supplier"

    def test_already_slug_like(self):
        assert slugify("leroy-merlin") == "leroy-merlin"

    def test_single_word(self):
        assert slugify("Brico") == "brico"

    def test_unicode_cjk_stripped(self):
        # CJK characters are non-ASCII; NFKD+ASCII encode drops them.
        # Result must be fallback since nothing survives.
        result = slugify("店舗")
        assert result == "supplier"
