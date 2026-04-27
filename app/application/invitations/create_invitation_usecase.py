"""CreateInvitationUseCase — orchestrates the invitation-or-direct-add flow."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.application.invitations.dtos import CreateInvitationResultDto
from app.application.invitations.exceptions import (
    PermissionDeniedError,
    RateLimitedError,
    RoleNotFoundError,
    ProjectNotFoundError,
)
from app.application.invitations.ports import (
    InvitationRepositoryPort,
    ProjectMembershipRepositoryPort,
    ProjectRepositoryPort,
    RoleRepositoryPort,
    UserWriteRepositoryPort,
)
from app.domain.entities.invitation import Invitation
from app.domain.entities.project_membership import ProjectMembership
from app.domain.exceptions.invitation_exceptions import RoleNotAllowedError
from tasks import EmailPayload


class CreateInvitationUseCase:
    """Send an invitation (or directly add an existing user) to a project."""

    def __init__(
        self,
        invitation_repo: InvitationRepositoryPort,
        project_membership_repo: ProjectMembershipRepositoryPort,
        user_repo: UserWriteRepositoryPort,
        project_repo: ProjectRepositoryPort,
        role_repo: RoleRepositoryPort,
        email_port: Any,       # EmailAdapterPort with .send(EmailPayload)
        email_renderer: Any,   # EmailRenderer with .render(template, locale, ctx)
        queue_port: Any,       # QueuePort with .enqueue(task_name, payload)
        app_base_url: str,
        project_invite_daily_cap: int = 50,
    ) -> None:
        self._inv_repo = invitation_repo
        self._membership_repo = project_membership_repo
        self._user_repo = user_repo
        self._project_repo = project_repo
        self._role_repo = role_repo
        self._email_port = email_port
        self._renderer = email_renderer
        self._queue = queue_port
        self._base_url = app_base_url.rstrip("/")
        self._daily_cap = project_invite_daily_cap

    # ------------------------------------------------------------------

    def execute(
        self,
        inviter_id: UUID,
        project_id: UUID,
        email: str,
        role_id: UUID,
        locale: str = "en",
    ) -> CreateInvitationResultDto:
        """Run the invite flow; return DTO indicating what happened."""

        # 1. Load inviter
        inviter = self._user_repo.find_by_id(inviter_id)
        if inviter is None:
            raise PermissionDeniedError(f"Inviter {inviter_id} not found.")

        # 2. Load project (needed for owner check before permission gate)
        project = self._project_repo.find_by_id(project_id)
        if project is None:
            raise ProjectNotFoundError(str(project_id))

        # Verify permission: role-based OR project owner
        if not self._can_invite(inviter, project.owner_id, inviter_id):
            raise PermissionDeniedError(
                f"User {inviter_id} does not have 'project:invite' permission."
            )

        # 3. Load role; guard superadmin
        role = self._role_repo.find_by_id(role_id)
        if role is None:
            raise RoleNotFoundError(f"Role {role_id} not found.")
        if role.name == "superadmin":
            raise RoleNotAllowedError("Cannot invite users with the 'superadmin' role.")

        # 4. Normalize email
        normalized_email = email.strip().lower()

        # 5. Existing-user fast-path
        existing_user = self._user_repo.find_by_email(normalized_email)
        if existing_user is not None:
            if not self._membership_repo.exists(existing_user.id, project_id):
                membership = ProjectMembership.create(
                    user_id=existing_user.id,
                    project_id=project_id,
                    role_id=role_id,
                    invited_by=inviter_id,
                )
                self._membership_repo.add(membership)
                self._enqueue_added_email(
                    to=existing_user.email,
                    project_name=project.name,
                    inviter_name=inviter.display_or_email,
                    role_name=role.name,
                    locale=locale,
                )
            return CreateInvitationResultDto(kind="direct_added", user_id=existing_user.id)

        # 6. Invitation flow — enforce daily cap
        daily_count = self._inv_repo.count_created_today_by_project(project_id)
        if daily_count >= self._daily_cap:
            raise RateLimitedError(
                f"Project {project_id} has reached the daily invitation limit "
                f"({self._daily_cap})."
            )

        # Revoke any existing pending invitation for same email+project
        pending = self._inv_repo.find_pending_by_email_and_project(
            normalized_email, project_id
        )
        if pending is not None:
            revoked = pending.revoke()
            self._inv_repo.save(revoked)

        # Create new invitation
        inv, raw_token = Invitation.create(
            email=normalized_email,
            project_id=project_id,
            role_id=role_id,
            invited_by=inviter_id,
        )
        self._inv_repo.save(inv)

        # Build accept URL and send email
        accept_url = f"{self._base_url}/{locale}/accept-invite/{raw_token}"
        self._enqueue_invite_email(
            to=normalized_email,
            accept_url=accept_url,
            project_name=project.name,
            role_name=role.name,
            inviter_name=inviter.display_or_email,
            locale=locale,
        )

        return CreateInvitationResultDto(
            kind="invitation_sent",
            invitation_id=inv.id,
            expires_at=inv.expires_at,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _can_invite(user: Any, project_owner_id: UUID, inviter_id: UUID) -> bool:
        """Return True if the user holds any invitation-granting permission or is the project owner."""
        if user.has_permission("*", "*"):
            return True
        if user.has_permission("project", "invite"):
            return True
        # Project owner may always invite — compare UUIDs directly
        if project_owner_id == inviter_id:
            return True
        return False

    def _enqueue_invite_email(
        self,
        to: str,
        accept_url: str,
        project_name: str,
        role_name: str,
        inviter_name: str,
        locale: str,
    ) -> None:
        ctx = {
            "accept_url": accept_url,
            "project_name": project_name,
            "role_name": role_name,
            "inviter_name": inviter_name,
            "expires_in_days": 7,
        }
        subject, text, html = self._renderer.render("invite", locale, ctx)
        payload = EmailPayload(to=to, subject=subject, body=text, html_body=html)
        self._queue.enqueue("tasks.send_email", {"payload": payload})

    def _enqueue_added_email(
        self,
        to: str,
        project_name: str,
        inviter_name: str,
        role_name: str,
        locale: str,
    ) -> None:
        ctx = {
            "project_name": project_name,
            "inviter_name": inviter_name,
            "role_name": role_name,
            "accept_url": f"{self._base_url}/{locale}/dashboard",
        }
        subject, text, html = self._renderer.render("added_to_project", locale, ctx)
        payload = EmailPayload(to=to, subject=subject, body=text, html_body=html)
        self._queue.enqueue("tasks.send_email", {"payload": payload})
