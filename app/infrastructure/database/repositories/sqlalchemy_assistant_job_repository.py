"""SQLAlchemy repository for ``assistant_jobs`` (feature B — invoice fetch — and feature A
— browser-based product search).

Constructed from a plain ``sqlalchemy.orm.Session`` — either Flask-SQLAlchemy's
``db.session`` (web process, ``process_fetched_invoice``/``process_product_search`` RQ
jobs) or a bare
``sessionmaker(bind=create_engine(DATABASE_URL))()`` (the ``ai-browser`` container,
which has no Flask app context — see ``app.infrastructure.browser_worker``). Neither
``AssistantJobModel`` nor this module imports anything Flask-specific, which is what
makes that dual use possible.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from app.application.assistant.jobs_repo import ACTIVE_JOB_STATUSES, AssistantJobRecord
from app.infrastructure.database.models.assistant_job import AssistantJobModel

#: A `running` job whose row hasn't been touched in this long is presumed abandoned
#: (worker crash, unhandled exception before the terminal write — review finding H2):
#: `claim_next` reclaims it exactly like a `not_ready` retry rather than leaving the
#: user's job_status message on "running" forever.
_STUCK_RUNNING_AFTER = timedelta(minutes=30)

#: A `running` row stuck past `_STUCK_RUNNING_AFTER` this many times (i.e. reclaimed and
#: abandoned again) is swept to `failed` instead of being reclaimed once more — mirrors
#: `features/invoice_fetch.py`'s own `MAX_ATTEMPTS` retry cap for the `not_ready` backoff
#: path (kept as an independent constant here rather than imported: this repository is
#: also constructed from a bare `sessionmaker` with no Flask app, so it must never gain a
#: dependency on the much heavier feature-layer import chain).
_MAX_RECLAIM_ATTEMPTS = 3

#: The other half of H2 (review finding NEW-H2): a job whose worker-reported terminal
#: status was written by `update_result` but whose `process_fetched_invoice` enqueue
#: never happened (or never ran) is not `running` — `claim_next`'s reaper never sees it.
#: `reap_unprocessed` uses the same window on `processed_at IS NULL` instead.
_UNPROCESSED_REAP_AFTER = timedelta(minutes=30)

#: Every status the browser worker can write via `update_result` that `on_result` treats
#: as one-shot terminal-or-terminal-pending — see `InvoiceFetchFeature.on_result`. `queued`
#: and `running` are deliberately excluded: those are covered by `claim_next`'s own reaper.
_TERMINAL_UNPROCESSED_STATUSES = ("done", "not_ready", "not_found", "blocked", "failed")


def _to_entity(m: AssistantJobModel) -> AssistantJobRecord:
    return AssistantJobRecord(
        id=m.id,
        type=m.type,
        user_id=m.user_id,
        merchant=m.merchant,
        amount_ttc=Decimal(m.amount_ttc) if m.amount_ttc is not None else None,
        date=m.date,
        project_hint=m.project_hint,
        status=m.status,
        attempts=m.attempts,
        run_after=m.run_after,
        result=dict(m.result) if m.result else None,
        pdf_storage_key=m.pdf_storage_key,
        status_message_id=m.status_message_id,
        lang=m.lang,
        processed_at=m.processed_at,
        created_at=m.created_at,
        updated_at=m.updated_at,
        params=dict(m.params) if m.params else None,
        channel_key=m.channel_key,
    )


class SqlAlchemyAssistantJobRepository:
    """Implements ``AssistantJobRepositoryPort``."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(
        self,
        *,
        user_id: UUID,
        job_type: str = "fetch_invoice",
        merchant: Optional[str] = None,
        amount_ttc: Optional[Decimal] = None,
        date: Optional[Any] = None,
        project_hint: Optional[str] = None,
        lang: Optional[str] = None,
        params: Optional[dict[str, Any]] = None,
        status_message_id: Optional[UUID] = None,
        channel_key: Optional[str] = None,
    ) -> AssistantJobRecord:
        if job_type == "fetch_invoice" and (merchant is None or amount_ttc is None or date is None):
            raise ValueError("fetch_invoice jobs require merchant, amount_ttc and date.")
        if job_type == "find_product" and params is None:
            raise ValueError("find_product jobs require params.")
        now = datetime.now(timezone.utc)
        model = AssistantJobModel(
            id=uuid4(),
            type=job_type,
            user_id=user_id,
            merchant=merchant,
            amount_ttc=amount_ttc,
            date=date,
            project_hint=project_hint,
            status="queued",
            attempts=0,
            run_after=now,
            result=None,
            pdf_storage_key=None,
            status_message_id=status_message_id,
            lang=lang,
            params=params,
            processed_at=None,
            created_at=now,
            updated_at=now,
            channel_key=channel_key,
        )
        self._session.add(model)
        self._session.commit()
        return _to_entity(model)

    def find_by_id(self, job_id: UUID) -> Optional[AssistantJobRecord]:
        model = self._session.get(AssistantJobModel, job_id)
        return _to_entity(model) if model is not None else None

    def find_duplicate(
        self,
        *,
        user_id: UUID,
        since: datetime,
        job_type: str = "fetch_invoice",
        merchant: Optional[str] = None,
        amount_ttc: Optional[Decimal] = None,
        date: Optional[Any] = None,
        photo_sha256: Optional[str] = None,
    ) -> Optional[AssistantJobRecord]:
        if job_type == "find_product":
            if photo_sha256 is None:
                raise ValueError("find_duplicate for find_product jobs requires photo_sha256.")
            candidates = (
                self._session.execute(
                    select(AssistantJobModel)
                    .where(
                        AssistantJobModel.user_id == user_id,
                        AssistantJobModel.type == "find_product",
                        AssistantJobModel.status.in_(ACTIVE_JOB_STATUSES),
                        AssistantJobModel.created_at >= since,
                    )
                    .order_by(AssistantJobModel.created_at.desc())
                )
                .scalars()
                .all()
            )
            for candidate in candidates:
                if (candidate.params or {}).get("photo_sha256") == photo_sha256:
                    return _to_entity(candidate)
            return None
        model = (
            self._session.execute(
                select(AssistantJobModel)
                .where(
                    AssistantJobModel.user_id == user_id,
                    AssistantJobModel.type == job_type,
                    AssistantJobModel.merchant == merchant,
                    AssistantJobModel.amount_ttc == amount_ttc,
                    AssistantJobModel.date == date,
                    AssistantJobModel.status.in_(ACTIVE_JOB_STATUSES),
                    AssistantJobModel.created_at >= since,
                )
                .order_by(AssistantJobModel.created_at.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
        return _to_entity(model) if model is not None else None

    def set_status_message(self, job_id: UUID, status_message_id: UUID) -> None:
        model = self._session.get(AssistantJobModel, job_id)
        if model is None:
            return
        model.status_message_id = status_message_id
        model.updated_at = datetime.now(timezone.utc)
        self._session.commit()

    def claim_next(self, now: datetime) -> Optional[AssistantJobRecord]:
        stuck_before = now - _STUCK_RUNNING_AFTER
        # A stuck `running` row that already exhausted its reclaim attempts is swept to
        # `failed` here, *before* the claim SELECT below — without this it would be
        # reclaimed forever (every 30 minutes, at full browser-agent cost), since the old
        # "attempts-exhausted -> failed path naturally finalizes" comment only covered the
        # `not_ready` retry path, never a crashing iteration that never reaches
        # `update_result` at all. `processed_at` stays NULL so `on_result`'s `failed`
        # branch and `reap_unprocessed` still notify the user exactly once.
        self._session.execute(
            update(AssistantJobModel)
            .where(
                AssistantJobModel.status == "running",
                AssistantJobModel.updated_at < stuck_before,
                AssistantJobModel.attempts >= _MAX_RECLAIM_ATTEMPTS,
            )
            .values(status="failed", updated_at=now)
        )
        query = (
            select(AssistantJobModel)
            .where(
                or_(
                    and_(AssistantJobModel.status == "queued", AssistantJobModel.run_after <= now),
                    and_(
                        AssistantJobModel.status == "running",
                        AssistantJobModel.updated_at < stuck_before,
                        AssistantJobModel.attempts < _MAX_RECLAIM_ATTEMPTS,
                    ),
                )
            )
            .order_by(AssistantJobModel.run_after.asc())
            .limit(1)
        )
        bind = self._session.get_bind()
        if bind is not None and bind.dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
        model = self._session.execute(query).scalars().first()
        if model is None:
            # End the SELECT's transaction: with FOR UPDATE it holds a ROW SHARE lock on
            # assistant_jobs, and an idle poller that keeps it open across its sleep blocks
            # every ALTER TABLE on the table (the v0.4.0 deploy hung on exactly that).
            # A commit (not a rollback) ends a read-only transaction just the same and
            # keeps the test fixtures' savepoint-based sessions intact.
            self._session.commit()
            return None
        if model.status == "running":
            # Reaped from a stuck row (guaranteed `attempts < _MAX_RECLAIM_ATTEMPTS` by
            # the query above) — bump attempts so it eventually hits the sweep above
            # instead of being reclaimed forever.
            model.attempts += 1
        model.status = "running"
        model.updated_at = datetime.now(timezone.utc)
        self._session.commit()
        return _to_entity(model)

    def update_status(
        self, job_id: UUID, *, status: str, attempts: Optional[int] = None, run_after: Optional[datetime] = None
    ) -> None:
        model = self._session.get(AssistantJobModel, job_id)
        if model is None:
            return
        model.status = status
        if attempts is not None:
            model.attempts = attempts
        if run_after is not None:
            model.run_after = run_after
        model.updated_at = datetime.now(timezone.utc)
        self._session.commit()

    def update_result(
        self,
        job_id: UUID,
        *,
        status: str,
        result: Optional[dict[str, Any]] = None,
        pdf_storage_key: Optional[str] = None,
    ) -> None:
        model = self._session.get(AssistantJobModel, job_id)
        if model is None:
            return
        model.status = status
        if result is not None:
            model.result = dict(result)
        if pdf_storage_key is not None:
            model.pdf_storage_key = pdf_storage_key
        model.updated_at = datetime.now(timezone.utc)
        self._session.commit()

    def list_recent_for_user(self, user_id: UUID, limit: int = 10) -> list[AssistantJobRecord]:
        models = (
            self._session.execute(
                select(AssistantJobModel)
                .where(AssistantJobModel.user_id == user_id)
                .order_by(AssistantJobModel.created_at.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return [_to_entity(m) for m in models]

    def mark_processed(self, job_id: UUID) -> bool:
        result = self._session.execute(
            update(AssistantJobModel)
            .where(AssistantJobModel.id == job_id, AssistantJobModel.processed_at.is_(None))
            .values(processed_at=datetime.now(timezone.utc))
        )
        self._session.commit()
        return bool(result.rowcount)

    def reap_unprocessed(self, now: datetime) -> list[UUID]:
        stale_before = now - _UNPROCESSED_REAP_AFTER
        ids = list(
            self._session.execute(
                select(AssistantJobModel.id).where(
                    AssistantJobModel.status.in_(_TERMINAL_UNPROCESSED_STATUSES),
                    AssistantJobModel.processed_at.is_(None),
                    AssistantJobModel.updated_at < stale_before,
                    # A job paused by the daily cost cap sets `run_after` to the next
                    # Paris-local midnight (see `jobs._notify_job_status`'s callers) so
                    # this sweep leaves it alone until the cap resets instead of
                    # re-enqueuing `process_*` every `_UNPROCESSED_REAP_AFTER` for
                    # hours while it can only be skipped again. Every job not paused
                    # this way keeps its `run_after` at creation time (always in the
                    # past by the time it could possibly be reaped), so this is a no-op
                    # for the common case.
                    AssistantJobModel.run_after <= now,
                )
            )
            .scalars()
            .all()
        )
        if ids:
            self._session.execute(update(AssistantJobModel).where(AssistantJobModel.id.in_(ids)).values(updated_at=now))
            self._session.commit()
        return [UUID(str(i)) for i in ids]
