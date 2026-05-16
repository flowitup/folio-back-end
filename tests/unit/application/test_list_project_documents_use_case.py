"""Unit tests for ListProjectDocumentsUseCase — pure pass-through to repository."""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

from app.application.project_documents.dtos import ListFiltersDTO, ListResultDTO
from app.application.project_documents.list_project_documents import ListProjectDocumentsUseCase
from app.application.project_documents.ports import IProjectDocumentRepository


def _make_repo() -> MagicMock:
    repo = MagicMock(spec=IProjectDocumentRepository)
    repo.list_for_project.return_value = ListResultDTO(items=[], total=0)
    return repo


class TestListProjectDocumentsUseCase:
    def test_delegates_to_repo(self):
        repo = _make_repo()
        uc = ListProjectDocumentsUseCase(repo=repo)
        project_id = uuid4()
        filters = ListFiltersDTO()

        uc.execute(project_id, filters)

        repo.list_for_project.assert_called_once_with(project_id, filters)

    def test_returns_repo_result(self):
        repo = _make_repo()
        expected = ListResultDTO(items=[], total=42)
        repo.list_for_project.return_value = expected

        uc = ListProjectDocumentsUseCase(repo=repo)
        result = uc.execute(uuid4(), ListFiltersDTO())

        assert result is expected

    def test_passes_filters_unchanged(self):
        repo = _make_repo()
        uc = ListProjectDocumentsUseCase(repo=repo)
        filters = ListFiltersDTO(kinds=("pdf", "image"), page=2, per_page=10, sort="name", order="asc")

        uc.execute(uuid4(), filters)

        _, called_filters = repo.list_for_project.call_args[0]
        assert called_filters is filters

    def test_passes_project_id_unchanged(self):
        repo = _make_repo()
        uc = ListProjectDocumentsUseCase(repo=repo)
        project_id = uuid4()

        uc.execute(project_id, ListFiltersDTO())

        called_project_id, _ = repo.list_for_project.call_args[0]
        assert called_project_id == project_id

    def test_default_filters_passed_when_no_filters_given(self):
        repo = _make_repo()
        uc = ListProjectDocumentsUseCase(repo=repo)
        default_filters = ListFiltersDTO()

        uc.execute(uuid4(), default_filters)

        repo.list_for_project.assert_called_once()
        _, passed_filters = repo.list_for_project.call_args[0]
        assert passed_filters.page == 1
        assert passed_filters.per_page == 25
        assert passed_filters.sort == "created_at"
        assert passed_filters.order == "desc"
