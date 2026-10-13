"""Route-side helper: tell the company's other admins about the calling admin's action."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from flask_jwt_extended import get_jwt_identity

from wiring import get_container


def notify_admins(
    event: str,
    subject: str,
    *,
    project_id: Optional[str] = None,
    company_id: Optional[str] = None,
    about_user_id: Optional[UUID] = None,
) -> None:
    """Fire-and-forget: the notifier swallows its own failures, so the request never pays for it."""
    notifier = get_container().company_activity_notifier
    if notifier is None:
        return
    notifier.admin_acted(
        event,
        actor_id=UUID(str(get_jwt_identity())),
        subject=subject,
        project_id=UUID(str(project_id)) if project_id else None,
        company_id=UUID(str(company_id)) if company_id else None,
        about_user_id=about_user_id,
    )
