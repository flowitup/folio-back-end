"""SQLAlchemy implementation of the Person repository."""

from typing import List, Optional
from uuid import UUID

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.application.persons.ports import IPersonRepository
from app.domain.entities.person import Person
from app.infrastructure.database.models import PersonModel


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

    def search(self, query: str, limit: int = 20) -> List[Person]:
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

        models = q.order_by(PersonModel.normalized_name).limit(limit).all()
        return [self._to_entity(m) for m in models]

    def find_by_phone(self, phone: str) -> Optional[Person]:
        if not phone:
            return None
        model = (
            self._session.query(PersonModel)
            .filter(PersonModel.phone == phone)
            .first()
        )
        return self._to_entity(model) if model else None

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
        )
