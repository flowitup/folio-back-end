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


class AddUserRequest(BaseModel):
    """Request body for adding user to project."""
    user_id: str = Field(..., description="UUID of user to add")


class ProjectResponse(BaseModel):
    """Single project response."""
    id: str
    name: str
    address: Optional[str]
    owner_id: str
    user_count: int
    created_at: str


class ProjectListResponse(BaseModel):
    """List of projects response."""
    projects: List[ProjectResponse]
    total: int


class ErrorResponse(BaseModel):
    """Error response format."""
    error: str
    message: str
    status_code: int
