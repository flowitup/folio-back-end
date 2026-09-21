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
        # Response must carry invoice_prefix — the API route serializes it into
        # ProjectResponse; a missing field here makes POST /projects raise and 500.
        assert result.invoice_prefix is None
        mock_repo.create.assert_called_once()

    def test_create_project_blank_name_falls_back_to_address(self):
        """The name is optional: blank or missing, the address labels the project."""
        for name in ("", "   ", None):
            mock_repo = Mock()
            mock_repo.create.side_effect = lambda project, **_: project
            usecase = CreateProjectUseCase(mock_repo)

            request = CreateProjectRequest(name=name, address="  12 Rue des Martyrs  ", owner_id=uuid4())
            result = usecase.execute(request)

            assert result.name == "12 Rue des Martyrs"
            assert result.address == "12 Rue des Martyrs"

    def test_create_project_derived_name_fits_name_column(self):
        """An address longer than the 255-char name column is cut when used as the name."""
        mock_repo = Mock()
        mock_repo.create.side_effect = lambda project, **_: project
        usecase = CreateProjectUseCase(mock_repo)

        result = usecase.execute(CreateProjectRequest(address="a" * 400, owner_id=uuid4()))

        assert result.address == "a" * 400
        assert result.name == "a" * 255

    def test_create_project_missing_address_fails(self):
        """The address is mandatory."""
        mock_repo = Mock()
        usecase = CreateProjectUseCase(mock_repo)

        for address in ("", "   "):
            with pytest.raises(InvalidProjectDataError, match="address is required"):
                usecase.execute(CreateProjectRequest(name="Test", address=address, owner_id=uuid4()))
        mock_repo.create.assert_not_called()

    def test_create_project_address_too_long_fails(self):
        mock_repo = Mock()
        usecase = CreateProjectUseCase(mock_repo)

        with pytest.raises(InvalidProjectDataError, match="address exceeds 500"):
            usecase.execute(CreateProjectRequest(name="Test", address="x" * 501, owner_id=uuid4()))

    def test_create_project_name_too_long_fails(self):
        """Test creation fails with name > 255 chars."""
        mock_repo = Mock()
        usecase = CreateProjectUseCase(mock_repo)

        request = CreateProjectRequest(name="x" * 256, address="123 St", owner_id=uuid4())

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

    def test_list_projects_for_platform_admin(self):
        """Legacy global `*:*` holder sees every project."""
        mock_repo = Mock()
        mock_repo.list_all.return_value = [
            Project(id=uuid4(), name="P1", address=None, owner_id=uuid4(), created_at=datetime.now(timezone.utc)),
            Project(id=uuid4(), name="P2", address=None, owner_id=uuid4(), created_at=datetime.now(timezone.utc)),
        ]

        usecase = ListProjectsUseCase(mock_repo)
        result = usecase.execute(uuid4(), is_platform_admin=True)

        assert len(result) == 2
        mock_repo.list_all.assert_called_once()

    def test_list_projects_for_user(self):
        """Regular user sees owned/assigned projects and their admin companies' projects."""
        user_id = uuid4()
        company_id = uuid4()
        mock_repo = Mock()
        mock_repo.list_for_user_and_companies.return_value = [
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
        result = usecase.execute(user_id, admin_company_ids=[company_id], is_platform_admin=False)

        assert len(result) == 1
        mock_repo.list_for_user_and_companies.assert_called_once_with(user_id, [company_id])

    def test_list_projects_defaults_to_no_admin_companies(self):
        """`admin_company_ids` defaults to an empty list, not None, at the repo boundary."""
        user_id = uuid4()
        mock_repo = Mock()
        mock_repo.list_for_user_and_companies.return_value = []

        usecase = ListProjectsUseCase(mock_repo)
        usecase.execute(user_id)

        mock_repo.list_for_user_and_companies.assert_called_once_with(user_id, [])


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

    def test_update_project_blank_name_relabels_by_address(self):
        """Clearing the name labels the project by its (possibly new) address."""
        project_id = uuid4()
        mock_repo = Mock()
        existing = Project(
            id=project_id,
            name="Old",
            address="Old Address",
            owner_id=uuid4(),
            created_at=datetime.now(timezone.utc),
        )
        mock_repo.find_by_id.return_value = existing
        mock_repo.update.return_value = existing

        usecase = UpdateProjectUseCase(mock_repo)
        usecase.execute(project_id, name="   ", address="New Address")

        assert existing.name == "New Address"
        assert existing.address == "New Address"

    def test_update_project_blank_name_without_address_change_uses_current_address(self):
        project_id = uuid4()
        mock_repo = Mock()
        existing = Project(
            id=project_id,
            name="Custom label",
            address="12 Rue X",
            owner_id=uuid4(),
            created_at=datetime.now(timezone.utc),
        )
        mock_repo.find_by_id.return_value = existing
        mock_repo.update.return_value = existing

        UpdateProjectUseCase(mock_repo).execute(project_id, name="")

        assert existing.name == "12 Rue X"

    def test_update_project_blank_name_on_legacy_project_without_address_keeps_name(self):
        """Projects created before the address became mandatory keep a non-empty name."""
        project_id = uuid4()
        mock_repo = Mock()
        existing = Project(
            id=project_id,
            name="Legacy",
            address=None,
            owner_id=uuid4(),
            created_at=datetime.now(timezone.utc),
        )
        mock_repo.find_by_id.return_value = existing
        mock_repo.update.return_value = existing

        UpdateProjectUseCase(mock_repo).execute(project_id, name="")

        assert existing.name == "Legacy"

    def test_update_project_address_only_change_keeps_address_label_in_sync(self):
        """Without a custom label, changing the address re-labels the project."""
        project_id = uuid4()
        mock_repo = Mock()
        existing = Project(
            id=project_id,
            name="12 Rue X",
            address="12 Rue X",
            owner_id=uuid4(),
            created_at=datetime.now(timezone.utc),
        )
        mock_repo.find_by_id.return_value = existing
        mock_repo.update.return_value = existing

        UpdateProjectUseCase(mock_repo).execute(project_id, address="9 Avenue Neuve")

        assert existing.name == "9 Avenue Neuve"
        assert existing.address == "9 Avenue Neuve"

    def test_update_project_address_only_change_keeps_custom_label(self):
        project_id = uuid4()
        mock_repo = Mock()
        existing = Project(
            id=project_id,
            name="Custom label",
            address="12 Rue X",
            owner_id=uuid4(),
            created_at=datetime.now(timezone.utc),
        )
        mock_repo.find_by_id.return_value = existing
        mock_repo.update.return_value = existing

        UpdateProjectUseCase(mock_repo).execute(project_id, address="9 Avenue Neuve")

        assert existing.name == "Custom label"

    def test_update_project_address_label_sync_survives_name_column_truncation(self):
        """A label cut to the 255-char name column still counts as the address label."""
        project_id = uuid4()
        mock_repo = Mock()
        long_address = "a" * 400
        existing = Project(
            id=project_id,
            name=long_address[:255],
            address=long_address,
            owner_id=uuid4(),
            created_at=datetime.now(timezone.utc),
        )
        mock_repo.find_by_id.return_value = existing
        mock_repo.update.return_value = existing

        UpdateProjectUseCase(mock_repo).execute(project_id, address="b" * 300)

        assert existing.name == "b" * 255

    def test_update_project_explicit_null_address_fails(self):
        """`"address": null` in the body is a blank address, not a no-op."""
        project_id = uuid4()
        mock_repo = Mock()
        mock_repo.find_by_id.return_value = Project(
            id=project_id,
            name="Old",
            address="Old Address",
            owner_id=uuid4(),
            created_at=datetime.now(timezone.utc),
        )

        with pytest.raises(InvalidProjectDataError, match="address cannot be empty"):
            UpdateProjectUseCase(mock_repo).execute(project_id, address=None, provided_fields={"address"})
        mock_repo.update.assert_not_called()

    def test_update_project_omitted_name_is_untouched(self):
        project_id = uuid4()
        mock_repo = Mock()
        existing = Project(
            id=project_id,
            name="Custom label",
            address="12 Rue X",
            owner_id=uuid4(),
            created_at=datetime.now(timezone.utc),
        )
        mock_repo.find_by_id.return_value = existing
        mock_repo.update.return_value = existing

        UpdateProjectUseCase(mock_repo).execute(project_id, invoice_prefix="ABC", provided_fields={"invoice_prefix"})

        assert existing.name == "Custom label"

    def test_update_project_empty_address_fails(self):
        """The address cannot be blanked once the project exists."""
        project_id = uuid4()
        mock_repo = Mock()
        mock_repo.find_by_id.return_value = Project(
            id=project_id,
            name="Old",
            address="Old Address",
            owner_id=uuid4(),
            created_at=datetime.now(timezone.utc),
        )

        usecase = UpdateProjectUseCase(mock_repo)

        with pytest.raises(InvalidProjectDataError, match="address cannot be empty"):
            usecase.execute(project_id, address="   ")
        mock_repo.update.assert_not_called()

    def test_update_project_name_too_long_fails(self):
        project_id = uuid4()
        mock_repo = Mock()
        mock_repo.find_by_id.return_value = Project(
            id=project_id,
            name="Old",
            address="Old Address",
            owner_id=uuid4(),
            created_at=datetime.now(timezone.utc),
        )

        with pytest.raises(InvalidProjectDataError, match="exceeds 255"):
            UpdateProjectUseCase(mock_repo).execute(project_id, name="x" * 256)


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
