"""
SQLAlchemy ORM models for the application.

These models map to PostgreSQL tables and handle persistence.
Domain entities are pure Python; these models bridge to the database.
"""

from app.infrastructure.database.models.base import Base
from app.infrastructure.database.models.associations import (
    user_roles,
    role_permissions,
    user_projects,
)
from app.infrastructure.database.models.user import UserModel
from app.infrastructure.database.models.role import RoleModel
from app.infrastructure.database.models.permission import PermissionModel
from app.infrastructure.database.models.project import ProjectModel
from app.infrastructure.database.models.worker import WorkerModel
from app.infrastructure.database.models.labor_entry import LaborEntryModel
from app.infrastructure.database.models.invoice import InvoiceModel
from app.infrastructure.database.models.invoice_attachment import InvoiceAttachmentModel
from app.infrastructure.database.models.task import TaskModel
from app.infrastructure.database.models.invitation import InvitationModel

__all__ = [
    "Base",
    "user_roles",
    "role_permissions",
    "user_projects",
    "UserModel",
    "RoleModel",
    "PermissionModel",
    "ProjectModel",
    "WorkerModel",
    "LaborEntryModel",
    "InvoiceModel",
    "InvoiceAttachmentModel",
    "TaskModel",
    "InvitationModel",
]
