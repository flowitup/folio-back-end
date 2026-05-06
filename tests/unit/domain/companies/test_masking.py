"""Unit tests for mask_company / _mask edge cases."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.domain.companies.company import Company
from app.domain.companies.masking import SENSITIVE_FIELDS, _mask, mask_company


def _make_company(**overrides) -> Company:
    now = datetime.now(timezone.utc)
    defaults = dict(
        id=uuid4(),
        legal_name="ACME SAS",
        address="1 rue de la Paix, 75001 Paris",
        siret="12345678901234",
        tva_number="FR12345678901",
        iban="FR7630001007941234567890185",
        bic="BNPAFRPP",
        logo_url=None,
        default_payment_terms=None,
        prefix_override=None,
        created_by=uuid4(),
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    return Company(**defaults)


class TestMaskFunction:
    def test_none_returns_none(self):
        assert _mask(None) is None

    def test_empty_string_returns_bullets(self):
        assert _mask("") == "····"

    def test_short_string_1_char_returns_bullets(self):
        assert _mask("A") == "····"

    def test_exactly_4_chars_returns_bullets(self):
        assert _mask("1234") == "····"

    def test_5_chars_shows_last_4(self):
        assert _mask("12345") == "····2345"

    def test_long_string_shows_last_4(self):
        assert _mask("12345678901234") == "····1234"

    def test_mask_preserves_last_4(self):
        value = "BNPAFRPP"
        masked = _mask(value)
        assert masked.endswith(value[-4:])
        assert masked.startswith("····")

    def test_mask_does_not_contain_full_value(self):
        value = "FR7630001007941234567890185"
        masked = _mask(value)
        assert value not in masked


class TestMaskCompany:
    def test_full_true_returns_same_instance(self):
        c = _make_company()
        result = mask_company(c, full=True)
        assert result is c

    def test_full_false_returns_new_instance(self):
        c = _make_company()
        result = mask_company(c, full=False)
        assert result is not c

    def test_non_admin_siret_masked(self):
        c = _make_company(siret="12345678901234")
        result = mask_company(c, full=False)
        assert result.siret == "····1234"
        assert result.siret != c.siret

    def test_non_admin_tva_masked(self):
        c = _make_company(tva_number="FR12345678901")
        result = mask_company(c, full=False)
        assert result.tva_number == "····8901"

    def test_non_admin_iban_masked(self):
        c = _make_company(iban="FR7630001007941234567890185")
        result = mask_company(c, full=False)
        assert result.iban == "····0185"

    def test_non_admin_bic_masked(self):
        c = _make_company(bic="BNPAFRPP")
        result = mask_company(c, full=False)
        assert result.bic == "····FRPP"

    def test_non_admin_none_sensitive_stays_none(self):
        c = _make_company(siret=None, tva_number=None, iban=None, bic=None)
        result = mask_company(c, full=False)
        assert result.siret is None
        assert result.tva_number is None
        assert result.iban is None
        assert result.bic is None

    def test_admin_sees_full_siret(self):
        c = _make_company(siret="12345678901234")
        result = mask_company(c, full=True)
        assert result.siret == "12345678901234"

    def test_non_sensitive_fields_unchanged_after_mask(self):
        c = _make_company(legal_name="ACME SAS", address="42 rue X")
        result = mask_company(c, full=False)
        assert result.legal_name == "ACME SAS"
        assert result.address == "42 rue X"
        assert result.id == c.id

    def test_sensitive_fields_constant(self):
        """Ensure the SENSITIVE_FIELDS tuple is not accidentally shrunk."""
        assert set(SENSITIVE_FIELDS) == {"siret", "tva_number", "iban", "bic"}

    def test_masking_never_leaks_full_value_to_non_admin(self):
        """test_masking_never_leaks_full_value_to_non_admin — required by spec."""
        c = _make_company(
            siret="12345678901234",
            tva_number="FR12345678901",
            iban="FR7630001007941234567890185",
            bic="BNPAFRPP",
        )
        result = mask_company(c, full=False)
        for field in SENSITIVE_FIELDS:
            raw = getattr(c, field)
            masked = getattr(result, field)
            if raw is not None:
                assert raw not in (masked or ""), (
                    f"Field {field!r}: full value {raw!r} leaked in masked output {masked!r}"
                )

    def test_admin_sees_full(self):
        """test_admin_sees_full — required by spec."""
        c = _make_company(
            siret="12345678901234",
            tva_number="FR12345678901",
            iban="FR7630001007941234567890185",
            bic="BNPAFRPP",
        )
        result = mask_company(c, full=True)
        assert result.siret == c.siret
        assert result.tva_number == c.tva_number
        assert result.iban == c.iban
        assert result.bic == c.bic
