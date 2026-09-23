"""Phase 04 item 4 — admin-only finance/payroll/supervision answers.

Every method here only ever runs when ``AssistantService`` has already checked
``scope.is_admin_channel`` (the router refuses these intents everywhere else, D17) —
this module does not re-check the channel itself, but every number it renders comes
straight from a fixed template, never model text, per the plan's explicit "templates
only, no model text" rule for this class of answer.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from app.application.assistant import formatting, reply
from app.application.assistant.audit_ports import AssistantAuditPort
from app.application.assistant.messages import AssistantMessenger
from app.application.assistant.models import ChannelScope
from app.application.authz.ports import AuthzReaderPort
from app.application.billing.ports import BillingDocumentRepositoryPort
from app.application.chat.ports import ChatDirectoryPort
from app.application.invoice.get_labor_payments_summary_usecase import (
    GetLaborPaymentsSummaryRequest,
    GetLaborPaymentsSummaryUseCase,
)
from app.application.invoice.ports import IInvoiceRepository
from app.application.projects.ports import IProjectRepository, ProjectSpentReaderPort
from app.domain.authz.resolver import has_permission
from app.domain.entities.project import Project
from app.domain.time import business_today

#: Billing document statuses counted as "unpaid" for a facture (draft/rejected/expired
#: are not yet — or no longer — payable; a devis never counts, only kind == "facture").
_UNPAID_STATUSES = frozenset({"sent", "overdue"})

#: The audit summary answers "this week" — a rolling 7-day window ending now.
_AUDIT_WINDOW = timedelta(days=7)

#: `ask_project_income` mirrors the HTTP budget gate (`app/api/v1/projects/routes.py`,
#: `budget_scope.py`) — a company admin/platform-ops holds this implicitly through the
#: matrix, so this re-check is defense in depth: the admin CHANNEL is already
#: admin-only, but the tapped/resolved project_id could in principle be one the caller's
#: own company role does not actually cover (e.g. a D8 deny), and a template must never
#: render a number the resolver itself would refuse on the equivalent HTTP route.
PROJECT_INCOME_PERMISSION = "project:view_budget"

#: `ask_unpaid_invoices` gates each project the same way `GET /projects/<id>/invoices`
#: does (`app/api/v1/invoices/invoice_routes.py`) — being an admin of the channel's own
#: company is not, on its own, being able to read every project's billing documents; a
#: project the caller was merely assigned to read (not this project) must not leak its
#: client invoices into the admin channel.
UNPAID_INVOICES_PERMISSION = "project:read"

#: `ask_unpaid_invoices` caps its listing so the reply never approaches the chat body
#: limit for a company with many unpaid factures; the rest is summarised as "+N".
_MAX_UNPAID_INVOICE_LINES = 30

#: New copy this module needs that has no existing `reply.py` template.
_LOCAL_TEMPLATES: dict[str, dict[str, str]] = {
    "unpaid_invoices_more": {
        "vi": "…và {count} hoá đơn khác.",
        "fr": "… et {count} autre(s) facture(s).",
        "en": "…and {count} more invoice(s).",
    },
}


def _local_render(key: str, lang: str, **kwargs: object) -> str:
    resolved_lang = lang if lang in reply.LANGUAGES else "fr"
    return _LOCAL_TEMPLATES[key][resolved_lang].format(**kwargs)


def _may_view_all_pay(authz_reader: AuthzReaderPort, user_id: UUID, project_id: UUID) -> bool:
    """Mirrors `app.api.v1.projects.labor_scope.labor_scope_for`'s unrestricted-read
    gate: manage_labor (write) or view_pay (read-only) both unlock every worker's pay,
    everyone else only ever sees their own — `ask_salary` must never show more."""
    is_platform_admin = authz_reader.is_platform_ops(user_id)
    return has_permission(
        authz_reader, user_id, "project:manage_labor", project_id=project_id, is_platform_admin=is_platform_admin
    ) or has_permission(
        authz_reader, user_id, "project:view_pay", project_id=project_id, is_platform_admin=is_platform_admin
    )


def _money(amount: object, lang: str) -> str:
    return formatting.format_money(amount, lang)


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
        authz_reader: AuthzReaderPort,
        project_spent_reader: ProjectSpentReaderPort,
    ) -> None:
        self._project_repo = project_repo
        self._invoice_repo = invoice_repo
        self._billing_repo = billing_repo
        self._labor_payments_usecase = labor_payments_usecase
        self._audit = audit
        self._directory = directory
        self._authz_reader = authz_reader
        self._project_spent_reader = project_spent_reader

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
        if not has_permission(
            self._authz_reader,
            user_id,
            PROJECT_INCOME_PERMISSION,
            project_id=project_id,
            is_platform_admin=self._authz_reader.is_platform_ops(user_id),
        ):
            messenger.post_text(
                user_id,
                reply.render("no_permission", lang),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=scope.channel,
                scope=scope,
            )
            return "refused"
        project = self._project_repo.find_by_id(project_id)
        project_name = project.name if project is not None else str(project_id)
        company_released, personal_released, _cash_advance = self._invoice_repo.sum_funds_released_split(project_id)
        released_total = company_released + personal_released
        # Same rule as the web/mobile home card's `computeBudgetMetrics`: "spent" is the
        # invoice ledger total (`ProjectSpent.invoiced` — every spend invoice net of
        # returns, the figure the app's own spend total adds up to on screen), and the
        # denominator is the project's budget when one is set, else released funds.
        spent_by_project = self._project_spent_reader.sum_spent_by_projects([project_id])
        spent_total = spent_by_project[project_id].invoiced if project_id in spent_by_project else Decimal("0")
        budget = project.budget if project is not None else None
        denominator = budget if budget is not None and budget > 0 else released_total
        remaining: Optional[float] = float(denominator) - float(spent_total)
        text = reply.render(
            "project_income_summary",
            lang,
            project=project_name,
            budget=_money(budget, lang) if budget is not None else "-",
            released=_money(released_total, lang),
            spent=_money(spent_total, lang),
            remaining=_money(remaining, lang) if remaining is not None else "-",
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
        if not _may_view_all_pay(self._authz_reader, user_id, project_id):
            messenger.post_text(
                user_id,
                reply.render("no_permission", lang),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=scope.channel,
                scope=scope,
            )
            return "refused"
        summary = self._labor_payments_usecase.execute(GetLaborPaymentsSummaryRequest(project_id=project_id))
        year, month = formatting.parse_month(text, business_today())
        month_bucket = next((m for m in summary.months if m.year == year and m.month == month), None)
        workers = month_bucket.workers if month_bucket is not None else []
        if not workers:
            messenger.post_text(
                user_id,
                reply.render("salary_summary_none", lang),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=scope.channel,
                scope=scope,
            )
            return "answered"
        # A worker actually named in the question narrows the list; asking with no name
        # ("bảng lương tháng này?") must list every worker of the month, not answer
        # "none" just because the whole sentence never matches a name as a substring.
        matched_names = formatting.match_names(text, (w.worker_name for w in workers))
        shown = [w for w in workers if w.worker_name in matched_names] if matched_names else workers
        label = f"{year}-{month:02d}"
        lines = [
            reply.render(
                "salary_summary_line",
                lang,
                worker=worker.worker_name,
                month=label,
                paid=_money(worker.paid, lang),
                count=worker.invoice_count,
            )
            for worker in shown
        ]
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
        is_platform_admin = self._authz_reader.is_platform_ops(user_id)
        # `projects` is already scoped to the channel's own company; a project the caller
        # cannot actually read (e.g. a company admin merely assigned, with no `project:read`
        # grant, to a project of a DIFFERENT company that happened to leak into an earlier
        # union) must still never surface its client invoices here — the same gate
        # `GET /projects/<id>/invoices` itself applies.
        readable_projects = [
            project
            for project in projects
            if has_permission(
                self._authz_reader,
                user_id,
                UNPAID_INVOICES_PERMISSION,
                project_id=project.id,
                is_platform_admin=is_platform_admin,
            )
        ]
        today = business_today()
        lines: list[str] = []
        for project in readable_projects:
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
                            amount=_money(doc.total_ttc, lang),
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
                            amount=_money(doc.total_ttc, lang),
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
        shown = lines[:_MAX_UNPAID_INVOICE_LINES]
        if len(lines) > _MAX_UNPAID_INVOICE_LINES:
            shown.append(_local_render("unpaid_invoices_more", lang, count=len(lines) - _MAX_UNPAID_INVOICE_LINES))
        messenger.post_text(
            user_id, "\n".join(shown), reply_to_id=message_id, trace_id=trace_id, channel=scope.channel, scope=scope
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
        since = business_today() - _AUDIT_WINDOW
        # Aggregated in SQL (`GROUP BY user_id`) rather than fetched and tallied
        # client-side, so the weekly count is never silently capped by
        # `list_for_company`'s own row limit for a busy company.
        counts = self._audit.count_by_user_for_company(
            scope.company_id, from_=datetime.combine(since, datetime.min.time(), tzinfo=timezone.utc)
        )
        if not counts:
            messenger.post_text(
                user_id,
                reply.render("audit_summary_none", lang),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=scope.channel,
                scope=scope,
            )
            return "answered"
        names = self._directory.display_names([c.user_id for c in counts if c.user_id is not None])
        lines = [
            reply.render(
                "audit_summary_line",
                lang,
                user=names.get(count.user_id, str(count.user_id)) if count.user_id is not None else "?",
                total=count.total,
                refused=count.refused,
            )
            for count in counts
        ]
        messenger.post_text(
            user_id, "\n".join(lines), reply_to_id=message_id, trace_id=trace_id, channel=scope.channel, scope=scope
        )
        return "answered"


__all__ = ["AdminAnswersFeature"]
