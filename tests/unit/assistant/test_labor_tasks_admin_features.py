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

from app.application.assistant.audit_ports import UserAuditCount
from app.application.assistant.features.admin_answers import AdminAnswersFeature
from app.application.assistant.features.labor import LaborFeature
from app.application.assistant.features.tasks import TasksFeature
from app.application.assistant.messages import AssistantMessenger
from app.application.assistant.models import ChannelScope, TaskDraft
from app.application.assistant.ports import ChoiceQuestion, Decision
from app.application.assistant.project_resolution import accessible_projects, resolve_project
from app.application.labor.bulk_log_attendance import (
    BulkLogAttendanceResponse,
    BulkLogAttendanceUseCase,
    ConflictsNotAcknowledgedError,
)
from app.application.labor.get_day_roster_usecase import RosterRow
from app.application.labor.list_pending_attendance import PendingAttendanceDto
from app.application.labor.ports import CrossProjectConflict, CrossProjectConflictEntry
from app.application.labor.validate_attendance import ValidateAttendanceResponse
from app.application.projects.ports import ProjectSpent
from app.domain.entities.chat_message import ChannelRef, ChatMessage
from app.domain.entities.project import Project
from app.domain.entities.task import Task, TaskPriority, TaskStatus
from app.domain.exceptions.labor_exceptions import InvalidLaborEntryError
from app.domain.time import business_today
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

    def rollback(self) -> None:
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
        self.calls: list[dict] = []

    def execute(
        self, *, user_id: UUID, company_id: Optional[UUID] = None, project_id: Optional[UUID] = None
    ) -> list[PendingAttendanceDto]:
        self.calls.append({"user_id": user_id, "company_id": company_id, "project_id": project_id})
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

    def test_validate_attendance_scopes_the_query_to_the_channels_company(self) -> None:
        """The company/project boundary is enforced by the query's own WHERE clause,
        not by fetching every company's rows and discarding the wrong ones afterwards —
        a company/admin channel must ask the use case for THIS channel's company only."""
        feature, _, _ = _labor_feature(pending=[])
        messenger = _messenger()
        company_id = uuid4()

        feature.validate_attendance(
            scope=_company_scope(company_id),
            user_id=uuid4(),
            message_id=uuid4(),
            lang="fr",
            messenger=messenger,
            trace_id="t",
        )

        [call] = feature._pending_attendance_usecase.calls
        assert call["company_id"] == company_id
        assert call["project_id"] is None

    def test_validate_attendance_scopes_the_query_to_the_project_channels_own_project(self) -> None:
        feature, _, _ = _labor_feature(pending=[])
        messenger = _messenger()
        project_id = uuid4()

        feature.validate_attendance(
            scope=_project_scope(project_id),
            user_id=uuid4(),
            message_id=uuid4(),
            lang="fr",
            messenger=messenger,
            trace_id="t",
        )

        [call] = feature._pending_attendance_usecase.calls
        assert call["project_id"] == project_id
        assert call["company_id"] is None


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

    def test_create_task_replies_temporarily_unavailable_on_a_provider_outage(self) -> None:
        vision = ScriptedVision(raise_llm_unavailable_error=True)
        create_uc = FakeCreateTaskUseCase()
        feature = TasksFeature(
            vision=vision,
            project_repo=FakeProjectRepo([]),
            create_usecase=create_uc,
            list_usecase=FakeListTasksUseCase([]),
            authz_reader=PermissiveAuthzReader(),
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
        assert outcome == "error"
        assert create_uc.calls == []
        assert "indisponible" in (_last(messenger).body or "").lower()

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
        today = business_today()
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


class FakeProjectSpentReader:
    """Stands in for `SqlAlchemyProjectSpentReader` — every requested project gets the
    same `invoiced` figure (all other `ProjectSpent` fields zeroed; `ask_project_income`
    only ever reads `invoiced`)."""

    def __init__(self, invoiced: Decimal = Decimal("0")) -> None:
        self._invoiced = invoiced

    def sum_spent_by_projects(self, project_ids: list[UUID]) -> dict[UUID, ProjectSpent]:
        zero = Decimal("0")
        return {
            pid: ProjectSpent(
                total=self._invoiced,
                invoiced=self._invoiced,
                by_credits=zero,
                personal=zero,
                labor_accrued=zero,
                labor_paid=zero,
                labor_unpaid=zero,
                personal_by_type={},
            )
            for pid in project_ids
        }


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
        project_spent_reader=FakeProjectSpentReader(),
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
    assert "10 000,00" in body


def test_ask_project_income_remaining_matches_the_home_cards_formula() -> None:
    """The old formula (`sum_spent_split`, floors each payment-method bucket at zero,
    drops unflagged/labor_unpaid invoices) and the home card's `computeBudgetMetrics`
    (denominator minus `ProjectSpent.invoiced`) disagree whenever an invoice carries no
    flagged payment method — exactly the project picked here."""
    project = _project("Villa Arcueil")
    project.budget = None  # denominator falls back to released funds, like the card.
    invoice_repo = FakeInvoiceRepoForAdmin(
        # Old "spent": only the flagged-payment-method invoices this fake stands in for.
        spent_split=(Decimal("2000"), Decimal("0")),
        released_split=(Decimal("5000"), Decimal("0"), Decimal("0")),
    )
    feature = AdminAnswersFeature(
        project_repo=FakeProjectRepo([project]),
        invoice_repo=invoice_repo,
        billing_repo=FakeBillingRepo({}),
        labor_payments_usecase=FakeLaborPaymentsUseCase(None),
        audit=None,
        directory=FakeDirectory(),
        authz_reader=PermissiveAuthzReader(),
        # The ledger total the app's own spend total sums on screen — includes the
        # unflagged-payment-method invoice the old formula silently dropped.
        project_spent_reader=FakeProjectSpentReader(invoiced=Decimal("3500")),
    )
    messenger = _messenger()
    outcome = feature.ask_project_income(
        scope=_company_scope(uuid4(), is_admin=True),
        project_id=project.id,
        user_id=uuid4(),
        message_id=uuid4(),
        lang="en",
        messenger=messenger,
        trace_id="t",
    )
    assert outcome == "answered"
    body = _last(messenger).body or ""
    # New rule: 5000 (released, no budget set) - 3500 (invoiced) = 1500.
    assert "1,500.00" in body
    # Old rule would have answered 5000 - 2000 = 3000 — must not appear.
    assert "3,000.00" not in body


def test_ask_unpaid_invoices_computes_days_late(monkeypatch: Any) -> None:
    import app.application.assistant.features.admin_answers as admin_answers_module

    # Pin the business day: an exact day count built from the runner's clock breaks
    # whenever the runner's date differs from the Europe/Paris date the feature counts in.
    forced_today = date(2020, 1, 15)
    monkeypatch.setattr(admin_answers_module, "business_today", lambda: forced_today)
    project = _project("Villa Arcueil")
    overdue_doc = FakeBillingDoc(
        id=uuid4(),
        kind=_Kind("facture"),
        document_number="F-001",
        status=_Kind("overdue"),
        payment_due_date=forced_today - timedelta(days=10),
    )
    feature = AdminAnswersFeature(
        project_repo=FakeProjectRepo([project]),
        invoice_repo=FakeInvoiceRepoForAdmin((Decimal(0), Decimal(0)), (Decimal(0), Decimal(0), Decimal(0))),
        billing_repo=FakeBillingRepo({project.id: [overdue_doc]}),
        labor_payments_usecase=FakeLaborPaymentsUseCase(None),
        audit=None,
        directory=FakeDirectory(),
        authz_reader=PermissiveAuthzReader(),
        project_spent_reader=FakeProjectSpentReader(),
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
    assert "10 jour(s) de retard" in body


def test_ask_audit_groups_by_user() -> None:
    company_id = uuid4()
    user_a = uuid4()

    class FakeAudit:
        def count_by_user_for_company(self, company_id_, **kwargs):
            return [UserAuditCount(user_id=user_a, total=2, refused=1)]

    feature = AdminAnswersFeature(
        project_repo=FakeProjectRepo([]),
        invoice_repo=FakeInvoiceRepoForAdmin((Decimal(0), Decimal(0)), (Decimal(0), Decimal(0), Decimal(0))),
        billing_repo=FakeBillingRepo({}),
        labor_payments_usecase=FakeLaborPaymentsUseCase(None),
        audit=FakeAudit(),
        directory=FakeDirectory(),
        authz_reader=PermissiveAuthzReader(),
        project_spent_reader=FakeProjectSpentReader(),
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
        project_spent_reader=FakeProjectSpentReader(),
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
        project_spent_reader=FakeProjectSpentReader(),
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


# ---------------------------------------------------------------------------
# Additional attendance, task, and admin-answer coverage.
# ---------------------------------------------------------------------------


class RealBulkAttendanceWorker:
    def __init__(self, id_: UUID, project_id: UUID, name: str) -> None:
        self.id = id_
        self.project_id = project_id
        self.name = name
        self.person_id = None


class RealBulkAttendanceWorkerRepo:
    """Enough of `IWorkerRepository` for `BulkLogAttendanceUseCase` to run for real."""

    def __init__(self, workers: list[RealBulkAttendanceWorker]) -> None:
        self._by_id = {w.id: w for w in workers}

    def find_by_id(self, worker_id: UUID) -> Optional[RealBulkAttendanceWorker]:
        return self._by_id.get(worker_id)

    def list_by_project(self, project_id: UUID, active_only: bool = True) -> list[RealBulkAttendanceWorker]:
        return [w for w in self._by_id.values() if w.project_id == project_id]


class RealBulkAttendanceEntryRepo:
    """Enough of `ILaborEntryRepository` for `BulkLogAttendanceUseCase` to run for real —
    `create` stores whatever `LaborEntry` the use case builds, so the domain entity's own
    `__post_init__` invariant check runs for real, not a fake standing in for it."""

    def __init__(self, worker_repo: RealBulkAttendanceWorkerRepo) -> None:
        self._worker_repo = worker_repo
        self.entries: dict[UUID, Any] = {}

    def list_by_project(
        self, project_id: UUID, date_from: Any = None, date_to: Any = None, worker_id: Any = None, **_kwargs: Any
    ) -> list[Any]:
        return [e for e in self.entries.values() if self._worker_repo.find_by_id(e.worker_id).project_id == project_id]

    def find_cross_project_conflicts(self, *, project_id: UUID, date: Any, person_ids: list[UUID]) -> list[Any]:
        return []

    def create(self, entry: Any) -> Any:
        self.entries[entry.id] = entry
        return entry


def test_confirm_bulk_attendance_runs_the_real_use_case_with_a_valid_shift() -> None:
    """The previous code sent `shift_type=None, supplement_hours=0`, which the real
    `LaborEntry.__post_init__` invariant rejects with `InvalidLaborEntryError` on every
    call — a fake bulk-log use case masked this from the rest of the suite. Running
    against the REAL `BulkLogAttendanceUseCase` (and therefore the real domain entity)
    pins that a confirmed attendance tap actually writes a valid entry."""
    project_id = uuid4()
    worker = RealBulkAttendanceWorker(uuid4(), project_id, "Minh")
    worker_repo = RealBulkAttendanceWorkerRepo([worker])
    entry_repo = RealBulkAttendanceEntryRepo(worker_repo)
    bulk_usecase = BulkLogAttendanceUseCase(worker_repo, entry_repo, FakeSession())
    feature = LaborFeature(
        authz_reader=PermissiveAuthzReader(),
        worker_repo=worker_repo,
        project_repo=FakeProjectRepo([]),
        day_roster_usecase=FakeDayRosterUseCase(None),
        bulk_log_usecase=bulk_usecase,
        validate_usecase=FakeValidateUseCase(),
        pending_attendance_usecase=FakePendingAttendanceUseCase([]),
    )
    messenger = _messenger()
    outcome = feature.log_attendance(
        scope=_project_scope(project_id),
        project_id=project_id,
        text="Minh a travaillé aujourd'hui",
        user_id=uuid4(),
        message_id=uuid4(),
        lang="fr",
        messenger=messenger,
        trace_id="t",
    )
    assert outcome == "asked"
    confirm_payload = _last(messenger).payload["options"][0]["payload"]

    outcome = feature.confirm_bulk_attendance(
        scope=_project_scope(project_id),
        payload=confirm_payload,
        user_id=uuid4(),
        message_id=uuid4(),
        lang="fr",
        messenger=messenger,
        trace_id="t",
    )
    assert outcome == "answered"
    assert "1" in (_last(messenger).body or "")
    assert len(entry_repo.entries) == 1
    saved_entry = next(iter(entry_repo.entries.values()))
    assert saved_entry.shift_type == "full"
    assert saved_entry.worker_id == worker.id


class RaisingBulkLogUseCase:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def execute(self, request: Any) -> BulkLogAttendanceResponse:
        raise self._exc


def test_confirm_bulk_attendance_catches_invalid_entry_with_a_clear_reply() -> None:
    worker_id = uuid4()
    feature = LaborFeature(
        authz_reader=PermissiveAuthzReader(),
        worker_repo=FakeWorkerRepo([FakeWorker(worker_id, "Minh Nguyen")]),
        project_repo=FakeProjectRepo([]),
        day_roster_usecase=FakeDayRosterUseCase(None),
        bulk_log_usecase=RaisingBulkLogUseCase(InvalidLaborEntryError("bad entry")),
        validate_usecase=FakeValidateUseCase(),
        pending_attendance_usecase=FakePendingAttendanceUseCase([]),
    )
    messenger = _messenger()
    project_id = uuid4()
    outcome = feature.confirm_bulk_attendance(
        scope=_project_scope(project_id),
        payload={"project_id": str(project_id), "worker_ids": [str(worker_id)], "date": "2026-09-20"},
        user_id=uuid4(),
        message_id=uuid4(),
        lang="en",
        messenger=messenger,
        trace_id="t",
    )
    assert outcome == "error"
    body = _last(messenger).body or ""
    assert "invalid" in body.lower()
    # The worker's name, not their raw id — a UUID means nothing to the person reading
    # the chat.
    assert "Minh Nguyen" in body
    assert str(worker_id) not in body


def test_confirm_bulk_attendance_falls_back_to_the_id_for_an_unresolvable_worker() -> None:
    """A worker deleted between the choice being offered and this submit still gets a
    readable (if less useful) reply instead of the lookup crashing the whole reply."""
    worker_id = uuid4()
    feature = LaborFeature(
        authz_reader=PermissiveAuthzReader(),
        worker_repo=FakeWorkerRepo([]),  # empty — the worker cannot be resolved
        project_repo=FakeProjectRepo([]),
        day_roster_usecase=FakeDayRosterUseCase(None),
        bulk_log_usecase=RaisingBulkLogUseCase(InvalidLaborEntryError("bad entry")),
        validate_usecase=FakeValidateUseCase(),
        pending_attendance_usecase=FakePendingAttendanceUseCase([]),
    )
    messenger = _messenger()
    project_id = uuid4()
    outcome = feature.confirm_bulk_attendance(
        scope=_project_scope(project_id),
        payload={"project_id": str(project_id), "worker_ids": [str(worker_id)], "date": "2026-09-20"},
        user_id=uuid4(),
        message_id=uuid4(),
        lang="en",
        messenger=messenger,
        trace_id="t",
    )
    assert outcome == "error"
    assert str(worker_id) in (_last(messenger).body or "")


def test_confirm_bulk_attendance_names_the_conflict_and_offers_to_log_anyway() -> None:
    conflict = CrossProjectConflict(
        person_id=uuid4(),
        person_name="Minh",
        entries=[
            CrossProjectConflictEntry(
                project_id=uuid4(), project_name="Villa Arcueil", shift_type="full", supplement_hours=0
            )
        ],
    )
    feature = LaborFeature(
        authz_reader=PermissiveAuthzReader(),
        worker_repo=FakeWorkerRepo([]),
        project_repo=FakeProjectRepo([]),
        day_roster_usecase=FakeDayRosterUseCase(None),
        bulk_log_usecase=RaisingBulkLogUseCase(ConflictsNotAcknowledgedError([conflict])),
        validate_usecase=FakeValidateUseCase(),
        pending_attendance_usecase=FakePendingAttendanceUseCase([]),
    )
    messenger = _messenger()
    project_id = uuid4()
    outcome = feature.confirm_bulk_attendance(
        scope=_project_scope(project_id),
        payload={"project_id": str(project_id), "worker_ids": [str(uuid4())], "date": "2026-09-20"},
        user_id=uuid4(),
        message_id=uuid4(),
        lang="en",
        messenger=messenger,
        trace_id="t",
    )
    assert outcome == "asked"
    posted = _last(messenger)
    assert posted.content_type == "choice"
    body = posted.body or ""
    assert "Minh" in body
    assert "Villa Arcueil" in body
    acknowledge_option = posted.payload["options"][0]
    assert acknowledge_option["payload"]["acknowledge_conflicts"] is True


def test_validate_attendance_never_offers_an_attendance_change_request() -> None:
    """A worker's open change request on an already-validated day must never be offered
    as something "validate" can settle — `ValidateAttendanceUseCase` is a no-op on it, so
    the old code's reply said "1 journée validée" while nothing actually changed."""
    pending = PendingAttendanceDto(
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
        kind="attendance_change",
    )
    feature, _, _ = _labor_feature(pending=[pending])
    messenger = _messenger()
    outcome = feature.validate_attendance(
        scope=_project_scope(uuid4()),
        user_id=uuid4(),
        message_id=uuid4(),
        lang="fr",
        messenger=messenger,
        trace_id="t",
    )
    assert outcome == "replied"
    assert "Minh" not in (_last(messenger).body or "")


def test_confirm_validate_attendance_notifies_the_worker() -> None:
    """The HTTP validate route pushes a notification to the worker after a validation —
    validating through @folio must send the same push, not leave the worker unaware."""

    class RecordingNotifier:
        def __init__(self) -> None:
            self.calls: list[Any] = []

        def decision(self, worker_id: UUID, day: date, entry_id: UUID, event: str) -> None:
            self.calls.append((worker_id, day, entry_id, event))

    notifier = RecordingNotifier()
    bulk = FakeBulkLogUseCase()
    validate = FakeValidateUseCase()
    feature = LaborFeature(
        authz_reader=PermissiveAuthzReader(),
        worker_repo=FakeWorkerRepo([]),
        project_repo=FakeProjectRepo([]),
        day_roster_usecase=FakeDayRosterUseCase(None),
        bulk_log_usecase=bulk,
        validate_usecase=validate,
        pending_attendance_usecase=FakePendingAttendanceUseCase([]),
        notifier=notifier,
    )
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
    assert outcome == "answered"
    assert len(notifier.calls) == 1
    assert notifier.calls[0][3] == "validated"


def test_log_attendance_word_boundary_matching_does_not_pick_up_an_extra_worker() -> None:
    """A plain substring match on worker "An" would pick it up inside "Tuấn Anh" too —
    word-boundary + longest-match-wins keeps only the actually-mentioned worker."""
    short_named_worker = FakeWorker(uuid4(), "An")
    long_named_worker = FakeWorker(uuid4(), "Tuấn Anh")
    feature, _, _ = _labor_feature(workers=[short_named_worker, long_named_worker])
    messenger = _messenger()
    outcome = feature.log_attendance(
        scope=_project_scope(uuid4()),
        project_id=uuid4(),
        text="Tuấn Anh đi làm hôm nay",
        user_id=uuid4(),
        message_id=uuid4(),
        lang="fr",
        messenger=messenger,
        trace_id="t",
    )
    assert outcome == "asked"
    confirm_payload = _last(messenger).payload["options"][0]["payload"]
    assert confirm_payload["worker_ids"] == [str(long_named_worker.id)]


def test_log_attendance_uses_business_today_not_the_server_clock(monkeypatch: Any) -> None:
    import app.application.assistant.features.labor as labor_module

    forced_today = date(2020, 1, 15)
    monkeypatch.setattr(labor_module, "business_today", lambda: forced_today)
    worker = FakeWorker(uuid4(), "Minh")
    feature, _, _ = _labor_feature(workers=[worker])
    messenger = _messenger()
    feature.log_attendance(
        scope=_project_scope(uuid4()),
        project_id=uuid4(),
        text="Minh a travaillé aujourd'hui",
        user_id=uuid4(),
        message_id=uuid4(),
        lang="fr",
        messenger=messenger,
        trace_id="t",
    )
    confirm_payload = _last(messenger).payload["options"][0]["payload"]
    assert confirm_payload["date"] == forced_today.isoformat()


def test_ask_tasks_uses_business_today_not_the_server_clock(monkeypatch: Any) -> None:
    import app.application.assistant.features.tasks as tasks_module

    forced_today = date(2020, 1, 15)
    monkeypatch.setattr(tasks_module, "business_today", lambda: forced_today)
    overdue = _task("Retard chantier", forced_today - timedelta(days=3))
    feature = TasksFeature(
        vision=ScriptedVision(),
        project_repo=FakeProjectRepo([]),
        create_usecase=FakeCreateTaskUseCase(),
        list_usecase=FakeListTasksUseCase([overdue]),
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
    assert "Retard chantier" in (_last(messenger).body or "")


def test_ask_tasks_includes_overdue_tasks_and_caps_the_listing() -> None:
    today = business_today()
    overdue = _task("Tâche en retard", today - timedelta(days=2))
    many_in_window = [_task(f"Tâche {i}", today + timedelta(days=1)) for i in range(20)]
    feature = TasksFeature(
        vision=ScriptedVision(),
        project_repo=FakeProjectRepo([]),
        create_usecase=FakeCreateTaskUseCase(),
        list_usecase=FakeListTasksUseCase([overdue, *many_in_window]),
        authz_reader=PermissiveAuthzReader(),
    )
    messenger = _messenger()
    outcome = feature.ask_tasks(
        scope=_project_scope(uuid4()),
        project_id=uuid4(),
        user_id=uuid4(),
        message_id=uuid4(),
        lang="en",
        messenger=messenger,
        trace_id="t",
    )
    assert outcome == "replied"
    body = _last(messenger).body or ""
    assert "Tâche en retard" in body
    assert "more" in body.lower()
    assert body.count("Tâche ") <= 16  # 15 shown (incl. the overdue one) + the "+N" line


def test_create_task_title_is_truncated_to_255_characters() -> None:
    vision = ScriptedVision(json_answers=[TaskDraft(title="x" * 400, due_date=None)])
    feature = TasksFeature(
        vision=vision,
        project_repo=FakeProjectRepo([]),
        create_usecase=FakeCreateTaskUseCase(),
        list_usecase=FakeListTasksUseCase([]),
        authz_reader=PermissiveAuthzReader(),
    )
    messenger = _messenger()
    project_id = uuid4()
    feature.create_task(
        scope=_project_scope(project_id),
        project_id=project_id,
        text="crée une tâche",
        user_id=uuid4(),
        message_id=uuid4(),
        lang="fr",
        messenger=messenger,
        trace_id="t",
    )
    confirm_payload = _last(messenger).payload["options"][0]["payload"]
    assert len(confirm_payload["title"]) == 255


def test_ask_unpaid_invoices_excludes_a_project_the_caller_cannot_read() -> None:
    """The billing gate `ask_unpaid_invoices` applies must match `GET /projects/<id>/invoices`
    (`project:read`) — a project handed in that the caller cannot actually read must not
    leak its client invoices, even though it is already scoped to the channel's company."""
    readable = _project("Villa Arcueil")
    unreadable = _project("Chantier confidentiel")
    unpaid_doc = FakeBillingDoc(
        id=uuid4(),
        kind=_Kind("facture"),
        document_number="F-100",
        status=_Kind("overdue"),
        payment_due_date=business_today() - timedelta(days=5),
    )

    class PartialReadAuthzReader(PermissiveAuthzReader):
        def company_role_for(self, user_id: UUID, company_id: UUID) -> "str | None":
            return "member"

        def is_assigned(self, user_id: UUID, project_id: UUID) -> bool:
            return project_id == readable.id

    authz = PartialReadAuthzReader()
    feature = AdminAnswersFeature(
        project_repo=FakeProjectRepo([readable, unreadable]),
        invoice_repo=FakeInvoiceRepoForAdmin((Decimal(0), Decimal(0)), (Decimal(0), Decimal(0), Decimal(0))),
        billing_repo=FakeBillingRepo({readable.id: [unpaid_doc], unreadable.id: [unpaid_doc]}),
        labor_payments_usecase=FakeLaborPaymentsUseCase(None),
        audit=None,
        directory=FakeDirectory(),
        authz_reader=authz,
        project_spent_reader=FakeProjectSpentReader(),
    )
    messenger = _messenger()
    outcome = feature.ask_unpaid_invoices(
        scope=_company_scope(uuid4(), is_admin=True),
        user_id=uuid4(),
        message_id=uuid4(),
        lang="fr",
        messenger=messenger,
        trace_id="t",
        projects=[readable, unreadable],
    )
    assert outcome == "answered"
    body = _last(messenger).body or ""
    assert "Villa Arcueil" in body
    assert "Chantier confidentiel" not in body


def test_ask_unpaid_invoices_caps_lines_and_appends_a_more_count() -> None:
    project = _project("Villa Arcueil")
    docs = [
        FakeBillingDoc(
            id=uuid4(),
            kind=_Kind("facture"),
            document_number=f"F-{i:03d}",
            status=_Kind("overdue"),
            payment_due_date=business_today() - timedelta(days=1),
        )
        for i in range(40)
    ]
    feature = AdminAnswersFeature(
        project_repo=FakeProjectRepo([project]),
        invoice_repo=FakeInvoiceRepoForAdmin((Decimal(0), Decimal(0)), (Decimal(0), Decimal(0), Decimal(0))),
        billing_repo=FakeBillingRepo({project.id: docs}),
        labor_payments_usecase=FakeLaborPaymentsUseCase(None),
        audit=None,
        directory=FakeDirectory(),
        authz_reader=PermissiveAuthzReader(),
        project_spent_reader=FakeProjectSpentReader(),
    )
    messenger = _messenger()
    outcome = feature.ask_unpaid_invoices(
        scope=_company_scope(uuid4(), is_admin=True),
        user_id=uuid4(),
        message_id=uuid4(),
        lang="en",
        messenger=messenger,
        trace_id="t",
        projects=[project],
    )
    assert outcome == "answered"
    body = _last(messenger).body or ""
    assert "F-000" in body
    assert "more" in body.lower()


def test_ask_unpaid_invoices_uses_business_today_not_the_server_clock(monkeypatch: Any) -> None:
    import app.application.assistant.features.admin_answers as admin_answers_module

    forced_today = date(2020, 1, 15)
    monkeypatch.setattr(admin_answers_module, "business_today", lambda: forced_today)
    project = _project("Villa Arcueil")
    doc = FakeBillingDoc(
        id=uuid4(),
        kind=_Kind("facture"),
        document_number="F-001",
        status=_Kind("overdue"),
        payment_due_date=forced_today - timedelta(days=10),
    )
    feature = AdminAnswersFeature(
        project_repo=FakeProjectRepo([project]),
        invoice_repo=FakeInvoiceRepoForAdmin((Decimal(0), Decimal(0)), (Decimal(0), Decimal(0), Decimal(0))),
        billing_repo=FakeBillingRepo({project.id: [doc]}),
        labor_payments_usecase=FakeLaborPaymentsUseCase(None),
        audit=None,
        directory=FakeDirectory(),
        authz_reader=PermissiveAuthzReader(),
        project_spent_reader=FakeProjectSpentReader(),
    )
    messenger = _messenger()
    feature.ask_unpaid_invoices(
        scope=_company_scope(uuid4(), is_admin=True),
        user_id=uuid4(),
        message_id=uuid4(),
        lang="en",
        messenger=messenger,
        trace_id="t",
        projects=[project],
    )
    assert "10 day" in (_last(messenger).body or "")


class FakeLaborPaymentsUseCaseForSalary:
    def __init__(self, months: list[Any]) -> None:
        self._months = months

    def execute(self, request: Any) -> Any:
        @dataclass
        class _Summary:
            months: list[Any]

        return _Summary(months=self._months)


@dataclass
class _MonthBucket:
    year: int
    month: int
    workers: list[Any]


@dataclass
class _WorkerPay:
    worker_id: str
    worker_name: str
    paid: float
    invoice_count: int


def test_ask_salary_filters_to_the_asked_month() -> None:
    september = _MonthBucket(2026, 9, [_WorkerPay(str(uuid4()), "Minh", 480.0, 3)])
    august = _MonthBucket(2026, 8, [_WorkerPay(str(uuid4()), "Minh", 300.0, 2)])
    feature = AdminAnswersFeature(
        project_repo=FakeProjectRepo([]),
        invoice_repo=FakeInvoiceRepoForAdmin((Decimal(0), Decimal(0)), (Decimal(0), Decimal(0), Decimal(0))),
        billing_repo=FakeBillingRepo({}),
        labor_payments_usecase=FakeLaborPaymentsUseCaseForSalary([august, september]),
        audit=None,
        directory=FakeDirectory(),
        authz_reader=PermissiveAuthzReader(),
        project_spent_reader=FakeProjectSpentReader(),
    )
    messenger = _messenger()
    outcome = feature.ask_salary(
        scope=_company_scope(uuid4(), is_admin=True),
        project_id=uuid4(),
        text="lương tháng 9 của Minh",
        user_id=uuid4(),
        message_id=uuid4(),
        lang="vi",
        messenger=messenger,
        trace_id="t",
    )
    assert outcome == "answered"
    body = _last(messenger).body or ""
    assert "480" in body
    assert "300" not in body


def test_ask_salary_lists_every_worker_when_no_name_is_named() -> None:
    bucket = _MonthBucket(
        2026,
        9,
        [
            _WorkerPay(str(uuid4()), "Minh", 480.0, 3),
            _WorkerPay(str(uuid4()), "Tuấn", 300.0, 2),
        ],
    )
    feature = AdminAnswersFeature(
        project_repo=FakeProjectRepo([]),
        invoice_repo=FakeInvoiceRepoForAdmin((Decimal(0), Decimal(0)), (Decimal(0), Decimal(0), Decimal(0))),
        billing_repo=FakeBillingRepo({}),
        labor_payments_usecase=FakeLaborPaymentsUseCaseForSalary([bucket]),
        audit=None,
        directory=FakeDirectory(),
        authz_reader=PermissiveAuthzReader(),
        project_spent_reader=FakeProjectSpentReader(),
    )
    messenger = _messenger()
    outcome = feature.ask_salary(
        scope=_company_scope(uuid4(), is_admin=True),
        project_id=uuid4(),
        text="bảng lương tháng 9",
        user_id=uuid4(),
        message_id=uuid4(),
        lang="vi",
        messenger=messenger,
        trace_id="t",
    )
    assert outcome == "answered"
    body = _last(messenger).body or ""
    assert "Minh" in body
    assert "Tuấn" in body


def test_ask_salary_word_boundary_accent_insensitive_worker_match() -> None:
    bucket = _MonthBucket(
        2026,
        9,
        [
            _WorkerPay(str(uuid4()), "An", 100.0, 1),
            _WorkerPay(str(uuid4()), "Tuấn Anh", 200.0, 1),
        ],
    )
    feature = AdminAnswersFeature(
        project_repo=FakeProjectRepo([]),
        invoice_repo=FakeInvoiceRepoForAdmin((Decimal(0), Decimal(0)), (Decimal(0), Decimal(0), Decimal(0))),
        billing_repo=FakeBillingRepo({}),
        labor_payments_usecase=FakeLaborPaymentsUseCaseForSalary([bucket]),
        audit=None,
        directory=FakeDirectory(),
        authz_reader=PermissiveAuthzReader(),
        project_spent_reader=FakeProjectSpentReader(),
    )
    messenger = _messenger()
    outcome = feature.ask_salary(
        scope=_company_scope(uuid4(), is_admin=True),
        project_id=uuid4(),
        text="luong thang 9 cua Tuan Anh",
        user_id=uuid4(),
        message_id=uuid4(),
        lang="vi",
        messenger=messenger,
        trace_id="t",
    )
    assert outcome == "answered"
    body = _last(messenger).body or ""
    assert "Tuấn Anh" in body
    assert "200" in body
    assert "100" not in body
