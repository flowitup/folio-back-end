"""Data Transfer Objects for admin use-case results.

All DTOs are frozen dataclasses so callers cannot mutate them after creation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional
from uuid import UUID


class BulkAddStatus(Enum):
    """Result status for a single project in a bulk-add operation."""

    ADDED = "added"
    ALREADY_MEMBER_SAME_ROLE = "already_member_same_role"
    ALREADY_MEMBER_DIFFERENT_ROLE = "already_member_different_role"
    PROJECT_NOT_FOUND = "project_not_found"


@dataclass(frozen=True)
class BulkAddResultItemDto:
    """Single per-project result in a bulk-add response.

    project_name is None when status is PROJECT_NOT_FOUND (project does not exist).
    """

    project_id: UUID
    project_name: Optional[str]
    status: BulkAddStatus


@dataclass(frozen=True)
class BulkAddResultDto:
    """Aggregate result of BulkAddExistingUserUseCase.execute()."""

    results: list[BulkAddResultItemDto]
