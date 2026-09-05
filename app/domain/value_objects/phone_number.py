"""Phone numbers are stored and compared in E.164 form (``+84912345678``).

Users type numbers the way they see them on their phone: with spaces, dots, a leading ``0`` or
``+``. ``normalize_phone`` turns those into one canonical string so lookups and the unique
constraint behave. Numbers without a country code are assumed to be from ``default_region``
(France by default: ``06 12 34 56 78`` → ``+33612345678``; Vietnamese numbers need ``+84``).
"""

from __future__ import annotations

import re

# Country calling code used when the number has a national prefix only ("0912…").
DEFAULT_COUNTRY_CODES: dict[str, str] = {"FR": "33", "VN": "84"}

_E164 = re.compile(r"^\+[1-9]\d{7,14}$")


class InvalidPhoneNumberError(ValueError):
    """The input cannot be read as a phone number."""


def normalize_phone(raw: str, default_region: str = "FR") -> str:
    """Return the E.164 form of ``raw`` or raise ``InvalidPhoneNumberError``.

    * ``06 12 34 56 78`` / ``0612345678`` (FR default) → ``+33612345678``
    * ``+84 912 345 678`` / ``0084912345678`` → ``+84912345678``; ``0912345678`` with ``VN`` → ``+84912345678``
    """
    text = raw.strip()
    if not text:
        raise InvalidPhoneNumberError("Phone number is empty")
    digits = re.sub(r"[\s().-]", "", text)
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    if digits.startswith("+"):
        candidate = digits
    elif digits.startswith("0"):
        country = DEFAULT_COUNTRY_CODES.get(default_region.upper())
        if country is None:
            raise InvalidPhoneNumberError(f"Unknown default region {default_region}")
        candidate = f"+{country}{digits[1:]}"
    else:
        raise InvalidPhoneNumberError("Phone number must start with a country code or 0")
    if not _E164.match(candidate):
        raise InvalidPhoneNumberError("Phone number is not valid")
    return candidate
