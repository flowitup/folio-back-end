"""CreateInvitationUseCase — orchestrates the invitation-or-direct-add flow."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from app.application.invitations.authz import can_manage_project_invites
from app.application.invitations.dtos import CreateInvitationResultDto
from app.application.invitations.exceptions import (
    PermissionDeniedError,
    RateLimitedError,
    ProjectNotFoundError,
)
from app.application.invitations.ports import (
    InvitationRepositoryPort,
    ProjectMembershipRepositoryPort,
    ProjectRepositoryPort,
    TransactionalSessionPort,
    UserWriteRepositoryPort,
)
from app.domain.companies.roles import CompanyRole
from app.domain.companies.user_company_access import UserCompanyAccess
from app.domain.entities.invitation import Invitation
from app.domain.entities.project_membership import ProjectMembership
from tasks import EmailPayload


class CreateInvitationUseCase:
    """Send an invitation (or directly add an existing user) to a project."""

    def __init__(
        self,
        invitation_repo: InvitationRepositoryPort,
        project_membership_repo: ProjectMembershipRepositoryPort,
        user_repo: UserWriteRepositoryPort,
        project_repo: ProjectRepositoryPort,
        email_port: Any,  # EmailAdapterPort with .send(EmailPayload)
        email_renderer: Any,  # EmailRenderer with .render(template, locale, ctx)
        queue_port: Any,  # QueuePort with .enqueue(task_name, payload)
        app_base_url: str,
        db_session: TransactionalSessionPort,
        project_invite_daily_cap: int = 50,
        authz_reader: Any = None,  # AuthzReaderPort — resolves project:invite
        access_repo: Any = None,  # UserCompanyAccessRepositoryPort — company attachment
    ) -> None:
        self._inv_repo = invitation_repo
        self._membership_repo = project_membership_repo
        self._user_repo = user_repo
        self._project_repo = project_repo
        self._email_port = email_port
        self._renderer = email_renderer
        self._queue = queue_port
        self._base_url = app_base_url.rstrip("/")
        self._db = db_session
        self._daily_cap = project_invite_daily_cap
        self._authz_reader = authz_reader
        self._access_repo = access_repo

    def set_authz_reader(self, reader: Any) -> None:
        """Inject the resolver read port after construction.

        `wiring.configure_container()` builds this use-case before the
        SQLAlchemy-backed reader exists; `app/__init__.py` calls this once it does.
        """
        self._authz_reader = reader

    def set_access_repo(self, access_repo: Any) -> None:
        """Inject the company-access repository after construction (same reason)."""
        self._access_repo = access_repo

    # ------------------------------------------------------------------

    # Accepting an invitation makes the invitee a `member` of the project's
    # company; the invite itself never carries a role.
    _GRANTED_ROLE = CompanyRole.MEMBER.value

    def execute(
        self,
        inviter_id: UUID,
        project_id: UUID,
        email: str,
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

        # Verify permission through the resolver (no owner bypass, D6).
        if not self._can_invite(inviter, project.owner_id, inviter_id, project_id):
            raise PermissionDeniedError(f"User {inviter_id} does not have 'project:invite' permission.")

        # 3. Normalize email
        normalized_email = email.strip().lower()

        # 4. Existing-user fast-path
        #
        # SECURITY/UX NOTE — kind discriminator leak (H3 from code-review):
        # The DTO returned below carries `kind="direct_added"` for existing emails
        # and `kind="invitation_sent"` for new ones. This leaks user-existence to
        # the authenticated admin/owner. Accepted within the admin trust boundary
        # because admins can already enumerate users via the /projects/<id>/members
        # endpoint and via project membership lists. Do NOT expose this discriminator
        # on any public-facing endpoint.
        existing_user = self._user_repo.find_by_email(normalized_email)
        if existing_user is not None:
            if not self._membership_repo.exists(existing_user.id, project_id):
                # Not yet assigned — assign + send notification email.
                membership = ProjectMembership.create(
                    user_id=existing_user.id,
                    project_id=project_id,
                    invited_by=inviter_id,
                )
                self._membership_repo.add(membership)
                # Permissions resolve through the company: an existing user who
                # belongs to another company (or to none) would land on the
                # project with zero permissions. Attach them as `member`, the
                # same grant accepting an invitation gives.
                self._attach_to_project_company(existing_user.id, project_id)
                # H2 — commit BEFORE enqueueing the email so the queue write only
                # happens after persistence is durable. If commit raises, no email
                # goes out for a membership that didn't land.
                self._db.commit()
                self._enqueue_added_email(
                    to=existing_user.email,
                    project_name=project.name,
                    inviter_name=inviter.display_or_email,
                    role_name=self._GRANTED_ROLE,
                    locale=locale,
                )
            # else: already assigned → idempotent no-op (don't re-send the email).
            return CreateInvitationResultDto(kind="direct_added", user_id=existing_user.id)

        # 5. Invitation flow — enforce daily cap
        daily_count = self._inv_repo.count_created_today_by_project(project_id)
        if daily_count >= self._daily_cap:
            raise RateLimitedError(
                f"Project {project_id} has reached the daily invitation limit " f"({self._daily_cap})."
            )

        # Revoke any existing pending invitation for same email+project
        pending = self._inv_repo.find_pending_by_email_and_project(normalized_email, project_id)
        if pending is not None:
            revoked = pending.revoke()
            self._inv_repo.save(revoked)

        # Create new invitation
        inv, raw_token = Invitation.create(
            email=normalized_email,
            project_id=project_id,
            invited_by=inviter_id,
        )
        self._inv_repo.save(inv)
        # H2 — commit BEFORE enqueueing so the email only fires for invitations
        # that persisted. If commit raises, no token email leaks.
        self._db.commit()

        # Build accept URL and send email
        accept_url = f"{self._base_url}/{locale}/accept-invite/{raw_token}"
        self._enqueue_invite_email(
            to=normalized_email,
            accept_url=accept_url,
            project_name=project.name,
            role_name=self._GRANTED_ROLE,
            inviter_name=inviter.display_or_email,
            locale=locale,
        )

        return CreateInvitationResultDto(
            kind="invitation_sent",
            invitation_id=inv.id,
            expires_at=inv.expires_at,
        )

    def _attach_to_project_company(self, user_id: UUID, project_id: UUID) -> None:
        """Make the directly-added user a `member` of the project's company.

        No-op when they are already attached (their existing role is kept) or
        when the deployment wired neither reader nor access repository.
        """
        if self._authz_reader is None or self._access_repo is None:
            return
        company_id = self._authz_reader.project_company_id(project_id)
        if company_id is None or self._access_repo.find(user_id, company_id) is not None:
            return
        self._access_repo.save(
            UserCompanyAccess(
                user_id=user_id,
                company_id=company_id,
                is_primary=len(self._access_repo.list_for_user(user_id)) == 0,
                attached_at=datetime.now(timezone.utc),
                role=self._GRANTED_ROLE,
            )
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _can_invite(self, user: Any, project_owner_id: UUID, inviter_id: UUID, project_id: UUID) -> bool:
        """Return True when the resolver grants `project:invite` on this project.

        Company admins hold it on every project of their company, an assigned
        manager on theirs, and a member only through an explicit D8 grant.
        There is no owner bypass (D6): without a reader wired this fails
        closed, since the route that calls this use-case has already made the
        same check.
        """
        return can_manage_project_invites(self._authz_reader, inviter_id, project_id)

    def _enqueue_invite_email(
        self,
        to: str,
        accept_url: str,
        project_name: str,
        role_name: str,
        inviter_name: str,
        locale: str,
    ) -> None:
        """Render + enqueue the invite email. Caller MUST commit before invoking (H2)."""
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
        """Render + enqueue the 'added directly' email. Caller MUST commit before invoking (H2)."""
        ctx = {
            "project_name": project_name,
            "inviter_name": inviter_name,
            "role_name": role_name,
            "accept_url": f"{self._base_url}/{locale}/dashboard",
        }
        subject, text, html = self._renderer.render("added_to_project", locale, ctx)
        payload = EmailPayload(to=to, subject=subject, body=text, html_body=html)
        self._queue.enqueue("tasks.send_email", {"payload": payload})
