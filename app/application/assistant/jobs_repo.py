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


#: Every ``assistant_jobs.type`` value this repository is asked to persist.
JOB_TYPES: tuple[str, ...] = ("fetch_invoice", "find_product")


@dataclass(frozen=True)
class AssistantJobRecord:
    """One row of ``assistant_jobs``.

    ``merchant``/``amount_ttc``/``date`` are ``fetch_invoice``-only (always set for that
    type, always ``None`` for ``find_product``); ``params`` is the reverse — ``None`` for
    ``fetch_invoice``, and for ``find_product`` holds the ``MaterialIdent`` dump, its
    ``search_queries``, ``company_id``, ``photo_sha256`` and ``message_id`` (see
    ``app.application.assistant.features.material``).
    """

    id: UUID
    type: str
    user_id: UUID
    merchant: Optional[str]
    amount_ttc: Optional[Decimal]
    date: Optional[date]
    project_hint: Optional[str]
    status: str
    attempts: int
    run_after: datetime
    result: Optional[dict[str, Any]]
    pdf_storage_key: Optional[str]
    status_message_id: Optional[UUID]
    lang: Optional[str]
    processed_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    params: Optional[dict[str, Any]] = None


class AssistantJobRepositoryPort(Protocol):
    """Persistence contract for ``assistant_jobs``."""

    def add(
        self,
        *,
        user_id: UUID,
        job_type: str = "fetch_invoice",
        merchant: Optional[str] = None,
        amount_ttc: Optional[Decimal] = None,
        date: Optional[date] = None,
        project_hint: Optional[str] = None,
        lang: Optional[str] = None,
        params: Optional[dict[str, Any]] = None,
        status_message_id: Optional[UUID] = None,
    ) -> AssistantJobRecord:
        """Insert a new job, ``status="queued"``, ``run_after=now``.

        ``job_type="fetch_invoice"`` (the default) requires ``merchant``/``amount_ttc``/
        ``date``; ``job_type="find_product"`` requires ``params`` instead (see
        ``AssistantJobRecord``'s docstring) and leaves the other three ``None``.
        """
        ...

    def find_by_id(self, job_id: UUID) -> Optional[AssistantJobRecord]:
        """Return the job, or None."""
        ...

    def find_duplicate(
        self,
        *,
        user_id: UUID,
        since: datetime,
        job_type: str = "fetch_invoice",
        merchant: Optional[str] = None,
        amount_ttc: Optional[Decimal] = None,
        date: Optional[date] = None,
        photo_sha256: Optional[str] = None,
    ) -> Optional[AssistantJobRecord]:
        """An active job (see ``ACTIVE_JOB_STATUSES``) created at or after ``since`` that
        matches this request's dedupe key — the 24h dedupe window.

        ``job_type="fetch_invoice"`` matches on ``merchant``/``amount_ttc``/``date``;
        ``job_type="find_product"`` matches on ``photo_sha256`` (read out of ``params``).
        """
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

    def mark_processed(self, job_id: UUID) -> bool:
        """Atomically set ``processed_at`` when it is still NULL.

        Returns True the first time (the caller should proceed with its one-time write),
        False on every subsequent call for the same job (the caller must skip its write
        — review finding H3, guards ``on_result``'s "done" -> create-invoice path against
        running twice for the same job).
        """
        ...

    def reap_unprocessed(self, now: datetime) -> list[UUID]:
        """Ids of terminal-status jobs (``done``/``not_ready``/``not_found``/``blocked``/
        ``failed``) whose ``processed_at`` is still NULL and whose ``updated_at`` is older
        than ``_UNPROCESSED_REAP_AFTER`` — review finding NEW-H2: the browser worker's
        ``update_result`` (terminal write, commits) and its ``queue.enqueue(...
        process_fetched_invoice)`` are two separate steps; if the process dies or Redis
        blips between them, the row is permanently `done`/`not_ready`/etc with nothing
        left to ever call ``on_result`` for it — every ``mark_processed`` guard in
        ``InvoiceFetchFeature`` exists so this method's caller can safely re-enqueue
        ``process_fetched_invoice`` for a returned id without risking a duplicate reply
        or a duplicate invoice.

        Touches ``updated_at`` on every id it returns, so a returned job is not returned
        again for another full window even if the immediate re-enqueue also fails.
        """
        ...


__all__ = ["AssistantJobRecord", "AssistantJobRepositoryPort", "JOB_STATUSES", "ACTIVE_JOB_STATUSES", "JOB_TYPES"]
