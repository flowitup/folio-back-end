"""SQLAlchemy implementation of the Person repository."""

from typing import List, Optional
from uuid import UUID

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.application.persons.ports import IPersonRepository
from app.domain.entities.person import Person
from app.infrastructure.database.models import PersonModel
from app.infrastructure.database.models.company_person import CompanyPersonModel


class SqlAlchemyPersonRepository(IPersonRepository):
    """Persistence adapter for Person."""

    def __init__(self, session: Session):
        self._session = session

    # ------------------------------------------------------------------
    # IPersonRepository
    # ------------------------------------------------------------------
    def create(self, person: Person) -> Person:
        model = PersonModel(
            id=person.id,
            name=person.name,
            phone=person.phone,
            normalized_name=person.normalized_name,
            created_by_user_id=person.created_by_user_id,
            created_at=person.created_at,
        )
        self._session.add(model)
        self._session.commit()
        # Refresh to pick up server-side defaults (updated_at)
        self._session.refresh(model)
        return self._to_entity(model)

    def find_by_id(self, person_id: UUID) -> Optional[Person]:
        model = self._session.query(PersonModel).filter_by(id=person_id).first()
        return self._to_entity(model) if model else None

    def find_by_user_id(self, user_id: UUID) -> Optional[Person]:
        model = self._session.query(PersonModel).filter_by(user_id=user_id).first()
        return self._to_entity(model) if model else None

    def set_user_id(self, person_id: UUID, user_id: UUID) -> Optional[Person]:
        model = self._session.query(PersonModel).filter_by(id=person_id).first()
        if model is None:
            return None
        model.user_id = user_id
        self._session.commit()
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

        if normalized:
            # Match either substring on normalized_name or exact phone (when
            # the query happens to be a phone number — cheap union, no
            # ambiguity since phone formats rarely collide with name fragments).
            q = q.filter(
                or_(
                    PersonModel.normalized_name.contains(normalized),
                    PersonModel.phone == query.strip(),
                )
            )

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

        models = q.order_by(PersonModel.normalized_name).limit(limit).all()
        return [self._to_entity(m) for m in models]

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
