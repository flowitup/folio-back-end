"""Unit tests for `SqlAlchemyAssistantJobRepository` against the in-memory SQLite DB
(the `session` fixture) — job lifecycle state machine, dedupe window, `claim_next`
ordering (SQLite takes the plain-SELECT branch, no FOR UPDATE SKIP LOCKED)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import update

from app.infrastructure.database.models.assistant_job import AssistantJobModel
from app.infrastructure.database.repositories.sqlalchemy_assistant_job_repository import (
    SqlAlchemyAssistantJobRepository,
)


def _repo(session) -> SqlAlchemyAssistantJobRepository:
    return SqlAlchemyAssistantJobRepository(session)


def _set_updated_at(session, job_id, when: datetime) -> None:
    """Test-only backdoor: every repo write stamps `updated_at = now()` itself, so
    ageing a row for the reaper tests needs a direct column write."""
    session.execute(update(AssistantJobModel).where(AssistantJobModel.id == job_id).values(updated_at=when))
    session.commit()


class TestAddAndFind:
    def test_add_then_find_by_id(self, session) -> None:
        repo = _repo(session)
        user_id = uuid4()
        job = repo.add(
            user_id=user_id,
            merchant="leroymerlin",
            amount_ttc=Decimal("79.54"),
            date=date(2026, 9, 10),
            project_hint="Villa Arcueil",
        )
        assert job.status == "queued"
        assert job.attempts == 0
        assert job.type == "fetch_invoice"

        found = repo.find_by_id(job.id)
        assert found is not None
        assert found.merchant == "leroymerlin"
        assert found.amount_ttc == Decimal("79.54")
        assert found.project_hint == "Villa Arcueil"

    def test_find_by_id_missing_returns_none(self, session) -> None:
        assert _repo(session).find_by_id(uuid4()) is None


class TestSetStatusMessage:
    def test_attaches_status_message_id(self, session) -> None:
        repo = _repo(session)
        job = repo.add(
            user_id=uuid4(), merchant="pointp", amount_ttc=Decimal("10.00"), date=date(2026, 9, 1), project_hint=None
        )
        message_id = uuid4()
        # No FK constraint enforced by SQLite in this fixture — the column write itself
        # is what's under test.
        repo.set_status_message(job.id, message_id)
        assert repo.find_by_id(job.id).status_message_id == message_id


class TestFindDuplicate:
    def test_matches_active_job_within_window(self, session) -> None:
        repo = _repo(session)
        user_id = uuid4()
        repo.add(
            user_id=user_id,
            merchant="leroymerlin",
            amount_ttc=Decimal("79.54"),
            date=date(2026, 9, 10),
            project_hint=None,
        )
        since = datetime.now(timezone.utc) - timedelta(hours=24)

        found = repo.find_duplicate(
            user_id=user_id, merchant="leroymerlin", amount_ttc=Decimal("79.54"), date=date(2026, 9, 10), since=since
        )
        assert found is not None

    def test_no_match_for_different_amount(self, session) -> None:
        repo = _repo(session)
        user_id = uuid4()
        repo.add(
            user_id=user_id,
            merchant="leroymerlin",
            amount_ttc=Decimal("79.54"),
            date=date(2026, 9, 10),
            project_hint=None,
        )
        since = datetime.now(timezone.utc) - timedelta(hours=24)

        found = repo.find_duplicate(
            user_id=user_id, merchant="leroymerlin", amount_ttc=Decimal("50.00"), date=date(2026, 9, 10), since=since
        )
        assert found is None

    def test_terminal_job_is_not_a_duplicate(self, session) -> None:
        repo = _repo(session)
        user_id = uuid4()
        job = repo.add(
            user_id=user_id,
            merchant="leroymerlin",
            amount_ttc=Decimal("79.54"),
            date=date(2026, 9, 10),
            project_hint=None,
        )
        repo.update_result(job.id, status="done")
        since = datetime.now(timezone.utc) - timedelta(hours=24)

        found = repo.find_duplicate(
            user_id=user_id, merchant="leroymerlin", amount_ttc=Decimal("79.54"), date=date(2026, 9, 10), since=since
        )
        assert found is None

    def test_outside_window_is_not_a_duplicate(self, session) -> None:
        repo = _repo(session)
        user_id = uuid4()
        repo.add(
            user_id=user_id,
            merchant="leroymerlin",
            amount_ttc=Decimal("79.54"),
            date=date(2026, 9, 10),
            project_hint=None,
        )
        since = datetime.now(timezone.utc) + timedelta(hours=1)  # job was created "before" this cutoff

        found = repo.find_duplicate(
            user_id=user_id, merchant="leroymerlin", amount_ttc=Decimal("79.54"), date=date(2026, 9, 10), since=since
        )
        assert found is None


class TestClaimNext:
    def test_claims_oldest_due_queued_job(self, session) -> None:
        repo = _repo(session)
        now = datetime.now(timezone.utc)
        older = repo.add(
            user_id=uuid4(), merchant="pointp", amount_ttc=Decimal("1"), date=date(2026, 1, 1), project_hint=None
        )
        repo.update_status(older.id, status="queued", run_after=now - timedelta(minutes=10))
        newer = repo.add(
            user_id=uuid4(), merchant="pointp", amount_ttc=Decimal("2"), date=date(2026, 1, 1), project_hint=None
        )
        repo.update_status(newer.id, status="queued", run_after=now - timedelta(minutes=5))

        claimed = repo.claim_next(now)
        assert claimed is not None
        assert claimed.id == older.id
        assert claimed.status == "running"

    def test_does_not_claim_future_run_after(self, session) -> None:
        repo = _repo(session)
        now = datetime.now(timezone.utc)
        job = repo.add(
            user_id=uuid4(), merchant="pointp", amount_ttc=Decimal("1"), date=date(2026, 1, 1), project_hint=None
        )
        repo.update_status(job.id, status="queued", run_after=now + timedelta(hours=1))

        assert repo.claim_next(now) is None

    def test_returns_none_when_nothing_queued(self, session) -> None:
        assert _repo(session).claim_next(datetime.now(timezone.utc)) is None

    def test_does_not_reclaim_running_job(self, session) -> None:
        repo = _repo(session)
        job = repo.add(
            user_id=uuid4(), merchant="pointp", amount_ttc=Decimal("1"), date=date(2026, 1, 1), project_hint=None
        )
        now = datetime.now(timezone.utc) + timedelta(seconds=1)
        first = repo.claim_next(now)
        assert first is not None and first.id == job.id
        assert repo.claim_next(now) is None


class TestUpdateStatusAndResult:
    def test_update_status_sets_attempts_and_run_after(self, session) -> None:
        repo = _repo(session)
        job = repo.add(
            user_id=uuid4(), merchant="pointp", amount_ttc=Decimal("1"), date=date(2026, 1, 1), project_hint=None
        )
        run_after = datetime.now(timezone.utc) + timedelta(hours=2)
        repo.update_status(job.id, status="queued", attempts=1, run_after=run_after)

        updated = repo.find_by_id(job.id)
        assert updated.status == "queued"
        assert updated.attempts == 1

    def test_update_result_sets_pdf_key_and_result(self, session) -> None:
        repo = _repo(session)
        job = repo.add(
            user_id=uuid4(), merchant="pointp", amount_ttc=Decimal("1"), date=date(2026, 1, 1), project_hint=None
        )
        repo.update_result(job.id, status="done", result={"status": "done"}, pdf_storage_key="assistant/jobs/x.pdf")

        updated = repo.find_by_id(job.id)
        assert updated.status == "done"
        assert updated.result == {"status": "done"}
        assert updated.pdf_storage_key == "assistant/jobs/x.pdf"


class TestListRecentForUser:
    def test_orders_newest_first(self, session) -> None:
        repo = _repo(session)
        user_id = uuid4()
        first = repo.add(
            user_id=user_id, merchant="pointp", amount_ttc=Decimal("1"), date=date(2026, 1, 1), project_hint=None
        )
        second = repo.add(
            user_id=user_id, merchant="pointp", amount_ttc=Decimal("2"), date=date(2026, 1, 2), project_hint=None
        )

        recent = repo.list_recent_for_user(user_id)
        assert [j.id for j in recent] == [second.id, first.id]

    def test_scoped_to_user(self, session) -> None:
        repo = _repo(session)
        mine = repo.add(
            user_id=uuid4(), merchant="pointp", amount_ttc=Decimal("1"), date=date(2026, 1, 1), project_hint=None
        )
        repo.add(user_id=uuid4(), merchant="pointp", amount_ttc=Decimal("1"), date=date(2026, 1, 1), project_hint=None)

        recent = repo.list_recent_for_user(mine.user_id)
        assert [j.id for j in recent] == [mine.id]


class TestReapUnprocessed:
    """Review finding NEW-H2: a job whose terminal status was written by
    `update_result` but whose `process_fetched_invoice` enqueue never happened (Redis
    blip, worker SIGKILL between the two calls) must not stay wedged forever."""

    def test_reaps_a_stale_done_row_with_processed_at_still_null(self, session) -> None:
        repo = _repo(session)
        job = repo.add(
            user_id=uuid4(), merchant="leroymerlin", amount_ttc=Decimal("1"), date=date(2026, 1, 1), project_hint=None
        )
        repo.update_result(job.id, status="done", result={"status": "done"})
        # Captured after `add()` sets its own `run_after` — `reap_unprocessed` now also
        # requires `run_after <= now` (the daily-cost-cap pause defers it into the
        # future), so `now` must not predate a `run_after` this test never touches.
        now = datetime.now(timezone.utc)
        _set_updated_at(session, job.id, now - timedelta(minutes=31))

        reaped = repo.reap_unprocessed(now)

        assert reaped == [job.id]

    def test_reaped_row_is_not_returned_again_within_the_window(self, session) -> None:
        repo = _repo(session)
        job = repo.add(
            user_id=uuid4(), merchant="leroymerlin", amount_ttc=Decimal("1"), date=date(2026, 1, 1), project_hint=None
        )
        repo.update_result(job.id, status="done", result={"status": "done"})
        now = datetime.now(timezone.utc)
        _set_updated_at(session, job.id, now - timedelta(minutes=31))

        first = repo.reap_unprocessed(now)
        second = repo.reap_unprocessed(now)

        assert first == [job.id]
        assert second == []  # the first call already bumped updated_at

    def test_does_not_reap_a_fresh_terminal_row(self, session) -> None:
        repo = _repo(session)
        now = datetime.now(timezone.utc)
        job = repo.add(
            user_id=uuid4(), merchant="leroymerlin", amount_ttc=Decimal("1"), date=date(2026, 1, 1), project_hint=None
        )
        repo.update_result(job.id, status="done", result={"status": "done"})

        assert repo.reap_unprocessed(now) == []

    def test_does_not_reap_an_already_processed_row(self, session) -> None:
        repo = _repo(session)
        now = datetime.now(timezone.utc)
        job = repo.add(
            user_id=uuid4(), merchant="leroymerlin", amount_ttc=Decimal("1"), date=date(2026, 1, 1), project_hint=None
        )
        repo.update_result(job.id, status="done", result={"status": "done"})
        assert repo.mark_processed(job.id) is True
        _set_updated_at(session, job.id, now - timedelta(minutes=31))

        assert repo.reap_unprocessed(now) == []

    def test_does_not_reap_a_row_deferred_into_the_future(self, session) -> None:
        """A job paused by the daily cost cap gets its `run_after` pushed to the next
        Paris midnight (`jobs._notify_job_status`'s callers) — the sweep must leave it
        alone until then even though `updated_at` alone looks stale enough."""
        repo = _repo(session)
        job = repo.add(
            user_id=uuid4(), merchant="leroymerlin", amount_ttc=Decimal("1"), date=date(2026, 1, 1), project_hint=None
        )
        repo.update_result(job.id, status="done", result={"status": "done"})
        now = datetime.now(timezone.utc)
        _set_updated_at(session, job.id, now - timedelta(minutes=31))
        repo.update_status(job.id, status="done", run_after=now + timedelta(hours=6))

        assert repo.reap_unprocessed(now) == []

    def test_does_not_reap_queued_or_running_jobs(self, session) -> None:
        repo = _repo(session)

        running_job = repo.add(
            user_id=uuid4(), merchant="pointp", amount_ttc=Decimal("2"), date=date(2026, 1, 2), project_hint=None
        )
        now = datetime.now(timezone.utc) + timedelta(seconds=1)
        claimed = repo.claim_next(now)
        assert claimed is not None and claimed.id == running_job.id
        _set_updated_at(session, running_job.id, now - timedelta(minutes=31))

        queued = repo.add(
            user_id=uuid4(), merchant="leroymerlin", amount_ttc=Decimal("1"), date=date(2026, 1, 1), project_hint=None
        )
        _set_updated_at(session, queued.id, now - timedelta(minutes=31))

        assert repo.reap_unprocessed(now) == []


class TestClaimNextLeavesNoOpenTransaction:
    def test_idle_claim_ends_the_transaction(self, session) -> None:
        """An idle poll must not keep the FOR UPDATE read open across the worker's sleep:
        that lock blocked ``ALTER TABLE assistant_jobs`` for the whole v0.4.0 deploy."""
        repo = _repo(session)
        assert repo.claim_next(datetime.now(timezone.utc)) is None
        assert session.in_transaction() is False


class TestClaimNextReclaimAttemptCap:
    """A stuck `running` job must eventually stop being reclaimed — the pre-fix
    `claim_next` bumped `attempts` on every reclaim with no cap, so a job that crashes
    its iteration (an S3 outage, a container SIGKILL) re-ran the full browser-agent
    spend every 30 minutes forever."""

    def test_stuck_job_reclaimed_up_to_the_cap_then_swept_to_failed(self, session) -> None:
        repo = _repo(session)
        job = repo.add(
            user_id=uuid4(), merchant="leroymerlin", amount_ttc=Decimal("1"), date=date(2026, 1, 1), project_hint=None
        )
        # `add()` stamps `run_after` with its own `now()` call, after this test's `now`
        # would otherwise be captured — the buffer keeps `run_after <= now` true (same
        # pattern as `TestClaimNext.test_does_not_reclaim_running_job` above).
        now = datetime.now(timezone.utc) + timedelta(seconds=1)
        claimed = repo.claim_next(now)
        assert claimed is not None and claimed.attempts == 0

        for expected_attempts in (1, 2, 3):
            _set_updated_at(session, job.id, now - timedelta(minutes=31))
            reclaimed = repo.claim_next(now)
            assert reclaimed is not None
            assert reclaimed.id == job.id
            assert reclaimed.attempts == expected_attempts
            assert reclaimed.status == "running"

        # A fourth stuck cycle must not be reclaimed again — swept to `failed` instead.
        _set_updated_at(session, job.id, now - timedelta(minutes=31))
        assert repo.claim_next(now) is None
        final = repo.find_by_id(job.id)
        assert final.status == "failed"
        assert final.attempts == 3
        assert final.processed_at is None
