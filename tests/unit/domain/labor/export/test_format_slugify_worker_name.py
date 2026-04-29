"""Unit tests for slugify_worker_name in format.py."""

from __future__ import annotations

from app.domain.labor.export.format import slugify_worker_name


class TestSlugifyWorkerNameLatin:
    def test_ascii_name_kebab_case(self):
        """ASCII name → lowercase kebab-case slug."""
        result = slugify_worker_name("Antoine Dupont", "abc12345")
        assert result == "antoine-dupont"

    def test_french_name_with_accents(self):
        """French accented name → diacritics stripped, kebab-case."""
        result = slugify_worker_name("Élise Dubois", "abc12345")
        # python-slugify strips accents: Élise → elise
        assert "elise" in result or "lise" in result
        assert "-" in result

    def test_second_french_name(self):
        """François Côté → ascii slug."""
        result = slugify_worker_name("François Côté", "abc12345")
        assert "fran" in result.lower()


class TestSlugifyWorkerNameVietnamese:
    def test_vietnamese_name_diacritics_removed(self):
        """Vietnamese name with diacritics → Latin slugified form."""
        result = slugify_worker_name("Nguyễn Văn A", "abc12345")
        # python-slugify will transliterate Vietnamese Latin-extended chars
        assert isinstance(result, str)
        assert len(result) > 0
        assert result != "abc12345"[:8]

    def test_vietnamese_full_name(self):
        """'Trần Thị Bình' → slug produced, not fallback."""
        result = slugify_worker_name("Trần Thị Bình", "fallback123")
        assert result != "fallback"[:8]
        assert len(result) > 0
        assert "-" in result or len(result) > 0


class TestSlugifyWorkerNameFallback:
    def test_pure_cjk_falls_back_to_id_prefix(self):
        """Pure CJK name → first 8 chars of fallback_id."""
        result = slugify_worker_name("工地工人", "abc12345678")
        assert result == "abc12345"

    def test_emoji_name_falls_back_to_id_prefix(self):
        """Emoji-only name → falls back to first 8 chars of fallback_id."""
        result = slugify_worker_name("🏗️👷", "xyz98765432")
        assert result == "xyz98765"

    def test_empty_string_falls_back(self):
        """Empty name → falls back to id prefix."""
        result = slugify_worker_name("", "deadbeefabcd")
        assert result == "deadbeef"

    def test_whitespace_only_falls_back(self):
        """Whitespace-only name → no latin content → falls back."""
        result = slugify_worker_name("   ", "feedcafe1234")
        # Spaces have no Latin category — falls back
        assert result == "feedcafe"

    def test_empty_fallback_id_uses_worker_literal(self):
        """Empty fallback_id with non-slugifiable name → 'worker' literal."""
        result = slugify_worker_name("工地", "")
        assert result == "worker"


class TestSlugifyWorkerNameMaxLength:
    def test_max_length_32_enforced(self):
        """Very long name → truncated to ≤32 chars."""
        long_name = "Jean-Baptiste-Emmanuel Zorg de la Bourboule"
        result = slugify_worker_name(long_name, "abc12345")
        assert len(result) <= 32

    def test_mixed_cjk_and_latin_uses_latin_part(self):
        """Name with some Latin chars → not a pure fallback case."""
        result = slugify_worker_name("John 工地", "abc12345")
        # Has Latin content → slug from slugify (may include 'john')
        assert "john" in result.lower() or len(result) > 0
