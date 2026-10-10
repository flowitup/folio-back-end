"""IBAN and BIC format checks for the bank details printed on invoices."""

from __future__ import annotations

import re

_IBAN = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{11,30}$")
_BIC = re.compile(r"^[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}([A-Z0-9]{3})?$")


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value).upper()


def compact_identifier(value: str) -> str:
    """Return ``value`` without whitespace, upper-cased: how a SIRET or TVA number is checked and stored."""
    return _compact(value)


def normalize_iban(value: str) -> str:
    """Return the IBAN without spaces, upper-cased; raise ValueError if it is not a valid IBAN.

    Checks the ISO 13616 shape and its mod-97 check digits.
    """
    iban = _compact(value)
    if not _IBAN.match(iban):
        raise ValueError("IBAN must be a country code, 2 check digits and 11 to 30 letters or digits")
    rearranged = iban[4:] + iban[:4]
    digits = "".join(str(int(ch, 36)) for ch in rearranged)
    if int(digits) % 97 != 1:
        raise ValueError("IBAN check digits are wrong")
    return iban


def normalize_bic(value: str) -> str:
    """Return the BIC without spaces, upper-cased; raise ValueError if it is not 8 or 11 characters of BIC shape."""
    bic = _compact(value)
    if not _BIC.match(bic):
        raise ValueError("BIC must be 8 or 11 letters or digits (bank, country, location, optional branch)")
    return bic
