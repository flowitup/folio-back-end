"""Admin application layer — use-cases, DTOs, and exceptions for superadmin operations."""

from app.application.admin.bulk_add_existing_user_usecase import BulkAddExistingUserUseCase
from app.application.admin.dtos import BulkAddResultDto, BulkAddResultItemDto, BulkAddStatus
from app.application.admin.exceptions import (
    EmptyProjectListError,
    PermissionDeniedError,
    RoleNotAllowedError,
    RoleNotFoundError,
    TargetUserNotFoundError,
    TooManyProjectsError,
)

__all__ = [
    # Use-case
    "BulkAddExistingUserUseCase",
    # DTOs
    "BulkAddResultDto",
    "BulkAddResultItemDto",
    "BulkAddStatus",
    # Exceptions
    "EmptyProjectListError",
    "PermissionDeniedError",
    "RoleNotAllowedError",
    "RoleNotFoundError",
    "TargetUserNotFoundError",
    "TooManyProjectsError",
]
