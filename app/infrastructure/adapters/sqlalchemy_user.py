"""SQLAlchemy implementation of UserRepositoryPort."""

from typing import List, Optional, Tuple
from uuid import UUID

from sqlalchemy.orm import Session

from app.domain.entities.user import User
from app.domain.entities.role import Role
from app.domain.entities.permission import Permission
from app.infrastructure.database.models import UserModel


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

    def search_by_email(self, query: str, limit: int = 10) -> List[Tuple[UUID, str]]:
        """Search users by email. Returns list of (id, email) tuples."""
        users = (
            self._session.query(UserModel)
            .filter(UserModel.email.ilike(f"%{query}%"))
            .filter(UserModel.is_active.is_(True))
            .limit(limit)
            .all()
        )
        return [(u.id, u.email) for u in users]

    def search_by_email_or_name(self, query: str, limit: int = 20) -> List[User]:
        """Search users by email or display_name (case-insensitive substring match).

        Returns up to ``limit`` active User entities ordered by email asc.
        Used by the superadmin user-search endpoint (phase 03).
        """
        from sqlalchemy import func, or_

        pattern = f"%{query.lower()}%"
        users = (
            self._session.query(UserModel)
            .filter(
                or_(
                    func.lower(UserModel.email).like(pattern),
                    func.lower(func.coalesce(UserModel.display_name, "")).like(pattern),
                )
            )
            .filter(UserModel.is_active.is_(True))
            .order_by(UserModel.email.asc())
            .limit(limit)
            .all()
        )
        return [self._to_entity(u) for u in users]

    def save(self, user: User) -> User:
        """Save a user (create or update)."""
        existing = self._session.query(UserModel).filter_by(id=user.id).first()
        if existing:
            existing.email = user.email
            existing.password_hash = user.password_hash
            existing.is_active = user.is_active
            existing.display_name = user.display_name
        else:
            user_model = UserModel(
                id=user.id,
                email=user.email,
                password_hash=user.password_hash,
                is_active=user.is_active,
                display_name=user.display_name,
            )
            self._session.add(user_model)
        self._session.flush()
        return user

    def _to_entity(self, model: UserModel) -> User:
        """Convert ORM model to domain entity."""
        roles = []
        for role_model in model.roles:
            permissions = [
                Permission(
                    id=p.id,
                    name=p.name,
                    resource=p.resource,
                    action=p.action,
                )
                for p in role_model.permissions
            ]
            roles.append(
                Role(
                    id=role_model.id,
                    name=role_model.name,
                    description=role_model.description or "",
                    permissions=permissions,
                )
            )

        return User(
            id=model.id,
            email=model.email,
            password_hash=model.password_hash,
            is_active=model.is_active,
            created_at=model.created_at,
            updated_at=model.updated_at,
            roles=roles,
            display_name=model.display_name,
        )
