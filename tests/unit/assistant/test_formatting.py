"""Unit tests for `app.application.assistant.formatting` — per-language money/date/
shift-type rendering, worker-name matching, and free-text month parsing."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.application.assistant import formatting


class TestFormatMoney:
    def test_en_uses_a_leading_symbol_and_comma_thousands(self) -> None:
        assert formatting.format_money(Decimal("1234.5"), "en") == "€1,234.50"

    def test_vi_uses_dot_thousands_and_comma_decimal(self) -> None:
        assert formatting.format_money(Decimal("1234.5"), "vi") == "1.234,50 €"

    def test_fr_uses_space_thousands_and_comma_decimal(self) -> None:
        assert formatting.format_money(Decimal("1234.5"), "fr") == "1 234,50 €"

    def test_negative_amount_keeps_the_sign(self) -> None:
        assert formatting.format_money(Decimal("-50"), "en") == "-€50.00"

    def test_none_amount_is_zero(self) -> None:
        assert formatting.format_money(None, "en") == "€0.00"


class TestFormatDate:
    def test_en_is_iso(self) -> None:
        assert formatting.format_date(date(2026, 9, 23), "en") == "2026-09-23"

    def test_fr_and_vi_are_day_first(self) -> None:
        assert formatting.format_date(date(2026, 9, 23), "fr") == "23/09/2026"
        assert formatting.format_date(date(2026, 9, 23), "vi") == "23/09/2026"

    def test_none_renders_a_dash(self) -> None:
        assert formatting.format_date(None, "en") == "-"


class TestShiftTypeLabel:
    def test_translates_known_shift_types(self) -> None:
        assert formatting.shift_type_label("full", "en") == "full day"
        assert formatting.shift_type_label("half", "fr") == "demi-journée"
        assert formatting.shift_type_label("overtime", "vi") == "tăng ca"

    def test_unknown_shift_type_passes_through(self) -> None:
        assert formatting.shift_type_label("banked", "en") == "banked"

    def test_none_renders_a_dash(self) -> None:
        assert formatting.shift_type_label(None, "en") == "-"


class TestMatchNames:
    def test_matches_a_whole_word_accent_insensitively(self) -> None:
        assert formatting.match_names("Tuan a travaille aujourd'hui", ["Tuấn"]) == ["Tuấn"]

    def test_does_not_match_a_name_embedded_in_a_longer_word(self) -> None:
        assert formatting.match_names("Tuấn Anh đi làm hôm nay", ["An"]) == []

    def test_longest_match_wins_when_one_name_is_a_prefix_of_another(self) -> None:
        matched = formatting.match_names("An Nguyen di lam hom nay", ["An", "An Nguyen"])
        assert matched == ["An Nguyen"]

    def test_matches_a_multi_word_name_as_a_phrase(self) -> None:
        assert formatting.match_names("Tuấn Anh đi làm hôm nay", ["Tuấn Anh"]) == ["Tuấn Anh"]

    def test_no_match_returns_an_empty_list(self) -> None:
        assert formatting.match_names("bonjour", ["Minh", "Tuấn"]) == []


class TestParseMonth:
    def test_explicit_iso_month_wins(self) -> None:
        assert formatting.parse_month("salaire 2026-03", date(2026, 9, 23)) == (2026, 3)

    def test_vietnamese_month_word(self) -> None:
        assert formatting.parse_month("lương tháng 9 của Minh", date(2026, 1, 1)) == (2026, 9)

    def test_french_month_word(self) -> None:
        assert formatting.parse_month("salaire du mois 8", date(2026, 1, 1)) == (2026, 8)

    def test_defaults_to_the_given_month_when_nothing_is_said(self) -> None:
        assert formatting.parse_month("bảng lương tháng này", date(2026, 9, 23)) == (2026, 9)

    def test_out_of_range_month_falls_back_to_default(self) -> None:
        assert formatting.parse_month("tháng 13", date(2026, 9, 23)) == (2026, 9)

    def test_french_month_name(self) -> None:
        assert formatting.parse_month("salaire de septembre", date(2026, 1, 1)) == (2026, 9)

    def test_french_month_name_with_a_year(self) -> None:
        assert formatting.parse_month("salaire de mars 2025", date(2026, 1, 1)) == (2025, 3)

    def test_english_month_name(self) -> None:
        assert formatting.parse_month("September payroll", date(2026, 1, 1)) == (2026, 9)

    def test_english_month_abbreviation(self) -> None:
        assert formatting.parse_month("payroll for Sept", date(2026, 1, 1)) == (2026, 9)

    def test_vietnamese_spelled_out_month_name(self) -> None:
        assert formatting.parse_month("lương tháng chín của Minh", date(2026, 1, 1)) == (2026, 9)
