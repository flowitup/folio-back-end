"""Phone number normalisation to E.164."""

import pytest

from app.domain.value_objects.phone_number import (
    InvalidPhoneNumberError,
    normalize_french_phone,
    normalize_phone,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0612345678", "+33612345678"),
        ("06 12 34 56 78", "+33612345678"),
        ("(06) 12.34.56.78", "+33612345678"),
        ("+33 6 12 34 56 78", "+33612345678"),
        ("0033612345678", "+33612345678"),
        ("+84 912-345-678", "+84912345678"),
        ("0084912345678", "+84912345678"),
    ],
)
def test_french_numbers_by_default(raw: str, expected: str) -> None:
    assert normalize_phone(raw) == expected


def test_national_prefix_follows_the_region_argument() -> None:
    assert normalize_phone("0912 345 678", default_region="VN") == "+84912345678"
    assert normalize_phone("0912 345 678") == "+33912345678"


@pytest.mark.parametrize("raw", ["", "   ", "abc", "12345", "+0123456789", "+8491234567890123", "912345678"])
def test_rejects_garbage(raw: str) -> None:
    with pytest.raises(InvalidPhoneNumberError):
        normalize_phone(raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0612345678", "+33612345678"),
        ("06 12 34 56 78", "+33612345678"),
        ("+33 6 12 34 56 78", "+33612345678"),
        ("0033612345678", "+33612345678"),
        ("01 42 34 56 78", "+33142345678"),
        # The international notation that keeps the trunk 0 in brackets.
        ("+33 (0)6 20 10 00 01", "+33620100001"),
        ("+33(0)620100001", "+33620100001"),
        ("0033 (0)6 20 10 00 01", "+33620100001"),
        ("+330620100001", "+33620100001"),
    ],
)
def test_sign_in_accepts_french_numbers(raw: str, expected: str) -> None:
    assert normalize_french_phone(raw) == expected


@pytest.mark.parametrize("raw", ["+84 912 345 678", "0084912345678", "+44 20 7946 0958", "+1 202 555 0173"])
def test_sign_in_refuses_numbers_from_other_countries(raw: str) -> None:
    with pytest.raises(InvalidPhoneNumberError):
        normalize_french_phone(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "+336\uff11\uff12\uff13\uff14\uff15\uff16\uff17\uff18",  # fullwidth digits after an ASCII prefix
        "+33\u0666\u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0668",  # Arabic-Indic digits
        "+33 6 \u0661\u0662 34 56 78",
    ],
)
def test_non_ascii_digits_are_refused(raw: str) -> None:
    """\\d matches every Unicode decimal digit: one number could be spelled many ways past per-number limits."""
    with pytest.raises(InvalidPhoneNumberError):
        normalize_french_phone(raw)
    with pytest.raises(InvalidPhoneNumberError):
        normalize_phone(raw)


def test_accepted_numbers_are_pure_ascii() -> None:
    for raw in ("06 12 34 56 78", "+84 912-345-678", "0033612345678"):
        assert normalize_phone(raw).isascii()
