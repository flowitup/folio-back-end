"""Phase 04 item 4 — admin-only finance/payroll/supervision answers.

Every method here only ever runs when ``AssistantService`` has already checked
``scope.is_admin_channel`` (the router refuses these intents everywhere else, D17) —
this module does not re-check the channel itself, but every number it renders comes
straight from a fixed template, never model text, per the plan's explicit "templates
only, no model text" rule for this class of answer.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from app.application.assistant import reply
from app.application.assistant.audit_ports import AssistantAuditPort
from app.application.assistant.messages import AssistantMessenger
from app.application.assistant.models import ChannelScope
from app.application.billing.ports import BillingDocumentRepositoryPort
from app.application.chat.ports import ChatDirectoryPort
from app.application.invoice.get_labor_payments_summary_usecase import (
    GetLaborPaymentsSummaryRequest,
    GetLaborPaymentsSummaryUseCase,
)
from app.application.invoice.ports import IInvoiceRepository
from app.application.projects.ports import IProjectRepository
from app.domain.entities.project import Project

#: Billing document statuses counted as "unpaid" for a facture (draft/rejected/expired
#: are not yet — or no longer — payable; a devis never counts, only kind == "facture").
_UNPAID_STATUSES = frozenset({"sent", "overdue"})

#: The audit summary answers "this week" — a rolling 7-day window ending now.
_AUDIT_WINDOW = timedelta(days=7)


def _money(amount: object) -> str:
    return f"{float(amount or 0):.2f}€"  # type: ignore[arg-type]


class AdminAnswersFeature:
    """Project money (1.1), worker salary for a month (2.4), unpaid client invoices
    (6.5) and the admin channel's own supervision query ("who asked what this week")."""

    def __init__(
        self,
        *,
        project_repo: IProjectRepository,
        invoice_repo: IInvoiceRepository,
        billing_repo: BillingDocumentRepositoryPort,
        labor_payments_usecase: GetLaborPaymentsSummaryUseCase,
        audit: AssistantAuditPort,
        directory: ChatDirectoryPort,
    ) -> None:
        self._project_repo = project_repo
        self._invoice_repo = invoice_repo
        self._billing_repo = billing_repo
        self._labor_payments_usecase = labor_payments_usecase
        self._audit = audit
        self._directory = directory

    # ------------------------------------------------------------------
    # 1.1 — project money: spent split, released, budget, remaining
    # ------------------------------------------------------------------

    def ask_project_income(
        self,
        *,
        scope: ChannelScope,
        project_id: UUID,
        user_id: UUID,
        message_id: UUID,
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
    ) -> str:
        project = self._project_repo.find_by_id(project_id)
        project_name = project.name if project is not None else str(project_id)
        company_spent, personal_spent = self._invoice_repo.sum_spent_split(project_id)
        company_released, personal_released, _cash_advance = self._invoice_repo.sum_funds_released_split(project_id)
        released_total = company_released + personal_released
        spent_total = company_spent + personal_spent
        budget = project.budget if project is not None else None
        # Same definition as the app's home card ("còn lại để chi"): what has been released
        # and not yet spent. The budget is reported on its own line.
        remaining: Optional[float] = float(released_total) - float(spent_total)
        text = reply.render(
            "project_income_summary",
            lang,
            project=project_name,
            budget=_money(budget) if budget is not None else "-",
            released=_money(released_total),
            spent=_money(spent_total),
            remaining=_money(remaining) if remaining is not None else "-",
        )
        messenger.post_text(
            user_id, text, reply_to_id=message_id, trace_id=trace_id, channel=scope.channel, scope=scope
        )
        return "answered"

    # ------------------------------------------------------------------
    # 2.4 — worker salary for a month (labor payments summary)
    # ------------------------------------------------------------------

    def ask_salary(
        self,
        *,
        scope: ChannelScope,
        project_id: UUID,
        text: str,
        user_id: UUID,
        message_id: UUID,
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
    ) -> str:
        summary = self._labor_payments_usecase.execute(GetLaborPaymentsSummaryRequest(project_id=project_id))
        needle = text.lower()
        lines: list[str] = []
        for month in summary.months:
            label = f"{month.year}-{month.month:02d}" if month.year and month.month else "-"
            for worker in month.workers:
                if needle and worker.worker_name.strip().lower() not in needle:
                    continue
                lines.append(
                    reply.render(
                        "salary_summary_line",
                        lang,
                        worker=worker.worker_name,
                        month=label,
                        paid=_money(worker.paid),
                        count=worker.invoice_count,
                    )
                )
        if not lines:
            messenger.post_text(
                user_id,
                reply.render("salary_summary_none", lang),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=scope.channel,
                scope=scope,
            )
            return "answered"
        messenger.post_text(
            user_id, "\n".join(lines), reply_to_id=message_id, trace_id=trace_id, channel=scope.channel, scope=scope
        )
        return "answered"

    # ------------------------------------------------------------------
    # 6.5 — unpaid client invoices, company-wide
    # ------------------------------------------------------------------

    def ask_unpaid_invoices(
        self,
        *,
        scope: ChannelScope,
        user_id: UUID,
        message_id: UUID,
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
        projects: list[Project],
    ) -> str:
        today = date.today()
        lines: list[str] = []
        for project in projects:
            for doc in self._billing_repo.list_by_project(project.id):
                if doc.kind.value != "facture" or doc.status.value not in _UNPAID_STATUSES:
                    continue
                due_date = doc.payment_due_date
                if due_date is not None and due_date < today:
                    lines.append(
                        reply.render(
                            "unpaid_invoices_line",
                            lang,
                            project=project.name,
                            number=doc.document_number,
                            amount=_money(doc.total_ttc),
                            days=(today - due_date).days,
                        )
                    )
                else:
                    lines.append(
                        reply.render(
                            "unpaid_invoices_line_not_due",
                            lang,
                            project=project.name,
                            number=doc.document_number,
                            amount=_money(doc.total_ttc),
                        )
                    )
        if not lines:
            messenger.post_text(
                user_id,
                reply.render("unpaid_invoices_none", lang),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=scope.channel,
                scope=scope,
            )
            return "answered"
        messenger.post_text(
            user_id, "\n".join(lines), reply_to_id=message_id, trace_id=trace_id, channel=scope.channel, scope=scope
        )
        return "answered"

    # ------------------------------------------------------------------
    # Admin-channel supervision — "tuần này ai hỏi gì?"
    # ------------------------------------------------------------------

    def ask_audit(
        self,
        *,
        scope: ChannelScope,
        user_id: UUID,
        message_id: UUID,
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
    ) -> str:
        if scope.company_id is None:
            messenger.post_text(
                user_id,
                reply.render("audit_summary_none", lang),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=scope.channel,
                scope=scope,
            )
            return "answered"
        since = date.today() - _AUDIT_WINDOW
        rows = self._audit.list_for_company(
            scope.company_id, from_=datetime.combine(since, datetime.min.time(), tzinfo=timezone.utc)
        )
        if not rows:
            messenger.post_text(
                user_id,
                reply.render("audit_summary_none", lang),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=scope.channel,
                scope=scope,
            )
            return "answered"
        counts: dict[Optional[UUID], list[int]] = {}
        for row in rows:
            bucket = counts.setdefault(row.user_id, [0, 0])
            bucket[0] += 1
            if row.outcome == "refused":
                bucket[1] += 1
        names = self._directory.display_names([uid for uid in counts if uid is not None])
        lines = [
            reply.render(
                "audit_summary_line",
                lang,
                user=names.get(user_id, str(user_id)) if user_id is not None else "?",
                total=total,
                refused=refused,
            )
            for user_id, (total, refused) in counts.items()
        ]
        messenger.post_text(
            user_id, "\n".join(lines), reply_to_id=message_id, trace_id=trace_id, channel=scope.channel, scope=scope
        )
        return "answered"


__all__ = ["AdminAnswersFeature"]
