"""Labor use cases and ports."""

from app.application.labor.ports import (
    IWorkerRepository,
    ILaborEntryRepository,
    LaborSummaryRow,
    MonthlyLaborSummaryRow,
)
from app.application.labor.create_worker import (
    CreateWorkerUseCase,
    CreateWorkerRequest,
    CreateWorkerResponse,
)
from app.application.labor.update_worker import (
    UpdateWorkerUseCase,
    UpdateWorkerRequest,
    UpdateWorkerResponse,
)
from app.application.labor.delete_worker import (
    DeleteWorkerUseCase,
    DeleteWorkerRequest,
)
from app.application.labor.list_workers import (
    ListWorkersUseCase,
    ListWorkersRequest,
    WorkerSummary,
)
from app.application.labor.log_attendance import (
    LogAttendanceUseCase,
    LogAttendanceRequest,
    LogAttendanceResponse,
)
from app.application.labor.update_attendance import (
    UpdateAttendanceUseCase,
    UpdateAttendanceRequest,
    UpdateAttendanceResponse,
)
from app.application.labor.delete_attendance import (
    DeleteAttendanceUseCase,
    DeleteAttendanceRequest,
)
from app.application.labor.list_labor_entries import (
    ListLaborEntriesUseCase,
    ListLaborEntriesRequest,
    LaborEntryDetail,
)
from app.application.labor.get_labor_summary import (
    GetLaborSummaryUseCase,
    GetLaborSummaryRequest,
    LaborSummaryResponse,
    WorkerCostSummary,
)
from app.application.labor.get_monthly_labor_summary import (
    GetMonthlyLaborSummaryUseCase,
    GetMonthlyLaborSummaryRequest,
    LaborMonthlySummaryResponse,
    MonthlySummaryRow,
)

__all__ = [
    # Ports
    "IWorkerRepository",
    "ILaborEntryRepository",
    "LaborSummaryRow",
    "MonthlyLaborSummaryRow",
    # Worker use cases
    "CreateWorkerUseCase",
    "CreateWorkerRequest",
    "CreateWorkerResponse",
    "UpdateWorkerUseCase",
    "UpdateWorkerRequest",
    "UpdateWorkerResponse",
    "DeleteWorkerUseCase",
    "DeleteWorkerRequest",
    "ListWorkersUseCase",
    "ListWorkersRequest",
    "WorkerSummary",
    # Attendance use cases
    "LogAttendanceUseCase",
    "LogAttendanceRequest",
    "LogAttendanceResponse",
    "UpdateAttendanceUseCase",
    "UpdateAttendanceRequest",
    "UpdateAttendanceResponse",
    "DeleteAttendanceUseCase",
    "DeleteAttendanceRequest",
    "ListLaborEntriesUseCase",
    "ListLaborEntriesRequest",
    "LaborEntryDetail",
    # Summary
    "GetLaborSummaryUseCase",
    "GetLaborSummaryRequest",
    "LaborSummaryResponse",
    "WorkerCostSummary",
    "GetMonthlyLaborSummaryUseCase",
    "GetMonthlyLaborSummaryRequest",
    "LaborMonthlySummaryResponse",
    "MonthlySummaryRow",
]
