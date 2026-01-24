"""Tests for ProjectRepository and ProjectModel."""

import pytest
from uuid import uuid4
from datetime import datetime, timezone

from app.infrastructure.database.models import ProjectModel, UserModel, RoleModel


@pytest.fixture
def owner_user(session):
    """Create owner user for projects."""
    user = UserModel(
        id=uuid4(),
        email="owner@test.com",
        password_hash="hashed",
        is_active=True
    )
    session.add(user)
    session.commit()
    return user


@pytest.fixture
def regular_user(session):
    """Create regular user to assign to projects."""
    user = UserModel(
        id=uuid4(),
        email="user@test.com",
        password_hash="hashed",
        is_active=True
    )
    session.add(user)
    session.commit()
    return user


@pytest.fixture
def sample_project(session, owner_user):
    """Create a sample project."""
    project = ProjectModel(
        id=uuid4(),
        name="Test Project",
        address="123 Test St",
        owner_id=owner_user.id
    )
    session.add(project)
    session.commit()
    return project


class TestProjectModel:
    """Test ProjectModel CRUD operations."""

    def test_create_project(self, session, owner_user):
        """Test creating a new project."""
        project = ProjectModel(
            id=uuid4(),
            name="New Project",
            address="456 New St",
            owner_id=owner_user.id
        )
        session.add(project)
        session.commit()

        result = session.get(ProjectModel, project.id)
        assert result is not None
        assert result.name == "New Project"
        assert result.address == "456 New St"
        assert result.owner_id == owner_user.id

    def test_create_project_without_address(self, session, owner_user):
        """Test creating project with null address."""
        project = ProjectModel(
            id=uuid4(),
            name="No Address Project",
            owner_id=owner_user.id
        )
        session.add(project)
        session.commit()

        result = session.get(ProjectModel, project.id)
        assert result.address is None

    def test_find_project_by_id(self, session, sample_project):
        """Test finding project by ID."""
        result = session.get(ProjectModel, sample_project.id)
        assert result is not None
        assert result.name == sample_project.name

    def test_update_project(self, session, sample_project):
        """Test updating project."""
        sample_project.name = "Updated Name"
        sample_project.address = "Updated Address"
        session.commit()

        result = session.get(ProjectModel, sample_project.id)
        assert result.name == "Updated Name"
        assert result.address == "Updated Address"

    def test_delete_project(self, session, sample_project):
        """Test deleting project."""
        project_id = sample_project.id
        session.delete(sample_project)
        session.commit()

        result = session.get(ProjectModel, project_id)
        assert result is None

    def test_project_owner_relationship(self, session, sample_project, owner_user):
        """Test project owner relationship."""
        result = session.get(ProjectModel, sample_project.id)
        assert result.owner.id == owner_user.id
        assert result.owner.email == "owner@test.com"


class TestProjectUserAssociation:
    """Test M:N relationship between projects and users."""

    def test_add_user_to_project(self, session, sample_project, regular_user):
        """Test adding user to project."""
        sample_project.users.append(regular_user)
        session.commit()

        result = session.get(ProjectModel, sample_project.id)
        assert regular_user in result.users
        assert len(result.users) == 1

    def test_add_multiple_users(self, session, sample_project, owner_user, regular_user):
        """Test adding multiple users to project."""
        sample_project.users.append(owner_user)
        sample_project.users.append(regular_user)
        session.commit()

        result = session.get(ProjectModel, sample_project.id)
        assert len(result.users) == 2

    def test_remove_user_from_project(self, session, sample_project, regular_user):
        """Test removing user from project."""
        sample_project.users.append(regular_user)
        session.commit()

        sample_project.users.remove(regular_user)
        session.commit()

        result = session.get(ProjectModel, sample_project.id)
        assert regular_user not in result.users
        assert len(result.users) == 0

    def test_user_projects_bidirectional(self, session, sample_project, regular_user):
        """Test bidirectional relationship."""
        sample_project.users.append(regular_user)
        session.commit()

        # Access from user side
        user = session.get(UserModel, regular_user.id)
        assert sample_project in user.projects

    def test_cascade_delete_user_projects(self, session, sample_project, regular_user):
        """Test that deleting project removes user associations."""
        sample_project.users.append(regular_user)
        session.commit()

        project_id = sample_project.id
        session.delete(sample_project)
        session.commit()

        # User should still exist
        user = session.get(UserModel, regular_user.id)
        assert user is not None
        # But project association gone
        assert len(user.projects) == 0


class TestProjectTimestamps:
    """Test automatic timestamp handling."""

    def test_created_at_auto_set(self, session, owner_user):
        """Test created_at is automatically set."""
        project = ProjectModel(
            id=uuid4(),
            name="Timestamp Test",
            owner_id=owner_user.id
        )
        session.add(project)
        session.commit()

        result = session.get(ProjectModel, project.id)
        assert result.created_at is not None

    def test_updated_at_auto_set(self, session, owner_user):
        """Test updated_at is automatically set."""
        project = ProjectModel(
            id=uuid4(),
            name="Timestamp Test",
            owner_id=owner_user.id
        )
        session.add(project)
        session.commit()

        result = session.get(ProjectModel, project.id)
        assert result.updated_at is not None
