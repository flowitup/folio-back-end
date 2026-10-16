"""QA round 3 labor fixes: worker rename/relink, single-log conflicts, person search, merge."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.application.labor import LogAttendanceRequest, LogAttendanceUseCase
from app.application.labor.bulk_log_attendance import ConflictsNotAcknowledgedError
from app.application.labor.update_worker import UpdateWorkerRequest, UpdateWorkerUseCase
from app.domain.entities.worker import Worker


def _worker(**kwargs) -> Worker:
    base = dict(
        id=uuid4(),
        project_id=uuid4(),
        name="+33620300003",
        daily_rate=Decimal("100"),
        is_active=True,
        created_at=datetime.now(timezone.utc),
        person_id=uuid4(),
    )
    base.update(kwargs)
    return Worker(**base)


def _usecase(worker, *, person, company_role="member", taken=None):
    repo = Mock()
    repo.find_by_id.return_value = worker
    repo.update.side_effect = lambda w: w
    repo.find_by_project_and_user.return_value = taken
    person_repo = Mock()
    person_repo.find_by_id.return_value = person
    company_id = uuid4()
    authz = Mock()
    authz.project_company_id.return_value = company_id
    authz.company_role_for.return_value = company_role
    # Listed by another company too: a plain rename would be refused.
    company_persons = Mock(list_for_person=Mock(return_value=[Mock(company_id=company_id), Mock(company_id=uuid4())]))
    return (
        UpdateWorkerUseCase(repo, person_repo=person_repo, company_person_repo=company_persons, authz_reader=authz),
        person_repo,
    )


class TestPhoneNamedPersonCanBeRenamed:
    def test_a_person_only_named_by_their_phone_gets_a_readable_name(self):
        worker = _worker()
        person = Mock(name="person", user_id=uuid4(), phone="+33620300003")
        person.name = "+33620300003"
        usecase, person_repo = _usecase(worker, person=person)

        result = usecase.execute(
            UpdateWorkerRequest(worker_id=worker.id, project_id=worker.project_id, name="Nguyễn Văn Đức")
        )

        person_repo.rename.assert_called_once_with(worker.person_id, "Nguyễn Văn Đức", commit=False)
        assert result.name == "Nguyễn Văn Đức"

    def test_a_real_name_of_an_account_holder_is_still_protected(self):
        from app.domain.exceptions.labor_exceptions import SharedPersonEditError

        worker = _worker(name="Đức")
        person = Mock(user_id=uuid4(), phone="+33620300003")
        person.name = "Đức"
        usecase, person_repo = _usecase(worker, person=person)
        with pytest.raises(SharedPersonEditError):
            usecase.execute(UpdateWorkerRequest(worker_id=worker.id, project_id=worker.project_id, name="Other"))
        person_repo.rename.assert_not_called()


class TestReactivationRelinksTheAccount:
    def test_restart_gives_the_person_their_account_back(self):
        account = uuid4()
        worker = _worker(is_active=False, user_id=None)
        person = Mock(user_id=account)
        usecase, _ = _usecase(worker, person=person)

        result = usecase.execute(
            UpdateWorkerRequest(worker_id=worker.id, project_id=worker.project_id, reactivate=True)
        )
        assert (result.is_active, result.user_id) == (True, str(account))

    @pytest.mark.parametrize("left_company, slot_taken", [(True, False), (False, True)])
    def test_no_relink_when_the_account_left_or_the_slot_is_taken(self, left_company, slot_taken):
        worker = _worker(is_active=False, user_id=None)
        usecase, _ = _usecase(
            worker,
            person=Mock(user_id=uuid4()),
            company_role=None if left_company else "member",
            taken=Mock(id=uuid4()) if slot_taken else None,
        )
        result = usecase.execute(
            UpdateWorkerRequest(worker_id=worker.id, project_id=worker.project_id, reactivate=True)
        )
        assert (result.is_active, result.user_id) == (True, None)


class TestSingleLogChecksOtherSites:
    def _usecase(self, conflicts):
        worker = _worker()
        workers = Mock(find_by_id=Mock(return_value=worker))
        entries = Mock()
        entries.create.side_effect = lambda e: e
        entries.find_cross_project_conflicts.return_value = conflicts
        return LogAttendanceUseCase(worker_repo=workers, entry_repo=entries), worker, entries

    def test_a_day_already_logged_on_another_site_is_refused(self):
        usecase, worker, entries = self._usecase([Mock()])
        with pytest.raises(ConflictsNotAcknowledgedError):
            usecase.execute(
                LogAttendanceRequest(
                    project_id=worker.project_id, worker_id=worker.id, date=date.today(), shift_type="full"
                )
            )
        entries.create.assert_not_called()

    def test_acknowledged_conflicts_are_logged(self):
        usecase, worker, entries = self._usecase([Mock()])
        usecase.execute(
            LogAttendanceRequest(
                project_id=worker.project_id,
                worker_id=worker.id,
                date=date.today(),
                shift_type="full",
                acknowledge_conflicts=True,
            )
        )
        entries.create.assert_called_once()
        entries.find_cross_project_conflicts.assert_not_called()


def _person_model(session, name, phone=None, phone_normalized=None):
    from app.infrastructure.database.models import PersonModel

    owner = _user(session)
    now = datetime.now(timezone.utc)
    model = PersonModel(
        id=uuid4(),
        name=name,
        normalized_name=name.lower(),
        phone=phone,
        phone_normalized=phone_normalized,
        created_by_user_id=owner,
        created_at=now,
        updated_at=now,
    )
    session.add(model)
    session.flush()
    return model


def _user(session):
    from app.infrastructure.database.models.user import UserModel

    user = UserModel(id=uuid4(), email=f"{uuid4().hex[:10]}@x.fr", is_active=True)
    session.add(user)
    session.flush()
    return user.id


class TestPersonSearchByPhoneInAnyFormat:
    @pytest.mark.parametrize("query", ["06 20 30 00 04", "0620300004", "+33620300004", "+33 6 20 30 00 04"])
    def test_e164_stored_number_is_found_whatever_the_typing(self, session, query):
        from app.infrastructure.database.repositories.sqlalchemy_person_repository import SqlAlchemyPersonRepository

        stored = _person_model(session, "Member", phone="+33620300004", phone_normalized="+33620300004")
        assert stored.id in [p.id for p in SqlAlchemyPersonRepository(session).search(query)]

    @pytest.mark.parametrize("query", ["+33620300010", "0620300010"])
    def test_free_text_stored_number_is_found_by_its_digits(self, session, query):
        from app.infrastructure.database.repositories.sqlalchemy_person_repository import SqlAlchemyPersonRepository

        stored = _person_model(session, "Jean", phone="06 20 30 00 10")
        assert stored.id in [p.id for p in SqlAlchemyPersonRepository(session).search(query)]


def test_merge_is_refused_when_both_persons_work_on_the_same_project(session):
    from app.application.persons.merge_persons import InvalidMergeError, MergePersonsRequest, MergePersonsUseCase
    from app.infrastructure.database.models import WorkerModel
    from app.infrastructure.database.models.project import ProjectModel
    from app.infrastructure.database.repositories.sqlalchemy_person_repository import SqlAlchemyPersonRepository

    source, target = _person_model(session, "Le Thi Hoa"), _person_model(session, "Lê Thị Hoa")
    from app.infrastructure.database.models.company import CompanyModel

    owner = _user(session)
    now = datetime.now(timezone.utc)
    company = CompanyModel(
        id=uuid4(), legal_name="Co", address="1 rue", created_by=owner, created_at=now, updated_at=now
    )
    session.add(company)
    session.flush()
    project = ProjectModel(id=uuid4(), name="P1", owner_id=owner, company_id=company.id)
    session.add(project)
    session.flush()
    for person, rate in ((source, "160"), (target, "150")):
        session.add(
            WorkerModel(id=uuid4(), project_id=project.id, person_id=person.id, name=person.name, daily_rate=rate)
        )
    session.flush()

    with pytest.raises(InvalidMergeError) as exc:
        MergePersonsUseCase(SqlAlchemyPersonRepository(session), session).execute(
            MergePersonsRequest(source_person_id=source.id, target_person_id=target.id)
        )
    assert str(project.id) in str(exc.value)


def test_company_wide_stop_only_reaches_sites_the_caller_manages(monkeypatch):
    """A manager of A and B who stops a worker on A never changes the worker on C."""
    from app.api.v1.labor import worker_routes

    a, b, c, caller = uuid4(), uuid4(), uuid4(), uuid4()
    roster = Mock(person_project_ids=Mock(return_value=[a, b, c]))
    monkeypatch.setattr(worker_routes, "get_container", lambda: Mock(enroll_company_workers_usecase=roster))
    monkeypatch.setattr(worker_routes, "get_jwt_identity", lambda: str(caller))
    monkeypatch.setattr(worker_routes, "caller_manages_labor", lambda project, user: project in (a, b))

    assert worker_routes._sites_caller_manages(str(a), uuid4()) == [a, b]
