"""Unit tests for the poll loop (`app.infrastructure.browser_worker.worker`).

Uses a REAL `SqlAlchemyAssistantJobRepository` against the in-memory SQLite DB (the
`session` fixture) plus a fake agent runner and a fake RQ queue — proves the loop claims
one job, stores its result, updates the job_status chat message, and enqueues
`process_fetched_invoice`, all without importing browser-use or touching a real browser.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from app.application.assistant.jobs_repo import AssistantJobRecord
from app.application.assistant.models import FetchResult
from app.infrastructure.browser_worker.agent import FetchOutcome
from app.infrastructure.browser_worker.worker import is_offpeak, run_once
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
