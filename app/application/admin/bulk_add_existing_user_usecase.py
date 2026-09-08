"""BulkAddExistingUserUseCase — adds an existing user to multiple projects at once."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.application.admin.dtos import BulkAddResultDto, BulkAddResultItemDto, BulkAddStatus
from app.application.admin.exceptions import (
    EmptyProjectListError,
    PermissionDeniedError,
    TargetUserNotFoundError,
    TooManyProjectsError,
)
from app.application.invitations.ports import (
    ProjectMembershipRepositoryPort,
    ProjectRepositoryPort,
    TransactionalSessionPort,
    UserWriteRepositoryPort,
)
from app.domain.entities.project_membership import ProjectMembership
from tasks import EmailPayload

_MAX_PROJECTS = 50

# Email locale for the consolidated "you've been added to N projects" notification.
# v1: always 'en' — Folio doesn't yet store a preferred locale on User. When that
# field lands, swap this for ``target_user.preferred_locale or EMAIL_DEFAULT_LOCALE``.
EMAIL_DEFAULT_LOCALE = "en"


class BulkAddExistingUserUseCase:
    """Platform-ops only: assign an existing user to N projects at once.

    Produces partial-success results per project and enqueues ONE consolidated
    email if at least one membership was actually created.
    """

    def __init__(
        self,
        user_repo: UserWriteRepositoryPort,
        project_repo: ProjectRepositoryPort,
        membership_repo: ProjectMembershipRepositoryPort,
        email_renderer: Any,  # EmailRenderer — render(template, locale, ctx) -> (subject, txt, html)
        queue_port: Any,  # QueuePort — enqueue(task_name, payload)
        app_base_url: str,
        db_session: TransactionalSessionPort,
        role_checker: Any = None,  # RoleCheckerPort — is_platform_admin(user_id)
    ) -> None:
        self._user_repo = user_repo
        self._project_repo = project_repo
        self._membership_repo = membership_repo
        self._renderer = email_renderer
        self._queue = queue_port
        self._base_url = app_base_url.rstrip("/")
        self._db = db_session
        self._role_checker = role_checker

    # ------------------------------------------------------------------

    def execute(
        self,
        requester_id: UUID,
        target_user_id: UUID,
        project_ids: list[UUID],
    ) -> BulkAddResultDto:
        """Run the bulk-add flow; return a per-project result DTO.

        Args:
            requester_id: UUID of the platform-ops user performing the action.
            target_user_id: UUID of the existing user being assigned.
            project_ids: List of project UUIDs to assign the user to (≤50, deduped).

        Raises:
            PermissionDeniedError: requester not found or is not platform ops.
            TargetUserNotFoundError: target user not found.
            EmptyProjectListError: project_ids is empty after deduplication.
            TooManyProjectsError: project_ids has more than 50 entries after dedup.
        """
        # 1. Load requester; guard existence
        requester = self._user_repo.find_by_id(requester_id)
        if requester is None:  # pragma: no cover - defense-in-depth: route's @jwt_required guarantees a valid identity
            raise PermissionDeniedError(f"Requester {requester_id} not found.")

        # 2. Defense-in-depth: bulk-add is flowitup support tooling, so it needs
        #    the platform-ops flag — the same gate the route applies, read live
        #    from the database (never from a role row or the token).
        if self._role_checker is None or not self._role_checker.is_platform_admin(requester_id):
            raise PermissionDeniedError(f"User {requester_id} is not platform ops (required for bulk-add).")

        # 3. Load target user
        target_user = self._user_repo.find_by_id(target_user_id)
        if target_user is None:
            raise TargetUserNotFoundError(f"Target user {target_user_id} not found.")

        # 4. Dedupe project_ids (preserve insertion order); validate bounds
        seen: dict[UUID, None] = {}
        for pid in project_ids:
            seen[pid] = None
        deduped = list(seen.keys())

        if not deduped:  # pragma: no cover - defense-in-depth: Pydantic Field(min_length=1) intercepts at the route
            raise EmptyProjectListError("project_ids must not be empty.")
        if (
            len(deduped) > _MAX_PROJECTS
        ):  # pragma: no cover - defense-in-depth: Pydantic Field(max_length=50) intercepts
            raise TooManyProjectsError(f"project_ids must not exceed {_MAX_PROJECTS} entries; got {len(deduped)}.")

        # 5. Per-project loop
        results: list[BulkAddResultItemDto] = []
        added_projects: list[dict] = []  # [{name: str}] for the consolidated email

        for pid in deduped:
            project = self._project_repo.find_by_id(pid)
            if project is None:
                results.append(
                    BulkAddResultItemDto(project_id=pid, project_name=None, status=BulkAddStatus.PROJECT_NOT_FOUND)
                )
                continue

            # Try to insert; the repo returns True only if a row was actually
            # written (False on ON CONFLICT DO NOTHING). This avoids the H1 race
            # where two concurrent bulk-adds both see no assignment and both
            # report ADDED even though only one INSERT succeeded.
            membership = ProjectMembership.create(
                user_id=target_user.id,
                project_id=project.id,
                invited_by=requester.id,
            )
            inserted = self._membership_repo.add(membership)

            if inserted:
                results.append(
                    BulkAddResultItemDto(project_id=pid, project_name=project.name, status=BulkAddStatus.ADDED)
                )
                added_projects.append({"name": project.name})
            else:
                # Conflict — already assigned to this project; nothing changed.
                results.append(
                    BulkAddResultItemDto(project_id=pid, project_name=project.name, status=BulkAddStatus.ALREADY_MEMBER)
                )

        # 6. Commit BEFORE enqueueing the email so the queue write only happens
        # after persistence is durable (H2 fix). If commit raises, the consolidated
        # email is never enqueued — no orphan "you've been added" notification.
        # Mirrors the explicit-commit pattern in ``AcceptInvitationUseCase``.
        self._db.commit()

        if added_projects:
            self._enqueue_consolidated_email(
                added_projects=added_projects,
                requester_name=requester.display_or_email,
                to_email=target_user.email,
            )

        return BulkAddResultDto(results=results)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _enqueue_consolidated_email(
        self,
        added_projects: list[dict],
        requester_name: str,
        to_email: str,
    ) -> None:
        """Render + enqueue the consolidated 'added to N projects' email.

        Caller MUST commit the DB transaction before invoking this (the H2 contract:
        emails only go out for state that persisted). Locale is
        ``EMAIL_DEFAULT_LOCALE`` (v1: 'en'); future enhancement keys off
        ``target_user.preferred_locale`` once that field exists.
        """
        ctx = {
            "added_projects": added_projects,
            "inviter_name": requester_name,
            "app_url": f"{self._base_url}/{EMAIL_DEFAULT_LOCALE}/dashboard",
        }
        subject, text, html = self._renderer.render("added_to_projects", EMAIL_DEFAULT_LOCALE, ctx)
        payload = EmailPayload(to=to_email, subject=subject, body=text, html_body=html)
        self._queue.enqueue("tasks.send_email", {"payload": payload})
