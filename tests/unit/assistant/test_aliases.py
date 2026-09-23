"""Unit tests for `app.application.assistant.aliases.expand` — whole-word alias
matching and the three Vietnamese words known to fold to the same ASCII spelling as an
unrelated alias term."""

from __future__ import annotations

from app.application.assistant import aliases


def test_expand_always_includes_the_raw_query() -> None:
    assert "où est le Hilti TE-30 ?" in aliases.expand("où est le Hilti TE-30 ?")


def test_expand_finds_no_group_for_an_unaliased_tool() -> None:
    assert aliases.expand("où est le Hilti TE-30 ?") == ["où est le Hilti TE-30 ?"]


def test_expand_matches_a_group_term_mentioned_in_a_full_sentence() -> None:
    terms = aliases.expand("máy khoan của anh Minh ở đâu")
    assert "perceuse" in terms
    assert "drill" in terms


def test_expand_matches_the_query_as_a_group_term_directly() -> None:
    terms = aliases.expand("carrelette")
    assert "máy cắt gạch" in terms


def test_expand_matches_a_group_term_embedded_in_a_longer_query() -> None:
    terms = aliases.expand("máy cắt gạch Bosch")
    assert "carrelette" in terms


def test_expand_does_not_let_cua_match_the_saw_group() -> None:
    """ "của" (possessive "of") folds (accent-stripped) to the exact same ASCII spelling
    as "cưa" (saw) — a sentence merely containing "của" must not pull in the saw group."""
    terms = aliases.expand("máy khoan của anh Minh ở đâu")
    assert "cưa" not in terms
    assert "scie" not in terms
    assert "saw" not in terms


def test_expand_still_finds_the_saw_group_when_actually_asked_for() -> None:
    terms = aliases.expand("cưa ở đâu")
    assert "scie" in terms
    assert "saw" in terms


def test_expand_does_not_let_bay_gio_match_the_trowel_group() -> None:
    """ "bây" (as in "bây giờ", now) folds to "bay", the trowel's own ASCII alias."""
    terms = aliases.expand("bây giờ máy mài ở kho nào")
    assert "truelle" not in terms
    assert "trowel" not in terms


def test_expand_still_finds_the_trowel_group_when_actually_asked_for() -> None:
    terms = aliases.expand("bay ở đâu")
    assert "truelle" in terms
    assert "trowel" in terms


def test_expand_does_not_let_thang_month_match_the_ladder_group() -> None:
    """ "tháng" (month) folds to "thang", the ladder's own ASCII alias."""
    terms = aliases.expand("tháng này có bao nhiêu tiền")
    assert "échelle" not in terms
    assert "ladder" not in terms


def test_expand_still_finds_the_ladder_group_when_actually_asked_for() -> None:
    terms = aliases.expand("thang ở đâu")
    assert "échelle" in terms
    assert "ladder" in terms


def test_expand_does_not_match_a_short_term_embedded_in_an_unrelated_word() -> None:
    """A whole-word match must not let a short alias term match inside a longer,
    unrelated word that merely contains the same letters — "bay" (trowel) embedded in
    the French word "abbaye" (abbey) must not pull in the trowel group."""
    terms = aliases.expand("on se retrouve devant l'abbaye du village")
    assert "truelle" not in terms
    assert "trowel" not in terms


def test_expand_matches_a_plural_french_term() -> None:
    terms = aliases.expand("où sont les échelles ?")
    assert "échelle" in terms
    assert "thang" in terms
    assert "ladder" in terms


def test_expand_matches_a_plural_french_term_with_a_different_tool() -> None:
    terms = aliases.expand("il faut ranger les perceuses")
    assert "perceuse" in terms
    assert "máy khoan" in terms
    assert "drill" in terms


def test_expand_matches_a_plural_english_term() -> None:
    terms = aliases.expand("any ladders on site?")
    assert "échelle" in terms
    assert "thang" in terms


def test_expand_matches_a_plural_english_term_with_a_different_tool() -> None:
    terms = aliases.expand("where are the drills")
    assert "perceuse" in terms
    assert "máy khoan" in terms


def test_expand_still_folds_cua_bay_thang_stopwords_with_the_plural_suffix_enabled() -> None:
    """The plural-suffix relaxation must not reopen the fold-collision hole the three
    stopwords guard against — none of them are alias terms, so this exercises the same
    query shapes as the stopword tests above with the new suffix logic active."""
    terms = aliases.expand("của anh, bây giờ, tháng 9")
    assert "cưa" not in terms
    assert "scie" not in terms
    assert "saw" not in terms
    assert "truelle" not in terms
    assert "trowel" not in terms
    assert "échelle" not in terms
    assert "ladder" not in terms
