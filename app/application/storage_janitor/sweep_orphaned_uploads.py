"""Use case: delete stored objects that no row owns (abandoned presigned uploads).

A presigned document upload PUTs the file to storage before the confirm call
creates its row. When the tab is closed mid-way, or confirm fails, the object
has no row and nothing ever references it. Objects under the prefix older than
the grace period whose key is in no row (live or soft-deleted) are deleted.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, Iterator, Protocol

_log = logging.getLogger(__name__)

# Presigned PUT URLs live 10 minutes; a day leaves room for a slow confirm retry.
DEFAULT_GRACE = timedelta(days=1)


class IStorageLister(Protocol):
    def list_keys_older_than(self, prefix: str, cutoff: datetime) -> Iterator[str]: ...

    def delete(self, key: str) -> None: ...


class SweepOrphanedUploadsUseCase:
    """Delete objects under ``prefix`` that no row references."""

    def __init__(
        self,
        storage: IStorageLister,
        known_keys: Callable[[Iterable[str]], set[str]],
        prefix: str = "project-documents/",
    ) -> None:
        self._storage = storage
        self._known_keys = known_keys
        self._prefix = prefix

    def execute(self, *, dry_run: bool = False, now: datetime | None = None, batch: int = 500) -> tuple[int, int]:
        """Return (orphans found, orphans deleted)."""
        cutoff = (now or datetime.now(timezone.utc)) - DEFAULT_GRACE
        found = deleted = 0
        chunk: list[str] = []

        def flush() -> None:
            nonlocal found, deleted
            known = self._known_keys(chunk)
            for key in chunk:
                if key in known:
                    continue
                found += 1
                if dry_run:
                    continue
                try:
                    self._storage.delete(key)
                    deleted += 1
                except Exception as exc:  # noqa: BLE001
                    _log.warning("Janitor: could not delete orphaned object %s: %s", key, exc)
            chunk.clear()

        for key in self._storage.list_keys_older_than(self._prefix, cutoff):
            chunk.append(key)
            if len(chunk) >= batch:
                flush()
        if chunk:
            flush()
        return found, deleted
