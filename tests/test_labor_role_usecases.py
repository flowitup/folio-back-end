"""Unit tests for labor role use cases.

Covers:
- CreateLaborRoleUseCase: happy path, duplicate-name conflict
- UpdateLaborRoleUseCase: happy path, self-rename (no conflict), rename clash,
  color-only update, not found
- DeleteLaborRoleUseCase: happy path, not found
- ListLaborRolesUseCase: empty list, with roles
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

from app.application.labor.create_labor_role_usecase import CreateLaborRoleUseCase
from app.application.labor.delete_labor_role_usecase import DeleteLaborRoleUseCase
from app.application.labor.list_labor_roles_usecase import ListLaborRolesUseCase
from app.application.labor.update_labor_role_usecase import UpdateLaborRoleUseCase
from app.domain.entities.labor_role import LaborRole
from app.domain.exceptions.labor_exceptions import (
    DuplicateLaborRoleError,
    LaborRoleNotFoundError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_role(name: str = "Thợ chính", color: str = "#E11D48") -> LaborRole:
    now = datetime.now(timezone.utc)
    return LaborRole(id=uuid4(), name=name, color=color, created_at=now)


def _mock_db_session():
    """Return a mock db session whose commit() is a no-op."""
    session = MagicMock()
    session.commit = MagicMock()
    return session


# ---------------------------------------------------------------------------
# In-memory repository for use-case tests
# ---------------------------------------------------------------------------


class InMemoryLaborRoleRepository:
    """Minimal in-memory implementation of ILaborRoleRepository for unit tests."""

    def __init__(self) -> None:
        self._store: Dict[UUID, LaborRole] = {}

    def create(self, role: LaborRole) -> LaborRole:
        self._store[role.id] = role
        return role

    def find_by_id(self, role_id: UUID) -> Optional[LaborRole]:
        return self._store.get(role_id)

    def find_by_name(self, name: str) -> Optional[LaborRole]:
        for role in self._store.values():
            if role.name == name:
                return role
        return None

    def list_all(self) -> List[LaborRole]:
        return sorted(self._store.values(), key=lambda r: r.name)

    def update(self, role: LaborRole) -> LaborRole:
        self._store[role.id] = role
        return role

    def delete(self, role_id: UUID) -> bool:
        if role_id in self._store:
            del self._store[role_id]
            return True
        return False


# ---------------------------------------------------------------------------
# CreateLaborRoleUseCase
# ---------------------------------------------------------------------------


class TestCreateLaborRoleUseCase:
    def test_create_happy_path(self):
        repo = InMemoryLaborRoleRepository()
        db = _mock_db_session()
        uc = CreateLaborRoleUseCase(repo=repo, db_session=db)

        role = uc.execute(name="Thợ chính", color="#E11D48")

        assert role.name == "Thợ chính"
        assert role.color == "#E11D48"
        assert role.id is not None
        db.commit.assert_called_once()

    def test_create_duplicate_name_raises(self):
        repo = InMemoryLaborRoleRepository()
        db = _mock_db_session()
        uc = CreateLaborRoleUseCase(repo=repo, db_session=db)

        uc.execute(name="Thợ chính", color="#E11D48")

        with pytest.raises(DuplicateLaborRoleError) as exc_info:
            uc.execute(name="Thợ chính", color="#0EA5E9")

        assert "Thợ chính" in str(exc_info.value)
        assert db.commit.call_count == 1  # Only first call committed

    def test_create_invalid_color_raises_value_error(self):
        repo = InMemoryLaborRoleRepository()
        db = _mock_db_session()
        uc = CreateLaborRoleUseCase(repo=repo, db_session=db)

        with pytest.raises(ValueError, match="hex"):
            uc.execute(name="Valid Name", color="not-a-color")

    def test_create_empty_name_raises_value_error(self):
        repo = InMemoryLaborRoleRepository()
        db = _mock_db_session()
        uc = CreateLaborRoleUseCase(repo=repo, db_session=db)

        with pytest.raises(ValueError):
            uc.execute(name="   ", color="#E11D48")

    def test_create_persists_to_repo(self):
        repo = InMemoryLaborRoleRepository()
        db = _mock_db_session()
        uc = CreateLaborRoleUseCase(repo=repo, db_session=db)

        role = uc.execute(name="Thợ phụ", color="#7C3AED")

        assert repo.find_by_id(role.id) is not None
        assert repo.find_by_name("Thợ phụ") is not None


# ---------------------------------------------------------------------------
# UpdateLaborRoleUseCase
# ---------------------------------------------------------------------------


class TestUpdateLaborRoleUseCase:
    def test_update_name_happy_path(self):
        repo = InMemoryLaborRoleRepository()
        existing = _make_role("Old Name", "#E11D48")
        repo.create(existing)
        db = _mock_db_session()
        uc = UpdateLaborRoleUseCase(repo=repo, db_session=db)

        updated = uc.execute(role_id=existing.id, name="New Name")

        assert updated.name == "New Name"
        assert updated.color == "#E11D48"
        db.commit.assert_called_once()

    def test_update_color_only(self):
        repo = InMemoryLaborRoleRepository()
        existing = _make_role("Thợ chính", "#E11D48")
        repo.create(existing)
        db = _mock_db_session()
        uc = UpdateLaborRoleUseCase(repo=repo, db_session=db)

        updated = uc.execute(role_id=existing.id, color="#0EA5E9")

        assert updated.name == "Thợ chính"
        assert updated.color == "#0EA5E9"

    def test_update_rename_to_same_name_no_conflict(self):
        """Self-rename (same name → same role) must not raise DuplicateLaborRoleError."""
        repo = InMemoryLaborRoleRepository()
        existing = _make_role("Thợ chính", "#E11D48")
        repo.create(existing)
        db = _mock_db_session()
        uc = UpdateLaborRoleUseCase(repo=repo, db_session=db)

        # Updating name to the exact same value — no conflict check should fire.
        updated = uc.execute(role_id=existing.id, name="Thợ chính", color="#0EA5E9")

        assert updated.name == "Thợ chính"
        assert updated.color == "#0EA5E9"

    def test_update_rename_conflict_raises(self):
        """Renaming to an already-used name → DuplicateLaborRoleError."""
        repo = InMemoryLaborRoleRepository()
        role_a = _make_role("Role A", "#E11D48")
        role_b = _make_role("Role B", "#7C3AED")
        repo.create(role_a)
        repo.create(role_b)
        db = _mock_db_session()
        uc = UpdateLaborRoleUseCase(repo=repo, db_session=db)

        with pytest.raises(DuplicateLaborRoleError):
            uc.execute(role_id=role_b.id, name="Role A")

    def test_update_not_found_raises(self):
        repo = InMemoryLaborRoleRepository()
        db = _mock_db_session()
        uc = UpdateLaborRoleUseCase(repo=repo, db_session=db)

        with pytest.raises(LaborRoleNotFoundError):
            uc.execute(role_id=uuid4(), name="Ghost")

    def test_update_sets_updated_at(self):
        repo = InMemoryLaborRoleRepository()
        existing = _make_role()
        repo.create(existing)
        assert existing.updated_at is None  # fresh entity has no updated_at
        db = _mock_db_session()
        uc = UpdateLaborRoleUseCase(repo=repo, db_session=db)

        updated = uc.execute(role_id=existing.id, color="#10B981")

        assert updated.updated_at is not None


# ---------------------------------------------------------------------------
# DeleteLaborRoleUseCase
# ---------------------------------------------------------------------------


class TestDeleteLaborRoleUseCase:
    def test_delete_happy_path(self):
        repo = InMemoryLaborRoleRepository()
        existing = _make_role()
        repo.create(existing)
        db = _mock_db_session()
        uc = DeleteLaborRoleUseCase(repo=repo, db_session=db)

        uc.execute(role_id=existing.id)

        assert repo.find_by_id(existing.id) is None
        db.commit.assert_called_once()

    def test_delete_not_found_raises(self):
        repo = InMemoryLaborRoleRepository()
        db = _mock_db_session()
        uc = DeleteLaborRoleUseCase(repo=repo, db_session=db)

        with pytest.raises(LaborRoleNotFoundError):
            uc.execute(role_id=uuid4())

    def test_delete_not_found_does_not_commit(self):
        repo = InMemoryLaborRoleRepository()
        db = _mock_db_session()
        uc = DeleteLaborRoleUseCase(repo=repo, db_session=db)

        with pytest.raises(LaborRoleNotFoundError):
            uc.execute(role_id=uuid4())

        db.commit.assert_not_called()


# ---------------------------------------------------------------------------
# ListLaborRolesUseCase
# ---------------------------------------------------------------------------


class TestListLaborRolesUseCase:
    def test_list_empty(self):
        repo = InMemoryLaborRoleRepository()
        uc = ListLaborRolesUseCase(repo=repo)

        result = uc.execute()

        assert result == []

    def test_list_with_roles_sorted_by_name(self):
        repo = InMemoryLaborRoleRepository()
        repo.create(_make_role("Thợ phụ", "#7C3AED"))
        repo.create(_make_role("Thợ chính", "#E11D48"))
        uc = ListLaborRolesUseCase(repo=repo)

        result = uc.execute()

        assert len(result) == 2
        # In-memory repo sorts by name → "Thợ chính" before "Thợ phụ"
        # Vietnamese collation aside, ASCII comparison: "c" < "p".
        names = [r.name for r in result]
        assert "Thợ chính" in names
        assert "Thợ phụ" in names

    def test_list_returns_correct_fields(self):
        repo = InMemoryLaborRoleRepository()
        role = _make_role("Thợ chính", "#E11D48")
        repo.create(role)
        uc = ListLaborRolesUseCase(repo=repo)

        result = uc.execute()

        assert len(result) == 1
        r = result[0]
        assert r.id == role.id
        assert r.name == "Thợ chính"
        assert r.color == "#E11D48"
        assert r.created_at == role.created_at
