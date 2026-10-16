"""Accent folding used by searches (QA3 documents-photos-analyses-13 / -14)."""

from app.infrastructure.database.text_collation import fold_text


def test_fold_text_strips_french_and_vietnamese_accents():
    assert fold_text("Étude Béton ÉRABLES") == "etude beton erables"
    assert fold_text("Hương Đà Nẵng Ưu Ơn") == "huong da nang uu on"


def test_fold_text_handles_decomposed_input():
    assert fold_text("béton") == "beton"


def test_fold_text_handles_nfd_input_from_macos_names():
    import unicodedata

    assert fold_text(unicodedata.normalize("NFD", "Étude Hương")) == "etude huong"
