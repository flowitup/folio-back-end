"""A name taken between the use case's check and the insert is a 409, not a 500.

Two concurrent requests with one room name, unit symbol or shop name both pass the
use case's "already exists?" look-up; the unique index then refuses the second insert.
These tests skip the look-up (as the losing request effectively did) and insert twice.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.application.chiffrage.exceptions import (
    RoomAlreadyExistsError,
    StoreAlreadyExistsError,
    UnitAlreadyExistsError,
)
from app.domain.entities.chiffrage_room import ChiffrageRoom
from app.domain.entities.chiffrage_store import ChiffrageStore
from app.domain.entities.chiffrage_unit import ChiffrageUnit
from app.infrastructure.database.models import ProjectModel, UserModel
from app.infrastructure.database.repositories.sqlalchemy_chiffrage_repository import SqlAlchemyChiffrageRepository
from tests.company_tenancy_helper import company_for_projects


@pytest.fixture
def project_id(session):
    owner = UserModel(email=f"chiffrage-race-{uuid4().hex[:8]}@test.com", is_active=True)
    session.add(owner)
    session.flush()
    project = ProjectModel(
        id=uuid4(), name="Race project", owner_id=owner.id, company_id=company_for_projects(session, owner.id)
    )
    session.add(project)
    session.flush()
    return project.id


def test_second_room_with_the_same_name_is_already_exists(session, project_id):
    repo = SqlAlchemyChiffrageRepository(session)
    repo.add_room(ChiffrageRoom.create(project_id=project_id, name="Cuisine", position=1000))

    with pytest.raises(RoomAlreadyExistsError):
        repo.add_room(ChiffrageRoom.create(project_id=project_id, name="Cuisine", position=2000))

    # The savepoint rolled back only the losing insert; the session stays usable.
    assert [room.name for room in repo.list_rooms(project_id)] == ["Cuisine"]


def test_second_unit_with_the_same_symbol_is_already_exists(session, project_id):
    repo = SqlAlchemyChiffrageRepository(session)
    repo.add_unit(ChiffrageUnit.create(project_id=project_id, symbol="palette"))

    with pytest.raises(UnitAlreadyExistsError):
        repo.add_unit(ChiffrageUnit.create(project_id=project_id, symbol="palette"))

    assert [unit.symbol for unit in repo.list_units(project_id)] == ["palette"]


def test_second_shop_with_the_same_name_is_already_exists(session, project_id):
    repo = SqlAlchemyChiffrageRepository(session)
    first = ChiffrageStore.create(project_id=project_id, name="Leroy Merlin", position=1000)
    repo.add_store(first)

    with pytest.raises(StoreAlreadyExistsError):
        repo.add_store(ChiffrageStore.create(project_id=project_id, name="leroy merlin", position=2000))

    assert repo.find_store(first.id) is not None
    assert not repo.store_name_exists(project_id, "Leroy Merlin", first.id)
