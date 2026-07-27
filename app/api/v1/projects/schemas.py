"""Project API schemas."""

from decimal import Decimal
from typing import Dict, List, Optional

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
    # Computed spend: labor accrued from attendance + invoice totals, excluding
    # released_funds and labor invoices (the latter settle the accrual, they are not
    # extra cost). Refunds net down.
    spent: float = 0
    # Share of spend funded with company money (the credit line). Same rule as the
    # Expense page's "spent by company" KPI, so the two always agree.
    spent_by_credits: float = 0
    # Share funded out of pocket. spent_by_credits + spent_personal + labor_unpaid == spent.
    spent_personal: float = 0
    # Labor accrued from attendance entries, settled by labor-type invoices.
    # labor_unpaid is owed to workers — not spent by anyone yet, so it sits outside
    # both spent_by_credits and spent_personal.
    labor_accrued: float = 0
    labor_paid: float = 0
    labor_unpaid: float = 0
    # Personal spend broken down by invoice type; values sum to spent_personal.
    personal_by_type: Dict[str, float] = {}


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
