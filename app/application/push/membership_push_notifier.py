"""Push notifications about membership and permission changes.

The recipient is always the person whose access changed — they are the one who cannot
otherwise find out. Bulk operations (admin bulk-add, company member import) deliberately
send nothing: they are administrative setup, and one push per row would train users to
mute Folio entirely.
"""

from __future__ import annotations

import logging
from typing import Dict, Optional, Protocol
from uuid import UUID

from app.application.push.dispatcher import PushDispatcher
from app.domain.notifications.categories import NotificationCategory

logger = logging.getLogger(__name__)

_TEXT: Dict[str, Dict[str, tuple]] = {
    "project_member_added": {
        "vi": ("Bạn được thêm vào công trình", "{name}"),
        "fr": ("Vous avez rejoint un chantier", "{name}"),
        "en": ("You were added to a project", "{name}"),
    },
    "project_member_removed": {
        "vi": ("Bạn đã rời khỏi công trình", "{name}"),
        "fr": ("Vous avez été retiré d'un chantier", "{name}"),
        "en": ("You were removed from a project", "{name}"),
    },
    "company_member_role_changed": {
        "vi": ("Vai trò công ty đã thay đổi", "{name} · {role}"),
        "fr": ("Votre rôle a changé", "{name} · {role}"),
        "en": ("Your company role changed", "{name} · {role}"),
    },
    "company_member_grants_changed": {
        "vi": ("Quyền của bạn đã thay đổi", "{name}"),
        "fr": ("Vos permissions ont changé", "{name}"),
        "en": ("Your permissions changed", "{name}"),
    },
    "invitation_accepted": {
        "vi": ("Lời mời đã được chấp nhận", "{name}"),
        "fr": ("Invitation acceptée", "{name}"),
        "en": ("Invitation accepted", "{name}"),
    },
    "company_member_removed": {
        "vi": ("Bạn đã rời khỏi công ty", "{name}"),
        "fr": ("Vous avez été retiré d'une entreprise", "{name}"),
        "en": ("You were removed from a company", "{name}"),
    },
}

_PROJECT_EVENTS = (
    "project_member_added",
    "project_member_removed",
    "invitation_accepted",
)


class NamedEntityReader(Protocol):
    def find_by_id(self, entity_id: UUID): ...


class MembershipPushNotifier:
    def __init__(
        self,
        dispatcher: PushDispatcher,
        project_repo: NamedEntityReader,
        company_repo: NamedEntityReader,
    ) -> None:
        self._dispatcher = dispatcher
        self._projects = project_repo
        self._companies = company_repo

    def notify(
        self,
        event: str,
        *,
        user_id: UUID,
        actor_id: UUID,
        entity_id: UUID,
        role: Optional[str] = None,
    ) -> None:
        """Tell ``user_id`` their access to ``entity_id`` changed. Self-changes stay silent."""
        try:
            if event not in _TEXT or user_id == actor_id:
                return
            is_project = event in _PROJECT_EVENTS
            repo = self._projects if is_project else self._companies
            entity = repo.find_by_id(entity_id)
            name = getattr(entity, "name", None) or getattr(entity, "legal_name", "") or ""
            title, body = _TEXT[event][self._dispatcher.locale]
            key = "project_id" if is_project else "company_id"
            self._dispatcher.dispatch(
                category=NotificationCategory.MEMBERSHIP.value,
                recipients=[user_id],
                title=title,
                body=body.format(name=name, role=role or ""),
                data={"kind": event, key: str(entity_id)},
                exclude=actor_id,
            )
        except Exception:  # a push must never break the membership change
            logger.exception("membership push failed event=%s", event)
