"""UpdateCompanyUseCase — admin partially updates a company entity."""

from __future__ import annotations

from datetime import datetime, timezone

from app.application.companies._helpers import (
    _assert_company_admin,
    _validate_address,
    _validate_legal_name,
    _validate_prefix_override,
)
from app.application.companies.dtos import CompanyResponse, UpdateCompanyInput
from app.application.companies.ports import (
    CompanyRepositoryPort,
    RoleCheckerPort,
    TransactionalSessionPort,
)
from app.domain.companies.exceptions import CompanyNotFoundError
from app.domain.companies.sections import normalize_hidden_sections
from app.domain.companies.masking import SENSITIVE_FIELDS, is_masked, mask_company

# Optional company fields an explicit null clears (legal_name and address are required).
CLEARABLE_FIELDS: frozenset[str] = frozenset(
    {"siret", "tva_number", "iban", "bic", "logo_url", "default_payment_terms", "prefix_override"}
)


class UpdateCompanyUseCase:
    """Partially update an existing company (company admin or platform admin).

    Only fields that are not None in the input are applied, and the optional
    ones listed in ``inp.clear_fields`` are cleared.
    legal_name and address cannot be set to blank if supplied.
    """

    def __init__(
        self,
        company_repo: CompanyRepositoryPort,
        role_checker: RoleCheckerPort,
    ) -> None:
        self._company_repo = company_repo
        self._role_checker = role_checker

    def execute(
        self,
        inp: UpdateCompanyInput,
        db_session: TransactionalSessionPort,
    ) -> CompanyResponse:
        # 1. Company-admin guard (platform '*:*' OR admin of this company)
        _assert_company_admin(self._role_checker, inp.caller_id, inp.id)

        # 2. Load entity (no FOR UPDATE needed — admin-only, low contention)
        company = self._company_repo.find_by_id(inp.id)
        if company is None:
            raise CompanyNotFoundError(inp.id)

        # 3. Build update kwargs from non-None inputs
        updates: dict = {"updated_at": datetime.now(timezone.utc)}

        if inp.legal_name is not None:
            updates["legal_name"] = _validate_legal_name(inp.legal_name)
        if inp.address is not None:
            updates["address"] = _validate_address(inp.address)
        if inp.prefix_override is not None:
            _validate_prefix_override(inp.prefix_override)
            updates["prefix_override"] = inp.prefix_override
        if inp.hidden_sections is not None:
            updates["hidden_sections"] = normalize_hidden_sections(inp.hidden_sections)
        # Nullable fields — None in input means "leave unchanged"; an explicit
        # null from the client arrives in inp.clear_fields instead (below).
        for field in ("siret", "tva_number", "iban", "bic", "logo_url", "default_payment_terms"):
            val = getattr(inp, field)
            if val is None:
                continue
            if field in SENSITIVE_FIELDS and is_masked(val):
                # The masked value a read returned came back unchanged: keep the stored one.
                continue
            if field in SENSITIVE_FIELDS and val == "":
                # "" is the clear value: store NULL, not an empty string that reads back masked.
                val = None
            updates[field] = val

        for field in inp.clear_fields & CLEARABLE_FIELDS:
            updates[field] = None

        updated = company.with_updates(**updates)
        saved = self._company_repo.save(updated)
        db_session.commit()
        # Same masking as a read (GET /companies/<id>): only platform admins see bank details in full.
        return CompanyResponse.from_entity(
            mask_company(saved, full=self._role_checker.is_platform_admin(inp.caller_id))
        )
