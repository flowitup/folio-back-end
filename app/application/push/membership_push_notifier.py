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
from app.application.push.project_label import project_label
from app.application.push.push_format import role_label
from app.domain.notifications.categories import NotificationCategory

logger = logging.getLogger(__name__)

_TEXT: Dict[str, Dict[str, tuple]] = {
    "project_member_added": {
        "vi": ("Bạn được thêm vào công trình", "{name}"),
        "fr": ("Vous avez été ajouté à un chantier", "{name}"),
        "en": ("You were added to a project", "{name}"),
    },
    "project_member_removed": {
        "vi": ("Bạn đã bị gỡ khỏi công trình", "{name}"),
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
    # Says who accepted: an admin who invited several people must tell them apart.
    "invitation_accepted": {
        "vi": ("Lời mời đã được chấp nhận", "{actor} đã chấp nhận lời mời · {name}"),
        "fr": ("Invitation acceptée", "{actor} a accepté l'invitation · {name}"),
        "en": ("Invitation accepted", "{actor} accepted the invitation · {name}"),
    },
    # An admin attached an existing account to their company (add by phone, import).
    "company_member_added": {
        "vi": ("Bạn đã được thêm vào công ty", "{name}"),
        "fr": ("Vous avez été ajouté à une entreprise", "{name}"),
        "en": ("You were added to a company", "{name}"),
    },
    "company_member_removed": {
        "vi": ("Bạn đã bị gỡ khỏi công ty", "{name}"),
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
        names=None,
    ) -> None:
        self._dispatcher = dispatcher
        self._projects = project_repo
        self._companies = company_repo
        # Optional ``display_names(user_ids) -> {id: name}``: names the actor where the text says who.
        self._names = names

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
            if is_project:
                name = project_label(entity)
            else:
                name = getattr(entity, "name", None) or getattr(entity, "legal_name", "") or ""
            key = "project_id" if is_project else "company_id"
            names_actor = "{actor}" in _TEXT[event]["en"][1]
            actor = ""
            if names_actor and self._names is not None:
                actor = self._names.display_names([actor_id]).get(actor_id, "") or ""

            def render(locale: str) -> tuple[str, str]:
                title, body = _TEXT[event][locale]
                if names_actor and not actor:
                    # No name to show: keep the plain "{name}" body rather than a dangling verb.
                    body = "{name}"
                return title, body.format(name=name, role=role_label(role, locale), actor=actor)

            self._dispatcher.dispatch(
                category=NotificationCategory.MEMBERSHIP.value,
                recipients=[user_id],
                render=render,
                data={"kind": event, key: str(entity_id), "actor_id": str(actor_id)},
                exclude=actor_id,
            )
        except Exception:  # a push must never break the membership change
            logger.exception("membership push failed event=%s", event)
