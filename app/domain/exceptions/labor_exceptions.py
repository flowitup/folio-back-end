"""Labor domain exceptions."""


class LaborError(Exception):
    """Base exception for labor domain errors."""

    pass


class WorkerNotFoundError(LaborError):
    """Raised when worker does not exist."""

    def __init__(self, worker_id: str):
        self.worker_id = worker_id
        super().__init__(f"Worker not found: {worker_id}")


class LaborEntryNotFoundError(LaborError):
    """Raised when labor entry does not exist."""

    def __init__(self, entry_id: str):
        self.entry_id = entry_id
        super().__init__(f"Labor entry not found: {entry_id}")


class DuplicateEntryError(LaborError):
    """Raised when a labor entry already exists for worker on date."""

    def __init__(self, worker_id: str, date: str):
        self.worker_id = worker_id
        self.date = date
        super().__init__(f"Entry already exists for worker {worker_id} on {date}")


class InvalidWorkerDataError(LaborError):
    """Raised when worker data validation fails."""

    def __init__(self, message: str):
        super().__init__(message)
