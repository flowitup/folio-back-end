"""SQLAlchemy implementation of UserRepositoryPort."""

from typing import List, Optional, Tuple
from uuid import UUID

from sqlalchemy.orm import Session

from app.domain.entities.user import User
from app.infrastructure.database.models import UserModel


def _escape_like(value: str) -> str:
    """Escape SQL LIKE wildcards so user-supplied input can't match more than its literal characters.

    Without this, queries like ``%`` or ``_`` match any/single character respectively, turning
    the search into a table-dump tool. We use ``\\`` as the escape character; callers MUST
    pass ``escape='\\\\'`` to ``.like()`` for the escape to take effect.
    """
    # Order matters: escape backslash FIRST, then the LIKE-wildcards.
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class SQLAlchemyUserRepository:
    """SQLAlchemy adapter for user persistence."""

    def __init__(self, session: Session):
        self._session = session

    def find_by_id(self, user_id: UUID) -> Optional[User]:
        """Find a user by ID."""
        user_model = self._session.query(UserModel).filter_by(id=user_id).first()
        if not user_model:
            return None
        return self._to_entity(user_model)

    def find_by_email(self, email: str) -> Optional[User]:
        """Find a user by email."""
        user_model = self._session.query(UserModel).filter_by(email=email.lower().strip()).first()
        if not user_model:
            return None
        return self._to_entity(user_model)

    def find_by_phone(self, phone: str) -> Optional[User]:
        """Find a user by E.164 phone number."""
        user_model = self._session.query(UserModel).filter_by(phone=phone).first()
        if not user_model:
            return None
        return self._to_entity(user_model)

    def search_by_email(self, query: str, limit: int = 10) -> List[Tuple[UUID, str]]:
        """Search users by email substring. Returns list of (id, email) tuples.

        Wildcard chars in ``query`` are escaped so a literal ``%`` matches a literal ``%``.
        """
        pattern = f"%{_escape_like(query)}%"
        users = (
            self._session.query(UserModel)
            .filter(UserModel.email.ilike(pattern, escape="\\"))
            .filter(UserModel.is_active.is_(True))
            .limit(limit)
            .all()
        )
        return [(u.id, u.email) for u in users]

    def search_by_email_or_name(self, query: str, limit: int = 20) -> List[User]:
        """Search users by email or display_name (case-insensitive substring match).

        Returns up to ``limit`` active User entities ordered by email asc.
        Used by the superadmin user-search endpoint (phase 03).

        Wildcard chars in ``query`` are escaped (see ``_escape_like``) so a literal ``%`` or
        ``_`` cannot match more than itself; without this, ``search='%'`` would dump the table.
        """
        from sqlalchemy import func, or_

        pattern = f"%{_escape_like(query.lower())}%"
        users = (
            self._session.query(UserModel)
            .filter(
                or_(
                    func.lower(UserModel.email).like(pattern, escape="\\"),
                    func.lower(func.coalesce(UserModel.display_name, "")).like(pattern, escape="\\"),
                )
            )
            .filter(UserModel.is_active.is_(True))
            .order_by(UserModel.email.asc())
            .limit(limit)
            .all()
        )
        return [self._to_entity(u) for u in users]

    def is_sign_in_allowed(self, user_id: UUID) -> bool:
        """True when the user exists, is active, and has not erased their account.

        Selects the two columns rather than the entity on purpose: this runs on
        every authenticated request, so it must not return an instance the
        session cached earlier in the request, and must not put one there for
        later readers to pick up.
        """
        # no_autoflush matters: this runs during token verification, before the
        # request handler has done anything. Letting it flush would push the
        # caller's half-built pending objects to the database at an arbitrary
        # point — surfacing unrelated integrity errors inside auth — and makes
        # every authenticated request pay for a flush it did not ask for.
        with self._session.no_autoflush:
            row = (
                self._session.query(UserModel.is_active, UserModel.deleted_at)
                .filter(UserModel.id == user_id)
                .first()
            )
        return bool(row is not None and row.is_active and row.deleted_at is None)

    def save(self, user: User) -> User:
        """Save a user (create or update)."""
        existing = self._session.query(UserModel).filter_by(id=user.id).first()
        if existing:
            existing.email = user.email
            existing.is_active = user.is_active
            existing.display_name = user.display_name
            existing.phone = user.phone
            # is_platform_ops is deliberately NOT written here: it is owned by ops
            # (set out-of-band in SQL), and round-tripping it through every caller's
            # entity silently cleared the flag for accounts that had it. Account
            # erasure clears it with a targeted UPDATE instead — see
            # SQLAlchemyPersonalDataEraser.
            # Never clear an erasure through the generic save(): a caller that
            # rebuilds a User from partial data and saves it over an existing id
            # would otherwise resurrect a deleted account. Same footgun shape as
            # the is_platform_ops incident above; guarded rather than trusted to
            # call-site discipline.
            if existing.deleted_at is None or user.deleted_at is not None:
                existing.deleted_at = user.deleted_at
        else:
            user_model = UserModel(
                id=user.id,
                email=user.email,
                is_active=user.is_active,
                display_name=user.display_name,
                phone=user.phone,
                deleted_at=user.deleted_at,
            )
            self._session.add(user_model)
        self._session.flush()
        return user

    def _to_entity(self, model: UserModel) -> User:
        """Convert ORM model to domain entity."""
        return User(
            id=model.id,
            email=model.email,
            is_active=model.is_active,
            created_at=model.created_at,
            updated_at=model.updated_at,
            display_name=model.display_name,
            phone=model.phone,
            is_platform_ops=bool(model.is_platform_ops),
            deleted_at=model.deleted_at,
        )
