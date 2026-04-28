"""Labor API schemas."""

from pydantic import BaseModel, Field, model_validator
from typing import Literal, Optional, List

# Shift type constraint shared by request and response schemas.
ShiftTypeLiteral = Literal["full", "half", "overtime"]


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


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
    """Request body for logging attendance.

    Valid combos:
    - shift_type set (supplement_hours may be 0 or >0)
    - shift_type None AND supplement_hours > 0  (supplement-only row)

    Invalid combos (422):
    - shift_type None AND supplement_hours == 0  (empty row)
    - shift_type None AND amount_override set    (override without shift)
    """

    worker_id: str = Field(...)
    date: str = Field(...)  # ISO date YYYY-MM-DD
    amount_override: Optional[float] = Field(None, ge=0)
    note: Optional[str] = Field(None, max_length=500)
    shift_type: Optional[ShiftTypeLiteral] = None
    supplement_hours: int = Field(default=0, ge=0, le=12)

    @model_validator(mode="after")
    def _validate_non_empty_and_override_consistency(self) -> "LogAttendanceRequest":
        if self.shift_type is None and self.supplement_hours == 0:
            raise ValueError("Empty entry: must set shift_type or supplement_hours > 0")
        if self.shift_type is None and self.amount_override is not None:
            raise ValueError("amount_override requires a shift_type")
        return self


class UpdateAttendanceRequest(BaseModel):
    """Request body for updating attendance.

    All fields are optional to allow partial updates.
    When shift_type is explicitly cleared (None) while amount_override is set,
    the validator rejects the combination.
    """

    amount_override: Optional[float] = Field(None, ge=0)
    note: Optional[str] = Field(None, max_length=500)
    shift_type: Optional[ShiftTypeLiteral] = None
    supplement_hours: Optional[int] = Field(None, ge=0, le=12)

    @model_validator(mode="after")
    def _validate_override_consistency(self) -> "UpdateAttendanceRequest":
        # Only reject when caller explicitly clears shift_type AND sets override
        if self.shift_type is None and self.amount_override is not None:
            raise ValueError("amount_override requires a shift_type")
        return self


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


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
    shift_type: Optional[ShiftTypeLiteral]
    supplement_hours: int
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
    banked_hours: int
    bonus_full_days: int
    bonus_half_days: int
    bonus_cost: float


class LaborSummaryResponse(BaseModel):
    """Labor summary response."""

    rows: List[WorkerSummaryRow]
    total_days: int
    total_cost: float
    total_banked_hours: int
    total_bonus_days: float
    total_bonus_cost: float


class ErrorResponse(BaseModel):
    """Error response format."""

    error: str
    message: str
    status_code: int
