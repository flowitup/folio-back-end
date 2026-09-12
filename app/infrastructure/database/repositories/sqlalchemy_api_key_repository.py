"""SQLAlchemy adapter implementing ApiKeyRepositoryPort."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.domain.entities.api_key import ApiKey
from app.infrastructure.database.models.api_key import ApiKeyOrm

logger = logging.getLogger(__name__)

# touch_last_used only writes when the stored value is missing or older than
# this threshold — caps the write rate on a key hammered by automation to at
# most once a minute, regardless of how often it is actually used.
_TOUCH_THROTTLE_SECONDS = 60


class SqlAlchemyApiKeyRepository:
    """Implements ApiKeyRepositoryPort: CRUD + lookup for the ApiKey aggregate."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # ApiKeyRepositoryPort
    # ------------------------------------------------------------------

    def add(self, api_key: ApiKey) -> None:
        """Insert a new API key."""
        orm = ApiKeyOrm.from_entity(api_key)
        self._session.add(orm)
        self._session.flush()

    def list_for_user(self, user_id: UUID) -> list[ApiKey]:
        """Return all API keys owned by user_id, newest first."""
        stmt = select(ApiKeyOrm).where(ApiKeyOrm.user_id == user_id).order_by(ApiKeyOrm.created_at.desc())
        rows = self._session.execute(stmt).scalars().all()
        return [r.to_entity() for r in rows]

    def find_by_token_hash(self, token_hash: str) -> Optional[ApiKey]:
        """Look up an API key by its sha256 token hash. Returns None if not found."""
        stmt = select(ApiKeyOrm).where(ApiKeyOrm.token_hash == token_hash)
        orm = self._session.execute(stmt).scalar_one_or_none()
        return orm.to_entity() if orm is not None else None

    def find_by_id_for_user(self, key_id: UUID, user_id: UUID) -> Optional[ApiKey]:
        """Look up an API key by id, scoped to its owner. None if missing or owned by someone else."""
        stmt = select(ApiKeyOrm).where(ApiKeyOrm.id == key_id, ApiKeyOrm.user_id == user_id)
        orm = self._session.execute(stmt).scalar_one_or_none()
        return orm.to_entity() if orm is not None else None

    def delete(self, key_id: UUID) -> None:
        """Delete an API key by id. No-op if not found."""
        orm = self._session.get(ApiKeyOrm, key_id)
        if orm is not None:
            self._session.delete(orm)
            self._session.flush()

    def count_for_user(self, user_id: UUID) -> int:
        """Return the number of API keys owned by user_id."""
        stmt = select(func.count()).select_from(ApiKeyOrm).where(ApiKeyOrm.user_id == user_id)
        return self._session.execute(stmt).scalar_one()

    def touch_last_used(self, key_id: UUID) -> None:
        """Best-effort last_used_at bump, throttled to once a minute per key.

        Issues its own guarded UPDATE and commits in its own transaction,
        independent of whatever the request this authenticates is about to
        do with the session — the guard (NULL or older than the threshold)
        keeps the write rate at most once a minute per key regardless of
        call volume. Called from the request-authentication seam
        (app/api/_helpers/api_key_request_auth.py) before the route handler
        runs, so nothing is normally pending on the session yet; the
        try/except still guarantees a failure here can never poison the
        session the route handler is about to use, nor turn an otherwise
        successful request into a 500 — every exception is swallowed and
        logged, never raised.
        """
        try:
            now = datetime.now(timezone.utc)
            threshold = now - timedelta(seconds=_TOUCH_THROTTLE_SECONDS)
            stmt = (
                update(ApiKeyOrm)
                .where(
                    ApiKeyOrm.id == key_id,
                    (ApiKeyOrm.last_used_at.is_(None)) | (ApiKeyOrm.last_used_at < threshold),
                )
                .values(last_used_at=now)
            )
            self._session.execute(stmt)
            self._session.commit()
        except Exception:
            logger.exception("touch_last_used failed for api key %s (non-fatal, swallowed)", key_id)
            try:
                self._session.rollback()
            except Exception:
                logger.exception("touch_last_used rollback also failed for api key %s", key_id)
