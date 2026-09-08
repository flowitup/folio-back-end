"""Pydantic v2 request/response schemas for the D8 member-grants API.

Strict mode (extra='forbid') enforced on all request schemas, matching
`app.api.v1.companies.schemas`. `permission` is a plain string here (not a
Literal over `CUSTOMISABLE_PERMISSIONS`) so an out-of-whitelist value reaches
`ManageGrantsUseCase` and comes back as a business-rule 400
(`InvalidGrantPermissionError`), not a generic 422 schema-validation error.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field

PermissionEffect = Literal["grant", "deny"]


class _StrictBase(BaseModel):
    model_config = {"extra": "forbid"}


class SetMemberGrantRequest(_StrictBase):
    """Request body for PUT /companies/<company_id>/members/<user_id>/grants."""

    permission: str = Field(..., min_length=1, max_length=64)
    effect: PermissionEffect
    project_id: Optional[UUID] = None


class RemoveMemberGrantRequest(_StrictBase):
    """Request body for DELETE /companies/<company_id>/members/<user_id>/grants."""

    permission: str = Field(..., min_length=1, max_length=64)
    project_id: Optional[UUID] = None


class MemberGrantRow(BaseModel):
    """One grant/deny row in the member-grants API responses."""

    permission: str
    effect: PermissionEffect
    project_id: Optional[UUID]
    granted_at: datetime


class MemberGrantsListResponse(BaseModel):
    """Response body for GET /companies/<company_id>/members/<user_id>/grants."""

    grants: List[MemberGrantRow]
    customisable: List[str]
