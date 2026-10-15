"""SQLAlchemy implementation of project repository."""

from decimal import Decimal
from typing import List, Optional, Tuple
from uuid import UUID

from sqlalchemy import delete, or_
from sqlalchemy.orm import Session, joinedload

from app.application.projects.ports import IProjectRepository
from app.domain.entities.project import Project
from app.infrastructure.database.models import ChatMessageOrm, ProjectModel, user_projects


class SQLAlchemyProjectRepository(IProjectRepository):
    """SQLAlchemy adapter for project persistence."""

    def __init__(self, session: Session):
        self._session = session

    def create(self, project: Project, company_id: Optional[UUID] = None) -> Project:
        model = ProjectModel(
            id=project.id,
            name=project.name,
            address=project.address,
            invoice_prefix=project.invoice_prefix,
            owner_id=project.owner_id,
            created_at=project.created_at,
            budget=project.budget,
            budget_source=project.budget_source,
            company_id=company_id,
        )
        self._session.add(model)
        self._session.commit()
        return self._to_entity(model)

    def find_by_id(self, project_id: UUID) -> Optional[Project]:
        model = (
            self._session.query(ProjectModel).options(joinedload(ProjectModel.users)).filter_by(id=project_id).first()
        )
        return self._to_entity(model) if model else None

    def list_all(self) -> List[Project]:
        models = self._session.query(ProjectModel).options(joinedload(ProjectModel.users)).all()
        return [self._to_entity(m) for m in models]

    def list_for_user_and_companies(self, user_id: UUID, company_ids: List[UUID]) -> List[Project]:
        member_project_ids = self._session.query(user_projects.c.project_id).filter(user_projects.c.user_id == user_id)
        conditions = [
            ProjectModel.owner_id == user_id,
            ProjectModel.id.in_(member_project_ids),
        ]
        if company_ids:
            conditions.append(ProjectModel.company_id.in_(company_ids))
        models = (
            self._session.query(ProjectModel).options(joinedload(ProjectModel.users)).filter(or_(*conditions)).all()
        )
        return [self._to_entity(m) for m in models]

    def update(self, project: Project) -> Project:
        model = self._session.query(ProjectModel).filter_by(id=project.id).first()
        if model:
            model.name = project.name
            model.address = project.address
            model.invoice_prefix = project.invoice_prefix
            model.budget = project.budget
            model.budget_source = project.budget_source
            self._session.commit()
            return self._to_entity(model)
        return project

    def delete(self, project_id: UUID) -> bool:
        # Load the entity so SQLAlchemy unit-of-work fires ORM-level cascades
        # alongside the FK ON DELETE CASCADE the schema already configures
        # (workers, labor entries, tasks, invoices, notes, invitations,
        # memberships). bulk-query .delete() bypasses ORM events.
        project = self._session.query(ProjectModel).filter_by(id=project_id).first()
        if project is None:
            return False
        # Chat channels are virtual (no FK from chat_messages to projects), so the project's messages
        # go here, in the same transaction; their reactions and reports follow by FK cascade.
        self._session.execute(
            delete(ChatMessageOrm).where(
                ChatMessageOrm.channel_kind == "project", ChatMessageOrm.channel_id == project_id
            ),
            execution_options={"synchronize_session": False},
        )
        self._session.delete(project)
        self._session.commit()
        return True

    def get_project_users(self, project_id: UUID) -> List[Tuple[UUID, str]]:
        project = (
            self._session.query(ProjectModel).options(joinedload(ProjectModel.users)).filter_by(id=project_id).first()
        )
        if not project:
            return []
        return [(u.id, u.email) for u in project.users]

    def _to_entity(self, model: ProjectModel) -> Project:
        return Project(
            id=model.id,
            name=model.name,
            address=model.address,
            invoice_prefix=model.invoice_prefix,
            owner_id=model.owner_id,
            created_at=model.created_at,
            updated_at=model.updated_at,
            user_ids=[u.id for u in model.users] if model.users else [],
            budget=Decimal(str(model.budget)) if model.budget is not None else None,
            budget_source=model.budget_source,
            company_id=model.company_id,
        )
