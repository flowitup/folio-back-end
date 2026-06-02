"""Pydantic v2 schemas for admin API endpoints (bulk-add memberships + user search)."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class BulkAddRequest(BaseModel):
    """POST /admin/users/<user_id>/memberships request body."""

    project_ids: list[UUID] = Field(min_length=1, max_length=50)
    role_id: UUID


class BulkAddResultItem(BaseModel):
    """Per-project result entry in a bulk-add response."""

    project_id: UUID
    project_name: str | None
    status: Literal[
        "added",
        "already_member_same_role",
        "already_member_different_role",
        "project_not_found",
    ]


class BulkAddResponse(BaseModel):
    """POST /admin/users/<user_id>/memberships response body."""

    results: list[BulkAddResultItem]


class UpdateUserRequest(BaseModel):
    """PATCH /admin/users/<user_id> request body. All fields optional; at least one required.

    ``email`` is the login identity — changing it is superadmin-only and must stay unique.
    ``display_name`` may be set to null/empty to clear it.
    """

    email: EmailStr | None = None
    display_name: str | None = Field(default=None, max_length=255)


class UserSearchItem(BaseModel):
    """Single user entry in user-search response. Excludes sensitive fields."""

    id: UUID
    email: EmailStr
    display_name: str | None


class UserSearchResponse(BaseModel):
    """GET /admin/users?search=<q> response body."""

    items: list[UserSearchItem]
    count: int
