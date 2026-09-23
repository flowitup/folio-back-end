"""Unit tests for `app.application.assistant.project_resolution.accessible_projects` —
the platform-ops company-channel narrowing must batch the project->company lookup
instead of issuing one query per candidate project."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from app.application.assistant.project_resolution import accessible_projects
from app.domain.entities.project import Project


class FakeProjectRepo:
    def __init__(self, projects: list[Project]) -> None:
        self._projects = projects

    def list_all(self) -> list[Project]:
        return list(self._projects)

    def list_for_user_and_companies(self, user_id: UUID, company_ids: list[UUID]) -> list[Project]:
        raise AssertionError("platform ops must use list_all(), not the per-company union")


class RecordingPlatformOpsAuthzReader:
    """`is_platform_ops` always True, so `has_permission` short-circuits without
    touching any other method — isolates this test to `project_company_id`'s call
    pattern, the thing under test."""

    def __init__(self, company_by_project: dict[UUID, UUID]) -> None:
        self._company_by_project = company_by_project
        self.project_company_id_calls: list[UUID] = []
        self.preload_calls: list[list[UUID]] = []

    def is_platform_ops(self, user_id: UUID) -> bool:
        return True

    def project_company_id(self, project_id: UUID) -> Optional[UUID]:
        self.project_company_id_calls.append(project_id)
        return self._company_by_project.get(project_id)

    def preload_project_company_ids(self, project_ids: list[UUID]) -> None:
        self.preload_calls.append(list(project_ids))


def _project(company_id: UUID) -> Project:
    return Project(id=uuid4(), name="P", owner_id=uuid4(), created_at=datetime.now(timezone.utc), address="1 rue X")


def test_narrowing_to_a_company_preloads_before_the_per_project_lookup() -> None:
    target_company = uuid4()
    other_company = uuid4()
    projects = [_project(target_company) for _ in range(3)] + [_project(other_company)]
    company_by_project = {}
    for p in projects[:3]:
        company_by_project[p.id] = target_company
    company_by_project[projects[3].id] = other_company

    reader = RecordingPlatformOpsAuthzReader(company_by_project)
    repo = FakeProjectRepo(projects)

    result = accessible_projects(project_repo=repo, authz_reader=reader, user_id=uuid4(), company_id=target_company)

    assert {p.id for p in result} == {p.id for p in projects[:3]}
    # Preloaded once, with every candidate, before the per-project calls below run.
    assert reader.preload_calls == [[p.id for p in projects]]
    assert set(reader.project_company_id_calls) == {p.id for p in projects}


def test_still_works_without_a_batch_preload_method_on_the_reader() -> None:
    """An `AuthzReaderPort` implementation that never grew `preload_project_company_ids`
    (a test fake, an older adapter) must keep working — the preload is duck-typed and
    optional, never required."""

    class NoPreloadReader:
        def __init__(self, company_by_project: dict[UUID, UUID]) -> None:
            self._company_by_project = company_by_project

        def is_platform_ops(self, user_id: UUID) -> bool:
            return True

        def project_company_id(self, project_id: UUID) -> Optional[UUID]:
            return self._company_by_project.get(project_id)

    company = uuid4()
    project = _project(company)
    reader = NoPreloadReader({project.id: company})
    repo = FakeProjectRepo([project])

    result = accessible_projects(project_repo=repo, authz_reader=reader, user_id=uuid4(), company_id=company)

    assert [p.id for p in result] == [project.id]
