"""Domain exceptions for the billing bounded context."""

from uuid import UUID


class BillingDomainError(Exception):
    """Base class for all billing domain errors."""


class InvalidStatusTransitionError(BillingDomainError):
    """Raised when a status transition is not allowed for the given document kind."""

    def __init__(self, kind: str, from_status: str, to_status: str) -> None:
        self.kind = kind
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(f"Invalid transition for {kind}: {from_status!r} → {to_status!r}")


class MissingCompanyProfileError(BillingDomainError):
    """Raised when a billing document is created but no company_profile exists for the user."""

    def __init__(self, user_id: UUID) -> None:
        self.user_id = user_id
        super().__init__(f"No company profile found for user {user_id}")


class BillingDocumentNotFoundError(BillingDomainError):
    """Raised when a billing document cannot be found."""

    def __init__(self, document_id: UUID) -> None:
        self.document_id = document_id
        super().__init__(f"Billing document {document_id} not found")


class BillingTemplateNotFoundError(BillingDomainError):
    """Raised when a billing template cannot be found."""

    def __init__(self, template_id: UUID) -> None:
        self.template_id = template_id
        super().__init__(f"Billing template {template_id} not found")


class BillingNumberCollisionError(BillingDomainError):
    """Raised on a duplicate (user_id, kind, document_number) constraint violation."""

    def __init__(self, document_number: str) -> None:
        self.document_number = document_number
        super().__init__(f"Document number collision: {document_number!r}")


class DevisAlreadyConvertedError(BillingDomainError):
    """Raised when attempting to convert a devis that was already converted to a facture."""

    def __init__(self, devis_id: UUID) -> None:
        self.devis_id = devis_id
        super().__init__(f"Devis {devis_id} was already converted to a facture")


class ForbiddenBillingDocumentError(BillingDomainError):
    """Raised when a user attempts to access a billing document they do not own."""

    def __init__(self, document_id: UUID) -> None:
        self.document_id = document_id
        super().__init__(f"Access denied to billing document {document_id}")


class ForbiddenProjectAccessError(BillingDomainError):
    """Raised when a user links a billing document to a project they cannot read."""

    def __init__(self, project_id: UUID) -> None:
        self.project_id = project_id
        super().__init__(f"User does not have project:read access on project {project_id}")


class BillingTemplateNameConflictError(BillingDomainError):
    """Raised when a template with the same (user_id, kind, name) already exists."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"A template named {name!r} already exists for this kind")


class BillingDocumentAlreadyExistsError(BillingDomainError):
    """Raised when importing a document with a (company_id, kind, document_number) that already exists."""

    def __init__(self, company_id: UUID, kind: str, document_number: str) -> None:
        self.company_id = company_id
        self.kind = kind
        self.document_number = document_number
        super().__init__(
            f"Billing document {document_number!r} of kind {kind!r} already exists " f"for company {company_id}"
        )


class CompanyNotAttachedError(BillingDomainError):
    """Raised when a user submits a billing document for a company they are no longer attached to.

    Distinct from MissingCompanyProfileError (which was the old no-profile case).
    Used for the 409 'company_no_longer_attached' race-condition guard.
    """

    def __init__(self, user_id: UUID, company_id: UUID) -> None:
        self.user_id = user_id
        self.company_id = company_id
        super().__init__(
            f"User {user_id} is no longer attached to company {company_id}. "
            "Re-attach before creating billing documents."
        )
