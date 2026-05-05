"""Public API for the billing application layer.

Re-exports all use-case classes and key DTO types for import by
infrastructure (wiring.py) and API (blueprints) layers.

No Flask / SQLAlchemy / infrastructure imports here.
"""

# --- Use-cases: billing documents ---
from app.application.billing.create_billing_document_usecase import CreateBillingDocumentUseCase
from app.application.billing.clone_billing_document_usecase import CloneBillingDocumentUseCase
from app.application.billing.convert_devis_to_facture_usecase import ConvertDevisToFactureUseCase
from app.application.billing.update_billing_document_usecase import UpdateBillingDocumentUseCase
from app.application.billing.update_billing_document_status_usecase import UpdateBillingDocumentStatusUseCase
from app.application.billing.list_billing_documents_usecase import (
    ListBillingDocumentsUseCase,
    ListBillingDocumentsResult,
)
from app.application.billing.get_billing_document_usecase import GetBillingDocumentUseCase
from app.application.billing.delete_billing_document_usecase import DeleteBillingDocumentUseCase
from app.application.billing.render_billing_document_pdf_usecase import RenderBillingDocumentPdfUseCase, RenderPdfResult

# --- Use-cases: templates ---
from app.application.billing.create_template_usecase import CreateTemplateUseCase
from app.application.billing.update_template_usecase import UpdateTemplateUseCase
from app.application.billing.list_templates_usecase import ListTemplatesUseCase
from app.application.billing.get_template_usecase import GetTemplateUseCase
from app.application.billing.delete_template_usecase import DeleteTemplateUseCase
from app.application.billing.apply_template_to_create_document_usecase import ApplyTemplateToCreateDocumentUseCase

# --- Use-cases: company profile ---
from app.application.billing.get_company_profile_usecase import GetCompanyProfileUseCase
from app.application.billing.upsert_company_profile_usecase import UpsertCompanyProfileUseCase

# --- DTOs ---
from app.application.billing.dtos import (
    ItemInput,
    CreateBillingDocumentInput,
    UpdateBillingDocumentInput,
    CloneBillingDocumentInput,
    ConvertDevisToFactureInput,
    UpdateStatusInput,
    CreateTemplateInput,
    UpdateTemplateInput,
    ApplyTemplateInput,
    UpsertCompanyProfileInput,
    ItemResponse,
    BillingDocumentResponse,
    BillingTemplateResponse,
    CompanyProfileResponse,
)

# --- Ports ---
from app.application.billing.ports import (
    BillingDocumentRepositoryPort,
    BillingTemplateRepositoryPort,
    CompanyProfileRepositoryPort,
    BillingNumberCounterRepositoryPort,
    BillingDocumentPdfRendererPort,
    TransactionalSessionPort,
)

# --- Domain exceptions re-exported for convenience ---
from app.domain.billing.exceptions import (
    BillingDomainError,
    InvalidStatusTransitionError,
    MissingCompanyProfileError,
    BillingDocumentNotFoundError,
    BillingTemplateNotFoundError,
    BillingNumberCollisionError,
    DevisAlreadyConvertedError,
    ForbiddenBillingDocumentError,
    ForbiddenProjectAccessError,
    BillingTemplateNameConflictError,
)

__all__ = [
    # use-cases: documents
    "CreateBillingDocumentUseCase",
    "CloneBillingDocumentUseCase",
    "ConvertDevisToFactureUseCase",
    "UpdateBillingDocumentUseCase",
    "UpdateBillingDocumentStatusUseCase",
    "ListBillingDocumentsUseCase",
    "ListBillingDocumentsResult",
    "GetBillingDocumentUseCase",
    "DeleteBillingDocumentUseCase",
    "RenderBillingDocumentPdfUseCase",
    "RenderPdfResult",
    # use-cases: templates
    "CreateTemplateUseCase",
    "UpdateTemplateUseCase",
    "ListTemplatesUseCase",
    "GetTemplateUseCase",
    "DeleteTemplateUseCase",
    "ApplyTemplateToCreateDocumentUseCase",
    # use-cases: company profile
    "GetCompanyProfileUseCase",
    "UpsertCompanyProfileUseCase",
    # DTOs
    "ItemInput",
    "CreateBillingDocumentInput",
    "UpdateBillingDocumentInput",
    "CloneBillingDocumentInput",
    "ConvertDevisToFactureInput",
    "UpdateStatusInput",
    "CreateTemplateInput",
    "UpdateTemplateInput",
    "ApplyTemplateInput",
    "UpsertCompanyProfileInput",
    "ItemResponse",
    "BillingDocumentResponse",
    "BillingTemplateResponse",
    "CompanyProfileResponse",
    # ports
    "BillingDocumentRepositoryPort",
    "BillingTemplateRepositoryPort",
    "CompanyProfileRepositoryPort",
    "BillingNumberCounterRepositoryPort",
    "BillingDocumentPdfRendererPort",
    "TransactionalSessionPort",
    # exceptions
    "BillingDomainError",
    "InvalidStatusTransitionError",
    "MissingCompanyProfileError",
    "BillingDocumentNotFoundError",
    "BillingTemplateNotFoundError",
    "BillingNumberCollisionError",
    "DevisAlreadyConvertedError",
    "ForbiddenBillingDocumentError",
    "ForbiddenProjectAccessError",
    "BillingTemplateNameConflictError",
]
