"""A person created inline by "Add worker" is listed in the project company's directory."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.application.labor.create_worker import CreateWorkerRequest, CreateWorkerUseCase
from app.domain.exceptions.labor_exceptions import WorkerAlreadyOnProjectError


def _usecase(company_person_find=None):
    company_id, project_id = uuid4(), uuid4()
    worker_repo = MagicMock()
    worker_repo.find_by_project_and_user.return_value = None
    worker_repo.create.side_effect = lambda w: w
    person_repo = MagicMock()
    person_repo.create.side_effect = lambda p, **_: p
    person_repo.find_by_id.side_effect = lambda pid: MagicMock(id=pid, phone_normalized=None)
    company_person_repo = MagicMock()
    company_person_repo.find.return_value = company_person_find
    authz_reader = MagicMock()
    authz_reader.project_company_id.return_value = company_id
    uc = CreateWorkerUseCase(
        worker_repo, person_repo=person_repo, company_person_repo=company_person_repo, authz_reader=authz_reader
    )
    return uc, company_person_repo, company_id, project_id


def test_inline_person_gets_a_profile_in_the_project_company():
    uc, company_person_repo, company_id, project_id = _usecase()
    result = uc.execute(
        CreateWorkerRequest(
            project_id=project_id, name="Nguyen Van A", daily_rate=Decimal("120"), created_by_user_id=uuid4()
        )
    )
    company_person_repo.save.assert_called_once()
    profile = company_person_repo.save.call_args.args[0]
    assert profile.company_id == company_id
    assert str(profile.person_id) == result.person_id
    assert profile.is_active is True


def test_existing_person_is_not_attached_to_the_company_by_the_worker_create():
    uc, company_person_repo, _, project_id = _usecase()
    uc.execute(
        CreateWorkerRequest(
            project_id=project_id,
            name="Picked",
            daily_rate=Decimal("120"),
            person_id=uuid4(),
            created_by_user_id=uuid4(),
        )
    )
    company_person_repo.save.assert_not_called()


def test_person_the_caller_just_created_in_the_picker_is_listed_in_the_company():
    """The web picker creates the person (POST /persons), then sends its id."""
    uc, company_person_repo, company_id, project_id = _usecase()
    caller = uuid4()
    uc._person_repo.find_by_id.side_effect = lambda pid: MagicMock(
        id=pid, phone_normalized=None, created_by_user_id=caller
    )
    person_id = uuid4()
    uc.execute(
        CreateWorkerRequest(
            project_id=project_id,
            name="Charlie",
            daily_rate=Decimal("80"),
            person_id=person_id,
            created_by_user_id=caller,
        )
    )
    company_person_repo.save.assert_called_once()
    profile = company_person_repo.save.call_args.args[0]
    assert (profile.company_id, profile.person_id) == (company_id, person_id)


@pytest.mark.parametrize("stored", ["+33620600001", "06 20 60 00 01", "0620600001"])
def test_inline_add_with_a_known_phone_reuses_the_company_person(stored):
    """However the directory person's number was typed, the same number matches them."""
    uc, company_person_repo, _, project_id = _usecase()
    known, other = uuid4(), uuid4()
    company_person_repo.find_by_phone.return_value = None
    company_person_repo.list_for_company.return_value = [MagicMock(person_id=other), MagicMock(person_id=known)]
    uc._repo.list_by_project.return_value = []
    uc._person_repo.find_by_ids.return_value = [
        MagicMock(id=other, phone="06 99 99 99 99"),
        MagicMock(id=known, phone=stored),
    ]
    result = uc.execute(
        CreateWorkerRequest(
            project_id=project_id,
            name="Alpha dup",
            phone="+33 6 20 60 00 01",
            daily_rate=Decimal("100"),
            created_by_user_id=uuid4(),
        )
    )
    uc._person_repo.create.assert_not_called()
    assert result.person_id == str(known)


def test_inline_add_with_the_phone_of_a_worker_already_on_the_project_is_refused():
    """A person on the roster but missing from the directory (older rows) is still matched by number."""
    uc, company_person_repo, _, project_id = _usecase()
    existing = MagicMock(id=uuid4(), person_id=uuid4(), person_phone="06 20 60 00 01", is_active=True)
    uc._repo.list_by_project.return_value = [existing]
    company_person_repo.find_by_phone.return_value = None
    company_person_repo.list_for_company.return_value = []
    with pytest.raises(WorkerAlreadyOnProjectError) as err:
        uc.execute(
            CreateWorkerRequest(
                project_id=project_id,
                name="Alpha again",
                phone="0620600001",
                daily_rate=Decimal("100"),
                created_by_user_id=uuid4(),
            )
        )
    assert err.value.worker_id == str(existing.id)
    uc._person_repo.create.assert_not_called()
    uc._repo.create.assert_not_called()


def test_a_person_already_on_the_project_is_refused():
    uc, _, _, project_id = _usecase()
    person_id = uuid4()
    existing = MagicMock(id=uuid4(), person_id=person_id, is_active=False)
    uc._repo.list_by_project.return_value = [existing]
    with pytest.raises(WorkerAlreadyOnProjectError) as err:
        uc.execute(
            CreateWorkerRequest(
                project_id=project_id,
                name="Twice",
                daily_rate=Decimal("100"),
                person_id=person_id,
                created_by_user_id=uuid4(),
            )
        )
    assert err.value.worker_id == str(existing.id)
    assert err.value.is_active is False
    uc._repo.create.assert_not_called()
