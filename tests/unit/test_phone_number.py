"""Phone number normalisation to E.164."""

import pytest

from app.domain.value_objects.phone_number import InvalidPhoneNumberError, normalize_phone


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0912345678", "+84912345678"),
        ("0912 345 678", "+84912345678"),
        ("+84 912-345-678", "+84912345678"),
        ("0084912345678", "+84912345678"),
        ("+33 6 12 34 56 78", "+33612345678"),
        ("(06) 12.34.56.78", "+33612345678"),
    ],
)
def test_normalizes_common_inputs(raw: str, expected: str) -> None:
    region = "FR" if expected.startswith("+33") and not raw.startswith("+") else "VN"
    assert normalize_phone(raw, default_region=region) == expected


@pytest.mark.parametrize("raw", ["", "   ", "abc", "12345", "+0123456789", "+8491234567890123", "912345678"])
def test_rejects_garbage(raw: str) -> None:
    with pytest.raises(InvalidPhoneNumberError):
        normalize_phone(raw)
