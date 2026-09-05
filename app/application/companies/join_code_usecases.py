"""Company join code: one short code per company, shared out of band, that attaches whoever
types it in the app as a ``member``. Unlike invite tokens it is reusable until revoked.
"""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import Optional
from uuid import UUID

from app.application.companies.dtos import CompanyResponse
from app.application.companies.ports import (
    ClockPort,
    CompanyRepositoryPort,
    TransactionalSessionPort,
    UserCompanyAccessRepositoryPort,
)
from app.domain.companies.exceptions import CompanyAlreadyAttachedError, CompanyNotFoundError
from app.domain.companies.roles import CompanyRole
from app.domain.companies.user_company_access import UserCompanyAccess

# No 0/O/1/I so the code survives being read aloud or hand-written on site.
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_CODE_LENGTH = 8


class JoinCodeNotFoundError(Exception):
    """No company has this join code (never issued, mistyped, or revoked)."""


def normalize_join_code(raw: str) -> str:
    """Upper-case and strip separators: ``"k7q2-m9xr"`` → ``"K7Q2M9XR"``."""
    return "".join(ch for ch in raw.strip().upper() if ch.isalnum())


class SetJoinCodeUseCase:
    def __init__(self, company_repo: CompanyRepositoryPort, clock: ClockPort) -> None:
        self._companies = company_repo
        self._clock = clock

    def execute(self, company_id: UUID, enable: bool, db_session: TransactionalSessionPort) -> Optional[str]:
        company = self._companies.find_by_id(company_id)
        if company is None:
            raise CompanyNotFoundError(company_id)
        code: Optional[str] = None
        if enable:
            for _ in range(10):
                candidate = "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_LENGTH))
                if self._companies.find_by_join_code(candidate) is None:
                    code = candidate
                    break
            if code is None:  # pragma: no cover - 32^8 space, collisions are theoretical
                raise RuntimeError("Could not allocate a unique join code")
        self._companies.save(company.with_updates(join_code=code, updated_at=self._clock.now()))
        db_session.commit()
        return code


class JoinCompanyByCodeUseCase:
    def __init__(
        self,
        company_repo: CompanyRepositoryPort,
        access_repo: UserCompanyAccessRepositoryPort,
        clock: ClockPort,
    ) -> None:
        self._companies = company_repo
        self._access = access_repo
        self._clock = clock

    def execute(self, user_id: UUID, raw_code: str, db_session: TransactionalSessionPort) -> CompanyResponse:
        code = normalize_join_code(raw_code)
        company = self._companies.find_by_join_code(code) if code else None
        if company is None:
            raise JoinCodeNotFoundError()
        if self._access.find(user_id, company.id) is not None:
            raise CompanyAlreadyAttachedError(user_id, company.id)
        now: datetime = self._clock.now()
        self._access.save(
            UserCompanyAccess(
                user_id=user_id,
                company_id=company.id,
                is_primary=len(self._access.list_for_user(user_id)) == 0,
                attached_at=now,
                role=CompanyRole.MEMBER.value,
            )
        )
        db_session.commit()
        return CompanyResponse.from_entity(company)
