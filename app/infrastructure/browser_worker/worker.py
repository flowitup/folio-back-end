"""The poll loop: claim one ``fetch_invoice`` job, run the browser agent, store the
result, enqueue ``process_fetched_invoice``. See the package docstring for why this
never touches Flask.

``run_once``/``run_forever`` take every dependency as a plain argument (no DI
container) so a unit test can swap in a fake ``job_runner`` and a fake queue against a
real SQLite-backed ``SqlAlchemyAssistantJobRepository`` — see
``tests/unit/browser_worker/test_worker.py``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Protocol
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.application.assistant import reply
from app.application.assistant.jobs_repo import AssistantJobRecord, AssistantJobRepositoryPort
from app.infrastructure.browser_worker.agent import FetchOutcome
from app.infrastructure.database.models.chat_message import ChatMessageOrm

logger = logging.getLogger(__name__)

_PARIS_TZ = ZoneInfo("Europe/Paris")
#: JOB_OFFPEAK_ONLY (owner runbook, docs/assistant-merchant-login.md): browser jobs only
#: run after this hour, Europe/Paris local time.
_OFFPEAK_START_HOUR = 12
#: How long the loop sleeps between polls when there was nothing to claim.
IDLE_SLEEP_SECONDS = 10.0
#: The RQ queue the browser container enqueues `process_fetched_invoice` onto — same
#: queue name the web process's `RqAssistantDispatcher` uses, consumed by the shared
#: `stack.queue.rq_worker` container (already listening on "assistant").
QUEUE_NAME = "assistant"


class StorageLike(Protocol):
    def put(self, key: str, fileobj: Any, content_type: str) -> None: ...


class QueueLike(Protocol):
    # `func_name`/return typed `Any` rather than matching `rq.Queue.enqueue`'s own
    # generic `FunctionReferenceType` signature exactly — this is a narrow duck-typed
    # boundary (the browser container's real queue is `rq.Queue`, tests use a plain
    # fake), not a contract worth fighting rq's stub generics over.
    def enqueue(self, func_name: Any, *args: Any, **kwargs: Any) -> Any: ...


JobRunner = Callable[..., Awaitable[FetchOutcome]]


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
) -> bool:
    """Claims and processes at most one job. Returns True when a job was claimed
    (whatever its outcome), False when there was nothing to do (or it is not off-peak
    yet) — the caller uses this to decide whether to sleep."""
    if offpeak_only and not is_offpeak(now):
        return False
    job = job_repo.claim_next(now)
    if job is None:
        return False

    logger.info(
        "browser_worker: claimed job %s merchant=%s amount=%.2f date=%s attempt=%d",
        job.id,
        job.merchant,
        float(job.amount_ttc),
        job.date,
        job.attempts + 1,
    )
    _update_status_message(session, job, state="running", text=reply.render("fetch_running", "fr"))

    outcome = await job_runner(
        job,
        chrome_path=chrome_path,
        profile_dir=profile_dir,
        downloads_dir=downloads_dir,
        deepseek_api_key=deepseek_api_key,
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
    queue.enqueue("app.application.assistant.jobs.process_fetched_invoice", str(job.id))
    return True


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
) -> None:
    """SIGTERM-safe poll loop: ``stop_event`` is checked between jobs, never mid-job —
    the caller (``__main__.py``) sets it from a signal handler, letting the current job
    (if any) finish cleanly before the process exits."""
    while not stop_event.is_set():
        now = datetime.now(timezone.utc)
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
        )
        if not processed:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=IDLE_SLEEP_SECONDS)
            except asyncio.TimeoutError:
                pass


__all__ = ["run_once", "run_forever", "is_offpeak", "QUEUE_NAME", "IDLE_SLEEP_SECONDS"]
