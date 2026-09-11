"""Domain exceptions for the companies bounded context."""

from uuid import UUID


class CompaniesDomainError(Exception):
    """Base class for all companies domain errors."""


class CompanyNotFoundError(CompaniesDomainError):
    """Raised when a company cannot be found by its ID."""

    def __init__(self, company_id: UUID) -> None:
        self.company_id = company_id
        super().__init__(f"Company {company_id} not found")


class UserCompanyAccessNotFoundError(CompaniesDomainError):
    """Raised when a user does not have access to a given company."""

    def __init__(self, user_id: UUID, company_id: UUID) -> None:
        self.user_id = user_id
        self.company_id = company_id
        super().__init__(f"User {user_id} has no access to company {company_id}")


class CompanyAlreadyAttachedError(CompaniesDomainError):
    """Raised when a user tries to join a company they already have access to."""

    def __init__(self, user_id: UUID, company_id: UUID) -> None:
        self.user_id = user_id
        self.company_id = company_id
        super().__init__(f"User {user_id} already has access to company {company_id}")


class ForbiddenCompanyError(CompaniesDomainError):
    """Raised when a non-admin user attempts an admin-only company operation."""

    def __init__(self, user_id: UUID, company_id: UUID) -> None:
        self.user_id = user_id
        self.company_id = company_id
        super().__init__(f"User {user_id} is not permitted to perform this operation on company {company_id}")


class MissingPrimaryCompanyError(CompaniesDomainError):
    """Raised when a user has no attached company but attempts to create a billing document.

    Defined here for symmetry; raised by billing use-cases.
    """

    def __init__(self, user_id: UUID) -> None:
        self.user_id = user_id
        super().__init__(f"User {user_id} has no attached company. Attach a company first.")


class LastCompanyAdminError(CompaniesDomainError):
    """Raised when demoting/removing the last remaining admin of a company.

    Every company must keep at least one admin so its billing stays manageable.
    """

    def __init__(self, company_id: UUID) -> None:
        self.company_id = company_id
        super().__init__(f"Company {company_id} must keep at least one admin")


class CompanyHasProjectsError(CompaniesDomainError):
    """Raised when deleting a company that still owns projects.

    `projects.company_id` is `ON DELETE RESTRICT` (migration 2ca24be9e3a8) —
    a company is a project's owner, not just a label, so deleting it while
    projects remain must fail loudly with the project count rather than let
    the DB raise an opaque IntegrityError.
    """

    def __init__(self, company_id: UUID, project_count: int) -> None:
        self.company_id = company_id
        self.project_count = project_count
        super().__init__(f"Company {company_id} still owns {project_count} project(s); delete or reassign them first")
