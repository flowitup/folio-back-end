"""The poll loop: claim one job (``fetch_invoice`` or ``find_product``), run the matching
browser agent, store the result, enqueue the matching ``process_*`` RQ job. See the
package docstring for why this never touches Flask.

``run_once``/``run_forever`` take every dependency as a plain argument (no DI
container) so a unit test can swap in a fake ``job_runner`` and a fake queue against a
real SQLite-backed ``SqlAlchemyAssistantJobRepository`` — see
``tests/unit/browser_worker/test_worker.py``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional, Protocol
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.application.assistant import reply
from app.application.assistant.jobs_repo import AssistantJobRecord, AssistantJobRepositoryPort
from app.application.assistant.models import ProductSearchResult
from app.application.assistant.ports import CostLedgerPort
from app.infrastructure.browser_worker.agent import FetchOutcome
from app.infrastructure.database.models.chat_message import ChatMessageOrm

logger = logging.getLogger(__name__)

_PARIS_TZ = ZoneInfo("Europe/Paris")
#: JOB_OFFPEAK_ONLY (owner runbook, docs/assistant-merchant-login.md): browser jobs only
#: run after this hour, Europe/Paris local time.
_OFFPEAK_START_HOUR = 12
#: How long the loop sleeps between polls when there was nothing to claim.
IDLE_SLEEP_SECONDS = 10.0
#: How often the "idling, FEATURE_ASSISTANT is off" line is logged while disabled — the
#: loop itself still polls every `IDLE_SLEEP_SECONDS`, this only throttles the log line
#: (review finding NEW-H3: the kill switch is real, but should not spam the container's
#: logs every ten seconds for however long the flag stays off).
DISABLED_LOG_INTERVAL = timedelta(minutes=5)
#: The RQ queue the browser container enqueues `process_fetched_invoice`/
#: `process_product_search` onto — same queue name the web process's
#: `RqAssistantDispatcher` uses, consumed by the shared `stack.queue.rq_worker`
#: container (already listening on "assistant").
QUEUE_NAME = "assistant"

#: Which RQ function a claimed/reaped job's result gets handed to, by `job.type`.
_RESULT_JOB_NAME: dict[str, str] = {
    "fetch_invoice": "app.application.assistant.jobs.process_fetched_invoice",
    "find_product": "app.application.assistant.jobs.process_product_search",
}


class StorageLike(Protocol):
    def put(self, key: str, fileobj: Any, content_type: str) -> None: ...


class QueueLike(Protocol):
    # `func_name`/return typed `Any` rather than matching `rq.Queue.enqueue`'s own
    # generic `FunctionReferenceType` signature exactly — this is a narrow duck-typed
    # boundary (the browser container's real queue is `rq.Queue`, tests use a plain
    # fake), not a contract worth fighting rq's stub generics over.
    def enqueue(self, func_name: Any, *args: Any, **kwargs: Any) -> Any: ...


JobRunner = Callable[..., Awaitable[FetchOutcome]]
ProductSearchRunner = Callable[..., Awaitable[ProductSearchResult]]


async def _unconfigured_product_search_runner(job: AssistantJobRecord, **_kwargs: Any) -> ProductSearchResult:
    """Default ``product_search_runner`` for every call site that never dispatches a
    ``find_product`` job — keeps the many ``fetch_invoice``-only tests/call sites from
    having to pass one. Raising loudly here (rather than silently no-opping) is
    deliberate: reaching this means a ``find_product`` job was claimed without a real
    runner wired in, which must never happen outside a test that forgot to configure one."""
    raise RuntimeError(f"product_search_runner not configured (job {job.id} is type={job.type!r}).")


def is_offpeak(now_utc: datetime) -> bool:
    """True once it is past noon Europe/Paris — ``JOB_OFFPEAK_ONLY``'s gate."""
    paris_now = now_utc.astimezone(_PARIS_TZ)
    return paris_now.hour >= _OFFPEAK_START_HOUR


def _update_status_message(session: Session, job: AssistantJobRecord, *, state: str, text: str) -> None:
    """Edits the job_status chat message's payload JSON directly (the browser container
    has no ``AssistantMessenger``/chat repo wiring — see the package docstring). Used
    only for the non-terminal "running" transition; every terminal transition is written
    by ``InvoiceFetchFeature.on_result`` (which does have the messenger, and pushes)."""
    if job.status_message_id is None:
        return
    message = session.get(ChatMessageOrm, job.status_message_id)
    if message is None:
        return
    payload = dict(message.payload or {})
    payload["state"] = state
    payload["text"] = text
    message.payload = payload
    session.commit()


def _reap_unprocessed(job_repo: AssistantJobRepositoryPort, queue: QueueLike, now: datetime) -> None:
    """The other half of the H2 fix (review finding NEW-H2): re-enqueue
    ``process_fetched_invoice`` for any terminal job whose own enqueue never happened
    (or never ran) after ``update_result`` committed. Run once per idle poll — the same
    cadence ``run_once`` already uses to check for new work, no separate scheduler — so a
    wedged job recovers within one ``IDLE_SLEEP_SECONDS`` of the 30-minute window closing
    instead of sitting forever with its user-facing job_status message stuck.

    A queue failure here is logged and swallowed exactly like a claimed job's own
    enqueue failure (``run_forever``'s outer ``except Exception``) — reaping is a
    best-effort recovery path, never something that should crash the poll loop.
    """
    for job_id in job_repo.reap_unprocessed(now):
        job = job_repo.find_by_id(job_id)
        func_name = _RESULT_JOB_NAME.get(
            job.type if job is not None else "fetch_invoice", "app.application.assistant.jobs.process_fetched_invoice"
        )
        try:
            queue.enqueue(func_name, str(job_id))
            logger.info("browser_worker: reaped unprocessed job %s, re-enqueued", job_id)
        except Exception:
            logger.exception("browser_worker: failed to re-enqueue reaped job %s", job_id)


async def run_once(
    *,
    session: Session,
    job_repo: AssistantJobRepositoryPort,
    storage: StorageLike,
    queue: QueueLike,
    job_runner: JobRunner,
    chrome_path: str,
    profile_dir: str,
    downloads_dir: str,
    deepseek_api_key: str,
    offpeak_only: bool,
    now: datetime,
    cost_ledger: Optional[CostLedgerPort] = None,
    product_search_runner: ProductSearchRunner = _unconfigured_product_search_runner,
) -> bool:
    """Claims and processes at most one job. Returns True when a job was claimed
    (whatever its outcome), False when there was nothing to do (or it is not off-peak
    yet) — the caller uses this to decide whether to sleep.

    ``cost_ledger`` (review finding NEW-H4) is forwarded to whichever runner handles the
    claimed job so the browser agent's own DeepSeek spend gets billed — ``None`` (the
    default) only in the smoke test's direct ``run_fetch``/``run_product_search`` calls;
    the real container always constructs one from ``REDIS_URL`` (see ``__main__.py``).

    Dispatches on ``job.type`` (owner decision D16 added ``find_product`` alongside the
    original ``fetch_invoice``): each type has its own runner/result shape/RQ callback,
    but shares this same claim/reaper/kill-switch/cost-billing plumbing.
    """
    if offpeak_only and not is_offpeak(now):
        return False
    job = job_repo.claim_next(now)
    if job is None:
        _reap_unprocessed(job_repo, queue, now)
        return False

    if job.type == "find_product":
        await _run_product_search_job(
            job,
            session=session,
            job_repo=job_repo,
            queue=queue,
            product_search_runner=product_search_runner,
            chrome_path=chrome_path,
            profile_dir=profile_dir,
            downloads_dir=downloads_dir,
            deepseek_api_key=deepseek_api_key,
            cost_ledger=cost_ledger,
        )
        return True

    await _run_fetch_invoice_job(
        job,
        session=session,
        job_repo=job_repo,
        storage=storage,
        queue=queue,
        job_runner=job_runner,
        chrome_path=chrome_path,
        profile_dir=profile_dir,
        downloads_dir=downloads_dir,
        deepseek_api_key=deepseek_api_key,
        cost_ledger=cost_ledger,
    )
    return True


async def _run_fetch_invoice_job(
    job: AssistantJobRecord,
    *,
    session: Session,
    job_repo: AssistantJobRepositoryPort,
    storage: StorageLike,
    queue: QueueLike,
    job_runner: JobRunner,
    chrome_path: str,
    profile_dir: str,
    downloads_dir: str,
    deepseek_api_key: str,
    cost_ledger: Optional[CostLedgerPort],
) -> None:
    logger.info(
        "browser_worker: claimed job %s merchant=%s amount=%.2f date=%s attempt=%d",
        job.id,
        job.merchant,
        float(job.amount_ttc) if job.amount_ttc is not None else 0.0,
        job.date,
        job.attempts + 1,
    )
    _update_status_message(session, job, state="running", text=reply.render("fetch_running", job.lang or "fr"))

    outcome = await job_runner(
        job,
        chrome_path=chrome_path,
        profile_dir=profile_dir,
        downloads_dir=downloads_dir,
        deepseek_api_key=deepseek_api_key,
        cost_ledger=cost_ledger,
    )

    pdf_key = None
    if outcome.result.status == "done" and outcome.pdf_path is not None:
        pdf_key = f"assistant/jobs/{job.id}.pdf"
        try:
            with open(outcome.pdf_path, "rb") as fileobj:
                storage.put(pdf_key, fileobj, content_type="application/pdf")
        except OSError:
            logger.exception("browser_worker: failed to upload PDF for job %s", job.id)
            outcome = FetchOutcome(
                result=outcome.result.model_copy(update={"status": "failed", "message": "PDF upload failed"}),
                pdf_path=None,
            )
            pdf_key = None

    job_repo.update_result(
        job.id, status=outcome.result.status, result=outcome.result.model_dump(), pdf_storage_key=pdf_key
    )
    logger.info("browser_worker: job %s finished with status=%s", job.id, outcome.result.status)
    queue.enqueue(_RESULT_JOB_NAME["fetch_invoice"], str(job.id))


async def _run_product_search_job(
    job: AssistantJobRecord,
    *,
    session: Session,
    job_repo: AssistantJobRepositoryPort,
    queue: QueueLike,
    product_search_runner: ProductSearchRunner,
    chrome_path: str,
    profile_dir: str,
    downloads_dir: str,
    deepseek_api_key: str,
    cost_ledger: Optional[CostLedgerPort],
) -> None:
    logger.info("browser_worker: claimed job %s type=find_product attempt=%d", job.id, job.attempts + 1)
    _update_status_message(session, job, state="running", text=reply.render("product_search_running", job.lang or "fr"))

    result = await product_search_runner(
        job,
        chrome_path=chrome_path,
        profile_dir=profile_dir,
        downloads_dir=downloads_dir,
        deepseek_api_key=deepseek_api_key,
        cost_ledger=cost_ledger,
    )

    job_repo.update_result(job.id, status=result.status, result=result.model_dump())
    logger.info("browser_worker: job %s finished with status=%s", job.id, result.status)
    queue.enqueue(_RESULT_JOB_NAME["find_product"], str(job.id))


async def run_forever(
    *,
    session: Session,
    job_repo: AssistantJobRepositoryPort,
    storage: StorageLike,
    queue: QueueLike,
    job_runner: JobRunner,
    chrome_path: str,
    profile_dir: str,
    downloads_dir: str,
    deepseek_api_key: str,
    offpeak_only: bool,
    stop_event: asyncio.Event,
    assistant_enabled: Callable[[], bool] = lambda: True,
    cost_ledger: Optional[CostLedgerPort] = None,
    product_search_runner: ProductSearchRunner = _unconfigured_product_search_runner,
) -> None:
    """SIGTERM-safe poll loop: ``stop_event`` is checked between jobs, never mid-job —
    the caller (``__main__.py``) sets it from a signal handler, letting the current job
    (if any) finish cleanly before the process exits.

    ``assistant_enabled`` is the container-side half of the ``FEATURE_ASSISTANT`` kill
    switch (review finding NEW-H3): re-read on every iteration (unlike the web process,
    this container never restarts on a config change) so flipping the flag off stops the
    poller claiming any *new* job — ``job_repo.claim_next`` is never even called while
    disabled. It does not interrupt a job already in flight; ``run_once`` only ever
    claims one job before returning.
    """
    last_disabled_log: Optional[datetime] = None
    while not stop_event.is_set():
        now = datetime.now(timezone.utc)
        if not assistant_enabled():
            if last_disabled_log is None or now - last_disabled_log >= DISABLED_LOG_INTERVAL:
                logger.info("browser_worker: FEATURE_ASSISTANT is off, idling (claiming no jobs)")
                last_disabled_log = now
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=IDLE_SLEEP_SECONDS)
            except asyncio.TimeoutError:
                pass
            continue
        last_disabled_log = None
        try:
            processed = await run_once(
                session=session,
                job_repo=job_repo,
                storage=storage,
                queue=queue,
                job_runner=job_runner,
                chrome_path=chrome_path,
                profile_dir=profile_dir,
                downloads_dir=downloads_dir,
                deepseek_api_key=deepseek_api_key,
                offpeak_only=offpeak_only,
                now=now,
                cost_ledger=cost_ledger,
                product_search_runner=product_search_runner,
            )
        except Exception:
            # A DB blip, an S3 error, an `rq` enqueue failure, or anything else raised
            # mid-iteration must never kill the poll loop (review finding H2) — log it,
            # roll back whatever the failed iteration left half-committed, and try again
            # on the next poll rather than crashing the container and leaving the job
            # (already flipped to `running` by `claim_next`) wedged forever.
            logger.exception("browser_worker: run_once failed, rolling back and continuing")
            session.rollback()
            processed = False
        if not processed:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=IDLE_SLEEP_SECONDS)
            except asyncio.TimeoutError:
                pass


__all__ = [
    "run_once",
    "run_forever",
    "is_offpeak",
    "QUEUE_NAME",
    "IDLE_SLEEP_SECONDS",
    "DISABLED_LOG_INTERVAL",
    "JobRunner",
    "ProductSearchRunner",
]
