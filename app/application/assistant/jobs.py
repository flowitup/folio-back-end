"""RQ job entry points for the ``assistant`` queue.

Each job builds its own Flask app (an RQ worker is a separate process with no request
context) and resolves the wired ``AssistantService`` from the DI container — the same
pattern the operational scripts under ``scripts/`` use.

Every entry point re-checks ``FEATURE_ASSISTANT``/the API keys via
``assistant_flags_enabled`` before touching the AI pipeline (review finding NEW-H3):
``RqAssistantDispatcher`` already refuses to *enqueue* while the flag is off, but a job
enqueued before the flag was flipped off would otherwise still run to completion once an
RQ worker picks it up — this is the second, consumption-side half of the kill switch.

``process_fetched_invoice``/``process_product_search`` are also the browser-result
reaper's own re-enqueue target (``app.infrastructure.browser_worker.worker._reap_
unprocessed``, which knows nothing about ``FEATURE_ASSISTANT``): while the switch is off,
that reaper keeps re-enqueueing the same unprocessed result forever, and once the switch
comes back on a result that has been sitting for days would otherwise be acted on and
posted as if it had just arrived. ``_expire_if_stale`` breaks that loop from BOTH
branches below: a result still within the window is skipped exactly as before (silently,
so the reaper retries later), but one that has aged past ``_UNPROCESSED_EXPIRE_AFTER`` is
marked ``processed``/``expired`` — the reaper's own ``processed_at IS NULL`` filter then
leaves it alone for good — and the user is told their request expired instead of getting
a stale reply days late. Checking it in the ENABLED branch too (not only while
disabled) matters just as much: the switch being off for over a day, then flipped back
on, left every backlog result to run and post late — the exact scenario the disabled
branch alone was meant to prevent.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from app.application.assistant import reply
from app.application.assistant.jobs_repo import AssistantJobRecord
from app.application.assistant.models import ChannelScope
from app.domain.entities.chat_message import ChannelRef

logger = logging.getLogger(__name__)

#: How long an unprocessed browser-worker result may sit while the assistant is disabled
#: before it is given up on instead of eventually being posted late.
_UNPROCESSED_EXPIRE_AFTER = timedelta(hours=24)

#: `RedisCostLedger`'s daily cap resets on the Paris-local calendar day boundary (see
#: `app.infrastructure.ai.cost`) — a job paused because of it has nothing to gain from
#: being retried before then.
_PARIS_TZ = ZoneInfo("Europe/Paris")


def _next_paris_midnight(now: datetime) -> datetime:
    """The next Europe/Paris local midnight strictly after ``now``, back in UTC."""
    paris_now = now.astimezone(_PARIS_TZ)
    next_midnight_local = (paris_now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return next_midnight_local.astimezone(timezone.utc)


def _assistant_enabled(app: Any) -> bool:
    """Reads the same three flags (``FEATURE_ASSISTANT`` + both API keys) that gate the
    synchronous chat surface — see ``app.api.v1.chat.routes.assistant_enabled``. Kept
    local (no Flask-request-scoped ``current_app``) since a job has an app context but
    never a request context."""
    from config import assistant_flags_enabled

    cfg = app.config
    return assistant_flags_enabled(
        cfg.get("FEATURE_ASSISTANT"), cfg.get("DEEPSEEK_API_KEY"), cfg.get("TYPESAFE_API_KEY")
    )


def _notify_job_status(
    container: Any, job: AssistantJobRecord, template_key: str, *, terminal: bool, state: str = "failed"
) -> None:
    """Best-effort update of the job's ``job_status`` chat message to a fixed template —
    never raises: a failure here must not turn an otherwise-handled job into a crashed
    RQ run (mirrors ``AssistantService._write_audit``'s own "never block on this"
    contract).

    ``state`` defaults to ``"failed"`` for every terminal caller (expiry, genuine
    failure); a caller pausing a job for a reason the job will still recover from on
    its own (the daily cost cap) passes a non-terminal value instead — showing
    ``"failed"`` there told the user their request was dead, so a re-ask duplicated the
    paid browser/DeepSeek work once the cap reset and the original one quietly went
    through anyway.
    """
    messenger = container.assistant_messenger
    company_reader = container.assistant_project_company_reader
    if messenger is None or job.status_message_id is None or job.channel_key is None or company_reader is None:
        return
    try:
        channel = ChannelRef.parse(job.channel_key)
        scope = ChannelScope.for_channel(
            channel, project_company_id=company_reader.project_company_id, asker_id=job.user_id
        )
        messenger.update_job_status(
            job.status_message_id,
            state=state,
            text=reply.render(template_key, job.lang or "fr"),
            scope=scope,
            terminal=terminal,
        )
    except Exception:
        logger.exception("assistant.jobs failed to update job_status for job_id=%s", job.id)


def _expire_if_stale(container: Any, job_id: str) -> bool:
    """A terminal browser-worker result that has been sitting unprocessed for over
    ``_UNPROCESSED_EXPIRE_AFTER`` is given up on — marked ``processed``/``expired`` so
    the reaper stops re-enqueueing it — instead of silently doing nothing forever
    (still exactly what happens for a result younger than the window). Called from
    both branches of ``process_fetched_invoice``/``process_product_search`` (disabled
    AND enabled) since a stale backlog does not stop being stale just because the
    switch flipped back on mid-way through the window.

    Returns whether it actually expired the job — the caller must not then also run
    the normal feature handler on the same (now-``processed``) job."""
    job_repo = container.assistant_job_repo
    if job_repo is None:
        return False
    try:
        job = job_repo.find_by_id(UUID(job_id))
    except Exception:
        logger.exception("assistant.jobs failed to look up job_id=%s while checking staleness", job_id)
        return False
    if job is None or job.processed_at is not None:
        return False
    # SQLite (tests) returns a naive `updated_at`; Postgres returns a tz-aware one.
    updated_at = job.updated_at if job.updated_at.tzinfo is not None else job.updated_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - updated_at < _UNPROCESSED_EXPIRE_AFTER:
        return False
    if not job_repo.mark_processed(job.id):
        return False  # a concurrent run already claimed it
    job_repo.update_status(job.id, status="expired")
    logger.info("assistant.jobs expired a stale unprocessed job job_id=%s type=%s", job.id, job.type)
    template = "fetch_failed" if job.type == "fetch_invoice" else "product_search_failed"
    _notify_job_status(container, job, template, terminal=True)
    return True


def _over_cost_cap(container: Any) -> bool:
    cost_ledger = container.assistant_cost_ledger
    return cost_ledger is not None and cost_ledger.over_cap()


def handle_message(user_id: str, message_id: str) -> None:
    """A user sent a message in their assistant conversation (queued by chat's
    ``SendMessageUseCase`` through ``RqAssistantDispatcher``)."""
    from app import create_app
    from wiring import get_container

    app = create_app()
    with app.app_context():
        if not _assistant_enabled(app):
            logger.info("assistant.jobs.handle_message skipped (FEATURE_ASSISTANT off) message_id=%s", message_id)
            return
        container = get_container()
        if container.assistant_service is None:
            logger.error("assistant_service not wired; dropping message %s", message_id)
            return
        container.assistant_service.handle_message(user_id=UUID(user_id), message_id=UUID(message_id))


def handle_action(user_id: str, message_id: str, action: str, payload: dict[str, Any]) -> None:
    """A user tapped a choice option (queued by ``SubmitAssistantActionUseCase``)."""
    from app import create_app
    from wiring import get_container

    app = create_app()
    with app.app_context():
        if not _assistant_enabled(app):
            logger.info(
                "assistant.jobs.handle_action skipped (FEATURE_ASSISTANT off) message_id=%s action=%s",
                message_id,
                action,
            )
            return
        container = get_container()
        if container.assistant_service is None:
            logger.error("assistant_service not wired; dropping action %s on message %s", action, message_id)
            return
        container.assistant_service.handle_action(
            user_id=UUID(user_id), message_id=UUID(message_id), action=action, payload=payload
        )


def process_fetched_invoice(job_id: str) -> None:
    """Feature B's ``on_result``: the ``ai-browser`` container reported a
    ``fetch_invoice`` job's outcome (enqueued by ``app.infrastructure.browser_worker``,
    a separate process/container with no Flask app of its own — see that package)."""
    import uuid as _uuid

    from app import create_app
    from wiring import get_container

    app = create_app()
    with app.app_context():
        container = get_container()
        if not _assistant_enabled(app):
            logger.info("assistant.jobs.process_fetched_invoice skipped (FEATURE_ASSISTANT off) job_id=%s", job_id)
            _expire_if_stale(container, job_id)
            return
        if container.assistant_invoice_fetch_feature is None or container.assistant_messenger is None:
            logger.error("assistant invoice-fetch feature not wired; dropping job result %s", job_id)
            return
        # Even while enabled, a result that sat unprocessed past the window (the switch
        # was off for a day+ and just came back on, or ai-browser itself stalled) must
        # not be acted on and posted as if it had just arrived — expire it exactly
        # like the disabled branch above instead of running the pipeline on stale data.
        if _expire_if_stale(container, job_id):
            return
        # The daily cost cap was only ever checked on the SYNCHRONOUS chat surface
        # (`AssistantService`) — a job already queued when the cap was reached would
        # otherwise still run its (up to 2 DeepSeek vision + 2-3 Jev) calls here.
        # Checked right before the feature handler, not inside it, so this applies the
        # same way regardless of which feature (A/B) posted the job.
        if _over_cost_cap(container):
            job_repo = container.assistant_job_repo
            job = job_repo.find_by_id(UUID(job_id)) if job_repo is not None else None
            logger.info("assistant.jobs.process_fetched_invoice skipped (cost cap reached) job_id=%s", job_id)
            if job is not None:
                if job_repo is not None:
                    # The job's own `status` (whatever the browser worker last set —
                    # "done", "not_ready", ...) is untouched: it stays terminal so the
                    # reaper's own status filter still finds it. Only `run_after`
                    # changes, so the reaper (which also checks it, see
                    # `reap_unprocessed`) waits for the cap to reset instead of
                    # re-triggering this same skip every 30 minutes.
                    job_repo.update_status(
                        job.id, status=job.status, run_after=_next_paris_midnight(datetime.now(timezone.utc))
                    )
                _notify_job_status(container, job, "quota_exceeded", terminal=False, state="queued")
            return
        trace_id = _uuid.uuid4().hex[:16]
        try:
            container.assistant_invoice_fetch_feature.on_result(
                UUID(job_id), messenger=container.assistant_messenger, trace_id=trace_id
            )
        except Exception:
            # Every known DB-error path inside `on_result` already rolls back before
            # its own failure reply; this is a last-resort net for anything that still
            # escapes, so the RQ worker's next job does not inherit a poisoned session.
            logger.exception("assistant.jobs.process_fetched_invoice: on_result crashed for job_id=%s", job_id)
            container.assistant_messenger.rollback()
            raise


def process_product_search(job_id: str) -> None:
    """Feature A's ``on_result``: the ``ai-browser`` container reported a
    ``find_product`` job's outcome (enqueued by ``app.infrastructure.browser_worker``,
    same as ``process_fetched_invoice`` above — see that function's docstring)."""
    import uuid as _uuid

    from app import create_app
    from wiring import get_container

    app = create_app()
    with app.app_context():
        container = get_container()
        if not _assistant_enabled(app):
            logger.info("assistant.jobs.process_product_search skipped (FEATURE_ASSISTANT off) job_id=%s", job_id)
            _expire_if_stale(container, job_id)
            return
        if container.assistant_material_feature is None or container.assistant_messenger is None:
            logger.error("assistant material feature not wired; dropping job result %s", job_id)
            return
        # Same staleness check as process_fetched_invoice, above.
        if _expire_if_stale(container, job_id):
            return
        # Same daily cost cap check as process_fetched_invoice, above.
        if _over_cost_cap(container):
            job_repo = container.assistant_job_repo
            job = job_repo.find_by_id(UUID(job_id)) if job_repo is not None else None
            logger.info("assistant.jobs.process_product_search skipped (cost cap reached) job_id=%s", job_id)
            if job is not None:
                # Same run_after deferral as process_fetched_invoice, above.
                if job_repo is not None:
                    job_repo.update_status(
                        job.id, status=job.status, run_after=_next_paris_midnight(datetime.now(timezone.utc))
                    )
                _notify_job_status(container, job, "quota_exceeded", terminal=False, state="queued")
            return
        trace_id = _uuid.uuid4().hex[:16]
        try:
            container.assistant_material_feature.on_result(
                UUID(job_id), messenger=container.assistant_messenger, trace_id=trace_id
            )
        except Exception:
            # Same last-resort net as process_fetched_invoice, above.
            logger.exception("assistant.jobs.process_product_search: on_result crashed for job_id=%s", job_id)
            container.assistant_messenger.rollback()
            raise
