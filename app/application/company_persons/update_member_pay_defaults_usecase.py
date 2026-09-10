"""UpdateMemberPayDefaultsUseCase — PATCH /companies/<id>/members/<person_id>.

The company profile carries the pay defaults a new project Worker inherits
(`CreateWorkerUseCase` falls back to `default_daily_rate` / `labor_role_id`
when the caller omits them). Until this use case, nothing but the one-off
`company_persons_from_workers` backfill ever wrote those columns, so a person
onboarded through `POST /companies/<id>/members` kept a NULL rate forever and
every project had to retype it.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional
from uuid import UUID

from app.application.companies.ports import (
    RoleCheckerPort,
    TransactionalSessionPort,
    UserCompanyAccessRepositoryPort,
)
from app.application.company_persons.dtos import MemberPayDefaults, UpdateMemberPayDefaultsInput
from app.application.company_persons.exceptions import (
    CompanyPersonNotFoundError,
    LaborRoleNotInCompanyError,
)
from app.application.company_persons.ports import CompanyPersonRepositoryPort
from app.application.labor.labor_role_ports import ILaborRoleRepository
from app.domain.companies.exceptions import ForbiddenCompanyError

# Who may set what a member costs. Mirrors the directory read gate
# (`require_company_role_any("admin", "manager")`): a manager already sees
# every rate in the directory, so letting them correct one adds no exposure.
_ALLOWED_ROLES = ("admin", "manager")


class UpdateMemberPayDefaultsUseCase:
    def __init__(
        self,
        company_person_repo: CompanyPersonRepositoryPort,
        labor_role_repo: ILaborRoleRepository,
        access_repo: UserCompanyAccessRepositoryPort,
        role_checker: RoleCheckerPort,
    ) -> None:
        self._company_persons = company_person_repo
        self._labor_roles = labor_role_repo
        self._access = access_repo
        self._role_checker = role_checker

    def execute(self, inp: UpdateMemberPayDefaultsInput, db_session: TransactionalSessionPort) -> MemberPayDefaults:
        self._assert_may_manage(inp.caller_id, inp.company_id)

        profile = self._company_persons.find(inp.company_id, inp.person_id)
        # A booted profile is not a member any more: refuse rather than
        # quietly reviving pay data for someone who left the company.
        if profile is None or not profile.is_active:
            raise CompanyPersonNotFoundError(inp.company_id, inp.person_id)

        if inp.set_labor_role_id and inp.labor_role_id is not None:
            self._assert_role_owned_by(inp.company_id, inp.labor_role_id)

        if inp.set_default_daily_rate:
            profile.default_daily_rate = _normalize_rate(inp.default_daily_rate)
        if inp.set_labor_role_id:
            profile.labor_role_id = inp.labor_role_id

        saved = self._company_persons.save(profile)
        db_session.commit()

        return MemberPayDefaults(
            person_id=saved.person_id,
            default_daily_rate=saved.default_daily_rate,
            labor_role_id=saved.labor_role_id,
        )

    def _assert_may_manage(self, caller_id: UUID, company_id: UUID) -> None:
        if self._role_checker.is_platform_admin(caller_id):
            return
        access = self._access.find(caller_id, company_id)
        if access is None or access.role not in _ALLOWED_ROLES:
            raise ForbiddenCompanyError(caller_id, company_id)

    def _assert_role_owned_by(self, company_id: UUID, labor_role_id: UUID) -> None:
        role = self._labor_roles.find_by_id(labor_role_id)
        # A legacy role with no company (company_id NULL) is rejected too: it is
        # excluded from every company's own list, so nothing should pin to it.
        if role is None or role.company_id != company_id:
            raise LaborRoleNotInCompanyError(company_id, labor_role_id)


def _normalize_rate(rate: Optional[Decimal]) -> Optional[Decimal]:
    """Quantize to the 2 decimals the column stores, so a read-back matches
    what the caller sent instead of surprising them with silent rounding."""
    if rate is None:
        return None
    return rate.quantize(Decimal("0.01"))
