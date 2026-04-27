"""SQLAlchemy implementation of InvitationRepositoryPort."""

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.application.invitations.ports import InvitationRepositoryPort
from app.domain.entities.invitation import Invitation, InvitationStatus
from app.infrastructure.database.models.invitation import InvitationModel


class SqlAlchemyInvitationRepository:
    """SQLAlchemy adapter for Invitation persistence."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, inv: Invitation) -> Invitation:
        """Persist an invitation (insert or update). Returns the saved instance."""
        existing = self._session.query(InvitationModel).filter_by(id=inv.id).first()
        if existing:
            existing.email = inv.email
            existing.project_id = inv.project_id
            existing.role_id = inv.role_id
            existing.token_hash = inv.token_hash
            existing.status = inv.status.value
            existing.expires_at = inv.expires_at
            existing.invited_by = inv.invited_by
            existing.accepted_at = inv.accepted_at
            existing.updated_at = inv.updated_at
        else:
            model = InvitationModel.from_entity(inv)
            self._session.add(model)
        self._session.flush()
        return inv

    def find_by_token_hash(self, token_hash: str) -> Optional[Invitation]:
        """Look up an invitation by its sha256 token hash."""
        model = self._session.query(InvitationModel).filter_by(token_hash=token_hash).first()
        return model.to_entity() if model else None

    def find_by_token_hash_for_update(self, token_hash: str) -> Optional[Invitation]:
        """Look up an invitation with a row-level lock (Postgres SELECT ... FOR UPDATE).

        Falls back to a plain SELECT on dialects that don't support FOR UPDATE
        (e.g. SQLite under tests). Must be called inside an open transaction.
        """
        query = self._session.query(InvitationModel).filter_by(token_hash=token_hash)
        # SQLite raises CompileError on with_for_update; degrade gracefully.
        dialect = self._session.bind.dialect.name if self._session.bind else ""
        if dialect != "sqlite":
            query = query.with_for_update()
        model = query.first()
        return model.to_entity() if model else None

    def find_by_id(self, invitation_id: UUID) -> Optional[Invitation]:
        """Look up an invitation by its UUID."""
        model = self._session.query(InvitationModel).filter_by(id=invitation_id).first()
        return model.to_entity() if model else None

    def find_pending_by_email_and_project(
        self, email: str, project_id: UUID
    ) -> Optional[Invitation]:
        """Return the first PENDING invitation for email + project, or None."""
        model = (
            self._session.query(InvitationModel)
            .filter_by(
                email=email.lower(),
                project_id=project_id,
                status=InvitationStatus.PENDING.value,
            )
            .first()
        )
        return model.to_entity() if model else None

    def list_by_project(
        self,
        project_id: UUID,
        status: Optional[InvitationStatus] = None,
    ) -> list[Invitation]:
        """Return all invitations for a project, optionally filtered by status."""
        query = self._session.query(InvitationModel).filter_by(project_id=project_id)
        if status is not None:
            query = query.filter_by(status=status.value)
        models = query.order_by(InvitationModel.created_at.desc()).all()
        return [m.to_entity() for m in models]

    def count_created_today_by_project(self, project_id: UUID) -> int:
        """Return count of invitations created today (UTC) for the project."""
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return (
            self._session.query(InvitationModel)
            .filter(
                InvitationModel.project_id == project_id,
                InvitationModel.created_at >= today_start,
            )
            .count()
        )
