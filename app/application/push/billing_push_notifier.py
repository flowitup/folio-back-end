"""Push notifications for money: expense refunds and billing-document status.

Refunds are set by a company admin, so the person who needs telling is the one waiting for
their money back — the payer who recorded the expense.

Billing status splits by audience: an accepted devis or a paid facture is news the company
can act on, while a rejection or an overdue invoice is administrative and belongs to
whoever raised the document.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Protocol
from uuid import UUID

from app.application.push.dispatcher import PushDispatcher
from app.domain.notifications.categories import NotificationCategory

logger = logging.getLogger(__name__)

_REFUND_TEXT: Dict[str, Dict[str, tuple]] = {
    "refund_pending": {
        "vi": ("Đang xử lý hoàn tiền", "Chi phí của bạn đang chờ hoàn · {project}"),
        "fr": ("Remboursement en cours", "Votre dépense est en attente de remboursement · {project}"),
        "en": ("Refund in progress", "Your expense is awaiting reimbursement · {project}"),
    },
    "refunded": {
        "vi": ("Đã hoàn tiền", "Chi phí của bạn đã được hoàn · {project}"),
        "fr": ("Remboursement effectué", "Votre dépense a été remboursée · {project}"),
        "en": ("Refunded", "Your expense has been reimbursed · {project}"),
    },
}

_STATUS_TEXT: Dict[str, Dict[str, tuple]] = {
    "accepted": {
        "vi": ("Báo giá được chấp nhận", "{number}"),
        "fr": ("Devis accepté", "{number}"),
        "en": ("Quote accepted", "{number}"),
    },
    "rejected": {
        "vi": ("Báo giá bị từ chối", "{number}"),
        "fr": ("Devis refusé", "{number}"),
        "en": ("Quote rejected", "{number}"),
    },
    "paid": {
        "vi": ("Hóa đơn đã thanh toán", "{number}"),
        "fr": ("Facture payée", "{number}"),
        "en": ("Invoice paid", "{number}"),
    },
    "overdue": {
        "vi": ("Hóa đơn quá hạn", "{number}"),
        "fr": ("Facture en retard", "{number}"),
        "en": ("Invoice overdue", "{number}"),
    },
}

# Good news the company can act on goes wide; admin chores stay with the author.
_TEAM_WIDE = ("accepted", "paid")


class ProjectNameReader(Protocol):
    def find_by_id(self, project_id: UUID): ...


class CompanyAccessReader(Protocol):
    def list_for_company(self, company_id: UUID) -> list: ...


class BillingPushNotifier:
    def __init__(
        self,
        dispatcher: PushDispatcher,
        project_repo: ProjectNameReader,
        access_repo: Optional[CompanyAccessReader] = None,
    ) -> None:
        self._dispatcher = dispatcher
        self._projects = project_repo
        self._access = access_repo

    def refund_status_changed(
        self, *, status: str, payer_id: UUID, actor_id: UUID, project_id: UUID, invoice_id: UUID
    ) -> None:
        """`refund_pending` / `refunded` reach the payer; other statuses are not news."""
        try:
            if status not in _REFUND_TEXT:
                return
            project = self._projects.find_by_id(project_id)
            title, body = _REFUND_TEXT[status][self._dispatcher.locale]
            self._dispatcher.dispatch(
                category=NotificationCategory.BILLING.value,
                recipients=[payer_id],
                title=title,
                body=body.format(project=project.name if project is not None else ""),
                data={
                    "kind": "refund_completed" if status == "refunded" else "refund_requested",
                    "project_id": str(project_id),
                    "invoice_id": str(invoice_id),
                },
                exclude=actor_id,
            )
        except Exception:  # a push must never break the refund update
            logger.exception("refund push failed status=%s", status)

    def document_status_changed(
        self,
        *,
        status: str,
        author_id: UUID,
        actor_id: UUID,
        document_id: UUID,
        company_id: Optional[UUID],
        number: str,
    ) -> None:
        """Accepted / paid go to the company's admins too; rejected / overdue stay with the author."""
        try:
            if status not in _STATUS_TEXT:
                return
            recipients: List[UUID] = [author_id]
            if status in _TEAM_WIDE and company_id is not None and self._access is not None:
                recipients += [
                    a.user_id for a in self._access.list_for_company(company_id) if getattr(a, "role", "") == "admin"
                ]
            title, body = _STATUS_TEXT[status][self._dispatcher.locale]
            self._dispatcher.dispatch(
                category=NotificationCategory.BILLING.value,
                recipients=set(recipients),
                title=title,
                body=body.format(number=number or ""),
                data={"kind": "billing_status", "document_id": str(document_id), "status": status},
                exclude=actor_id,
            )
        except Exception:  # a push must never break the status transition
            logger.exception("billing status push failed status=%s", status)
