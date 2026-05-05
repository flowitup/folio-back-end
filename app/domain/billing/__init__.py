"""Public API for the billing bounded context domain layer.

Import surface used by application and infrastructure layers.
Pure Python — no Flask, no SQLAlchemy.
"""

from app.domain.billing.company_profile import CompanyProfile
from app.domain.billing.document import BillingDocument
from app.domain.billing.enums import BillingDocumentKind, BillingDocumentStatus
from app.domain.billing.exceptions import (
    BillingDomainError,
    BillingDocumentNotFoundError,
    BillingNumberCollisionError,
    BillingTemplateNotFoundError,
    DevisAlreadyConvertedError,
    ForbiddenBillingDocumentError,
    InvalidStatusTransitionError,
    MissingCompanyProfileError,
)
from app.domain.billing.numbering import kind_to_token, next_document_number
from app.domain.billing.status import validate_status_transition
from app.domain.billing.template import BillingDocumentTemplate
from app.domain.billing.totals import compute_totals, vat_breakdown
from app.domain.billing.value_objects import BillingDocumentItem, DocumentTotals

__all__ = [
    # entities
    "BillingDocument",
    "BillingDocumentTemplate",
    "CompanyProfile",
    # value objects
    "BillingDocumentItem",
    "DocumentTotals",
    # enums
    "BillingDocumentKind",
    "BillingDocumentStatus",
    # domain functions
    "compute_totals",
    "vat_breakdown",
    "validate_status_transition",
    "next_document_number",
    "kind_to_token",
    # exceptions
    "BillingDomainError",
    "InvalidStatusTransitionError",
    "MissingCompanyProfileError",
    "BillingDocumentNotFoundError",
    "BillingTemplateNotFoundError",
    "BillingNumberCollisionError",
    "DevisAlreadyConvertedError",
    "ForbiddenBillingDocumentError",
]
