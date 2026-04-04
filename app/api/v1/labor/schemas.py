"""Labor API schemas."""

from pydantic import BaseModel, Field
from typing import Optional, List


# Request schemas
class CreateWorkerRequest(BaseModel):
    """Request body for creating a worker."""
    name: str = Field(..., min_length=1, max_length=255)
    daily_rate: float = Field(..., gt=0)
    phone: Optional[str] = Field(None, max_length=50)


class UpdateWorkerRequest(BaseModel):
    """Request body for updating a worker."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    daily_rate: Optional[float] = Field(None, gt=0)
    phone: Optional[str] = Field(None, max_length=50)


class LogAttendanceRequest(BaseModel):
    """Request body for logging attendance."""
    worker_id: str = Field(...)
    date: str = Field(...)  # ISO date YYYY-MM-DD
    amount_override: Optional[float] = Field(None, ge=0)
    note: Optional[str] = Field(None, max_length=500)


class UpdateAttendanceRequest(BaseModel):
    """Request body for updating attendance."""
    amount_override: Optional[float] = Field(None, ge=0)
    note: Optional[str] = Field(None, max_length=500)


# Response schemas
class WorkerResponse(BaseModel):
    """Single worker response."""
    id: str
    project_id: str
    name: str
    phone: Optional[str]
    daily_rate: float
    is_active: bool
    created_at: str


class WorkerListResponse(BaseModel):
    """List of workers response."""
    workers: List[WorkerResponse]
    total: int


class LaborEntryResponse(BaseModel):
    """Single labor entry response."""
    id: str
    worker_id: str
    worker_name: str
    date: str
    amount_override: Optional[float]
    effective_cost: float
    note: Optional[str]
    created_at: str


class LaborEntryListResponse(BaseModel):
    """List of labor entries response."""
    entries: List[LaborEntryResponse]
    total: int


class WorkerSummaryRow(BaseModel):
    """Worker summary row in labor summary."""
    worker_id: str
    worker_name: str
    days_worked: int
    total_cost: float


class LaborSummaryResponse(BaseModel):
    """Labor summary response."""
    rows: List[WorkerSummaryRow]
    total_days: int
    total_cost: float


class ErrorResponse(BaseModel):
    """Error response format."""
    error: str
    message: str
    status_code: int
