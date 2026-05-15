"""Domain exceptions for the payment_methods bounded context.

All exceptions inherit from ``PaymentMethodError`` which itself inherits
from plain ``Exception`` (no shared DomainError base exists in this codebase;
mirroring the pattern used in ``app/domain/companies/exceptions.py``).
"""

from uuid import UUID


class PaymentMethodError(Exception):
    """Base class for all payment method domain errors."""


class PaymentMethodNotFoundError(PaymentMethodError):
    """Raised when a payment method cannot be found by its ID.

    Maps to HTTP 404 at the API boundary.
    """

    def __init__(self, payment_method_id: UUID) -> None:
        self.payment_method_id = payment_method_id
        super().__init__(f"Payment method {payment_method_id} not found")


class PaymentMethodAlreadyExistsError(PaymentMethodError):
    """Raised when a label already exists (case-insensitive) among active methods.

    The uniqueness constraint is per-company and applies only to active rows
    (partial unique index ``WHERE is_active = true``).

    Maps to HTTP 409 at the API boundary.
    """

    def __init__(self, company_id: UUID, label: str) -> None:
        self.company_id = company_id
        self.label = label
        super().__init__(f"An active payment method with label {label!r} already exists " f"for company {company_id}")


class BuiltinPaymentMethodDeletionError(PaymentMethodError):
    """Raised when a caller attempts to delete a builtin payment method.

    Builtin methods (Cash, company legal name) are seeded automatically and
    must not be removed. Renaming is still permitted.

    Maps to HTTP 409 at the API boundary.
    """

    def __init__(self, payment_method_id: UUID) -> None:
        self.payment_method_id = payment_method_id
        super().__init__(f"Payment method {payment_method_id} is a builtin method and cannot be deleted")


class PaymentMethodNotActiveError(PaymentMethodError):
    """Raised when an invoice references a soft-deleted payment method.

    A method with ``is_active = False`` cannot be assigned to new or updated
    invoices. The snapshot label on existing invoices is unaffected.

    Maps to HTTP 409 at the API boundary.
    """

    def __init__(self, payment_method_id: UUID) -> None:
        self.payment_method_id = payment_method_id
        super().__init__(f"Payment method {payment_method_id} is inactive and cannot be referenced")
