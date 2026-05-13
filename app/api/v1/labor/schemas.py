"""Labor API schemas."""

from pydantic import BaseModel, Field, model_validator
from typing import Literal, Optional, List

# Shift type constraint shared by request and response schemas.
ShiftTypeLiteral = Literal["full", "half", "overtime"]


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class CreateWorkerRequest(BaseModel):
    """Request body for creating a worker.

    ``person_id`` (cook 1d-ii-b) lets the caller link this Worker to an
    existing Person picked via the PersonTypeahead. When omitted, the
    legacy flow runs: name + phone create a fresh Person via the
    CreateWorkerUseCase before linking. Either path produces a Worker
    with a non-null person_id once Phase 1c backfill has completed.
    """

    name: str = Field(..., min_length=1, max_length=255)
    daily_rate: float = Field(..., gt=0)
    phone: Optional[str] = Field(None, max_length=50)
    person_id: Optional[str] = Field(None, min_length=36, max_length=36)


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


class BulkLogAttendanceEntry(BaseModel):
    """One row inside a bulk-log request body."""

    worker_id: str = Field(...)
    amount_override: Optional[float] = Field(None, ge=0)
    note: Optional[str] = Field(None, max_length=500)
    shift_type: Optional[ShiftTypeLiteral] = None
    supplement_hours: int = Field(default=0, ge=0, le=12)

    @model_validator(mode="after")
    def _validate_non_empty_and_override_consistency(self) -> "BulkLogAttendanceEntry":
        if self.shift_type is None and self.supplement_hours == 0:
            raise ValueError("Empty entry: must set shift_type or supplement_hours > 0")
        if self.shift_type is None and self.amount_override is not None:
            raise ValueError("amount_override requires a shift_type")
        return self


class BulkLogAttendanceRequest(BaseModel):
    """Request body for the bulk-log endpoint.

    Single date + N entries, atomic. Cook 3a of phase-03. Cap at 50
    entries per request to keep the worst-case worker-validation
    O(N) loop bounded and to discourage abuse from a compromised JWT.
    """

    date: str = Field(...)  # ISO date YYYY-MM-DD
    entries: list[BulkLogAttendanceEntry] = Field(..., min_length=1, max_length=50)
    # Phase 4 — when True, the caller has seen the cross-project
    # conflict modal and chooses to proceed anyway. The server still
    # re-runs the check inside the transaction; if the flag is absent
    # and conflicts exist, the endpoint returns 409 with the conflict
    # payload so the FE can render its modal.
    acknowledge_conflicts: bool = False


class BulkLogAttendanceResponse(BaseModel):
    """Response body for the bulk-log endpoint."""

    created: list[str]
    skipped_worker_ids: list[str]


# ---------------------------------------------------------------------------
# Phase 4 — cross-project conflict warn
# ---------------------------------------------------------------------------


class CrossProjectConflictEntryResponse(BaseModel):
    """One other-project entry inside a conflict group."""

    project_id: str
    project_name: str
    shift_type: Optional[ShiftTypeLiteral] = None
    supplement_hours: int


class CrossProjectConflictResponse(BaseModel):
    """Conflict group: one Person who is logged in another project."""

    person_id: str
    person_name: str
    entries: list[CrossProjectConflictEntryResponse]


class CrossProjectConflictsResponse(BaseModel):
    """Wrapper response for GET /labor-entries/conflicts."""

    conflicts: list[CrossProjectConflictResponse]


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
    """Single worker response.

    person_id / person_name / person_phone surface the joined Person identity
    introduced by plan 260512-2341-labor-calendar-and-bulk-log (cook 1d-ii-a).
    They are additive — the legacy ``name`` and ``phone`` fields remain
    populated from the workers table for back-compat with FE callers that
    haven't migrated yet. A follow-up release tightens the contract to
    require person_id and ultimately drops the inline name/phone columns.
    """

    id: str
    project_id: str
    name: str
    phone: Optional[str]
    daily_rate: float
    is_active: bool
    created_at: str

    # Joined Person identity. Nullable during the Phase 1c backfill rollout
    # — once 100% of workers have person_id populated, a follow-up release
    # makes these non-optional.
    person_id: Optional[str] = None
    person_name: Optional[str] = None
    person_phone: Optional[str] = None


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


class MonthlyWorkerSubRowResponse(BaseModel):
    """One worker's contribution within a (year, month) bucket."""

    worker_id: str
    worker_name: str
    days_worked: int
    total_cost: float


class MonthlySummaryRowResponse(BaseModel):
    """One (year, month) bucket of project-wide labor totals.

    Carries the per-worker sub-rows inline so the FE can render them
    under each month header without a follow-up request.
    """

    year: int
    month: int
    total_days: int
    total_cost: float
    workers: List[MonthlyWorkerSubRowResponse]


class LaborMonthlySummaryResponse(BaseModel):
    """Per-month labor summary, ordered most-recent first."""

    rows: List[MonthlySummaryRowResponse]


class ExportLaborQuery(BaseModel):
    """Query-string schema for GET /projects/<id>/labor-export.

    Uses aliases so that ?from=YYYY-MM maps to from_month (Python keyword conflict).
    """

    from_month: str = Field(
        ...,
        alias="from",
        pattern=r"^(19|20|21)\d{2}-(0[1-9]|1[0-2])$",
        description="Start month, inclusive. Format: YYYY-MM",
    )
    to_month: str = Field(
        ...,
        alias="to",
        pattern=r"^(19|20|21)\d{2}-(0[1-9]|1[0-2])$",
        description="End month, inclusive. Format: YYYY-MM",
    )
    format: Literal["xlsx", "pdf"] = Field(..., description="Export format")

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def _validate_range(self) -> "ExportLaborQuery":
        from datetime import datetime as _dt

        from_d = _dt.strptime(self.from_month, "%Y-%m").date().replace(day=1)
        to_d = _dt.strptime(self.to_month, "%Y-%m").date().replace(day=1)
        if from_d > to_d:
            raise ValueError("'from' must be <= 'to'")
        span = (to_d.year - from_d.year) * 12 + (to_d.month - from_d.month) + 1
        if span > 24:
            raise ValueError("range must be <= 24 months")
        return self


class ErrorResponse(BaseModel):
    """Error response format."""

    error: str
    message: str
    status_code: int
