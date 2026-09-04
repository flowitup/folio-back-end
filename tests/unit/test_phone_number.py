"""Phone number normalisation to E.164."""

import pytest

from app.domain.value_objects.phone_number import InvalidPhoneNumberError, normalize_phone


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
