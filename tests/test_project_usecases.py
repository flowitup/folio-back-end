"""Tests for project use cases."""

import pytest
from uuid import uuid4
from datetime import datetime, timezone
from unittest.mock import Mock

from app.domain.entities.project import Project
from app.domain.exceptions.project_exceptions import ProjectNotFoundError, InvalidProjectDataError
from app.application.projects import (
    CreateProjectUseCase,
    CreateProjectRequest,
    ListProjectsUseCase,
    GetProjectUseCase,
    UpdateProjectUseCase,
    DeleteProjectUseCase,
)


class TestCreateProjectUseCase:
    """Test CreateProjectUseCase."""

    def test_create_project_success(self):
        """Test successful project creation."""
        mock_repo = Mock()
        owner_id = uuid4()
        project_id = uuid4()

        mock_repo.create.return_value = Project(
            id=project_id,
            name="Test Project",
            address="123 St",
            owner_id=owner_id,
            created_at=datetime.now(timezone.utc),
        )

        usecase = CreateProjectUseCase(mock_repo)
        request = CreateProjectRequest(name="Test Project", address="123 St", owner_id=owner_id)

        result = usecase.execute(request)

        assert result.name == "Test Project"
        assert result.address == "123 St"
        mock_repo.create.assert_called_once()

    def test_create_project_empty_name_fails(self):
        """Test creation fails with empty name."""
        mock_repo = Mock()
        usecase = CreateProjectUseCase(mock_repo)

        request = CreateProjectRequest(name="", owner_id=uuid4())

        with pytest.raises(InvalidProjectDataError, match="name is required"):
            usecase.execute(request)

    def test_create_project_whitespace_name_fails(self):
        """Test creation fails with whitespace-only name."""
        mock_repo = Mock()
        usecase = CreateProjectUseCase(mock_repo)

        request = CreateProjectRequest(name="   ", owner_id=uuid4())

        with pytest.raises(InvalidProjectDataError, match="name is required"):
            usecase.execute(request)

    def test_create_project_name_too_long_fails(self):
        """Test creation fails with name > 255 chars."""
        mock_repo = Mock()
        usecase = CreateProjectUseCase(mock_repo)

        request = CreateProjectRequest(name="x" * 256, owner_id=uuid4())

        with pytest.raises(InvalidProjectDataError, match="exceeds 255"):
            usecase.execute(request)

    def test_create_project_strips_whitespace(self):
        """Test name and address are stripped."""
        mock_repo = Mock()
        owner_id = uuid4()

        mock_repo.create.return_value = Project(
            id=uuid4(),
            name="Trimmed",
            address="Trimmed Address",
            owner_id=owner_id,
            created_at=datetime.now(timezone.utc),
        )

        usecase = CreateProjectUseCase(mock_repo)
        request = CreateProjectRequest(name="  Trimmed  ", address="  Trimmed Address  ", owner_id=owner_id)

        usecase.execute(request)

        # Verify the project passed to repo has trimmed values
        call_args = mock_repo.create.call_args[0][0]
        assert call_args.name == "Trimmed"
        assert call_args.address == "Trimmed Address"


class TestGetProjectUseCase:
    """Test GetProjectUseCase."""

    def test_get_project_success(self):
        """Test getting existing project."""
        project_id = uuid4()
        mock_repo = Mock()
        mock_repo.find_by_id.return_value = Project(
            id=project_id,
            name="Test",
            address=None,
            owner_id=uuid4(),
            created_at=datetime.now(timezone.utc),
        )

        usecase = GetProjectUseCase(mock_repo)
        result = usecase.execute(project_id)

        assert result.id == project_id
        assert result.name == "Test"

    def test_get_project_not_found(self):
        """Test getting non-existent project raises error."""
        mock_repo = Mock()
        mock_repo.find_by_id.return_value = None

        usecase = GetProjectUseCase(mock_repo)

        with pytest.raises(ProjectNotFoundError):
            usecase.execute(uuid4())


class TestListProjectsUseCase:
    """Test ListProjectsUseCase."""

    def test_list_projects_for_admin(self):
        """Admin sees all projects."""
        mock_repo = Mock()
        mock_repo.list_all.return_value = [
            Project(id=uuid4(), name="P1", address=None, owner_id=uuid4(), created_at=datetime.now(timezone.utc)),
            Project(id=uuid4(), name="P2", address=None, owner_id=uuid4(), created_at=datetime.now(timezone.utc)),
        ]

        usecase = ListProjectsUseCase(mock_repo)
        result = usecase.execute(uuid4(), is_admin=True)

        assert len(result) == 2
        mock_repo.list_all.assert_called_once()

    def test_list_projects_for_user(self):
        """Regular user sees only assigned projects."""
        user_id = uuid4()
        mock_repo = Mock()
        mock_repo.list_by_user.return_value = [
            Project(
                id=uuid4(),
                name="P1",
                address=None,
                owner_id=uuid4(),
                created_at=datetime.now(timezone.utc),
                user_ids=[user_id],
            ),
        ]

        usecase = ListProjectsUseCase(mock_repo)
        result = usecase.execute(user_id, is_admin=False)

        assert len(result) == 1
        mock_repo.list_by_user.assert_called_once_with(user_id)


class TestUpdateProjectUseCase:
    """Test UpdateProjectUseCase."""

    def test_update_project_success(self):
        """Test successful update."""
        project_id = uuid4()
        mock_repo = Mock()

        existing = Project(
            id=project_id,
            name="Old Name",
            address="Old Address",
            owner_id=uuid4(),
            created_at=datetime.now(timezone.utc),
        )
        mock_repo.find_by_id.return_value = existing
        mock_repo.update.return_value = existing

        usecase = UpdateProjectUseCase(mock_repo)
        usecase.execute(project_id, name="New Name", address="New Address")

        assert existing.name == "New Name"
        assert existing.address == "New Address"
        mock_repo.update.assert_called_once()

    def test_update_project_not_found(self):
        """Test updating non-existent project."""
        mock_repo = Mock()
        mock_repo.find_by_id.return_value = None

        usecase = UpdateProjectUseCase(mock_repo)

        with pytest.raises(ProjectNotFoundError):
            usecase.execute(uuid4(), name="New Name")

    def test_update_project_empty_name_fails(self):
        """Test update with empty name fails."""
        project_id = uuid4()
        mock_repo = Mock()
        mock_repo.find_by_id.return_value = Project(
            id=project_id,
            name="Old",
            address=None,
            owner_id=uuid4(),
            created_at=datetime.now(timezone.utc),
        )

        usecase = UpdateProjectUseCase(mock_repo)

        with pytest.raises(InvalidProjectDataError, match="cannot be empty"):
            usecase.execute(project_id, name="")


class TestDeleteProjectUseCase:
    """Test DeleteProjectUseCase."""

    def test_delete_project_success(self):
        """Test successful deletion."""
        project_id = uuid4()
        mock_repo = Mock()
        mock_repo.find_by_id.return_value = Project(
            id=project_id,
            name="To Delete",
            address=None,
            owner_id=uuid4(),
            created_at=datetime.now(timezone.utc),
        )
        mock_repo.delete.return_value = True

        usecase = DeleteProjectUseCase(mock_repo)
        usecase.execute(project_id)

        mock_repo.delete.assert_called_once_with(project_id)

    def test_delete_nonexistent_project(self):
        """Test deleting non-existent project raises error."""
        mock_repo = Mock()
        mock_repo.find_by_id.return_value = None

        usecase = DeleteProjectUseCase(mock_repo)

        with pytest.raises(ProjectNotFoundError):
            usecase.execute(uuid4())
