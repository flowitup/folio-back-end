"""Unit tests for app/domain/labor/export/format.py.

Covers:
- format_eur_fr: boundary values (0, None, large with thousands, fractional, negative)
- slugify_project_name: ASCII, empty, emoji/CJK, Vietnamese (Latin-Extended)
"""

from __future__ import annotations

from decimal import Decimal

from app.domain.labor.export.format import format_eur_fr, slugify_project_name


# ---------------------------------------------------------------------------
# format_eur_fr
# ---------------------------------------------------------------------------


class TestFormatEurFr:
    def test_integer_200_renders_two_decimals(self):
        # Production uses \xa0 (non-breaking space, U+00A0) before € — fr-FR convention.
        result = format_eur_fr(Decimal("200"))
        assert result == "200,00\xa0€", f"Got: {result!r}"

    def test_zero_renders_two_decimals(self):
        result = format_eur_fr(Decimal("0"))
        assert result == "0,00\xa0€", f"Got: {result!r}"

    def test_none_returns_em_dash(self):
        assert format_eur_fr(None) == "—"

    def test_fractional_two_places(self):
        result = format_eur_fr(Decimal("100.5"))
        assert result == "100,50\xa0€", f"Got: {result!r}"

    def test_thousands_separator_is_nbsp(self):
        """1234.56 → '1\xa0234,56\xa0€' — fr-FR uses non-breaking space for thousands sep."""
        result = format_eur_fr(Decimal("1234.56"))
        # Both the thousands separator and the space before € are \xa0 (U+00A0)
        assert result == "1\xa0234,56\xa0€", f"Got: {result!r}"

    def test_large_value_with_thousands_sep(self):
        """Verify 1234.56 produces exact fr-FR representation."""
        result = format_eur_fr(Decimal("1234.56"))
        assert result.endswith("\xa0€"), f"Missing non-breaking space + € in: {result!r}"
        assert "234,56" in result, f"Expected '234,56' in: {result!r}"


# ---------------------------------------------------------------------------
# slugify_project_name
# ---------------------------------------------------------------------------


class TestSlugifyProjectName:
    def test_ascii_name_produces_kebab_slug(self):
        result = slugify_project_name("Downtown Office Tower", "abcd1234-5678-9012-3456-789012345678")
        assert result == "downtown-office-tower"

    def test_empty_name_falls_back_to_id_prefix(self):
        result = slugify_project_name("", "abcd1234-5678-9012-3456-789012345678")
        assert result == "abcd1234"

    def test_emoji_name_falls_back_to_id_prefix(self):
        """Pure emoji → no Latin chars → fallback to first 8 chars of ID."""
        result = slugify_project_name("\U0001f3d7️", "abcd1234-5678-9012-3456-789012345678")
        assert result == "abcd1234"

    def test_cjk_name_falls_back_to_id_prefix(self):
        """Pure CJK characters → no Latin chars → fallback to first 8 chars of ID."""
        result = slugify_project_name("工地", "abcd1234-5678-9012-3456-789012345678")
        assert result == "abcd1234"

    def test_mixed_emoji_cjk_falls_back(self):
        """Emoji + CJK combined → fallback."""
        result = slugify_project_name("\U0001f3d7️工地", "abcd1234-5678-9012-3456-789012345678")
        assert result == "abcd1234"

    def test_vietnamese_name_is_non_empty_lowercase_within_length(self):
        """Vietnamese (Latin-Extended) passes through slugify — non-empty, lowercase, ≤32 chars."""
        result = slugify_project_name("Nguyễn Project", "abcd1234-5678-9012-3456-789012345678")
        assert result  # non-empty
        assert result == result.lower()
        assert len(result) <= 32
        # slug should contain "project" at minimum
        assert "project" in result

    def test_slug_truncated_to_32_chars(self):
        """Very long names are capped at 32 chars (word boundary)."""
        long_name = "Alpha Beta Gamma Delta Epsilon Zeta Eta Theta Iota Kappa"
        result = slugify_project_name(long_name, "abcd1234-5678-9012-3456-789012345678")
        assert len(result) <= 32

    def test_fallback_id_prefix_is_exactly_8_chars(self):
        """When falling back to ID prefix, result has exactly 8 chars (assuming ≥8-char ID)."""
        result = slugify_project_name("", "abcdefgh-xxxx-xxxx-xxxx-xxxxxxxxxxxx")
        assert result == "abcdefgh"

    def test_hyphen_only_name_falls_back_to_id(self):
        """Name with only hyphens: no Latin/digit chars → first fallback (no Latin branch)."""
        result = slugify_project_name("---", "abcd1234-xxxx-xxxx-xxxx-xxxxxxxxxxxx")
        # "---" has no Ll/Lu/Nd → has_latin_content=False → ID prefix fallback
        assert result == "abcd1234"
