"""Deletes the rows that belong to a person, for self-service account deletion.

The split this file encodes is the whole point of the feature: a user's
*credentials, devices and access grants* are personal data and go; the invoices,
labor entries, projects and billing documents they created belong to the company
and stay. Every table listed here has ``ondelete="CASCADE"`` to ``users.id``
(i.e. the schema already treats it as user-owned) — but the deletes are written
out explicitly rather than relying on cascade, because two CASCADE tables,
``billing_documents`` and ``chat_messages``, are emphatically NOT personal data
and must survive. Anything absent from this list is kept on purpose.
"""

from uuid import UUID

from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from app.infrastructure.database.models.api_key import ApiKeyOrm
from app.infrastructure.database.models.associations import user_projects
from app.infrastructure.database.models.chat_message import ChatChannelReadOrm
from app.infrastructure.database.models.chat_push_marker import ChatPushMarkerModel
from app.infrastructure.database.models.company_member_grant import CompanyMemberGrantModel
from app.infrastructure.database.models.login_otp import LoginOtpOrm
from app.infrastructure.database.models.notification_preference import (
    NotificationPreferenceModel,
)
from app.infrastructure.database.models.push_device import PushDeviceOrm
from app.infrastructure.database.models.user import UserModel
from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel


class SQLAlchemyPersonalDataEraser:
    """SQLAlchemy adapter for PersonalDataEraserPort."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def erase_for_user(self, user_id: UUID) -> None:
        """Delete every personal row owned by ``user_id``.

        Ordered credentials-first so that a failure part-way through has already
        removed the ways back into the account. Idempotent: re-running deletes
        nothing and raises nothing.
        """
        statements = (
            # Credentials and sign-in material.
            delete(ApiKeyOrm).where(ApiKeyOrm.user_id == user_id),
            delete(LoginOtpOrm).where(LoginOtpOrm.user_id == user_id),
            # Devices — leaving these would keep pushing notifications to a phone
            # whose owner just deleted their account.
            delete(PushDeviceOrm).where(PushDeviceOrm.user_id == user_id),
            delete(NotificationPreferenceModel).where(NotificationPreferenceModel.user_id == user_id),
            delete(ChatPushMarkerModel).where(ChatPushMarkerModel.user_id == user_id),
            delete(ChatChannelReadOrm).where(ChatChannelReadOrm.user_id == user_id),
            # Access grants — company and project membership.
            delete(CompanyMemberGrantModel).where(CompanyMemberGrantModel.user_id == user_id),
            delete(UserCompanyAccessModel).where(UserCompanyAccessModel.user_id == user_id),
            user_projects.delete().where(user_projects.c.user_id == user_id),
            # Cleared here rather than through the user entity: the repository does
            # not persist is_platform_ops (ops set it out of band, and writing it
            # back from every caller's entity would wipe it for real ops accounts).
            # An erased support account must not keep its bypass.
            update(UserModel).where(UserModel.id == user_id).values(is_platform_ops=False),
        )
        for statement in statements:
            self._session.execute(statement, execution_options={"synchronize_session": False})
        self._session.flush()
