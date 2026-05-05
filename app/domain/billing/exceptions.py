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
