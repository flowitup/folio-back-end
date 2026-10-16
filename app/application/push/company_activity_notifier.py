"""Tell a company's other admins what one admin just did.

Several admins share a company, and none of them can see what the others change unless
something says so. Each event here is administrative and lands in every other admin's
bell (and push, unless they muted its family); the actor is never told about their own
action. The categories are the existing families, so no new switch is needed.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Callable, Dict, Optional, Protocol, Union
from uuid import UUID

from app.application.push.dispatcher import PushDispatcher
from app.application.push.project_label import project_label
from app.application.push.push_format import format_amount, role_label
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
# The apps call a project's money entries "Dépenses" (expenses); "factures" are the customer
# invoices under Facturation, so the expense events say "dépense".
_EVENTS: Dict[str, tuple] = {
    "invoice_created": (_BILLING, {"vi": "Chi phí mới", "fr": "Nouvelle dépense", "en": "Expense added"}),
    "invoice_updated": (_BILLING, {"vi": "Chi phí đã sửa", "fr": "Dépense modifiée", "en": "Expense edited"}),
    "invoice_deleted": (_BILLING, {"vi": "Chi phí đã xoá", "fr": "Dépense supprimée", "en": "Expense deleted"}),
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
    # The actor is the person who joined with the company code. Push only: the bell lists
    # newcomers in its own "New members" section.
    "member_joined": (
        _MEMBERSHIP,
        {"vi": "Thành viên mới", "fr": "Nouveau membre", "en": "New member"},
    ),
    # The actor is the person who left: there is no other admin action behind it.
    "member_left": (
        _MEMBERSHIP,
        {"vi": "Thành viên đã rời công ty", "fr": "Un membre a quitté l'entreprise", "en": "A member left the company"},
    ),
}

# Expense titles by kind of entry, so a fund release or a cash advance is not called an expense.
_INVOICE_TITLES: Dict[str, Dict[str, Dict[str, str]]] = {
    "released_funds": {
        "invoice_created": {"vi": "Vốn đã giải ngân", "fr": "Fonds débloqués", "en": "Funds released"},
        "invoice_updated": {
            "vi": "Vốn giải ngân đã sửa",
            "fr": "Déblocage de fonds modifié",
            "en": "Fund release edited",
        },
        "invoice_deleted": {
            "vi": "Vốn giải ngân đã xoá",
            "fr": "Déblocage de fonds supprimé",
            "en": "Fund release deleted",
        },
    },
    "cash_advance": {
        "invoice_created": {"vi": "Tạm ứng tiền mặt", "fr": "Avance en espèces", "en": "Cash advance"},
        "invoice_updated": {
            "vi": "Tạm ứng tiền mặt đã sửa",
            "fr": "Avance en espèces modifiée",
            "en": "Cash advance edited",
        },
        "invoice_deleted": {
            "vi": "Tạm ứng tiền mặt đã xoá",
            "fr": "Avance en espèces supprimée",
            "en": "Cash advance deleted",
        },
    },
    "return": {
        "invoice_created": {"vi": "Trả hàng mới", "fr": "Nouvel avoir", "en": "Return added"},
        "invoice_updated": {"vi": "Trả hàng đã sửa", "fr": "Avoir modifié", "en": "Return edited"},
        "invoice_deleted": {"vi": "Trả hàng đã xoá", "fr": "Avoir supprimé", "en": "Return deleted"},
    },
}

# "(API key <name>)" after the actor, in the reader's language.
_VIA_API_KEY = {"vi": "(khoá API {name})", "fr": "(clé API {name})", "en": "(API key {name})"}

# A worker stopped from the worker list is stopped on every site of the company, not one.
_ALL_SITES = {
    "vi": "tất cả công trình của công ty",
    "fr": "tous les chantiers de l'entreprise",
    "en": "all of the company's sites",
}
_WORKER_STOPPED = {"vi": "Công nhân đã ngừng", "fr": "Ouvrier arrêté", "en": "Worker stopped"}

Subject = Union[str, Callable[[str], str]]


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
        subject: Subject,
        project_id: Optional[UUID] = None,
        company_id: Optional[UUID] = None,
        about_user_id: Optional[UUID] = None,
        via_api_key: Optional[str] = None,
        amount: Optional[Decimal] = None,
        variant: Optional[str] = None,
        role: Optional[str] = None,
        all_sites: bool = False,
        extra_data: Optional[Dict[str, str]] = None,
        record: bool = True,
    ) -> None:
        """Notify every admin of the company except ``actor_id``. Never raises.

        ``about_user_id`` puts that person's name in front of ``subject`` (member events);
        ``via_api_key`` names the API key the action came through, so a key's use shows.
        ``subject`` may be a ``locale -> text`` callable; ``amount`` is written in each
        reader's own format, ``role`` as the localized role name, and ``variant`` picks a
        more precise expense title (released_funds / cash_advance / return). ``all_sites``
        says a worker was stopped company-wide. ``extra_data`` adds ids the apps route on.
        ``record=False`` pushes without a bell activity entry."""
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
            titles = _INVOICE_TITLES.get(variant or "", {}).get(event, titles)
            if event == "worker_removed" and all_sites:
                titles = _WORKER_STOPPED
            looked_up = [actor_id] + ([about_user_id] if about_user_id is not None else [])
            names = self._names.display_names(looked_up)
            actor_name = names.get(actor_id, "?")
            about_name = names.get(about_user_id, "") if about_user_id is not None else ""
            site = project_label(project)

            def render(locale: str) -> tuple[str, str]:
                actor = actor_name
                if via_api_key:
                    actor = f"{actor} {_VIA_API_KEY[locale].format(name=via_api_key)}"
                text = subject(locale) if callable(subject) else subject
                if role:
                    text = " ".join(p for p in (text, f"→ {role_label(role, locale)}") if p)
                if about_name:
                    text = " ".join(p for p in (about_name, text) if p)
                where = _ALL_SITES[locale] if all_sites else site
                parts = (actor, text, format_amount(amount, locale), where)
                return titles[locale], " · ".join(p for p in parts if p)

            data = {"kind": event, "company_id": str(company_id), "actor_id": str(actor_id)}
            if project_id is not None:
                data["project_id"] = str(project_id)
            if about_user_id is not None:
                data["user_id"] = str(about_user_id)
            for key, value in (extra_data or {}).items():
                if value is not None:
                    data[key] = str(value)
            self._dispatcher.dispatch(
                category=category,
                recipients=admins,
                render=render,
                data=data,
                exclude=actor_id,
                record=record,
            )
        except Exception:  # telling the others must never break the action itself
            logger.exception("company activity notification failed event=%s", event)
