"""A person created inline by "Add worker" is listed in the project company's directory."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

from app.application.labor.create_worker import CreateWorkerRequest, CreateWorkerUseCase


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
