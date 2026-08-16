"""Shared mutation authorization for project analyses.

Reading an analysis requires project membership. Changing or deleting one
requires more than that, and this module is the single place that rule lives so
the update and delete use-cases cannot drift apart.
"""

from __future__ import annotations

from uuid import UUID

from app.application.project_analyses.exceptions import PermissionDeniedError
from app.domain.entities.project_analysis import ProjectAnalysis


def authorize_analysis_mutation(
    *,
    analysis: ProjectAnalysis,
    actor_id: UUID,
    project_owner_id: UUID | None,
    is_admin: bool,
) -> None:
    """Permit an edit/delete only for the uploader, the project owner, or an admin.

    Project membership alone is deliberately NOT sufficient. Membership gates
    *reading* an analysis; if it also gated writing, any member of a project
    could silently rewrite or delete a colleague's report. This mirrors
    ``DeleteProjectDocumentUseCase``, the closest analogue in the codebase,
    which requires uploader OR project owner OR admin.

    Raises:
        PermissionDeniedError: the actor is none of the three.
    """
    if is_admin:
        return
    if analysis.uploader_user_id == actor_id:
        return
    if project_owner_id is not None and project_owner_id == actor_id:
        return
    raise PermissionDeniedError(f"User {actor_id} may not modify analysis {analysis.id}.")
