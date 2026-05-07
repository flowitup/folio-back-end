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


class InviteTokenNotFoundError(CompaniesDomainError):
    """Raised when an invite token cannot be found."""

    def __init__(self, token_id: UUID) -> None:
        self.token_id = token_id
        super().__init__(f"Invite token {token_id} not found")


class InviteTokenExpiredError(CompaniesDomainError):
    """Raised when an invite token has passed its expiry timestamp."""

    def __init__(self, token_id: UUID) -> None:
        self.token_id = token_id
        super().__init__(f"Invite token {token_id} has expired")


class InviteTokenAlreadyRedeemedError(CompaniesDomainError):
    """Raised when an invite token was already redeemed by a user."""

    def __init__(self, token_id: UUID) -> None:
        self.token_id = token_id
        super().__init__(f"Invite token {token_id} has already been redeemed")


class ActiveInviteTokenAlreadyExistsError(CompaniesDomainError):
    """Raised when an admin tries to generate a second active token without revoking the first."""

    def __init__(self, company_id: UUID) -> None:
        self.company_id = company_id
        super().__init__(
            f"An active invite token already exists for company {company_id}. "
            "Revoke the existing token before generating a new one."
        )


class CompanyAlreadyAttachedError(CompaniesDomainError):
    """Raised when a user tries to redeem a token for a company they already have access to."""

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


class InviteTokenSystemOverloadError(CompaniesDomainError):
    """Raised when the DOS guard fires: too many active invite tokens in the system.

    The route handler maps this to HTTP 503 with reason=redeem_overloaded so
    callers can surface a user-friendly message and retry after admin cleanup.
    """

    def __init__(self, count: int) -> None:
        self.count = count
        super().__init__(
            f"Too many active invite tokens ({count}). " "Admin must revoke stale tokens before redemption is allowed."
        )
