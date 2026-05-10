"""SQLAlchemy adapter implementing BillingDocumentRepositoryPort."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.application.billing.dtos import (
    ActivityCategoryDTO,
    ActivitySuggestionDTO,
    ActivitySuggestionsResponse,
)
from app.domain.billing.document import BillingDocument
from app.domain.billing.enums import BillingDocumentKind, BillingDocumentStatus
from app.infrastructure.database.models.billing_document import BillingDocumentModel
from app.infrastructure.database.serializers.billing_serializers import (
    deserialize_orm_to_doc,
    serialize_doc_to_orm,
)


class SqlAlchemyBillingDocumentRepository:
    """Implements BillingDocumentRepositoryPort against a SQLAlchemy session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def find_by_id(self, doc_id: UUID) -> Optional[BillingDocument]:
        """Return document by UUID, or None if not found."""
        row = self._session.get(BillingDocumentModel, doc_id)
        if row is None:
            return None
        return deserialize_orm_to_doc(row)

    def find_by_id_for_update(self, doc_id: UUID) -> Optional[BillingDocument]:
        """Return document with SELECT FOR UPDATE lock (serialises concurrent ops).

        Falls back to a plain SELECT on dialects that don't support FOR UPDATE
        (e.g. SQLite in tests) — SQLAlchemy silently drops the hint on SQLite.
        """
        stmt = select(BillingDocumentModel).where(BillingDocumentModel.id == doc_id).with_for_update()
        row = self._session.execute(stmt).scalar_one_or_none()
        if row is None:
            return None
        return deserialize_orm_to_doc(row)

    def list_for_user(
        self,
        user_id: UUID,
        kind: BillingDocumentKind,
        status: Optional[BillingDocumentStatus] = None,
        project_id: Optional[UUID] = None,
        company_id: Optional[UUID] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[BillingDocument], int]:
        """Return paginated documents for a user, with unfiltered total count.

        Returns (items, total_count).
        Optional company_id filter restricts results to documents issued by that company.
        """
        base = select(BillingDocumentModel).where(
            BillingDocumentModel.user_id == user_id,
            BillingDocumentModel.kind == kind.value,
        )
        if status is not None:
            base = base.where(BillingDocumentModel.status == status.value)
        if project_id is not None:
            base = base.where(BillingDocumentModel.project_id == project_id)
        if company_id is not None:
            base = base.where(BillingDocumentModel.company_id == company_id)

        # Total count (no pagination)
        count_stmt = select(func.count()).select_from(base.subquery())
        total: int = self._session.execute(count_stmt).scalar_one()

        # Paginated rows, newest first
        rows_stmt = base.order_by(BillingDocumentModel.created_at.desc()).limit(limit).offset(offset)
        rows = self._session.execute(rows_stmt).scalars().all()
        return ([deserialize_orm_to_doc(r) for r in rows], total)

    def find_by_source_devis_id(self, devis_id: UUID) -> Optional[BillingDocument]:
        """Return the facture linked to a given source devis, or None.

        Used as a race-condition guard in ConvertDevisToFactureUseCase.
        """
        stmt = select(BillingDocumentModel).where(BillingDocumentModel.source_devis_id == devis_id)
        row = self._session.execute(stmt).scalar_one_or_none()
        if row is None:
            return None
        return deserialize_orm_to_doc(row)

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def save(self, doc: BillingDocument) -> BillingDocument:
        """Insert or update a document. Returns the persisted instance."""
        row = self._session.get(BillingDocumentModel, doc.id)
        if row is None:
            row = BillingDocumentModel()
            serialize_doc_to_orm(doc, row)
            self._session.add(row)
        else:
            serialize_doc_to_orm(doc, row)
        self._session.flush()
        return deserialize_orm_to_doc(row)

    def delete(self, doc_id: UUID) -> None:
        """Hard-delete a document by UUID. No-op if not found."""
        row = self._session.get(BillingDocumentModel, doc_id)
        if row is not None:
            self._session.delete(row)
            self._session.flush()

    # ------------------------------------------------------------------
    # Activity suggestions aggregation (phase 04)
    # ------------------------------------------------------------------

    def aggregate_item_suggestions(
        self,
        user_id: UUID,
        category: Optional[str],
        q: Optional[str],
        limit: int,
    ) -> ActivitySuggestionsResponse:
        """Aggregate line-item suggestions for a user.

        Dispatches to the Postgres JSONB lateral-join path or the SQLite
        in-Python aggregation path based on the active dialect.
        """
        try:
            bind = self._session.get_bind()
            dialect = bind.dialect.name
        except Exception:  # noqa: BLE001
            dialect = "sqlite"

        if dialect == "postgresql":
            return self._aggregate_suggestions_postgres(user_id, category, q, limit)
        return self._aggregate_suggestions_sqlite(user_id, category, q, limit)

    def _aggregate_suggestions_postgres(
        self,
        user_id: UUID,
        category: Optional[str],
        q: Optional[str],
        limit: int,
    ) -> ActivitySuggestionsResponse:
        """Postgres path: jsonb_array_elements lateral join + GROUP BY."""
        from sqlalchemy import literal_column

        # Lateral subquery: unnest items JSONB array per document
        # We use a raw text fragment for jsonb_array_elements because
        # SQLAlchemy's support for LATERAL with JSONB is dialect-specific.
        user_docs_subq = (
            select(
                BillingDocumentModel.id.label("doc_id"),
                BillingDocumentModel.created_at.label("doc_created_at"),
                func.jsonb_array_elements(BillingDocumentModel.items).label("item"),
            )
            .where(BillingDocumentModel.user_id == user_id)
            .subquery("user_items")
        )

        item = user_docs_subq.c.item
        doc_created_at = user_docs_subq.c.doc_created_at

        item_desc = item.op("->>")(literal_column("'description'"))
        item_cat = item.op("->>")(literal_column("'category'"))
        item_unit = item.op("->>")(literal_column("'unit'"))
        item_price = item.op("->>")(literal_column("'unit_price'"))
        item_vat = item.op("->>")(literal_column("'vat_rate'"))

        # --- Suggestions query ---
        suggestions_q = select(
            item_desc.label("description"),
            item_cat.label("category"),
            func.count().label("frequency"),
            # Last item's fields by most recent doc created_at
            func.max(doc_created_at).label("last_at"),
            # Use string_agg ordered by created_at to get the most-recent values
            # We use a window-function approach: pick values from the max created_at row.
            # Simpler: just use MAX on a concatenated key, then re-parse. Instead use:
            # array_agg for unit_price / vat / unit, take first element (sorted desc).
            # PostgreSQL: (array_agg(x ORDER BY created_at DESC))[1]
            func.array_agg(
                func.json_build_object(
                    literal_column("'unit'"),
                    item_unit,
                    literal_column("'unit_price'"),
                    item_price,
                    literal_column("'vat_rate'"),
                    item_vat,
                    literal_column("'created_at'"),
                    doc_created_at,
                ).op("ORDER BY")(doc_created_at.desc())
            ).label("last_hints"),
        ).group_by(item_desc, item_cat)

        if category is not None:
            suggestions_q = suggestions_q.where(item_cat == category)
        if q:
            suggestions_q = suggestions_q.where(item_desc.ilike(f"{q}%"))

        suggestions_q = suggestions_q.order_by(
            func.count().desc(),
            item_desc.asc(),
        ).limit(limit)

        rows = self._session.execute(suggestions_q).all()
        suggestions = []
        for row in rows:
            hints = row.last_hints[0] if row.last_hints else {}
            suggestions.append(
                ActivitySuggestionDTO(
                    description=row.description or "",
                    category=row.category,
                    frequency=row.frequency,
                    last_unit=hints.get("unit"),
                    last_unit_price=hints.get("unit_price"),
                    last_vat_rate=hints.get("vat_rate"),
                )
            )

        # --- Categories query ---
        cats_q2 = (
            select(
                item_cat.label("name"),
                func.count().label("frequency"),
            )
            .select_from(user_docs_subq)
            .where(item_cat.isnot(None))
            .group_by(item_cat)
            .order_by(item_cat.asc())
            .limit(50)
        )
        cat_rows = self._session.execute(cats_q2).all()
        categories = [ActivityCategoryDTO(name=r.name, frequency=r.frequency) for r in cat_rows]

        return ActivitySuggestionsResponse(categories=categories, suggestions=suggestions)

    def _aggregate_suggestions_sqlite(
        self,
        user_id: UUID,
        category: Optional[str],
        q: Optional[str],
        limit: int,
    ) -> ActivitySuggestionsResponse:
        """SQLite (and test) path: load all user docs → in-Python aggregation.

        Returns the same shape as the Postgres path.
        """
        from collections import defaultdict

        # Load all user docs
        stmt = select(BillingDocumentModel).where(BillingDocumentModel.user_id == user_id)
        rows = self._session.execute(stmt).scalars().all()

        # Collect (category, description) → [(created_at, unit, unit_price, vat_rate)]
        groups: dict[tuple, list] = defaultdict(list)
        cat_counts: dict[str, int] = defaultdict(int)

        for row in rows:
            for item_dict in row.items or []:
                desc = item_dict.get("description") or ""
                item_cat = item_dict.get("category")
                item_unit = item_dict.get("unit")
                item_price = item_dict.get("unit_price")
                item_vat = item_dict.get("vat_rate")

                # category filter
                if category is not None and item_cat != category:
                    continue
                # q prefix filter (case-insensitive)
                if q and not desc.lower().startswith(q.lower()):
                    continue

                groups[(item_cat, desc)].append((row.created_at, item_unit, item_price, item_vat))
                if item_cat:
                    cat_counts[item_cat] += 1

        # Build suggestions sorted by frequency DESC, description ASC
        suggestion_list = []
        for (item_cat, desc), entries in groups.items():
            entries.sort(key=lambda e: e[0] or "", reverse=True)
            last = entries[0]
            suggestion_list.append(
                ActivitySuggestionDTO(
                    description=desc,
                    category=item_cat,
                    frequency=len(entries),
                    last_unit=last[1],
                    last_unit_price=last[2],
                    last_vat_rate=last[3],
                )
            )

        suggestion_list.sort(key=lambda s: (-s.frequency, s.description))
        suggestions = suggestion_list[:limit]

        # Build categories sorted alphabetically, capped at 50
        # Count across all items (not filtered by category/q) for the categories list
        # Re-aggregate without filters for the categories panel
        all_cat_counts: dict[str, int] = defaultdict(int)
        for row in rows:
            for item_dict in row.items or []:
                item_cat = item_dict.get("category")
                if item_cat:
                    all_cat_counts[item_cat] += 1

        categories = sorted(
            [ActivityCategoryDTO(name=k, frequency=v) for k, v in all_cat_counts.items()],
            key=lambda c: c.name,
        )[:50]

        return ActivitySuggestionsResponse(categories=categories, suggestions=suggestions)
