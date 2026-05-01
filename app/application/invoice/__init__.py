"""Invoice use cases and ports."""

from app.application.invoice.ports import IAttachmentStorage, IInvoiceAttachmentRepository, IInvoiceRepository
from app.application.invoice.dtos import InvoiceItemResponse, InvoiceResponse
from app.application.invoice.create_invoice import CreateInvoiceUseCase, CreateInvoiceRequest
from app.application.invoice.list_invoices import ListInvoicesUseCase, ListInvoicesRequest
from app.application.invoice.get_invoice import GetInvoiceUseCase
from app.application.invoice.update_invoice import UpdateInvoiceUseCase, UpdateInvoiceRequest
from app.application.invoice.delete_invoice import DeleteInvoiceUseCase
from app.application.invoice.upload_attachment import (
    UploadAttachmentUseCase,
    FileTooLargeError,
    UnsupportedFileTypeError,
    MAX_FILE_SIZE_BYTES,
    ALLOWED_MIME_TYPES,
)
from app.application.invoice.manage_attachments import (
    ListAttachmentsUseCase,
    GetAttachmentUseCase,
    DeleteAttachmentUseCase,
    AttachmentNotFoundError,
)
from app.application.invoice.export_invoices_usecase import (
    ExportInvoicesUseCase,
    ExportInvoicesRequest,
    ExportInvoicesResult,
)

__all__ = [
    # Ports
    "IInvoiceRepository",
    "IAttachmentStorage",
    "IInvoiceAttachmentRepository",
    # DTOs
    "InvoiceItemResponse",
    "InvoiceResponse",
    # Invoice use cases
    "CreateInvoiceUseCase",
    "CreateInvoiceRequest",
    "ListInvoicesUseCase",
    "ListInvoicesRequest",
    "GetInvoiceUseCase",
    "UpdateInvoiceUseCase",
    "UpdateInvoiceRequest",
    "DeleteInvoiceUseCase",
    # Attachment use cases
    "UploadAttachmentUseCase",
    "ListAttachmentsUseCase",
    "GetAttachmentUseCase",
    "DeleteAttachmentUseCase",
    # Export use case
    "ExportInvoicesUseCase",
    "ExportInvoicesRequest",
    "ExportInvoicesResult",
    # Errors and constants
    "FileTooLargeError",
    "UnsupportedFileTypeError",
    "AttachmentNotFoundError",
    "MAX_FILE_SIZE_BYTES",
    "ALLOWED_MIME_TYPES",
]
