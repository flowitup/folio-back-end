"""Unit tests for the poll loop (`app.infrastructure.browser_worker.worker`).

Uses a REAL `SqlAlchemyAssistantJobRepository` against the in-memory SQLite DB (the
`session` fixture) plus a fake agent runner and a fake RQ queue — proves the loop claims
one job, stores its result, updates the job_status chat message, and enqueues
`process_fetched_invoice`, all without importing browser-use or touching a real browser.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import update

from app.application.assistant.jobs_repo import AssistantJobRecord
from app.application.assistant.models import FetchResult, ProductSearchResult
from app.infrastructure.browser_worker.agent import FetchOutcome
from app.infrastructure.browser_worker.worker import is_offpeak, run_forever, run_once
from app.infrastructure.database.models.assistant_job import AssistantJobModel
from app.infrastructure.database.models.chat_message import ChatMessageOrm
from app.infrastructure.database.repositories.sqlalchemy_assistant_job_repository import (
    SqlAlchemyAssistantJobRepository,
)


class FakeStorage:
    def __init__(self) -> None:
        self.puts: list[tuple[str, bytes, str]] = []

    def put(self, key: str, fileobj: Any, content_type: str) -> None:
        self.puts.append((key, fileobj.read(), content_type))


class FakeQueue:
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, tuple[Any, ...]]] = []

    def enqueue(self, func_name: str, *args: Any) -> None:
        self.enqueued.append((func_name, args))


def _make_job_runner(outcome: FetchOutcome):
    calls: list[AssistantJobRecord] = []

    async def _runner(job: AssistantJobRecord, **_kwargs: Any) -> FetchOutcome:
        calls.append(job)
        return outcome

    _runner.calls = calls  # type: ignore[attr-defined]
    return _runner


def _make_product_search_runner(result: ProductSearchResult):
    calls: list[AssistantJobRecord] = []

    async def _runner(job: AssistantJobRecord, **_kwargs: Any) -> ProductSearchResult:
        calls.append(job)
        return result

    _runner.calls = calls  # type: ignore[attr-defined]
    return _runner


def _status_message(session, job_id) -> ChatMessageOrm:
    message = ChatMessageOrm(
        id=uuid4(),
        channel_kind="assistant",
        channel_id=uuid4(),
        sender_type="assistant",
        content_type="job_status",
        payload={"job_id": str(job_id), "state": "queued", "text": "Je m'en occupe", "progress": None},
    )
    session.add(message)
    session.commit()
    return message


class TestIsOffpeak:
    def test_afternoon_paris_time_is_offpeak(self) -> None:
        # 14:00 UTC in September (CEST, UTC+2) is 16:00 Paris — past noon.
        assert is_offpeak(datetime(2026, 9, 10, 14, 0, tzinfo=timezone.utc)) is True

    def test_morning_paris_time_is_not_offpeak(self) -> None:
        # 08:00 UTC in September (CEST, UTC+2) is 10:00 Paris — before noon.
        assert is_offpeak(datetime(2026, 9, 10, 8, 0, tzinfo=timezone.utc)) is False


class TestRunOnce:
    @pytest.mark.parametrize("pdf_bytes", [b"%PDF-1.4 fake pdf bytes"])
    def test_claims_job_stores_pdf_and_enqueues(self, session, tmp_path, pdf_bytes: bytes) -> None:
        job_repo = SqlAlchemyAssistantJobRepository(session)
        job = job_repo.add(
            user_id=uuid4(),
            merchant="leroymerlin",
            amount_ttc=Decimal("79.54"),
            date=date(2026, 9, 10),
            project_hint=None,
        )
        status_message = _status_message(session, job.id)
        job_repo.set_status_message(job.id, status_message.id)

        pdf_path = tmp_path / "invoice.pdf"
        pdf_path.write_bytes(pdf_bytes)
        outcome = FetchOutcome(result=FetchResult(status="done", message="ok"), pdf_path=str(pdf_path))
        job_runner = _make_job_runner(outcome)
        storage = FakeStorage()
        queue = FakeQueue()

        processed = asyncio.run(
            run_once(
                session=session,
                job_repo=job_repo,
                storage=storage,
                queue=queue,
                job_runner=job_runner,
                chrome_path="/usr/bin/google-chrome",
                profile_dir="/app/profile",
                downloads_dir=str(tmp_path),
                deepseek_api_key="key",
                offpeak_only=False,
                now=datetime.now(timezone.utc),
            )
        )

        assert processed is True
        assert len(job_runner.calls) == 1  # type: ignore[attr-defined]
        updated = job_repo.find_by_id(job.id)
        assert updated.status == "done"
        assert updated.pdf_storage_key == f"assistant/jobs/{job.id}.pdf"
        assert storage.puts[0][1] == pdf_bytes
        assert queue.enqueued == [("app.application.assistant.jobs.process_fetched_invoice", (str(job.id),))]

        session.refresh(status_message)
        assert status_message.payload["state"] == "running"  # only the "running" transition is written here

    def test_idle_when_nothing_to_claim(self, session) -> None:
        job_repo = SqlAlchemyAssistantJobRepository(session)
        job_runner = _make_job_runner(FetchOutcome(result=FetchResult(status="done"), pdf_path=None))
        processed = asyncio.run(
            run_once(
                session=session,
                job_repo=job_repo,
                storage=FakeStorage(),
                queue=FakeQueue(),
                job_runner=job_runner,
                chrome_path="",
                profile_dir="",
                downloads_dir="",
                deepseek_api_key="",
                offpeak_only=False,
                now=datetime.now(timezone.utc),
            )
        )
        assert processed is False
        assert job_runner.calls == []  # type: ignore[attr-defined]

    def test_offpeak_only_skips_before_noon_paris(self, session) -> None:
        job_repo = SqlAlchemyAssistantJobRepository(session)
        job_repo.add(
            user_id=uuid4(), merchant="leroymerlin", amount_ttc=Decimal("1"), date=date(2026, 9, 10), project_hint=None
        )
        job_runner = _make_job_runner(FetchOutcome(result=FetchResult(status="done"), pdf_path=None))
        morning_utc = datetime(2026, 9, 10, 8, 0, tzinfo=timezone.utc)  # 10:00 Paris

        processed = asyncio.run(
            run_once(
                session=session,
                job_repo=job_repo,
                storage=FakeStorage(),
                queue=FakeQueue(),
                job_runner=job_runner,
                chrome_path="",
                profile_dir="",
                downloads_dir="",
                deepseek_api_key="",
                offpeak_only=True,
                now=morning_utc,
            )
        )
        assert processed is False
        assert job_runner.calls == []  # type: ignore[attr-defined]

    def test_not_ready_result_does_not_store_a_pdf_key(self, session, tmp_path) -> None:
        job_repo = SqlAlchemyAssistantJobRepository(session)
        job = job_repo.add(
            user_id=uuid4(), merchant="pointp", amount_ttc=Decimal("30"), date=date(2026, 9, 1), project_hint=None
        )
        job_runner = _make_job_runner(
            FetchOutcome(result=FetchResult(status="not_ready", message="wait"), pdf_path=None)
        )
        queue = FakeQueue()

        processed = asyncio.run(
            run_once(
                session=session,
                job_repo=job_repo,
                storage=FakeStorage(),
                queue=queue,
                job_runner=job_runner,
                chrome_path="",
                profile_dir="",
                downloads_dir=str(tmp_path),
                deepseek_api_key="",
                offpeak_only=False,
                now=datetime.now(timezone.utc),
            )
        )
        assert processed is True
        updated = job_repo.find_by_id(job.id)
        assert updated.status == "not_ready"
        assert updated.pdf_storage_key is None
        assert queue.enqueued  # process_fetched_invoice still runs the retry/fail logic


class TestRunOnceDispatchesFindProductJobs:
    """Owner decision D16: `run_once` now claims two job types off the same table —
    these prove a `find_product` job is routed to `product_search_runner` (never
    `job_runner`), stores the `ProductSearchResult` dict (no PDF bookkeeping), and
    enqueues `process_product_search` rather than `process_fetched_invoice`."""

    def test_claims_a_find_product_job_and_enqueues_process_product_search(self, session) -> None:
        job_repo = SqlAlchemyAssistantJobRepository(session)
        job = job_repo.add(
            job_type="find_product",
            user_id=uuid4(),
            params={
                "ident": {"name": "Perceuse", "category": "outillage", "confidence": 0.9, "search_queries": ["x"]},
                "search_queries": ["perceuse bosch"],
                "company_id": str(uuid4()),
                "photo_sha256": "abc123",
                "message_id": str(uuid4()),
                "lang": "fr",
            },
            lang="fr",
        )
        status_message = _status_message(session, job.id)
        job_repo.set_status_message(job.id, status_message.id)

        result = ProductSearchResult(status="done", candidates=[])
        product_search_runner = _make_product_search_runner(result)
        fetch_job_runner = _make_job_runner(FetchOutcome(result=FetchResult(status="done"), pdf_path=None))
        queue = FakeQueue()

        processed = asyncio.run(
            run_once(
                session=session,
                job_repo=job_repo,
                storage=FakeStorage(),
                queue=queue,
                job_runner=fetch_job_runner,
                product_search_runner=product_search_runner,
                chrome_path="",
                profile_dir="",
                downloads_dir="",
                deepseek_api_key="",
                offpeak_only=False,
                now=datetime.now(timezone.utc),
            )
        )

        assert processed is True
        assert len(product_search_runner.calls) == 1  # type: ignore[attr-defined]
        assert fetch_job_runner.calls == []  # type: ignore[attr-defined]
        updated = job_repo.find_by_id(job.id)
        assert updated.status == "done"
        assert updated.pdf_storage_key is None
        assert queue.enqueued == [("app.application.assistant.jobs.process_product_search", (str(job.id),))]
        session.refresh(status_message)
        assert status_message.payload["state"] == "running"

    def test_a_find_product_job_never_calls_the_unconfigured_default_runner(self, session) -> None:
        """`run_once`'s default `product_search_runner` raises loudly (see the module's
        `_unconfigured_product_search_runner`) — this proves a real `find_product` job
        reaching that default would fail fast rather than silently doing nothing, by
        checking the opposite: passing a working runner explicitly succeeds."""
        job_repo = SqlAlchemyAssistantJobRepository(session)
        job_repo.add(
            job_type="find_product",
            user_id=uuid4(),
            params={"ident": {"name": "x", "category": "y", "confidence": 0.9}, "photo_sha256": "x"},
        )
        with pytest.raises(RuntimeError, match="product_search_runner not configured"):
            asyncio.run(
                run_once(
                    session=session,
                    job_repo=job_repo,
                    storage=FakeStorage(),
                    queue=FakeQueue(),
                    job_runner=_make_job_runner(FetchOutcome(result=FetchResult(status="done"), pdf_path=None)),
                    chrome_path="",
                    profile_dir="",
                    downloads_dir="",
                    deepseek_api_key="",
                    offpeak_only=False,
                    now=datetime.now(timezone.utc),
                )
            )


class TestRunOnceReapsUnprocessedJobs:
    """Review finding NEW-H2: `run_once`'s idle branch (nothing new to claim) also
    re-enqueues any terminal job whose own `process_fetched_invoice` enqueue never
    happened — the repository-level behaviour is proven in `test_jobs_repo.py`; this
    proves the wiring from the poll loop into it."""

    def test_a_stale_done_row_gets_re_enqueued_exactly_once(self, session) -> None:
        job_repo = SqlAlchemyAssistantJobRepository(session)
        job = job_repo.add(
            user_id=uuid4(), merchant="leroymerlin", amount_ttc=Decimal("1"), date=date(2026, 1, 1), project_hint=None
        )
        job_repo.update_result(job.id, status="done", result={"status": "done"})
        stale = datetime.now(timezone.utc) - timedelta(minutes=31)
        session.execute(update(AssistantJobModel).where(AssistantJobModel.id == job.id).values(updated_at=stale))
        session.commit()

        queue = FakeQueue()
        job_runner = _make_job_runner(FetchOutcome(result=FetchResult(status="done"), pdf_path=None))

        processed = asyncio.run(
            run_once(
                session=session,
                job_repo=job_repo,
                storage=FakeStorage(),
                queue=queue,
                job_runner=job_runner,
                chrome_path="",
                profile_dir="",
                downloads_dir="",
                deepseek_api_key="",
                offpeak_only=False,
                now=datetime.now(timezone.utc),
            )
        )

        assert processed is False  # no *new* job was claimed
        assert queue.enqueued == [("app.application.assistant.jobs.process_fetched_invoice", (str(job.id),))]

        # A second idle poll must not re-enqueue the same job again — the first reap
        # already bumped `updated_at`, resetting the 30-minute window.
        queue.enqueued.clear()
        asyncio.run(
            run_once(
                session=session,
                job_repo=job_repo,
                storage=FakeStorage(),
                queue=queue,
                job_runner=job_runner,
                chrome_path="",
                profile_dir="",
                downloads_dir="",
                deepseek_api_key="",
                offpeak_only=False,
                now=datetime.now(timezone.utc),
            )
        )
        assert queue.enqueued == []

    def test_a_fresh_done_row_is_not_reaped(self, session) -> None:
        job_repo = SqlAlchemyAssistantJobRepository(session)
        job_repo.add(
            user_id=uuid4(), merchant="leroymerlin", amount_ttc=Decimal("1"), date=date(2026, 1, 1), project_hint=None
        )
        job = job_repo.add(
            user_id=uuid4(), merchant="pointp", amount_ttc=Decimal("2"), date=date(2026, 1, 2), project_hint=None
        )
        job_repo.update_result(job.id, status="done", result={"status": "done"})
        queue = FakeQueue()
        job_runner = _make_job_runner(FetchOutcome(result=FetchResult(status="done"), pdf_path=None))

        # The other job (still `queued`) gets claimed first — process it out of the way
        # so the second `run_once` call below exercises the idle/reap branch.
        asyncio.run(
            run_once(
                session=session,
                job_repo=job_repo,
                storage=FakeStorage(),
                queue=queue,
                job_runner=job_runner,
                chrome_path="",
                profile_dir="",
                downloads_dir="",
                deepseek_api_key="",
                offpeak_only=False,
                now=datetime.now(timezone.utc),
            )
        )
        queue.enqueued.clear()

        asyncio.run(
            run_once(
                session=session,
                job_repo=job_repo,
                storage=FakeStorage(),
                queue=queue,
                job_runner=job_runner,
                chrome_path="",
                profile_dir="",
                downloads_dir="",
                deepseek_api_key="",
                offpeak_only=False,
                now=datetime.now(timezone.utc),
            )
        )
        assert queue.enqueued == []  # `job` (status=done) is fresh, not reap-worthy yet

    def test_a_stale_find_product_job_is_reaped_to_process_product_search(self, session) -> None:
        job_repo = SqlAlchemyAssistantJobRepository(session)
        job = job_repo.add(job_type="find_product", user_id=uuid4(), params={"ident": {}, "photo_sha256": "x"})
        job_repo.update_result(job.id, status="done", result={"status": "done", "candidates": []})
        stale = datetime.now(timezone.utc) - timedelta(minutes=31)
        session.execute(update(AssistantJobModel).where(AssistantJobModel.id == job.id).values(updated_at=stale))
        session.commit()

        queue = FakeQueue()
        processed = asyncio.run(
            run_once(
                session=session,
                job_repo=job_repo,
                storage=FakeStorage(),
                queue=queue,
                job_runner=_make_job_runner(FetchOutcome(result=FetchResult(status="done"), pdf_path=None)),
                chrome_path="",
                profile_dir="",
                downloads_dir="",
                deepseek_api_key="",
                offpeak_only=False,
                now=datetime.now(timezone.utc),
            )
        )

        assert processed is False
        assert queue.enqueued == [("app.application.assistant.jobs.process_product_search", (str(job.id),))]


class TestRunForeverFeatureFlag:
    """Review finding NEW-H3: the `ai-browser` container's own poll loop must also
    honour `FEATURE_ASSISTANT` — `run_once`'s tests above prove the claim/process
    behaviour; these prove the flag stops the loop from ever calling `run_once` (and
    therefore `job_repo.claim_next`) in the first place."""

    def test_flag_off_claims_no_jobs(self, session) -> None:
        job_repo = SqlAlchemyAssistantJobRepository(session)
        job = job_repo.add(
            user_id=uuid4(), merchant="leroymerlin", amount_ttc=Decimal("10"), date=date(2026, 9, 10), project_hint=None
        )
        job_runner = _make_job_runner(FetchOutcome(result=FetchResult(status="done"), pdf_path=None))
        stop_event = asyncio.Event()
        calls = {"n": 0}

        def assistant_enabled() -> bool:
            # Set the event before returning False: the loop's `wait_for(stop_event...)`
            # then resolves immediately instead of sleeping for the real idle interval —
            # this test only needs to prove `claim_next` was never reached, not exercise
            # the sleep itself.
            calls["n"] += 1
            stop_event.set()
            return False

        asyncio.run(
            run_forever(
                session=session,
                job_repo=job_repo,
                storage=FakeStorage(),
                queue=FakeQueue(),
                job_runner=job_runner,
                chrome_path="",
                profile_dir="",
                downloads_dir="",
                deepseek_api_key="",
                offpeak_only=False,
                stop_event=stop_event,
                assistant_enabled=assistant_enabled,
            )
        )

        assert calls["n"] == 1
        assert job_runner.calls == []  # type: ignore[attr-defined]
        # Still `queued` — `claim_next` (which would flip it to `running`) was never
        # reached while the flag reported disabled.
        assert job_repo.find_by_id(job.id).status == "queued"

    def test_flag_on_processes_normally(self, session) -> None:
        job_repo = SqlAlchemyAssistantJobRepository(session)
        job = job_repo.add(
            user_id=uuid4(), merchant="leroymerlin", amount_ttc=Decimal("10"), date=date(2026, 9, 10), project_hint=None
        )
        job_runner = _make_job_runner(FetchOutcome(result=FetchResult(status="done"), pdf_path=None))
        stop_event = asyncio.Event()
        calls = {"n": 0}

        def assistant_enabled() -> bool:
            # Stop right after the job has had a chance to be claimed and processed —
            # avoids the real IDLE_SLEEP_SECONDS wait once there is nothing left to do.
            calls["n"] += 1
            if calls["n"] >= 2:
                stop_event.set()
            return True

        asyncio.run(
            run_forever(
                session=session,
                job_repo=job_repo,
                storage=FakeStorage(),
                queue=FakeQueue(),
                job_runner=job_runner,
                chrome_path="",
                profile_dir="",
                downloads_dir="",
                deepseek_api_key="",
                offpeak_only=False,
                stop_event=stop_event,
                assistant_enabled=assistant_enabled,
            )
        )

        assert len(job_runner.calls) == 1  # type: ignore[attr-defined]
        assert job_repo.find_by_id(job.id).status == "done"
