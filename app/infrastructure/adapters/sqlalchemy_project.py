"""SQLAlchemy implementation of project repository."""

from typing import List, Optional, Tuple
from uuid import UUID

from sqlalchemy.orm import Session, joinedload

from app.application.projects.ports import IProjectRepository
from app.domain.entities.project import Project
from app.infrastructure.database.models import ProjectModel, UserModel, user_projects


class SQLAlchemyProjectRepository(IProjectRepository):
    """SQLAlchemy adapter for project persistence."""

    def __init__(self, session: Session):
        self._session = session

    def create(self, project: Project) -> Project:
        model = ProjectModel(
            id=project.id,
            name=project.name,
            address=project.address,
            owner_id=project.owner_id,
            created_at=project.created_at,
        )
        self._session.add(model)
        self._session.flush()
        return self._to_entity(model)

    def find_by_id(self, project_id: UUID) -> Optional[Project]:
        model = (
            self._session.query(ProjectModel).options(joinedload(ProjectModel.users)).filter_by(id=project_id).first()
        )
        return self._to_entity(model) if model else None

    def list_by_user(self, user_id: UUID) -> List[Project]:
        models = (
            self._session.query(ProjectModel)
            .join(user_projects)
            .filter(user_projects.c.user_id == user_id)
            .options(joinedload(ProjectModel.users))
            .all()
        )
        return [self._to_entity(m) for m in models]

    def list_all(self) -> List[Project]:
        models = self._session.query(ProjectModel).options(joinedload(ProjectModel.users)).all()
        return [self._to_entity(m) for m in models]

    def update(self, project: Project) -> Project:
        model = self._session.query(ProjectModel).filter_by(id=project.id).first()
        if model:
            model.name = project.name
            model.address = project.address
            self._session.flush()
            return self._to_entity(model)
        return project

    def delete(self, project_id: UUID) -> bool:
        result = self._session.query(ProjectModel).filter_by(id=project_id).delete()
        return result > 0

    def add_user(self, project_id: UUID, user_id: UUID) -> None:
        project = (
            self._session.query(ProjectModel).options(joinedload(ProjectModel.users)).filter_by(id=project_id).first()
        )
        user = self._session.query(UserModel).filter_by(id=user_id).first()
        if project and user and user not in project.users:
            project.users.append(user)
            self._session.commit()

    def remove_user(self, project_id: UUID, user_id: UUID) -> None:
        project = (
            self._session.query(ProjectModel).options(joinedload(ProjectModel.users)).filter_by(id=project_id).first()
        )
        user = self._session.query(UserModel).filter_by(id=user_id).first()
        if project and user and user in project.users:
            project.users.remove(user)
            self._session.commit()

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
            owner_id=model.owner_id,
            created_at=model.created_at,
            updated_at=model.updated_at,
            user_ids=[u.id for u in model.users] if model.users else [],
        )
