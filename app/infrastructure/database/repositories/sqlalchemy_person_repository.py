"""SQLAlchemy implementation of the Person repository."""

from typing import List, Optional
from uuid import UUID

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.application.persons.ports import IPersonRepository
from app.domain.entities.person import Person, fold_accents
from app.domain.value_objects.phone_number import InvalidPhoneNumberError, is_phone_number, normalize_phone
from app.infrastructure.database.models import PersonModel
from app.infrastructure.database.models.company_person import CompanyPersonModel
from app.infrastructure.database.text_fold import folded as folded_column


def _phone_conditions(query: str) -> list:
    """Extra matches when the query is a phone number typed in any format.

    "06 20 30 00 04", "0620300004" and "+33620300004" are the same number: match the stored
    E.164 form, and a stored free-text number ("06 20 30 00 04") by its last nine digits.
    """
    digits = "".join(ch for ch in query if ch.isdigit())
    if len(digits) < 9 or not is_phone_number(query):
        return []
    try:
        e164 = normalize_phone(query)
    except InvalidPhoneNumberError:
        return []
    stripped_phone = PersonModel.phone
    for separator in (" ", ".", "-", "(", ")", "/"):
        stripped_phone = func.replace(stripped_phone, separator, "")
    return [
        PersonModel.phone_normalized == e164,
        PersonModel.phone == e164,
        stripped_phone.like(f"%{e164[-9:]}"),
    ]


class SqlAlchemyPersonRepository(IPersonRepository):
    """Persistence adapter for Person."""

    def __init__(self, session: Session):
        self._session = session

    # ------------------------------------------------------------------
    # IPersonRepository
    # ------------------------------------------------------------------
    def create(self, person: Person, *, commit: bool = True) -> Person:
        model = PersonModel(
            id=person.id,
            name=person.name,
            phone=person.phone,
            normalized_name=person.normalized_name,
            created_by_user_id=person.created_by_user_id,
            created_at=person.created_at,
            user_id=person.user_id,
            phone_normalized=person.phone_normalized,
        )
        self._session.add(model)
        if commit:
            # M8: default kept True for existing single-write callers. Multi-write
            # onboarding use cases (add-by-phone, sign-up linking, join-by-code)
            # pass commit=False so a Person insert can never survive as a
            # committed orphan when a LATER write in the same use case fails —
            # they flush here and commit once at the end of their own transaction.
            self._session.commit()
        else:
            self._session.flush()
        # Refresh to pick up server-side defaults (updated_at) — safe pre- or
        # post-commit, flush() alone already assigned them.
        self._session.refresh(model)
        return self._to_entity(model)

    def find_by_id(self, person_id: UUID) -> Optional[Person]:
        model = self._session.query(PersonModel).filter_by(id=person_id).first()
        return self._to_entity(model) if model else None

    def find_by_ids(self, person_ids: List[UUID]) -> List[Person]:
        if not person_ids:
            return []
        unique_ids = list(dict.fromkeys(person_ids))
        models = self._session.query(PersonModel).filter(PersonModel.id.in_(unique_ids)).all()
        return [self._to_entity(m) for m in models]

    def find_by_user_id(self, user_id: UUID) -> Optional[Person]:
        model = self._session.query(PersonModel).filter_by(user_id=user_id).first()
        return self._to_entity(model) if model else None

    def set_user_id(self, person_id: UUID, user_id: UUID, *, commit: bool = True) -> Optional[Person]:
        model = self._session.query(PersonModel).filter_by(id=person_id).first()
        if model is None:
            return None
        model.user_id = user_id
        if commit:
            self._session.commit()
        else:
            self._session.flush()
        self._session.refresh(model)
        return self._to_entity(model)

    def rename(self, person_id: UUID, name: str, *, commit: bool = True) -> Optional[Person]:
        from app.infrastructure.database.models.worker import WorkerModel

        model = self._session.query(PersonModel).filter_by(id=person_id).first()
        if model is None:
            return None
        model.name = name
        model.normalized_name = Person.normalize(name)
        # workers.name is a per-project copy of the person's name; keep every copy in step.
        for worker in self._session.query(WorkerModel).filter_by(person_id=person_id).all():
            worker.name = name
        if commit:
            self._session.commit()
        else:
            self._session.flush()
        self._session.refresh(model)
        return self._to_entity(model)

    def change_phone(self, person_id: UUID, phone: Optional[str], *, commit: bool = True) -> Optional[Person]:
        from app.infrastructure.database.models.company import CompanyModel
        from app.infrastructure.database.models.worker import WorkerModel

        model = self._session.query(PersonModel).filter_by(id=person_id).first()
        if model is None:
            return None
        profiles = self._session.query(CompanyPersonModel).filter_by(person_id=person_id).all()
        region = None
        if profiles:
            region = (
                self._session.query(CompanyModel.default_phone_region)
                .filter(CompanyModel.id == profiles[0].company_id)
                .scalar()
            )
        normalized = _normalized_or_none(phone, region or "FR")

        model.phone = phone
        model.phone_normalized = normalized
        # workers.phone is a per-project copy of the person's phone; keep every copy in step.
        for worker in self._session.query(WorkerModel).filter_by(person_id=person_id).all():
            worker.phone = phone
        for profile in profiles:
            taken = normalized is not None and (
                self._session.query(CompanyPersonModel.id)
                .filter(
                    CompanyPersonModel.company_id == profile.company_id,
                    CompanyPersonModel.phone_normalized == normalized,
                    CompanyPersonModel.person_id != person_id,
                )
                .first()
                is not None
            )
            # One profile per number and company, removed ones included (the unique index covers every
            # row with a number): when another person's profile already holds the number there, this
            # one carries none.
            profile.phone_normalized = None if taken else normalized
        if commit:
            self._session.commit()
        else:
            self._session.flush()
        self._session.refresh(model)
        return self._to_entity(model)

    def search(
        self,
        query: str,
        limit: int = 20,
        company_ids: Optional[List[UUID]] = None,
    ) -> List[Person]:
        q = self._session.query(PersonModel)
        normalized = Person.normalize(query)
        # Duplicates usually differ only by accents ('Tran' / 'Trần') or by how the number was
        # typed ('06 20 80 00 52' / '+33620800052'), which is what this search is for.
        folded = fold_accents(query)
        phones = {query.strip()}
        try:
            phones.add(normalize_phone(query))
        except ValueError:
            pass
        fold_in_python = False
        phone_ids: set = set()

        if normalized:
            # A number typed in any format ("06 20 30 00 04" / "+33620300004") also matches the
            # stored E.164 form or a free-text number with the same last nine digits.
            phone_conditions = _phone_conditions(query)
            phone_match = or_(
                PersonModel.phone.in_(phones), PersonModel.phone_normalized.in_(phones), *phone_conditions
            )
            if self._session.get_bind().dialect.name == "postgresql":
                folded_name = folded_column(PersonModel.normalized_name)
                # autoescape: "%" and "_" in the search text match only themselves.
                q = q.filter(or_(folded_name.contains(folded, autoescape=True), phone_match))
            else:
                # No translate() (SQLite, the test database): fold the scoped rows here.
                fold_in_python = True
                if phone_conditions:
                    phone_ids = {row.id for row in self._session.query(PersonModel.id).filter(or_(*phone_conditions))}

        if company_ids is not None:
            # Tenancy scope (Phase 2): only persons with an active
            # company_persons row in one of the caller's admin/manager
            # companies. `.distinct()` guards against duplicate rows when a
            # person has profiles in more than one of the given companies.
            q = q.join(
                CompanyPersonModel,
                CompanyPersonModel.person_id == PersonModel.id,
            ).filter(
                CompanyPersonModel.company_id.in_(company_ids),
                CompanyPersonModel.is_active.is_(True),
            )
            q = q.distinct()

        q = q.order_by(PersonModel.normalized_name)
        if fold_in_python:
            models = [
                m
                for m in q.all()
                if folded in fold_accents(m.normalized_name)
                or m.phone in phones
                or m.phone_normalized in phones
                or m.id in phone_ids
            ][:limit]
        else:
            models = q.limit(limit).all()
        return [self._to_entity(m) for m in models]

    def has_references(self, person_id: UUID) -> bool:
        from app.infrastructure.database.models.worker import WorkerModel

        return self._session.query(WorkerModel).filter_by(person_id=person_id).first() is not None

    def delete(self, person_id: UUID) -> bool:
        model = self._session.query(PersonModel).filter_by(id=person_id).first()
        if model is None:
            return False
        self._session.delete(model)
        # The caller (MergePersonsUseCase) owns the transaction boundary
        # and commits both the worker reassignment + this delete together.
        self._session.flush()
        return True

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _to_entity(self, model: PersonModel) -> Person:
        return Person(
            id=model.id,
            name=model.name,
            normalized_name=model.normalized_name,
            created_by_user_id=model.created_by_user_id,
            created_at=model.created_at,
            phone=model.phone,
            updated_at=model.updated_at,
            user_id=model.user_id,
            phone_normalized=model.phone_normalized,
        )


def _normalized_or_none(phone: Optional[str], region: str) -> Optional[str]:
    """E.164 form of ``phone`` for matching, or None when it does not parse (a free-text phone)."""
    if not phone:
        return None
    try:
        return normalize_phone(phone, default_region=region)
    except InvalidPhoneNumberError:
        return None
