"""UpsertCompanyProfileUseCase — create or update the company profile for a user."""

from __future__ import annotations

from datetime import datetime, timezone

from app.application.billing.dtos import CompanyProfileResponse, UpsertCompanyProfileInput
from app.application.billing.ports import CompanyProfileRepositoryPort, TransactionalSessionPort
from app.domain.billing.company_profile import CompanyProfile


class UpsertCompanyProfileUseCase:
    """Create or fully replace the company profile for a user.

    If a profile already exists for user_id it is replaced in full (all fields
    overwritten from the input). created_at is preserved from the existing row.
    """

    def __init__(self, profile_repo: CompanyProfileRepositoryPort) -> None:
        self._profile_repo = profile_repo

    def execute(
        self,
        inp: UpsertCompanyProfileInput,
        db_session: TransactionalSessionPort,
    ) -> CompanyProfileResponse:
        legal_name = inp.legal_name.strip() if inp.legal_name else ""
        if not legal_name:
            raise ValueError("Company legal name is required")

        address = inp.address.strip() if inp.address else ""
        if not address:
            raise ValueError("Company address is required")

        now = datetime.now(timezone.utc)
        existing = self._profile_repo.find_by_user_id(inp.user_id)
        created_at = existing.created_at if existing is not None else now

        profile = CompanyProfile(
            user_id=inp.user_id,
            legal_name=legal_name,
            address=address,
            siret=inp.siret,
            tva_number=inp.tva_number,
            iban=inp.iban,
            bic=inp.bic,
            logo_url=inp.logo_url,
            default_payment_terms=inp.default_payment_terms,
            prefix_override=inp.prefix_override,
            created_at=created_at,
            updated_at=now,
        )

        saved = self._profile_repo.save(profile)
        db_session.commit()
        return CompanyProfileResponse.from_entity(saved)
