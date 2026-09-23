"""Unit tests for `app.application.assistant.jobs` — the RQ entry points' own
`FEATURE_ASSISTANT` gate (review finding NEW-H3). `RqAssistantDispatcher` already
refuses to *enqueue* while the flag is off; these tests prove the *consumption* side: a
job that somehow reaches a worker (enqueued before the flag flipped, or a stale job
sitting in Redis) must not touch `AssistantService`/the invoice-fetch feature while the
flag (or either API key) is off.

Also covers two later fixes to the same entry points: the daily cost cap is now also
checked here, before a browser-result feature handler runs, and a browser-result left
unprocessed for over 24h while the assistant is disabled is expired instead of
eventually being acted on and posted late once the flag comes back on.

`create_app`/`get_container` are imported locally inside each entry point (so the web
process never has to import the heavier AI pipeline just to enqueue a job) — patched
here at their source modules (`app.create_app`, `wiring.get_container`), which is where
those local imports resolve them from at call time.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

import pytest
from flask import Flask

from app.application.assistant import jobs
from app.application.assistant.jobs_repo import AssistantJobRecord


class _FakeAssistantService:
    def __init__(self) -> None:
        self.handle_message_calls: list[tuple[Any, Any]] = []
        self.handle_action_calls: list[tuple[Any, Any, Any, Any]] = []

    def handle_message(self, *, user_id: Any, message_id: Any) -> None:
        self.handle_message_calls.append((user_id, message_id))

    def handle_action(self, *, user_id: Any, message_id: Any, action: Any, payload: Any) -> None:
        self.handle_action_calls.append((user_id, message_id, action, payload))


class _FakeInvoiceFetchFeature:
    def __init__(self) -> None:
        self.on_result_calls: list[Any] = []

    def on_result(self, job_id: Any, *, messenger: Any, trace_id: Any) -> None:
        self.on_result_calls.append(job_id)


class _FakeMaterialFeature:
    def __init__(self) -> None:
        self.on_result_calls: list[Any] = []

    def on_result(self, job_id: Any, *, messenger: Any, trace_id: Any) -> None:
        self.on_result_calls.append(job_id)


class _FakeCostLedger:
    def __init__(self, *, over: bool = False) -> None:
        self._over = over

    def over_cap(self) -> bool:
        return self._over


class _FakeProjectCompanyReader:
    def project_company_id(self, project_id: UUID) -> Optional[UUID]:
        return None


class _FakeMessenger:
    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []

    def update_job_status(
        self,
        message_id: UUID,
        *,
        state: str,
        text: str,
        scope: Any,
        progress: Optional[float] = None,
        terminal: bool = False,
    ) -> None:
        self.updates.append({"message_id": message_id, "state": state, "text": text, "terminal": terminal})


class _FakeJobRepo:
    def __init__(self, jobs: Optional[list[AssistantJobRecord]] = None) -> None:
        self._jobs: dict[UUID, AssistantJobRecord] = {j.id: j for j in (jobs or [])}
        self.mark_processed_calls: list[UUID] = []
        self.status_updates: list[tuple[UUID, str]] = []

    def find_by_id(self, job_id: UUID) -> Optional[AssistantJobRecord]:
        return self._jobs.get(job_id)

    def mark_processed(self, job_id: UUID) -> bool:
        self.mark_processed_calls.append(job_id)
        job = self._jobs[job_id]
        if job.processed_at is not None:
            return False
        self._jobs[job_id] = replace(job, processed_at=datetime.now(timezone.utc))
        return True

    def update_status(
        self, job_id: UUID, *, status: str, attempts: Optional[int] = None, run_after: Optional[datetime] = None
    ) -> None:
        self.status_updates.append((job_id, status))
        job = self._jobs[job_id]
        updates: dict[str, Any] = {"status": status}
        if run_after is not None:
            updates["run_after"] = run_after
        self._jobs[job_id] = replace(job, **updates)


def _job_record(
    *,
    job_id: Optional[UUID] = None,
    job_type: str = "fetch_invoice",
    updated_at: Optional[datetime] = None,
    processed_at: Optional[datetime] = None,
    status_message_id: Optional[UUID] = None,
    channel_key: Optional[str] = None,
) -> AssistantJobRecord:
    now = datetime.now(timezone.utc)
    return AssistantJobRecord(
        id=job_id or uuid4(),
        type=job_type,
        user_id=uuid4(),
        merchant="Leroy Merlin" if job_type == "fetch_invoice" else None,
        amount_ttc=None,
        date=None,
        project_hint=None,
        status="done",
        attempts=0,
        run_after=now,
        result=None,
        pdf_storage_key=None,
        status_message_id=status_message_id,
        lang="fr",
        processed_at=processed_at,
        created_at=now,
        updated_at=updated_at or now,
        params=None,
        channel_key=channel_key,
    )


class _FakeContainer:
    def __init__(self) -> None:
        self.assistant_service: Any = _FakeAssistantService()
        self.assistant_invoice_fetch_feature: Any = _FakeInvoiceFetchFeature()
        self.assistant_material_feature: Any = _FakeMaterialFeature()
        self.assistant_messenger: Any = _FakeMessenger()
        self.assistant_job_repo: Any = None
        self.assistant_cost_ledger: Any = None
        self.assistant_project_company_reader: Any = _FakeProjectCompanyReader()


def _fake_app(*, feature_assistant: bool, deepseek_key: str = "key", typesafe_key: str = "key") -> Flask:
    app = Flask(__name__)
    app.config["FEATURE_ASSISTANT"] = feature_assistant
    app.config["DEEPSEEK_API_KEY"] = deepseek_key
    app.config["TYPESAFE_API_KEY"] = typesafe_key
    return app


@pytest.fixture()
def fake_container(monkeypatch: pytest.MonkeyPatch) -> _FakeContainer:
    container = _FakeContainer()
    monkeypatch.setattr("wiring.get_container", lambda: container)
    return container


class TestFeatureFlagOff:
    def test_handle_message_is_a_noop(self, monkeypatch: pytest.MonkeyPatch, fake_container: _FakeContainer) -> None:
        monkeypatch.setattr("app.create_app", lambda: _fake_app(feature_assistant=False))
        jobs.handle_message(str(uuid4()), str(uuid4()))
        assert fake_container.assistant_service.handle_message_calls == []

    def test_handle_action_is_a_noop(self, monkeypatch: pytest.MonkeyPatch, fake_container: _FakeContainer) -> None:
        monkeypatch.setattr("app.create_app", lambda: _fake_app(feature_assistant=False))
        jobs.handle_action(str(uuid4()), str(uuid4()), "confirm", {})
        assert fake_container.assistant_service.handle_action_calls == []

    def test_process_fetched_invoice_is_a_noop(
        self, monkeypatch: pytest.MonkeyPatch, fake_container: _FakeContainer
    ) -> None:
        monkeypatch.setattr("app.create_app", lambda: _fake_app(feature_assistant=False))
        jobs.process_fetched_invoice(str(uuid4()))
        assert fake_container.assistant_invoice_fetch_feature.on_result_calls == []

    @pytest.mark.parametrize("missing_key", ["DEEPSEEK_API_KEY", "TYPESAFE_API_KEY"])
    def test_handle_message_is_a_noop_when_a_key_is_missing(
        self, monkeypatch: pytest.MonkeyPatch, fake_container: _FakeContainer, missing_key: str
    ) -> None:
        keys = {"DEEPSEEK_API_KEY": "key", "TYPESAFE_API_KEY": "key"}
        keys[missing_key] = ""
        monkeypatch.setattr(
            "app.create_app",
            lambda: _fake_app(
                feature_assistant=True, deepseek_key=keys["DEEPSEEK_API_KEY"], typesafe_key=keys["TYPESAFE_API_KEY"]
            ),
        )
        jobs.handle_message(str(uuid4()), str(uuid4()))
        assert fake_container.assistant_service.handle_message_calls == []


class TestFeatureFlagOn:
    def test_handle_message_dispatches_to_the_service(
        self, monkeypatch: pytest.MonkeyPatch, fake_container: _FakeContainer
    ) -> None:
        monkeypatch.setattr("app.create_app", lambda: _fake_app(feature_assistant=True))
        user_id, message_id = uuid4(), uuid4()
        jobs.handle_message(str(user_id), str(message_id))
        assert fake_container.assistant_service.handle_message_calls == [(user_id, message_id)]

    def test_handle_action_dispatches_to_the_service(
        self, monkeypatch: pytest.MonkeyPatch, fake_container: _FakeContainer
    ) -> None:
        monkeypatch.setattr("app.create_app", lambda: _fake_app(feature_assistant=True))
        user_id, message_id = uuid4(), uuid4()
        jobs.handle_action(str(user_id), str(message_id), "confirm", {"a": 1})
        assert fake_container.assistant_service.handle_action_calls == [(user_id, message_id, "confirm", {"a": 1})]

    def test_process_fetched_invoice_dispatches_to_the_feature(
        self, monkeypatch: pytest.MonkeyPatch, fake_container: _FakeContainer
    ) -> None:
        monkeypatch.setattr("app.create_app", lambda: _fake_app(feature_assistant=True))
        job_id = uuid4()
        jobs.process_fetched_invoice(str(job_id))
        assert fake_container.assistant_invoice_fetch_feature.on_result_calls == [job_id]


# ---------------------------------------------------------------------------
# The daily cost cap also gates a queued browser-result job.
# ---------------------------------------------------------------------------


class TestCostCapOnBrowserResults:
    def test_process_fetched_invoice_skips_on_result_over_cap(
        self, monkeypatch: pytest.MonkeyPatch, fake_container: _FakeContainer
    ) -> None:
        monkeypatch.setattr("app.create_app", lambda: _fake_app(feature_assistant=True))
        fake_container.assistant_cost_ledger = _FakeCostLedger(over=True)
        status_message_id = uuid4()
        job = _job_record(status_message_id=status_message_id, channel_key=f"company:{uuid4()}")
        fake_container.assistant_job_repo = _FakeJobRepo([job])

        jobs.process_fetched_invoice(str(job.id))

        assert fake_container.assistant_invoice_fetch_feature.on_result_calls == []
        assert fake_container.assistant_messenger.updates
        assert fake_container.assistant_messenger.updates[-1]["message_id"] == status_message_id
        assert (
            fake_container.assistant_messenger.updates[-1]["text"] == "Le quota du jour est atteint, réessaie demain."
        )
        # A job paused by the cap must not look dead to the user (a re-ask would
        # duplicate the paid work once the cap resets and the original one goes
        # through anyway) — "queued", not "failed".
        assert fake_container.assistant_messenger.updates[-1]["state"] == "queued"
        # The job's own status is untouched (stays terminal for the reaper's status
        # filter); only `run_after` moves, past the window this poll already checked.
        assert fake_container.assistant_job_repo.status_updates == [(job.id, job.status)]
        deferred = fake_container.assistant_job_repo._jobs[job.id].run_after
        assert deferred > datetime.now(timezone.utc) + timedelta(hours=1)

    def test_process_product_search_skips_on_result_over_cap(
        self, monkeypatch: pytest.MonkeyPatch, fake_container: _FakeContainer
    ) -> None:
        monkeypatch.setattr("app.create_app", lambda: _fake_app(feature_assistant=True))
        fake_container.assistant_cost_ledger = _FakeCostLedger(over=True)
        job = _job_record(job_type="find_product", status_message_id=uuid4(), channel_key=f"company:{uuid4()}")
        fake_container.assistant_job_repo = _FakeJobRepo([job])

        jobs.process_product_search(str(job.id))

        assert fake_container.assistant_material_feature.on_result_calls == []
        assert fake_container.assistant_messenger.updates[-1]["state"] == "queued"
        deferred = fake_container.assistant_job_repo._jobs[job.id].run_after
        assert deferred > datetime.now(timezone.utc) + timedelta(hours=1)

    def test_process_fetched_invoice_runs_normally_under_the_cap(
        self, monkeypatch: pytest.MonkeyPatch, fake_container: _FakeContainer
    ) -> None:
        monkeypatch.setattr("app.create_app", lambda: _fake_app(feature_assistant=True))
        fake_container.assistant_cost_ledger = _FakeCostLedger(over=False)
        job = _job_record()
        fake_container.assistant_job_repo = _FakeJobRepo([job])

        jobs.process_fetched_invoice(str(job.id))

        assert fake_container.assistant_invoice_fetch_feature.on_result_calls == [job.id]


# ---------------------------------------------------------------------------
# An unprocessed browser result is expired, not posted late, once the assistant has
# been disabled for over 24h.
# ---------------------------------------------------------------------------


class TestExpireStaleResultsWhileDisabled:
    def test_a_stale_unprocessed_result_is_marked_expired_and_processed(
        self, monkeypatch: pytest.MonkeyPatch, fake_container: _FakeContainer
    ) -> None:
        monkeypatch.setattr("app.create_app", lambda: _fake_app(feature_assistant=False))
        status_message_id = uuid4()
        stale_at = datetime.now(timezone.utc) - timedelta(hours=25)
        job = _job_record(updated_at=stale_at, status_message_id=status_message_id, channel_key=f"company:{uuid4()}")
        job_repo = _FakeJobRepo([job])
        fake_container.assistant_job_repo = job_repo

        jobs.process_fetched_invoice(str(job.id))

        assert job_repo.mark_processed_calls == [job.id]
        assert job_repo.status_updates == [(job.id, "expired")]
        assert fake_container.assistant_messenger.updates
        assert fake_container.assistant_messenger.updates[-1]["message_id"] == status_message_id

    def test_a_recent_unprocessed_result_is_left_alone_while_disabled(
        self, monkeypatch: pytest.MonkeyPatch, fake_container: _FakeContainer
    ) -> None:
        monkeypatch.setattr("app.create_app", lambda: _fake_app(feature_assistant=False))
        job = _job_record(updated_at=datetime.now(timezone.utc))
        job_repo = _FakeJobRepo([job])
        fake_container.assistant_job_repo = job_repo

        jobs.process_product_search(str(job.id))

        assert job_repo.mark_processed_calls == []
        assert job_repo.status_updates == []

    def test_an_already_processed_result_is_left_alone_while_disabled(
        self, monkeypatch: pytest.MonkeyPatch, fake_container: _FakeContainer
    ) -> None:
        monkeypatch.setattr("app.create_app", lambda: _fake_app(feature_assistant=False))
        stale_at = datetime.now(timezone.utc) - timedelta(hours=48)
        job = _job_record(updated_at=stale_at, processed_at=datetime.now(timezone.utc))
        job_repo = _FakeJobRepo([job])
        fake_container.assistant_job_repo = job_repo

        jobs.process_fetched_invoice(str(job.id))

        assert job_repo.mark_processed_calls == []
        assert job_repo.status_updates == []


class TestExpireStaleResultsWhileEnabled:
    """The assistant coming back on after being off for over a day (or a stuck
    ai-browser leaving a result unclaimed that long) must not run the pipeline on a
    backlog result and post it as if it had just arrived — the same staleness check
    the disabled branch already applied now also runs while enabled."""

    def test_a_stale_unprocessed_fetch_result_is_expired_instead_of_run(
        self, monkeypatch: pytest.MonkeyPatch, fake_container: _FakeContainer
    ) -> None:
        monkeypatch.setattr("app.create_app", lambda: _fake_app(feature_assistant=True))
        status_message_id = uuid4()
        stale_at = datetime.now(timezone.utc) - timedelta(hours=25)
        job = _job_record(updated_at=stale_at, status_message_id=status_message_id, channel_key=f"company:{uuid4()}")
        job_repo = _FakeJobRepo([job])
        fake_container.assistant_job_repo = job_repo

        jobs.process_fetched_invoice(str(job.id))

        assert job_repo.mark_processed_calls == [job.id]
        assert job_repo.status_updates == [(job.id, "expired")]
        assert fake_container.assistant_invoice_fetch_feature.on_result_calls == []
        assert fake_container.assistant_messenger.updates
        assert fake_container.assistant_messenger.updates[-1]["message_id"] == status_message_id

    def test_a_stale_unprocessed_product_search_result_is_expired_instead_of_run(
        self, monkeypatch: pytest.MonkeyPatch, fake_container: _FakeContainer
    ) -> None:
        monkeypatch.setattr("app.create_app", lambda: _fake_app(feature_assistant=True))
        stale_at = datetime.now(timezone.utc) - timedelta(hours=25)
        job = _job_record(job_type="find_product", updated_at=stale_at)
        job_repo = _FakeJobRepo([job])
        fake_container.assistant_job_repo = job_repo

        jobs.process_product_search(str(job.id))

        assert job_repo.mark_processed_calls == [job.id]
        assert job_repo.status_updates == [(job.id, "expired")]
        assert fake_container.assistant_material_feature.on_result_calls == []

    def test_a_recent_unprocessed_result_still_runs_normally_while_enabled(
        self, monkeypatch: pytest.MonkeyPatch, fake_container: _FakeContainer
    ) -> None:
        monkeypatch.setattr("app.create_app", lambda: _fake_app(feature_assistant=True))
        job = _job_record(updated_at=datetime.now(timezone.utc))
        job_repo = _FakeJobRepo([job])
        fake_container.assistant_job_repo = job_repo

        jobs.process_fetched_invoice(str(job.id))

        assert job_repo.mark_processed_calls == []
        assert fake_container.assistant_invoice_fetch_feature.on_result_calls == [job.id]
