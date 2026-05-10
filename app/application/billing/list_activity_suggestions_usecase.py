"""ListActivitySuggestionsUseCase — aggregate line-item suggestions for the FE Combobox.

Returns distinct (category, description) pairs from the authenticated user's
billing documents, ranked by frequency, with last-seen unit/price/vat hints
so the FE can pre-fill a new item row.

User-scoped: the repo MUST filter by user_id. No admin escape.
"""

from __future__ import annotations

from uuid import UUID

from app.application.billing.dtos import ActivitySuggestionsResponse
from app.application.billing.ports import BillingDocumentRepositoryPort


# Hard limits to prevent abuse / excessive DB scan
_MAX_LIMIT = 100
_MIN_LIMIT = 1
_MAX_CATEGORY_LEN = 120
_MAX_Q_LEN = 200


class ListActivitySuggestionsUseCase:
    """Return activity suggestions for the authenticated user.

    Validates query params (limit, q, category) then delegates to the
    repository aggregate method that dispatches by DB dialect.
    """

    def __init__(self, doc_repo: BillingDocumentRepositoryPort) -> None:
        self._doc_repo = doc_repo

    def execute(
        self,
        user_id: UUID,
        category: str | None = None,
        q: str | None = None,
        limit: int = 20,
    ) -> ActivitySuggestionsResponse:
        # Validate limit
        if limit < _MIN_LIMIT or limit > _MAX_LIMIT:
            raise ValueError(f"limit must be between {_MIN_LIMIT} and {_MAX_LIMIT}")

        # Validate + normalise category (empty → None)
        if category is not None:
            category = category.strip() or None
            if category and len(category) > _MAX_CATEGORY_LEN:
                raise ValueError(f"category exceeds {_MAX_CATEGORY_LEN} characters")

        # Validate + normalise q (empty → None)
        if q is not None:
            q = q.strip() or None
            if q and len(q) > _MAX_Q_LEN:
                raise ValueError(f"q exceeds {_MAX_Q_LEN} characters")

        return self._doc_repo.aggregate_item_suggestions(
            user_id=user_id,
            category=category,
            q=q,
            limit=limit,
        )
