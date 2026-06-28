"""Project API schemas."""

from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, Field


class CreateProjectRequest(BaseModel):
    """Request body for creating a project."""

    name: str = Field(..., min_length=1, max_length=255)
    address: Optional[str] = Field(None, max_length=500)
    budget: Optional[Decimal] = Field(None, ge=0)
    budget_source: Optional[str] = Field(None, max_length=120)


class UpdateProjectRequest(BaseModel):
    """Request body for updating a project.

    Uses model_fields_set to distinguish "field omitted" (no-op) from
    "field explicitly set to null" (clear the value). This prevents a
    PATCH of only budget_source from accidentally wiping budget.
    """

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    address: Optional[str] = Field(None, max_length=500)
    invoice_prefix: Optional[str] = Field(None, max_length=8)
    budget: Optional[Decimal] = Field(None, ge=0)
    budget_source: Optional[str] = Field(None, max_length=120)


class ProjectResponse(BaseModel):
    """Single project response."""

    id: str
    name: str
    address: Optional[str]
    owner_id: str
    user_count: int
    created_at: str
    company_id: Optional[str] = None
    invoice_prefix: Optional[str] = None
    # Caller's EFFECTIVE permissions on this project: global-role perms UNION the
    # caller's membership-role perms for this project. Lets the frontend gate
    # per-project UI (e.g. "log labor") on the project role, not just the global role.
    my_permissions: List[str] = []
    # Budget tracking — None means no budget set.
    budget: Optional[float] = None
    budget_source: Optional[str] = None
    # Computed spend: labor cost + non-released_funds invoice totals (refunds net down).
    spent: float = 0


class ProjectListResponse(BaseModel):
    """List of projects response."""

    projects: List[ProjectResponse]
    total: int


class ErrorResponse(BaseModel):
    """Error response format."""

    error: str
    message: str
    status_code: int


class ProjectUserResponse(BaseModel):
    """User associated with a project."""

    id: str
    email: str


class ProjectUsersListResponse(BaseModel):
    """List of users for a project."""

    users: List[ProjectUserResponse]
    total: int
