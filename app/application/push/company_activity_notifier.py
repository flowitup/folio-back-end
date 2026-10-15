"""Tell a company's other admins what one admin just did.

Several admins share a company, and none of them can see what the others change unless
something says so. Each event here is administrative and lands in every other admin's
bell (and push, unless they muted its family); the actor is never told about their own
action. The categories are the existing families, so no new switch is needed.
"""

from __future__ import annotations

import logging
from typing import Dict, Optional, Protocol
from uuid import UUID

from app.application.push.dispatcher import PushDispatcher
from app.application.push.project_label import project_label
from app.domain.notifications.categories import NotificationCategory

logger = logging.getLogger(__name__)


class ProjectReader(Protocol):
    def find_by_id(self, project_id: UUID): ...


class CompanyAccessReader(Protocol):
    def list_for_company(self, company_id: UUID) -> list: ...


class NameReader(Protocol):
    def display_names(self, user_ids: list) -> Dict[UUID, str]: ...


_ATTENDANCE = NotificationCategory.ATTENDANCE.value
_BILLING = NotificationCategory.BILLING.value
_MEMBERSHIP = NotificationCategory.MEMBERSHIP.value

# event -> (category, {locale: title}). The body is always "{actor} · {subject}" plus the site.
_EVENTS: Dict[str, tuple] = {
    "invoice_created": (_BILLING, {"vi": "Hoá đơn mới", "fr": "Nouvelle facture", "en": "Invoice added"}),
    "invoice_updated": (_BILLING, {"vi": "Hoá đơn đã sửa", "fr": "Facture modifiée", "en": "Invoice edited"}),
    "invoice_deleted": (_BILLING, {"vi": "Hoá đơn đã xoá", "fr": "Facture supprimée", "en": "Invoice deleted"}),
    "worker_added": (_ATTENDANCE, {"vi": "Thêm công nhân", "fr": "Ouvrier ajouté", "en": "Worker added"}),
    "worker_removed": (_ATTENDANCE, {"vi": "Xoá công nhân", "fr": "Ouvrier retiré", "en": "Worker removed"}),
    "attendance_edited": (
        _ATTENDANCE,
        {"vi": "Sửa chấm công", "fr": "Pointage modifié", "en": "Attendance edited"},
    ),
    "attendance_deleted": (
        _ATTENDANCE,
        {"vi": "Xoá chấm công", "fr": "Pointage supprimé", "en": "Attendance deleted"},
    ),
    "project_created": (_MEMBERSHIP, {"vi": "Công trình mới", "fr": "Nouveau chantier", "en": "Project created"}),
    "project_deleted": (_MEMBERSHIP, {"vi": "Công trình đã xoá", "fr": "Chantier supprimé", "en": "Project deleted"}),
    "member_added": (_MEMBERSHIP, {"vi": "Thêm thành viên", "fr": "Membre ajouté", "en": "Member added"}),
    "member_role_changed": (
        _MEMBERSHIP,
        {"vi": "Đổi vai trò thành viên", "fr": "Rôle modifié", "en": "Member role changed"},
    ),
    "member_removed": (_MEMBERSHIP, {"vi": "Xoá thành viên", "fr": "Membre retiré", "en": "Member removed"}),
}


class CompanyActivityNotifier:
    def __init__(
        self,
        dispatcher: PushDispatcher,
        project_repo: ProjectReader,
        access_repo: CompanyAccessReader,
        names: NameReader,
    ) -> None:
        self._dispatcher = dispatcher
        self._projects = project_repo
        self._access = access_repo
        self._names = names

    def admin_acted(
        self,
        event: str,
        *,
        actor_id: UUID,
        subject: str,
        project_id: Optional[UUID] = None,
        company_id: Optional[UUID] = None,
        about_user_id: Optional[UUID] = None,
        via_api_key: Optional[str] = None,
    ) -> None:
        """Notify every admin of the company except ``actor_id``. Never raises.

        ``about_user_id`` puts that person's name in front of ``subject`` (member events);
        ``via_api_key`` names the API key the action came through, so a key's use shows."""
        try:
            if event not in _EVENTS:
                return
            project = self._projects.find_by_id(project_id) if project_id is not None else None
            company_id = company_id or getattr(project, "company_id", None)
            if company_id is None:
                return
            # The person the action is about already hears it through their own notification.
            admins = [
                a.user_id
                for a in self._access.list_for_company(company_id)
                if getattr(a, "role", "") == "admin" and a.user_id != about_user_id
            ]
            if not [u for u in admins if u != actor_id]:
                return
            category, titles = _EVENTS[event]
            looked_up = [actor_id] + ([about_user_id] if about_user_id is not None else [])
            names = self._names.display_names(looked_up)
            actor = names.get(actor_id, "?")
            if via_api_key:
                actor = f"{actor} (API key {via_api_key})"
            if about_user_id is not None:
                subject = " ".join(part for part in (names.get(about_user_id, ""), subject) if part)
            site = project_label(project)
            line = " · ".join(part for part in (actor, subject, site) if part)
            data = {"kind": event, "company_id": str(company_id)}
            if project_id is not None:
                data["project_id"] = str(project_id)
            self._dispatcher.dispatch(
                category=category,
                recipients=admins,
                render=lambda locale: (titles[locale], line),
                data=data,
                exclude=actor_id,
            )
        except Exception:  # telling the others must never break the action itself
            logger.exception("company activity notification failed event=%s", event)
