"""Project API schemas."""

from pydantic import BaseModel, Field
from typing import Optional, List


class CreateProjectRequest(BaseModel):
    """Request body for creating a project."""

    name: str = Field(..., min_length=1, max_length=255)
    address: Optional[str] = Field(None, max_length=500)


class UpdateProjectRequest(BaseModel):
    """Request body for updating a project."""

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    address: Optional[str] = Field(None, max_length=500)
    invoice_prefix: Optional[str] = Field(None, max_length=8)


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
