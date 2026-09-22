"""Phase 04 — labor/tasks handlers on channels + admin-only finance/payroll answers.

Unit-level: each feature is constructed directly with fake use-case objects (the same
style as ``test_service.py``'s fakes) rather than through the full HTTP/DI stack, since
``tests/conftest.py``'s ``invitation_app`` fixture does not wire task/day-roster use
cases at all (a pre-existing gap, unrelated to this phase).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID, uuid4

from app.application.assistant.features.admin_answers import AdminAnswersFeature
from app.application.assistant.features.labor import LaborFeature
from app.application.assistant.features.tasks import TasksFeature
from app.application.assistant.messages import AssistantMessenger
from app.application.assistant.models import ChannelScope, TaskDraft
from app.application.assistant.ports import ChoiceQuestion, Decision
from app.application.assistant.project_resolution import accessible_projects, resolve_project
from app.application.labor.bulk_log_attendance import BulkLogAttendanceResponse
from app.application.labor.get_day_roster_usecase import RosterRow
from app.application.labor.list_pending_attendance import PendingAttendanceDto
from app.application.labor.validate_attendance import ValidateAttendanceResponse
from app.domain.entities.chat_message import ChannelRef, ChatMessage
from app.domain.entities.project import Project
from app.domain.entities.task import Task, TaskPriority, TaskStatus
from tests.fakes.ai import ScriptedVision


# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------


class FakeMessageRepo:
    def __init__(self) -> None:
        self.messages: dict[UUID, ChatMessage] = {}

    def add(self, message: ChatMessage) -> None:
        self.messages[message.id] = message

    def find_by_id(self, message_id: UUID) -> Optional[ChatMessage]:
        return self.messages.get(message_id)

    def update_payload(self, message_id: UUID, payload: dict[str, Any]) -> None:
        pass

    def list_recent_addressed(self, channel: ChannelRef, limit: int = 10) -> list[ChatMessage]:
        return []


class FakeSession:
    def commit(self) -> None:
        pass


def _messenger() -> AssistantMessenger:
    return AssistantMessenger(FakeMessageRepo(), FakeSession())


def _last(messenger: AssistantMessenger) -> ChatMessage:
    return sorted(messenger._messages.messages.values(), key=lambda m: m.created_at)[-1]  # type: ignore[attr-defined]


def _company_scope(company_id: UUID, is_admin: bool = False) -> ChannelScope:
    return ChannelScope(
        kind="admin" if is_admin else "company",
        company_id=company_id,
        project_id=None,
        is_admin_channel=is_admin,
        asker_id=uuid4(),
    )


def _project_scope(project_id: UUID) -> ChannelScope:
    return ChannelScope(
        kind="project", company_id=uuid4(), project_id=project_id, is_admin_channel=False, asker_id=uuid4()
    )


class FakeAuthzReader:
    def __init__(self, *, permitted: bool = True) -> None:
        self._permitted = permitted

    def company_role_for(self, user_id: UUID, company_id: UUID) -> "str | None":
        return "admin" if self._permitted else "member"

    def is_assigned(self, user_id: UUID, project_id: UUID) -> bool:
        return True

    def project_company_id(self, project_id: UUID) -> "UUID | None":
        return None

    def project_exists(self, project_id: UUID) -> bool:
        return True

    def is_platform_ops(self, user_id: UUID) -> bool:
        return False


class PermissiveAuthzReader:
    """Grants every project:* permission — used to isolate a use case's own behaviour
    from ``has_permission``'s matrix, mirroring the rest of the assistant test suite.

    ``company_role_for``/``project_company_id`` must agree on a real company id, or
    ``has_permission`` resolves an empty permission set regardless of role (D8's
    ``effective_permissions`` treats an unresolvable project as "no access").
    """

    def __init__(self) -> None:
        self.company_id = uuid4()

    def company_role_for(self, user_id: UUID, company_id: UUID) -> "str | None":
        return "admin"

    def is_assigned(self, user_id: UUID, project_id: UUID) -> bool:
        return True

    def project_company_id(self, project_id: UUID) -> "UUID | None":
        return self.company_id

    def project_exists(self, project_id: UUID) -> bool:
        return True

    def is_platform_ops(self, user_id: UUID) -> bool:
        return False

    def grants_for(self, user_id: UUID, company_id: UUID, project_id: "UUID | None") -> list:
        return []


class RestrictiveAuthzReader(PermissiveAuthzReader):
    def company_role_for(self, user_id: UUID, company_id: UUID) -> "str | None":
        return "member"


class NoAccessAuthzReader(PermissiveAuthzReader):
    """No `user_company_access` row at all — every permission check on this project
    (including `project:read`, which a mere `member` role always keeps) resolves to an
    empty set. Used where a test needs a genuine "cannot even read this project" caller,
    unlike `RestrictiveAuthzReader` (a `member`, who DOES retain read)."""

    def company_role_for(self, user_id: UUID, company_id: UUID) -> "str | None":
        return None


class FakeProjectRepo:
    def __init__(self, projects: list[Project]) -> None:
        self._by_id = {p.id: p for p in projects}

    def find_by_id(self, project_id: UUID) -> Optional[Project]:
        return self._by_id.get(project_id)

    def list_for_user_and_companies(self, user_id: UUID, company_ids: list[UUID]) -> list[Project]:
        return list(self._by_id.values())


# ---------------------------------------------------------------------------
# resolve_project
# ---------------------------------------------------------------------------


class FixedProjectDecision:
    def __init__(self, label: str, confidence: float) -> None:
        self._label = label
        self._confidence = confidence

    def decide(self, state: dict, questions: dict) -> Decision:
        assert isinstance(questions["project"], ChoiceQuestion)
        return Decision(choices={"project": (self._label, self._confidence, {})}, nouls={})


def _project(name: str) -> Project:
    from datetime import datetime, timezone

    return Project(id=uuid4(), name=name, owner_id=uuid4(), created_at=datetime.now(timezone.utc))


class _ResolveProjectAuthzReader:
    """Grants `project:read` on every project it is asked about, and agrees with a
    fixed `company_id` for `project_company_id` — the accessible-projects filter (C1)
    needs the scope's company and the project's owning company to actually match, or
    every candidate gets filtered out regardless of the union step above it."""

    def __init__(self, company_id: UUID) -> None:
        self.company_id = company_id

    def is_platform_ops(self, user_id: UUID) -> bool:
        return False

    def admin_company_ids(self, user_id: UUID) -> list[UUID]:
        return [self.company_id]

    def project_company_id(self, project_id: UUID) -> "UUID | None":
        return self.company_id

    def company_role_for(self, user_id: UUID, company_id: UUID) -> "str | None":
        return "admin"

    def is_assigned(self, user_id: UUID, project_id: UUID) -> bool:
        return True

    def grants_for(self, user_id: UUID, company_id: UUID, project_id: "UUID | None") -> list:
        return []


class TestResolveProject:
    def test_project_channel_returns_its_own_project_without_calling_jev(self) -> None:
        project_id = uuid4()
        scope = _project_scope(project_id)
        messenger = _messenger()

        class ExplodingDecisions:
            def decide(self, state, questions):
                raise AssertionError("resolve_project must not call Jev for a project channel")

        result = resolve_project(
            scope=scope,
            text="peu importe",
            user_id=uuid4(),
            message_id=uuid4(),
            lang="fr",
            trace_id="t",
            pending_intent="ask_roster",
            project_repo=FakeProjectRepo([]),
            decisions=ExplodingDecisions(),
            messenger=messenger,
            authz_reader=_ResolveProjectAuthzReader(uuid4()),
        )
        assert result == project_id

    def test_company_channel_with_a_single_project_auto_resolves(self) -> None:
        p = _project("Villa Arcueil")
        company_id = uuid4()
        scope = _company_scope(company_id)
        messenger = _messenger()

        class ExplodingDecisions:
            def decide(self, state, questions):
                raise AssertionError("a single project should never need Jev")

        result = resolve_project(
            scope=scope,
            text="peu importe",
            user_id=uuid4(),
            message_id=uuid4(),
            lang="fr",
            trace_id="t",
            pending_intent="ask_roster",
            project_repo=FakeProjectRepo([p]),
            decisions=ExplodingDecisions(),
            messenger=messenger,
            authz_reader=_ResolveProjectAuthzReader(company_id),
        )
        assert result == p.id

    def test_company_channel_with_no_project_posts_the_none_template(self) -> None:
        company_id = uuid4()
        scope = _company_scope(company_id)
        messenger = _messenger()
        message_id = uuid4()

        class ExplodingDecisions:
            def decide(self, state, questions):
                raise AssertionError("no projects to disambiguate")

        result = resolve_project(
            scope=scope,
            text="x",
            user_id=uuid4(),
            message_id=message_id,
            lang="fr",
            trace_id="t",
            pending_intent="ask_roster",
            project_repo=FakeProjectRepo([]),
            decisions=ExplodingDecisions(),
            messenger=messenger,
            authz_reader=_ResolveProjectAuthzReader(company_id),
        )
        assert result is None
        assert "chantier" in (_last(messenger).body or "").lower()

    def test_high_confidence_jev_match_auto_resolves(self) -> None:
        p1, p2 = _project("A"), _project("B")
        company_id = uuid4()
        scope = _company_scope(company_id)
        messenger = _messenger()
        result = resolve_project(
            scope=scope,
            text="chez A",
            user_id=uuid4(),
            message_id=uuid4(),
            lang="fr",
            trace_id="t",
            pending_intent="ask_roster",
            project_repo=FakeProjectRepo([p1, p2]),
            decisions=FixedProjectDecision(str(p1.id), 0.9),
            messenger=messenger,
            authz_reader=_ResolveProjectAuthzReader(company_id),
        )
        assert result == p1.id

    def test_low_confidence_posts_a_choice_addressed_to_the_asker(self) -> None:
        p1, p2 = _project("A"), _project("B")
        company_id = uuid4()
        scope = _company_scope(company_id)
        messenger = _messenger()
        user_id = uuid4()
        result = resolve_project(
            scope=scope,
            text="quelque part",
            user_id=user_id,
            message_id=uuid4(),
            lang="fr",
            trace_id="t",
            pending_intent="ask_roster",
            project_repo=FakeProjectRepo([p1, p2]),
            decisions=FixedProjectDecision(str(p1.id), 0.5),
            messenger=messenger,
            authz_reader=_ResolveProjectAuthzReader(company_id),
        )
        assert result is None
        posted = _last(messenger)
        assert posted.content_type == "choice"
        assert (posted.payload or {}).get("addressed_to") == str(user_id)
        assert len((posted.payload or {}).get("options", [])) == 2

    def test_candidate_list_never_offers_a_project_the_caller_may_not_read(self) -> None:
        """C1: even if the repository union hands back a project of the right company,
        a caller with no `project:read` on it (e.g. an unassigned member) must never see
        it in the choice or have it auto-picked."""
        readable = _project("Readable")
        unreadable = _project("Unreadable")
        company_id = uuid4()
        scope = _company_scope(company_id)
        messenger = _messenger()

        class _PartialAuthzReader(_ResolveProjectAuthzReader):
            def company_role_for(self, user_id: UUID, company_id: UUID) -> "str | None":
                return "member"

            def is_assigned(self, user_id: UUID, project_id: UUID) -> bool:
                return project_id == readable.id

        result = resolve_project(
            scope=scope,
            text="peu importe",
            user_id=uuid4(),
            message_id=uuid4(),
            lang="fr",
            trace_id="t",
            pending_intent="ask_roster",
            project_repo=FakeProjectRepo([readable, unreadable]),
            decisions=None,  # a single readable candidate never calls Jev
            messenger=messenger,
            authz_reader=_PartialAuthzReader(company_id),
        )
        assert result == readable.id

    def test_uses_preload_for_projects_when_the_reader_offers_it(self) -> None:
        """NEW-M1: `accessible_projects` must prime the authz reader's per-request cache
        in ONE batched call before the `has_permission` loop below it, instead of
        leaving every candidate project to issue its own uncached round trip —
        `SqlAlchemyAuthzReader.preload_for_projects` already exists for exactly this
        (`GET /projects` already wires it the same way via `getattr`); this pins the
        opt-in call so a future refactor cannot silently drop it."""

        class _PreloadCountingAuthzReader(_ResolveProjectAuthzReader):
            def __init__(self, company_id: UUID) -> None:
                super().__init__(company_id)
                self.preload_calls: list[tuple[UUID, list[UUID]]] = []

            def preload_for_projects(self, user_id: UUID, project_ids: list[UUID]) -> None:
                self.preload_calls.append((user_id, list(project_ids)))

        company_id = uuid4()
        projects = [_project("A"), _project("B"), _project("C")]
        authz = _PreloadCountingAuthzReader(company_id)
        user_id = uuid4()

        result = accessible_projects(
            project_repo=FakeProjectRepo(projects), authz_reader=authz, user_id=user_id, company_id=company_id
        )

        assert {p.id for p in result} == {p.id for p in projects}
        # One batched call carrying every candidate id — not one per-candidate call.
        assert len(authz.preload_calls) == 1
        called_user_id, called_project_ids = authz.preload_calls[0]
        assert called_user_id == user_id
        assert set(called_project_ids) == {p.id for p in projects}

    def test_tolerates_a_reader_with_no_preload_method(self) -> None:
        """The `getattr` opt-in must not blow up on a reader (e.g. every other fake in
        this file) that never implements `preload_for_projects`."""
        company_id = uuid4()
        p = _project("A")

        result = accessible_projects(
            project_repo=FakeProjectRepo([p]),
            authz_reader=_ResolveProjectAuthzReader(company_id),
            user_id=uuid4(),
            company_id=company_id,
        )

        assert [proj.id for proj in result] == [p.id]


# ---------------------------------------------------------------------------
# LaborFeature
# ---------------------------------------------------------------------------


class FakeDayRosterUseCase:
    def __init__(self, rows: "list[RosterRow] | None") -> None:
        self._rows = rows

    def execute(self, request: Any) -> "list[RosterRow] | None":
        return self._rows


class FakeBulkLogUseCase:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    def execute(self, request: Any) -> BulkLogAttendanceResponse:
        self.calls.append(request)
        return BulkLogAttendanceResponse(created=[str(e.worker_id) for e in request.entries], skipped_worker_ids=[])


class FakeValidateUseCase:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    def execute(self, request: Any) -> ValidateAttendanceResponse:
        self.calls.append(request)
        return ValidateAttendanceResponse(
            id=str(request.entry_id),
            worker_id=str(uuid4()),
            date="2026-09-21",
            status="validated",
            validated_by_user_id=str(request.validator_user_id),
            validated_at="2026-09-21T00:00:00Z",
        )


class FakePendingAttendanceUseCase:
    def __init__(self, items: list[PendingAttendanceDto]) -> None:
        self._items = items

    def execute(self, *, user_id: UUID) -> list[PendingAttendanceDto]:
        return self._items


class FakeWorker:
    def __init__(self, id_: UUID, name: str) -> None:
        self.id = id_
        self.project_id = uuid4()
        self.name = name
        self.person_id = None


class FakeWorkerRepo:
    def __init__(self, workers: list[FakeWorker]) -> None:
        self._workers = workers

    def list_by_project(self, project_id: UUID, active_only: bool = True) -> list[FakeWorker]:
        return self._workers

    def find_by_id(self, worker_id: UUID) -> Optional[FakeWorker]:
        return next((w for w in self._workers if w.id == worker_id), None)


def _labor_feature(
    *, authz=None, workers=None, day_roster_rows=None, pending=None
) -> tuple[LaborFeature, FakeBulkLogUseCase, FakeValidateUseCase]:
    bulk = FakeBulkLogUseCase()
    validate = FakeValidateUseCase()
    feature = LaborFeature(
        authz_reader=authz or PermissiveAuthzReader(),
        worker_repo=FakeWorkerRepo(workers or []),
        project_repo=FakeProjectRepo([]),
        day_roster_usecase=FakeDayRosterUseCase(day_roster_rows),
        bulk_log_usecase=bulk,
        validate_usecase=validate,
        pending_attendance_usecase=FakePendingAttendanceUseCase(pending or []),
    )
    return feature, bulk, validate


class TestLaborFeature:
    def test_ask_roster_happy_path_never_mentions_money(self) -> None:
        rows = [RosterRow(worker_id=uuid4(), name="Minh", status="present", hours=8.0, day_type="full")]
        feature, _, _ = _labor_feature(day_roster_rows=rows)
        messenger = _messenger()
        outcome = feature.ask_roster(
            scope=_project_scope(uuid4()),
            project_id=uuid4(),
            user_id=uuid4(),
            message_id=uuid4(),
            lang="fr",
            messenger=messenger,
            trace_id="t",
        )
        assert outcome == "replied"
        body = _last(messenger).body or ""
        assert "Minh" in body
        assert "€" not in body

    def test_ask_roster_permission_denied(self) -> None:
        feature, _, _ = _labor_feature(day_roster_rows=None)
        messenger = _messenger()
        outcome = feature.ask_roster(
            scope=_project_scope(uuid4()),
            project_id=uuid4(),
            user_id=uuid4(),
            message_id=uuid4(),
            lang="fr",
            messenger=messenger,
            trace_id="t",
        )
        assert outcome == "refused"

    def test_log_attendance_matches_names_and_confirms(self) -> None:
        worker = FakeWorker(uuid4(), "Minh")
        feature, bulk, _ = _labor_feature(workers=[worker])
        messenger = _messenger()
        outcome = feature.log_attendance(
            scope=_project_scope(uuid4()),
            project_id=uuid4(),
            text="Minh a travaillé aujourd'hui",
            user_id=uuid4(),
            message_id=uuid4(),
            lang="fr",
            messenger=messenger,
            trace_id="t",
        )
        assert outcome == "asked"
        posted = _last(messenger)
        assert posted.content_type == "choice"

        # Tap "confirm": AssistantService normally does this; call the feature directly.
        confirm_payload = posted.payload["options"][0]["payload"]
        outcome = feature.confirm_bulk_attendance(
            scope=_project_scope(uuid4()),
            payload=confirm_payload,
            user_id=uuid4(),
            message_id=uuid4(),
            lang="fr",
            messenger=messenger,
            trace_id="t",
        )
        assert outcome == "answered"
        assert len(bulk.calls) == 1
        assert str(worker.id) in confirm_payload["worker_ids"]

    def test_log_attendance_permission_denied(self) -> None:
        worker = FakeWorker(uuid4(), "Minh")
        feature, _, _ = _labor_feature(workers=[worker], authz=RestrictiveAuthzReader())
        messenger = _messenger()
        outcome = feature.log_attendance(
            scope=_project_scope(uuid4()),
            project_id=uuid4(),
            text="Minh a travaillé",
            user_id=uuid4(),
            message_id=uuid4(),
            lang="fr",
            messenger=messenger,
            trace_id="t",
        )
        assert outcome == "refused"

    def test_confirm_bulk_attendance_permission_denied_on_a_stale_tap(self) -> None:
        """NEW-L1: a stale `confirm_bulk_attendance` tap re-checked and denied by
        `_worker_permitted` must report "refused", not the default "answered" — the
        audit row needs to tell a revoked-role stale tap apart from a real write."""
        feature, bulk, _ = _labor_feature(authz=RestrictiveAuthzReader())
        messenger = _messenger()
        project_id = uuid4()
        outcome = feature.confirm_bulk_attendance(
            scope=_project_scope(project_id),
            payload={"project_id": str(project_id), "worker_ids": [str(uuid4())], "date": "2026-09-20"},
            user_id=uuid4(),
            message_id=uuid4(),
            lang="fr",
            messenger=messenger,
            trace_id="t",
        )
        assert outcome == "refused"
        assert bulk.calls == []

    def test_log_attendance_no_names_matched(self) -> None:
        feature, _, _ = _labor_feature(workers=[FakeWorker(uuid4(), "Minh")])
        messenger = _messenger()
        outcome = feature.log_attendance(
            scope=_project_scope(uuid4()),
            project_id=uuid4(),
            text="bonjour",
            user_id=uuid4(),
            message_id=uuid4(),
            lang="fr",
            messenger=messenger,
            trace_id="t",
        )
        assert outcome == "asked"
        assert _last(messenger).content_type == "text"

    def test_validate_attendance_none_pending(self) -> None:
        feature, _, _ = _labor_feature(pending=[])
        messenger = _messenger()
        outcome = feature.validate_attendance(
            scope=_company_scope(uuid4()),
            user_id=uuid4(),
            message_id=uuid4(),
            lang="fr",
            messenger=messenger,
            trace_id="t",
        )
        assert outcome == "replied"

    def test_validate_attendance_offers_a_choice_and_the_all_option(self) -> None:
        item = PendingAttendanceDto(
            entry_id=str(uuid4()),
            project_id=str(uuid4()),
            project_name="Villa Arcueil",
            worker_id=str(uuid4()),
            worker_name="Minh",
            date="2026-09-20",
            shift_type="full",
            supplement_hours=0,
            note=None,
            submitted_at="2026-09-20T00:00:00Z",
        )
        # H3: the item's project must resolve to the SAME company as the channel scope,
        # or it is filtered out — `PermissiveAuthzReader.project_company_id` always
        # answers its own `company_id`, so the scope must be built from that same id.
        authz = PermissiveAuthzReader()
        feature, _, validate = _labor_feature(authz=authz, pending=[item])
        messenger = _messenger()
        outcome = feature.validate_attendance(
            scope=_company_scope(authz.company_id),
            user_id=uuid4(),
            message_id=uuid4(),
            lang="fr",
            messenger=messenger,
            trace_id="t",
        )
        assert outcome == "asked"
        posted = _last(messenger)
        assert len(posted.payload["options"]) == 2  # one entry + "all"

        all_payload = posted.payload["options"][-1]["payload"]
        outcome = feature.confirm_validate_attendance(
            scope=_company_scope(authz.company_id),
            payload=all_payload,
            user_id=uuid4(),
            message_id=uuid4(),
            lang="fr",
            messenger=messenger,
            trace_id="t",
        )
        assert outcome == "answered"
        assert len(validate.calls) == 1
        assert "1" in (_last(messenger).body or "")

    def test_confirm_validate_attendance_permission_denied_on_a_stale_tap(self) -> None:
        """NEW-L1: every tapped entry re-checked and denied by `_worker_permitted`
        (a role revoked since the choice was offered) must report "refused" — not the
        default "answered" a silently-skipped-to-zero validation would otherwise get."""
        feature, _, validate = _labor_feature(authz=RestrictiveAuthzReader())
        messenger = _messenger()
        entry_id, project_id = uuid4(), uuid4()
        outcome = feature.confirm_validate_attendance(
            scope=_company_scope(uuid4()),
            payload={"entry_ids": [str(entry_id)], "project_ids": [str(project_id)]},
            user_id=uuid4(),
            message_id=uuid4(),
            lang="fr",
            messenger=messenger,
            trace_id="t",
        )
        assert outcome == "refused"
        assert validate.calls == []

    def test_validate_attendance_excludes_pending_days_of_another_company(self) -> None:
        """H3: a company/admin channel must never enumerate pending days across every
        company the caller may validate in — only the ones in THIS channel's company."""
        item = PendingAttendanceDto(
            entry_id=str(uuid4()),
            project_id=str(uuid4()),
            project_name="Chantier d'une autre société",
            worker_id=str(uuid4()),
            worker_name="Minh",
            date="2026-09-20",
            shift_type="full",
            supplement_hours=0,
            note=None,
            submitted_at="2026-09-20T00:00:00Z",
        )
        authz = PermissiveAuthzReader()
        feature, _, _ = _labor_feature(authz=authz, pending=[item])
        messenger = _messenger()
        outcome = feature.validate_attendance(
            scope=_company_scope(uuid4()),  # a DIFFERENT company than authz.company_id
            user_id=uuid4(),
            message_id=uuid4(),
            lang="fr",
            messenger=messenger,
            trace_id="t",
        )
        assert outcome == "replied"
        assert "Chantier d'une autre société" not in (_last(messenger).body or "")


# ---------------------------------------------------------------------------
# TasksFeature
# ---------------------------------------------------------------------------


class FakeCreateTaskUseCase:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    def execute(self, request: Any) -> Task:
        self.calls.append(request)
        from datetime import datetime, timezone

        return Task(
            id=uuid4(),
            project_id=request.project_id,
            title=request.title,
            status=request.status,
            priority=request.priority,
            position=1000,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            due_date=request.due_date,
        )


class FakeListTasksUseCase:
    def __init__(self, tasks: list[Task]) -> None:
        self._tasks = tasks

    def execute(self, project_id: UUID, status: Any = None) -> list[Task]:
        return self._tasks


def _task(title: str, due_date: Optional[date], status: TaskStatus = TaskStatus.TODO) -> Task:
    from datetime import datetime, timezone

    return Task(
        id=uuid4(),
        project_id=uuid4(),
        title=title,
        status=status,
        priority=TaskPriority.MEDIUM,
        position=1000,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        due_date=due_date,
    )


class TestTasksFeature:
    def test_create_task_parses_title_and_confirms(self) -> None:
        vision = ScriptedVision(json_answers=[TaskDraft(title="Couler la dalle", due_date=None)])
        create_uc = FakeCreateTaskUseCase()
        feature = TasksFeature(
            vision=vision,
            project_repo=FakeProjectRepo([]),
            create_usecase=create_uc,
            list_usecase=FakeListTasksUseCase([]),
            authz_reader=PermissiveAuthzReader(),
        )
        messenger = _messenger()
        project_id = uuid4()
        outcome = feature.create_task(
            scope=_project_scope(project_id),
            project_id=project_id,
            text="crée une tâche pour couler la dalle",
            user_id=uuid4(),
            message_id=uuid4(),
            lang="fr",
            messenger=messenger,
            trace_id="t",
        )
        assert outcome == "asked"
        posted = _last(messenger)
        assert posted.content_type == "choice"
        confirm_payload = posted.payload["options"][0]["payload"]
        assert confirm_payload["title"] == "Couler la dalle"

        confirm_outcome = feature.confirm_create_task(
            scope=_project_scope(project_id),
            payload=confirm_payload,
            user_id=uuid4(),
            message_id=uuid4(),
            lang="fr",
            messenger=messenger,
            trace_id="t",
        )
        assert confirm_outcome == "answered"
        assert len(create_uc.calls) == 1
        assert "Couler la dalle" in (_last(messenger).body or "")

    def test_create_task_asks_again_when_title_cannot_be_parsed(self) -> None:
        vision = ScriptedVision(json_answers=[TaskDraft(title=None, due_date=None)])
        feature = TasksFeature(
            vision=vision,
            project_repo=FakeProjectRepo([]),
            create_usecase=FakeCreateTaskUseCase(),
            list_usecase=FakeListTasksUseCase([]),
            authz_reader=PermissiveAuthzReader(),
        )
        messenger = _messenger()
        outcome = feature.create_task(
            scope=_project_scope(uuid4()),
            project_id=uuid4(),
            text="fais un truc",
            user_id=uuid4(),
            message_id=uuid4(),
            lang="fr",
            messenger=messenger,
            trace_id="t",
        )
        assert outcome == "asked"
        assert _last(messenger).content_type == "text"

    def test_ask_tasks_lists_open_tasks_within_the_week(self) -> None:
        today = date.today()
        in_window = _task("Poser le carrelage", today + timedelta(days=2))
        out_of_window = _task("Peinture finale", today + timedelta(days=30))
        no_due = _task("Nettoyage chantier", None)
        done = _task("Fondations", today, status=TaskStatus.DONE)
        feature = TasksFeature(
            vision=ScriptedVision(),
            project_repo=FakeProjectRepo([]),
            create_usecase=FakeCreateTaskUseCase(),
            list_usecase=FakeListTasksUseCase([in_window, out_of_window, no_due, done]),
            authz_reader=PermissiveAuthzReader(),
        )
        messenger = _messenger()
        outcome = feature.ask_tasks(
            scope=_project_scope(uuid4()),
            project_id=uuid4(),
            user_id=uuid4(),
            message_id=uuid4(),
            lang="fr",
            messenger=messenger,
            trace_id="t",
        )
        assert outcome == "replied"
        body = _last(messenger).body or ""
        assert "Poser le carrelage" in body
        assert "Nettoyage chantier" in body
        assert "Peinture finale" not in body
        assert "Fondations" not in body

    def test_ask_tasks_none_open(self) -> None:
        feature = TasksFeature(
            vision=ScriptedVision(),
            project_repo=FakeProjectRepo([]),
            create_usecase=FakeCreateTaskUseCase(),
            list_usecase=FakeListTasksUseCase([]),
            authz_reader=PermissiveAuthzReader(),
        )
        messenger = _messenger()
        outcome = feature.ask_tasks(
            scope=_project_scope(uuid4()),
            project_id=uuid4(),
            user_id=uuid4(),
            message_id=uuid4(),
            lang="fr",
            messenger=messenger,
            trace_id="t",
        )
        assert outcome == "replied"

    def test_ask_tasks_permission_denied(self) -> None:
        """C1: a caller with no `project:read` on the project must never see its tasks,
        even though `resolve_project` would already have filtered it out in the real
        dispatch path — the feature re-checks on its own."""
        feature = TasksFeature(
            vision=ScriptedVision(),
            project_repo=FakeProjectRepo([]),
            create_usecase=FakeCreateTaskUseCase(),
            list_usecase=FakeListTasksUseCase([_task("Poser le carrelage", None)]),
            authz_reader=NoAccessAuthzReader(),
        )
        messenger = _messenger()
        outcome = feature.ask_tasks(
            scope=_project_scope(uuid4()),
            project_id=uuid4(),
            user_id=uuid4(),
            message_id=uuid4(),
            lang="fr",
            messenger=messenger,
            trace_id="t",
        )
        assert outcome == "refused"
        assert "Poser le carrelage" not in (_last(messenger).body or "")

    def test_create_task_permission_denied(self) -> None:
        vision = ScriptedVision(json_answers=[TaskDraft(title="Couler la dalle", due_date=None)])
        create_uc = FakeCreateTaskUseCase()
        feature = TasksFeature(
            vision=vision,
            project_repo=FakeProjectRepo([]),
            create_usecase=create_uc,
            list_usecase=FakeListTasksUseCase([]),
            authz_reader=NoAccessAuthzReader(),
        )
        messenger = _messenger()
        outcome = feature.create_task(
            scope=_project_scope(uuid4()),
            project_id=uuid4(),
            text="crée une tâche pour couler la dalle",
            user_id=uuid4(),
            message_id=uuid4(),
            lang="fr",
            messenger=messenger,
            trace_id="t",
        )
        assert outcome == "refused"
        assert create_uc.calls == []

    def test_confirm_create_task_permission_denied_on_a_stale_tap(self) -> None:
        """A stale `confirm_create_task` tap must be re-checked, not honoured just
        because the choice was offered while the caller still had the role."""
        create_uc = FakeCreateTaskUseCase()
        feature = TasksFeature(
            vision=ScriptedVision(),
            project_repo=FakeProjectRepo([]),
            create_usecase=create_uc,
            list_usecase=FakeListTasksUseCase([]),
            authz_reader=NoAccessAuthzReader(),
        )
        messenger = _messenger()
        project_id = uuid4()
        outcome = feature.confirm_create_task(
            scope=_project_scope(project_id),
            payload={"project_id": str(project_id), "title": "Couler la dalle", "due_date": None},
            user_id=uuid4(),
            message_id=uuid4(),
            lang="fr",
            messenger=messenger,
            trace_id="t",
        )
        # NEW-L1: the audit row must see this as a refusal, not a successful answer.
        assert outcome == "refused"
        assert create_uc.calls == []
        assert _last(messenger).content_type == "text"


# ---------------------------------------------------------------------------
# AdminAnswersFeature
# ---------------------------------------------------------------------------


class FakeInvoiceRepoForAdmin:
    def __init__(self, spent_split, released_split) -> None:
        self._spent_split = spent_split
        self._released_split = released_split

    def sum_spent_split(self, project_id: UUID):
        return self._spent_split

    def sum_funds_released_split(self, project_id: UUID):
        return self._released_split


@dataclass
class FakeBillingDoc:
    id: UUID
    kind: Any
    document_number: str
    status: Any
    payment_due_date: Optional[date]
    items: list = field(default_factory=list)

    @property
    def total_ttc(self) -> Decimal:
        return Decimal("500.00")


class _Kind:
    def __init__(self, value: str) -> None:
        self.value = value


class FakeBillingRepo:
    def __init__(self, docs_by_project: dict[UUID, list[FakeBillingDoc]]) -> None:
        self._docs = docs_by_project

    def list_by_project(self, project_id: UUID) -> list[FakeBillingDoc]:
        return self._docs.get(project_id, [])


class FakeLaborPaymentsUseCase:
    def __init__(self, response: Any) -> None:
        self._response = response

    def execute(self, request: Any) -> Any:
        return self._response


class FakeDirectory:
    def display_names(self, user_ids: list[UUID]) -> dict[UUID, str]:
        return {uid: f"User {str(uid)[:4]}" for uid in user_ids}


def test_ask_project_income_renders_a_template_with_no_model_text() -> None:
    project = _project("Villa Arcueil")
    project.budget = Decimal("10000")
    repo = FakeProjectRepo([project])
    invoice_repo = FakeInvoiceRepoForAdmin(
        spent_split=(Decimal("2000"), Decimal("0")), released_split=(Decimal("5000"), Decimal("0"), Decimal("0"))
    )
    feature = AdminAnswersFeature(
        project_repo=repo,
        invoice_repo=invoice_repo,
        billing_repo=FakeBillingRepo({}),
        labor_payments_usecase=FakeLaborPaymentsUseCase(None),
        audit=None,
        directory=FakeDirectory(),
        authz_reader=PermissiveAuthzReader(),
    )
    messenger = _messenger()
    outcome = feature.ask_project_income(
        scope=_company_scope(uuid4(), is_admin=True),
        project_id=project.id,
        user_id=uuid4(),
        message_id=uuid4(),
        lang="fr",
        messenger=messenger,
        trace_id="t",
    )
    assert outcome == "answered"
    body = _last(messenger).body or ""
    assert "Villa Arcueil" in body
    assert "10000" in body or "10000.00" in body


def test_ask_unpaid_invoices_computes_days_late() -> None:
    project = _project("Villa Arcueil")
    overdue_doc = FakeBillingDoc(
        id=uuid4(),
        kind=_Kind("facture"),
        document_number="F-001",
        status=_Kind("overdue"),
        payment_due_date=date.today() - timedelta(days=10),
    )
    feature = AdminAnswersFeature(
        project_repo=FakeProjectRepo([project]),
        invoice_repo=FakeInvoiceRepoForAdmin((Decimal(0), Decimal(0)), (Decimal(0), Decimal(0), Decimal(0))),
        billing_repo=FakeBillingRepo({project.id: [overdue_doc]}),
        labor_payments_usecase=FakeLaborPaymentsUseCase(None),
        audit=None,
        directory=FakeDirectory(),
        authz_reader=PermissiveAuthzReader(),
    )
    messenger = _messenger()
    outcome = feature.ask_unpaid_invoices(
        scope=_company_scope(uuid4(), is_admin=True),
        user_id=uuid4(),
        message_id=uuid4(),
        lang="fr",
        messenger=messenger,
        trace_id="t",
        projects=[project],
    )
    assert outcome == "answered"
    body = _last(messenger).body or ""
    assert "F-001" in body
    assert "10" in body


def test_ask_audit_groups_by_user() -> None:
    company_id = uuid4()
    user_a = uuid4()

    @dataclass
    class Row:
        user_id: Any
        outcome: str

    class FakeAudit:
        def list_for_company(self, company_id_, **kwargs):
            return [Row(user_id=user_a, outcome="answered"), Row(user_id=user_a, outcome="refused")]

    feature = AdminAnswersFeature(
        project_repo=FakeProjectRepo([]),
        invoice_repo=FakeInvoiceRepoForAdmin((Decimal(0), Decimal(0)), (Decimal(0), Decimal(0), Decimal(0))),
        billing_repo=FakeBillingRepo({}),
        labor_payments_usecase=FakeLaborPaymentsUseCase(None),
        audit=FakeAudit(),
        directory=FakeDirectory(),
        authz_reader=PermissiveAuthzReader(),
    )
    messenger = _messenger()
    outcome = feature.ask_audit(
        scope=_company_scope(company_id, is_admin=True),
        user_id=uuid4(),
        message_id=uuid4(),
        lang="fr",
        messenger=messenger,
        trace_id="t",
    )
    assert outcome == "answered"
    body = _last(messenger).body or ""
    assert "2" in body and "1" in body


def test_ask_project_income_permission_denied() -> None:
    """C1 defense in depth: even inside the admin channel, `ask_project_income` must
    still hold `project:view_budget` on the SPECIFIC project — a caller without it (e.g.
    a D8 deny, or a project outside their own company) gets refused, never a number."""
    project = _project("Villa Arcueil")
    project.budget = Decimal("10000")
    feature = AdminAnswersFeature(
        project_repo=FakeProjectRepo([project]),
        invoice_repo=FakeInvoiceRepoForAdmin(
            spent_split=(Decimal("2000"), Decimal("0")), released_split=(Decimal("5000"), Decimal("0"), Decimal("0"))
        ),
        billing_repo=FakeBillingRepo({}),
        labor_payments_usecase=FakeLaborPaymentsUseCase(None),
        audit=None,
        directory=FakeDirectory(),
        authz_reader=NoAccessAuthzReader(),
    )
    messenger = _messenger()
    outcome = feature.ask_project_income(
        scope=_company_scope(uuid4(), is_admin=True),
        project_id=project.id,
        user_id=uuid4(),
        message_id=uuid4(),
        lang="fr",
        messenger=messenger,
        trace_id="t",
    )
    assert outcome == "refused"
    assert "10000" not in (_last(messenger).body or "")


def test_ask_salary_permission_denied() -> None:
    feature = AdminAnswersFeature(
        project_repo=FakeProjectRepo([]),
        invoice_repo=FakeInvoiceRepoForAdmin((Decimal(0), Decimal(0)), (Decimal(0), Decimal(0), Decimal(0))),
        billing_repo=FakeBillingRepo({}),
        labor_payments_usecase=FakeLaborPaymentsUseCase(None),
        audit=None,
        directory=FakeDirectory(),
        authz_reader=NoAccessAuthzReader(),
    )
    messenger = _messenger()
    outcome = feature.ask_salary(
        scope=_company_scope(uuid4(), is_admin=True),
        project_id=uuid4(),
        text="Minh",
        user_id=uuid4(),
        message_id=uuid4(),
        lang="fr",
        messenger=messenger,
        trace_id="t",
    )
    assert outcome == "refused"
