"""MergePersonsUseCase keeps the source's company profiles and account (QA3 chiffrage-library-inventory-01)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.application.persons.merge_persons import MergeConflictError, MergePersonsRequest, MergePersonsUseCase
from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.company_person import CompanyPersonModel
from app.infrastructure.database.models.person import PersonModel
from app.infrastructure.database.models.user import UserModel
from app.infrastructure.database.repositories.sqlalchemy_person_repository import SqlAlchemyPersonRepository

NOW = datetime.now(timezone.utc)


def _user(session) -> UserModel:
    user = UserModel(id=uuid4(), email=f"qa3-{uuid4().hex[:8]}@test.com", is_active=True)
    session.add(user)
    session.flush()
    return user


def _company(session, creator) -> CompanyModel:
    company = CompanyModel(
        id=uuid4(), legal_name=f"QA3 {uuid4().hex[:6]}", address="1 rue", created_by=creator.id,
        created_at=NOW, updated_at=NOW,
    )  # fmt: skip
    session.add(company)
    session.flush()
    return company


def _person(session, creator, name, *, user=None, phone=None) -> PersonModel:
    person = PersonModel(
        id=uuid4(), name=name, normalized_name=name.lower(), created_by_user_id=creator.id,
        user_id=user.id if user else None, phone=phone, phone_normalized=phone,
    )  # fmt: skip
    session.add(person)
    session.flush()
    return person


def _profile(session, company, person, *, rate=None, phone=None, pending=False) -> CompanyPersonModel:
    row = CompanyPersonModel(
        id=uuid4(), company_id=company.id, person_id=person.id, default_daily_rate=rate,
        phone_normalized=phone, pending_expires_at=NOW + timedelta(days=30) if pending else None,
    )  # fmt: skip
    session.add(row)
    session.flush()
    return row


def _merge(session, source, target):
    uc = MergePersonsUseCase(person_repo=SqlAlchemyPersonRepository(session), db_session=session)
    return uc.execute(MergePersonsRequest(source_person_id=source.id, target_person_id=target.id))


def test_profiles_in_other_companies_move_to_the_target(session):
    admin = _user(session)
    c, o = _company(session, admin), _company(session, admin)
    target = _person(session, admin, "QA3 Trần Văn Dũng")
    source = _person(session, admin, "QA3 Tran Van Dung")
    _profile(session, c, target, rate=Decimal("180"))
    _profile(session, o, source, rate=Decimal("200"), phone="+33620800051")

    result = _merge(session, source, target)

    rows = session.query(CompanyPersonModel).filter_by(person_id=target.id).all()
    assert {r.company_id: r.default_daily_rate for r in rows} == {c.id: Decimal("180"), o.id: Decimal("200")}
    assert result.profiles_moved == 1 and result.profiles_merged == 0


def test_same_company_profiles_merge_and_fill_blanks(session):
    admin = _user(session)
    c = _company(session, admin)
    target = _person(session, admin, "QA3 Phuc (doublon)")
    source = _person(session, admin, "QA3 Phúc", user=_user(session), phone="+33620800005")
    _profile(session, c, target, pending=True, phone="+33620800054")
    _profile(session, c, source, rate=Decimal("150"))

    result = _merge(session, source, target)

    merged = session.query(CompanyPersonModel).filter_by(company_id=c.id).all()
    assert len(merged) == 1
    assert merged[0].person_id == target.id
    assert merged[0].default_daily_rate == Decimal("150")
    assert merged[0].pending_expires_at is None  # the source was a signed-up member
    session.refresh(target)
    assert target.user_id is not None and result.account_linked is True
    assert result.profiles_merged == 1


def test_two_different_accounts_cannot_be_merged(session):
    admin = _user(session)
    a = _person(session, admin, "QA3 A", user=_user(session))
    b = _person(session, admin, "QA3 B", user=_user(session))
    with pytest.raises(MergeConflictError):
        _merge(session, a, b)
