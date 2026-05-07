"""UpdateCompanyUseCase — admin partially updates a company entity."""

from __future__ import annotations

from datetime import datetime, timezone

from app.application.companies._helpers import (
    _assert_admin,
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

_ADMIN_PERMISSION = "*:*"


class UpdateCompanyUseCase:
    """Partially update an existing company (admin only).

    Only fields that are not None in the input are applied.
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
        # 1. Admin guard
        is_admin = self._role_checker.has_permission(inp.caller_id, _ADMIN_PERMISSION)
        _assert_admin(inp.caller_id, inp.id, is_admin)

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
        # Nullable fields — None in input means "leave unchanged";
        # to explicitly clear a field the API layer must pass a sentinel
        # (handled at Pydantic schema level in phase 04).
        for field in ("siret", "tva_number", "iban", "bic", "logo_url", "default_payment_terms"):
            val = getattr(inp, field)
            if val is not None:
                updates[field] = val

        updated = company.with_updates(**updates)
        saved = self._company_repo.save(updated)
        db_session.commit()
        return CompanyResponse.from_entity(saved)
