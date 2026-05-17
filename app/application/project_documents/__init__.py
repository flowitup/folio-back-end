"""Project documents application layer — public API re-exports."""

from app.application.project_documents.ports import (
    IDocumentStorage,
    IFilenameSanitizer,
    IProjectDocumentRepository,
    ITransactionalSession,
)
from app.application.project_documents.dtos import (
    ListFiltersDTO,
    ListResultDTO,
    PurgeFailureDTO,
    PurgeResultDTO,
)
from app.application.project_documents.exceptions import (
    DocumentFileTooLargeError,
    EmptyFileError,
    UnsupportedDocumentTypeError,
    DocumentPermissionDeniedError,
)
from app.application.project_documents.upload_project_document import (
    UploadProjectDocumentUseCase,
    MAX_SIZE_BYTES,
    ALLOWED_EXTENSIONS,
    ALLOWED_MIME_TYPES,
    validate_file_type,
)
from app.application.project_documents.list_project_documents import ListProjectDocumentsUseCase
from app.application.project_documents.get_project_document import GetProjectDocumentUseCase
from app.application.project_documents.delete_project_document import DeleteProjectDocumentUseCase
from app.application.project_documents.purge_soft_deleted_documents import (
    PurgeSoftDeletedDocumentsUseCase,
)
from app.domain.exceptions.project_document_exceptions import ProjectDocumentNotFoundError

__all__ = [
    # Ports
    "IDocumentStorage",
    "IFilenameSanitizer",
    "IProjectDocumentRepository",
    "ITransactionalSession",
    # DTOs
    "ListFiltersDTO",
    "ListResultDTO",
    "PurgeFailureDTO",
    "PurgeResultDTO",
    # Use cases
    "UploadProjectDocumentUseCase",
    "ListProjectDocumentsUseCase",
    "GetProjectDocumentUseCase",
    "DeleteProjectDocumentUseCase",
    "PurgeSoftDeletedDocumentsUseCase",
    # Exceptions
    "ProjectDocumentNotFoundError",
    "DocumentFileTooLargeError",
    "EmptyFileError",
    "UnsupportedDocumentTypeError",
    "DocumentPermissionDeniedError",
    # Upload constants / helpers (useful for Flask layer validation)
    "MAX_SIZE_BYTES",
    "ALLOWED_EXTENSIONS",
    "ALLOWED_MIME_TYPES",
    "validate_file_type",
]
