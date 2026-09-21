"""Persistence port for ``assistant_jobs`` (feature B — invoice fetch via the browser
worker container).

Kept out of ``app.application.assistant.ports`` (the phase-02 provider-facing module)
and ``import_ports.py`` (phase-03's invoice/material AI-import extension tables) since
this one is polled by a *different process* (``app.infrastructure.browser_worker``,
which never boots the Flask app — see that package's docstring) as well as by the web
process's ``InvoiceFetchFeature``/``process_fetched_invoice`` RQ job. Both sides talk to
the same table through ``SqlAlchemyAssistantJobRepository``, constructed either from
``db.session`` (web/worker processes) or a bare ``sessionmaker(bind=engine)()`` (the
browser container, which has no Flask app context at all).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional, Protocol
from uuid import UUID

#: Every state a job can be in. "queued" also covers a `not_ready` retry waiting for its
#: `run_after` — the state machine folds `not_ready` back into `queued` (see gate.py /
#: `features/invoice_fetch.py::_apply_not_ready`) rather than adding a distinct polled
#: state, so the browser worker's claim query only ever needs to filter on `queued`.
JOB_STATUSES: tuple[str, ...] = ("queued", "running", "not_ready", "blocked", "done", "failed", "not_found")

#: Jobs the browser worker is still actively working towards a result for — used by the
#: dedupe window (`find_duplicate`): a brand new request that matches one of these should
#: be told to wait rather than spawning a second job for the same purchase.
ACTIVE_JOB_STATUSES: tuple[str, ...] = ("queued", "running", "not_ready")


@dataclass(frozen=True)
class AssistantJobRecord:
    """One row of ``assistant_jobs``."""

    id: UUID
    type: str
    user_id: UUID
    merchant: str
    amount_ttc: Decimal
    date: date
    project_hint: Optional[str]
    status: str
    attempts: int
    run_after: datetime
    result: Optional[dict[str, Any]]
    pdf_storage_key: Optional[str]
    status_message_id: Optional[UUID]
    created_at: datetime
    updated_at: datetime


class AssistantJobRepositoryPort(Protocol):
    """Persistence contract for ``assistant_jobs``."""

    def add(
        self,
        *,
        user_id: UUID,
        merchant: str,
        amount_ttc: Decimal,
        date: date,
        project_hint: Optional[str],
        status_message_id: Optional[UUID] = None,
    ) -> AssistantJobRecord:
        """Insert a new ``fetch_invoice`` job, ``status="queued"``, ``run_after=now``."""
        ...

    def find_by_id(self, job_id: UUID) -> Optional[AssistantJobRecord]:
        """Return the job, or None."""
        ...

    def find_duplicate(
        self, *, user_id: UUID, merchant: str, amount_ttc: Decimal, date: date, since: datetime
    ) -> Optional[AssistantJobRecord]:
        """An active job (see ``ACTIVE_JOB_STATUSES``) for the same user/merchant/amount/
        date created at or after ``since`` — the 24h dedupe window."""
        ...

    def set_status_message(self, job_id: UUID, status_message_id: UUID) -> None:
        """Attach the ``job_status`` chat message id once it exists (the message itself
        embeds the job's id in its payload, so it can only be created after ``add()``)."""
        ...

    def claim_next(self, now: datetime) -> Optional[AssistantJobRecord]:
        """Atomically claim the oldest ``queued`` job whose ``run_after <= now``.

        Marks it ``running`` and returns it, or None when no job is claimable. Uses
        ``SELECT ... FOR UPDATE SKIP LOCKED`` on Postgres so exactly one poller ever
        claims a given row even under concurrent callers; falls back to a plain SELECT
        on backends that do not support it (SQLite, in tests).
        """
        ...

    def update_status(
        self, job_id: UUID, *, status: str, attempts: Optional[int] = None, run_after: Optional[datetime] = None
    ) -> None:
        """Transition a job's ``status`` (and optionally ``attempts``/``run_after`` —
        the ``not_ready`` retry-backoff path)."""
        ...

    def update_result(
        self,
        job_id: UUID,
        *,
        status: str,
        result: Optional[dict[str, Any]] = None,
        pdf_storage_key: Optional[str] = None,
    ) -> None:
        """Record a terminal (or near-terminal) outcome: ``result`` is the
        ``FetchResult``-shaped dict the browser worker reported."""
        ...

    def list_recent_for_user(self, user_id: UUID, limit: int = 10) -> list[AssistantJobRecord]:
        """Most recent jobs for a user, newest first."""
        ...


__all__ = ["AssistantJobRecord", "AssistantJobRepositoryPort", "JOB_STATUSES", "ACTIVE_JOB_STATUSES"]
