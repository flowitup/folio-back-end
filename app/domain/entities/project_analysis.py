"""ProjectAnalysis domain entity — metadata for an uploaded self-contained HTML report."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from uuid import UUID, uuid4

_MAX_TITLE_LEN = 300
_MAX_SUMMARY_LEN = 2000
_MAX_TAG_LEN = 100
_MAX_TAGS = 20


# ------------------------------------------------------------------
# Sentinel for "field not provided" in with_updates — distinguishes
# "leave unchanged" from "explicitly clear to None" (or, for tags,
# "explicitly replace with an empty set").
# ------------------------------------------------------------------


class _Unset:
    """Private sentinel type — distinguishes 'not passed' from None."""

    _instance: _Unset | None = None

    def __new__(cls) -> _Unset:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance


_UNSET: _Unset = _Unset()


# ------------------------------------------------------------------
# Validation helpers
# ------------------------------------------------------------------


def _validate_title(title: str) -> str:
    """Strip and validate title. Raises ValueError on failure."""
    stripped = title.strip()
    if not stripped:
        raise ValueError("Analysis title must not be empty.")
    if len(stripped) > _MAX_TITLE_LEN:
        raise ValueError(f"Analysis title must not exceed {_MAX_TITLE_LEN} characters.")
    return stripped


def _validate_summary(summary: str | None) -> str | None:
    """Validate summary length. Raises ValueError on failure."""
    if summary is None:
        return None
    stripped = summary.strip()
    if not stripped:
        return None
    if len(stripped) > _MAX_SUMMARY_LEN:
        raise ValueError(f"Analysis summary must not exceed {_MAX_SUMMARY_LEN} characters.")
    return stripped


def _validate_source_url(source_url: str | None) -> str | None:
    """Validate source_url is http(s). Raises ValueError on failure."""
    if source_url is None:
        return None
    stripped = source_url.strip()
    if not stripped:
        return None
    if not (stripped.startswith("http://") or stripped.startswith("https://")):
        raise ValueError("Analysis source_url must start with http:// or https://.")
    return stripped


def _validate_tags(tags: tuple[str, ...]) -> tuple[str, ...]:
    """Normalize and validate tags: strip, lowercase, dedupe, cap count and length."""
    normalized = [t.strip().lower() for t in tags if t.strip()]
    deduped = tuple(dict.fromkeys(normalized))
    if len(deduped) > _MAX_TAGS:
        raise ValueError(f"An analysis may have at most {_MAX_TAGS} tags.")
    for tag in deduped:
        if len(tag) > _MAX_TAG_LEN:
            raise ValueError(f"Each tag must be {_MAX_TAG_LEN} characters or fewer.")
    return deduped


def validate_metadata(
    *,
    title: str,
    summary: str | None,
    source_url: str | None,
    tags: tuple[str, ...],
) -> tuple[str, str | None, str | None, tuple[str, ...]]:
    """Validate and normalize the metadata fields shared by create/update.

    Public so use-cases can fail fast on bad metadata BEFORE performing any
    storage I/O (e.g. CreateProjectAnalysisUseCase validates before uploading
    the report body — an invalid title should never orphan an S3 object).

    Returns:
        (title, summary, source_url, tags) normalized and validated.

    Raises:
        ValueError: if any field fails validation.
    """
    return (
        _validate_title(title),
        _validate_summary(summary),
        _validate_source_url(source_url),
        _validate_tags(tags),
    )


# ------------------------------------------------------------------
# Entity
# ------------------------------------------------------------------


@dataclass(frozen=True)
class ProjectAnalysis:
    """Immutable project-scoped analysis report entity.

    The report body (self-contained HTML) lives in object storage under
    ``storage_key``. This entity holds only the metadata needed to list,
    display, and manage it. Soft-deletion is tracked via ``deleted_at``.

    State transitions (with_updates, soft_delete) always return new
    instances — never mutate in place.
    """

    id: UUID
    project_id: UUID
    uploader_user_id: UUID
    title: str
    summary: str | None
    source_url: str | None
    storage_key: str
    size_bytes: int
    tags: tuple[str, ...]
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        *,
        project_id: UUID,
        uploader_user_id: UUID,
        title: str,
        summary: str | None,
        source_url: str | None,
        storage_key: str,
        size_bytes: int,
        tags: tuple[str, ...] = (),
        id: UUID | None = None,
    ) -> ProjectAnalysis:
        """Create a new ProjectAnalysis with validated fields.

        ``id`` may be pre-supplied when the caller must know the id before
        construction (e.g. to build ``storage_key`` from it ahead of the
        upload). Defaults to a fresh uuid4() otherwise.

        Raises:
            ValueError: if title, summary, source_url, or tags fail validation.
        """
        now = datetime.now(timezone.utc)
        return cls(
            id=id if id is not None else uuid4(),
            project_id=project_id,
            uploader_user_id=uploader_user_id,
            title=_validate_title(title),
            summary=_validate_summary(summary),
            source_url=_validate_source_url(source_url),
            storage_key=storage_key,
            size_bytes=size_bytes,
            tags=_validate_tags(tags),
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )

    # ------------------------------------------------------------------
    # Updates (returns new frozen instance)
    # ------------------------------------------------------------------

    def with_updates(
        self,
        *,
        title: str | None = None,
        summary: str | None | _Unset = _UNSET,
        source_url: str | None | _Unset = _UNSET,
        tags: tuple[str, ...] | _Unset = _UNSET,
    ) -> ProjectAnalysis:
        """Return a new ProjectAnalysis with the given fields replaced.

        Every optional field uses the "unset sentinel" pattern so a PATCH of
        only one field never silently clears the others:

        - ``title=None`` (default) leaves the title unchanged; a non-None
          value replaces it (title cannot be cleared — it is required).
        - ``summary`` / ``source_url``: omit (or pass the sentinel) to leave
          unchanged; pass ``None`` to explicitly clear; pass a string to replace.
        - ``tags``: omit (or pass the sentinel) to leave unchanged; pass a
          tuple (including an empty one) to replace the full tag set.

        Raises:
            ValueError: if any provided field fails validation.
        """
        new_title = _validate_title(title) if title is not None else self.title

        new_summary: str | None
        if isinstance(summary, _Unset):
            new_summary = self.summary
        else:
            new_summary = _validate_summary(summary)

        new_source_url: str | None
        if isinstance(source_url, _Unset):
            new_source_url = self.source_url
        else:
            new_source_url = _validate_source_url(source_url)

        new_tags: tuple[str, ...]
        if isinstance(tags, _Unset):
            new_tags = self.tags
        else:
            new_tags = _validate_tags(tags)

        return replace(
            self,
            title=new_title,
            summary=new_summary,
            source_url=new_source_url,
            tags=new_tags,
            updated_at=datetime.now(timezone.utc),
        )

    def soft_delete(self, at: datetime) -> ProjectAnalysis:
        """Return a new instance marked deleted at the given timestamp."""
        return replace(self, deleted_at=at, updated_at=at)

    @property
    def is_deleted(self) -> bool:
        """True if this analysis has been soft-deleted."""
        return self.deleted_at is not None
