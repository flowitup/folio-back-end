"""Unit tests for library_category.py — normalize_category() and helpers.

Covers:
- Exact match for each of the 15 canonical univers + autre
- Accent/case folding
- Substring match
- Parquet tie-break (revetement, not menuiserie)
- Unmapped text → autre
- None / empty / whitespace → None
- Idempotency: every slug normalises to itself
"""

from __future__ import annotations

import pytest

from app.domain.value_objects.library_category import (
    LIBRARY_CATEGORY_SLUGS,
    normalize_category,
    is_valid_category_slug,
)


# ---------------------------------------------------------------------------
# Exact canonical FR label matches — one per univers
# ---------------------------------------------------------------------------


class TestExactCanonicalFrLabel:
    """Each canonical FR label should map to its slug, regardless of case/accents."""

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("Terrasse et jardin", "terrasse_jardin"),
            ("Revêtement sol, mur et peinture", "revetement_sol_mur_peinture"),
            ("Chauffage, climatisation et ventilation", "chauffage_clim_ventilation"),
            ("Salle de bains", "salle_de_bains"),
            ("Meuble et rangement", "meuble_rangement"),
            ("Cuisine", "cuisine"),
            ("Luminaire", "luminaire"),
            ("Décoration", "decoration"),
            ("Menuiserie", "menuiserie"),
            ("Matériaux de construction", "materiaux_construction"),
            ("Electricité et domotique", "electricite_domotique"),
            ("Outillage", "outillage"),
            ("Plomberie", "plomberie"),
            ("Quincaillerie", "quincaillerie"),
            ("Droguerie", "droguerie"),
            ("Autre / Non classé", "autre"),
        ],
    )
    def test_exact_fr_label(self, raw: str, expected: str) -> None:
        assert normalize_category(raw) == expected

    def test_case_insensitive_fr_label(self) -> None:
        assert normalize_category("PLOMBERIE") == "plomberie"
        assert normalize_category("plomberie") == "plomberie"
        assert normalize_category("Plomberie") == "plomberie"


# ---------------------------------------------------------------------------
# Accent and case folding
# ---------------------------------------------------------------------------


class TestAccentCaseFolding:
    def test_accented_category(self) -> None:
        # "Décoration" has accent; should still match
        assert normalize_category("Décoration") == "decoration"
        assert normalize_category("decoration") == "decoration"

    def test_chauffage_with_accent_variant(self) -> None:
        assert normalize_category("CHAUFFAGE, climatisation et ventilation") == "chauffage_clim_ventilation"

    def test_materiaux_with_accent(self) -> None:
        # "Matériaux" has accent; fold should strip it
        assert normalize_category("Matériaux de construction") == "materiaux_construction"

    def test_electricite_with_accent(self) -> None:
        assert normalize_category("Électricité et domotique") == "electricite_domotique"

    def test_revetement_with_accent(self) -> None:
        assert normalize_category("Revêtement sol, mur et peinture") == "revetement_sol_mur_peinture"

    def test_mixed_case_slug_like_input(self) -> None:
        assert normalize_category("OUTILLAGE") == "outillage"
        assert normalize_category("outillage") == "outillage"


# ---------------------------------------------------------------------------
# Substring matching
# ---------------------------------------------------------------------------


class TestSubstringMatch:
    def test_compound_category_with_separator(self) -> None:
        # "Quincaillerie / Visserie" should map to quincaillerie (both keywords present,
        # "quincaillerie" wins as longest match over "visserie")
        assert normalize_category("Quincaillerie / Visserie") == "quincaillerie"

    def test_partial_keyword_in_value(self) -> None:
        # "tuyau de plomberie" — "plomberie" and "tuyau" both in synonym table
        result = normalize_category("tuyau de plomberie")
        assert result == "plomberie"  # "plomberie" (9 chars) > "tuyau" (5 chars)

    def test_substring_with_extra_words(self) -> None:
        # "sanitaire" (9 chars, → salle_de_bains) and "plomberie" (9 chars, → plomberie)
        # are the same length; the tie goes to whichever appears first in the sorted
        # keyword list. The important assertion is that a valid canonical slug is returned.
        result = normalize_category("produits de plomberie sanitaire")
        assert result in ("plomberie", "salle_de_bains")

    def test_keyword_in_longer_phrase(self) -> None:
        assert normalize_category("achat outillage pour chantier") == "outillage"

    def test_salle_de_bains_partial(self) -> None:
        # "douche" is a synonym for salle_de_bains
        assert normalize_category("douche italienne") == "salle_de_bains"


# ---------------------------------------------------------------------------
# Parquet tie-break — must map to revetement, NOT menuiserie
# ---------------------------------------------------------------------------


class TestParquetTieBreak:
    def test_parquet_maps_to_revetement(self) -> None:
        """parquet is curated in revetement_sol_mur_peinture only — not menuiserie.

        This documents the intentional tie-break: parquet is a floor covering.
        """
        assert normalize_category("parquet") == "revetement_sol_mur_peinture"

    def test_parquet_with_accents(self) -> None:
        assert normalize_category("Parquet flottant") == "revetement_sol_mur_peinture"

    def test_parquet_not_menuiserie(self) -> None:
        result = normalize_category("parquet en chêne")
        assert result == "revetement_sol_mur_peinture"
        assert result != "menuiserie"


# ---------------------------------------------------------------------------
# Unmapped text → autre
# ---------------------------------------------------------------------------


class TestUnmappedToAutre:
    def test_completely_unrelated_word(self) -> None:
        assert normalize_category("Café gourmand") == "autre"

    def test_random_string(self) -> None:
        assert normalize_category("xyz123") == "autre"

    def test_single_unrelated_word(self) -> None:
        assert normalize_category("informatique") == "autre"

    def test_numeric_string(self) -> None:
        assert normalize_category("12345") == "autre"

    def test_autre_slug_itself(self) -> None:
        # "autre" is both a valid slug AND a synonym key → itself
        assert normalize_category("autre") == "autre"


# ---------------------------------------------------------------------------
# None / empty / whitespace → None
# ---------------------------------------------------------------------------


class TestNullAndEmpty:
    def test_none_returns_none(self) -> None:
        assert normalize_category(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert normalize_category("") is None

    def test_whitespace_only_returns_none(self) -> None:
        assert normalize_category("   ") is None
        assert normalize_category("\t\n") is None

    def test_non_empty_after_fold_returns_slug(self) -> None:
        # non-whitespace content must still be normalised, not treated as empty
        assert normalize_category("  plomberie  ") == "plomberie"


# ---------------------------------------------------------------------------
# Idempotency — every slug normalises to itself
# ---------------------------------------------------------------------------


class TestIdempotency:
    @pytest.mark.parametrize("slug", LIBRARY_CATEGORY_SLUGS)
    def test_slug_normalises_to_itself(self, slug: str) -> None:
        """Passing a canonical slug back through normalize_category must return the same slug."""
        assert normalize_category(slug) == slug, f"Slug {slug!r} is not idempotent"

    def test_double_normalisation_is_stable(self) -> None:
        # Normalise twice — result must be same as normalising once
        raw_values = [
            "Plomberie",
            "CHAUFFAGE, climatisation et ventilation",
            "jardin",
            "Quincaillerie / Visserie",
            "Café gourmand",
        ]
        for raw in raw_values:
            once = normalize_category(raw)
            twice = normalize_category(once)
            assert once == twice, f"normalize_category not idempotent on {raw!r}: {once!r} → {twice!r}"


# ---------------------------------------------------------------------------
# is_valid_category_slug
# ---------------------------------------------------------------------------


class TestIsValidCategorySlug:
    @pytest.mark.parametrize("slug", LIBRARY_CATEGORY_SLUGS)
    def test_all_canonical_slugs_are_valid(self, slug: str) -> None:
        assert is_valid_category_slug(slug) is True

    def test_free_text_is_invalid(self) -> None:
        assert is_valid_category_slug("Plomberie") is False  # capitalised
        assert is_valid_category_slug("Tools") is False
        assert is_valid_category_slug("") is False

    def test_unknown_slug_is_invalid(self) -> None:
        assert is_valid_category_slug("hardware") is False

    def test_autre_is_valid(self) -> None:
        assert is_valid_category_slug("autre") is True


# ---------------------------------------------------------------------------
# LIBRARY_CATEGORY_SLUGS tuple
# ---------------------------------------------------------------------------


class TestLibraryCategorySlugs:
    def test_has_16_entries(self) -> None:
        assert len(LIBRARY_CATEGORY_SLUGS) == 16

    def test_autre_is_last(self) -> None:
        assert LIBRARY_CATEGORY_SLUGS[-1] == "autre"

    def test_no_duplicates(self) -> None:
        assert len(LIBRARY_CATEGORY_SLUGS) == len(set(LIBRARY_CATEGORY_SLUGS))

    def test_first_is_terrasse_jardin(self) -> None:
        assert LIBRARY_CATEGORY_SLUGS[0] == "terrasse_jardin"


# ---------------------------------------------------------------------------
# Word-boundary matching: short keys (sol/mur/vis) must NOT match inside words
# ---------------------------------------------------------------------------


class TestWordBoundaryMatching:
    @pytest.mark.parametrize(
        "raw",
        [
            "Tournevis",  # 'vis' inside tourneVIS — must not → quincaillerie
            "Visiere de protection",  # 'vis' inside VISiere
            "Console murale",  # 'sol' inside conSOLe (and 'mur' is not token 'murale')
            "Isolant mince",  # 'sol' inside iSOLant
        ],
    )
    def test_short_key_not_matched_inside_word(self, raw: str) -> None:
        # These have no legitimate whole-token keyword → safe fallback, never a mis-map.
        assert normalize_category(raw) == "autre"

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Revetement sol, mur et peinture", "revetement_sol_mur_peinture"),  # sol/mur as real tokens
            ("Quincaillerie / Visserie", "quincaillerie"),
            ("plan de travail", "cuisine"),  # multi-word phrase still matches
            ("Salle de bains", "salle_de_bains"),  # multi-word phrase
            ("Aerosol peinture", "revetement_sol_mur_peinture"),  # matches via real token 'peinture'
        ],
    )
    def test_legitimate_token_and_phrase_matches_still_work(self, raw: str, expected: str) -> None:
        assert normalize_category(raw) == expected
