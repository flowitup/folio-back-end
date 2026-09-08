"""Application layer for the company_persons bounded context (Phase 2 slice A).

Only the repository port + adapter land in this slice — onboarding use cases
(add-by-phone, import, sign-up linking, grants management) are a later slice
built on top of these tables.
"""

from app.application.company_persons.ports import CompanyPersonRepositoryPort

__all__ = ["CompanyPersonRepositoryPort"]
