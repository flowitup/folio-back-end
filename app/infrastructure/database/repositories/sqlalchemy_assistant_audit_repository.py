"""SQLAlchemy repository for ``assistant_audit_log``. Implements ``AssistantAuditPort``."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.assistant.audit_ports import AuditLogEntry
from app.infrastructure.database.models.assistant_audit_log import AssistantAuditLogModel


def _to_entity(m: AssistantAuditLogModel) -> AuditLogEntry:
    return AuditLogEntry(
        id=m.id,
        company_id=m.company_id,
        channel_key=m.channel_key,
        user_id=m.user_id,
        message_id=m.message_id,
        intent=m.intent,
        feature=m.feature,
        tools=dict(m.tools) if isinstance(m.tools, dict) else m.tools,
        outcome=m.outcome,
        refused_reason=m.refused_reason,
        cost_usd=Decimal(m.cost_usd),
        trace_id=m.trace_id,
        created_at=m.created_at,
    )


class SqlAlchemyAssistantAuditRepository:
    """Implements ``AssistantAuditPort`` against a SQLAlchemy session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(
        self,
        *,
        company_id: Optional[UUID],
        channel_key: str,
        user_id: Optional[UUID],
        message_id: Optional[UUID],
        intent: Optional[str],
        feature: Optional[str],
        tools: Optional[Any] = None,
        outcome: Optional[str],
        refused_reason: Optional[str] = None,
        cost_usd: Decimal = Decimal("0"),
        trace_id: Optional[str] = None,
    ) -> AuditLogEntry:
        model = AssistantAuditLogModel(
            id=uuid4(),
            company_id=company_id,
            channel_key=channel_key,
            user_id=user_id,
            message_id=message_id,
            intent=intent,
            feature=feature,
            tools=tools,
            outcome=outcome,
            refused_reason=refused_reason,
            cost_usd=cost_usd,
            trace_id=trace_id,
        )
        self._session.add(model)
        self._session.commit()
        return _to_entity(model)

    def list_for_company(
        self,
        company_id: UUID,
        *,
        from_: Optional[datetime] = None,
        to: Optional[datetime] = None,
        user_id: Optional[UUID] = None,
        limit: int = 200,
    ) -> list[AuditLogEntry]:
        stmt = select(AssistantAuditLogModel).where(AssistantAuditLogModel.company_id == company_id)
        if from_ is not None:
            stmt = stmt.where(AssistantAuditLogModel.created_at >= from_)
        if to is not None:
            stmt = stmt.where(AssistantAuditLogModel.created_at <= to)
        if user_id is not None:
            stmt = stmt.where(AssistantAuditLogModel.user_id == user_id)
        stmt = stmt.order_by(AssistantAuditLogModel.created_at.desc()).limit(limit)
        rows = self._session.execute(stmt).scalars().all()
        return [_to_entity(r) for r in rows]
