"""Unit tests for VerifyInvitationUseCase."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.application.invitations.verify_invitation_usecase import VerifyInvitationUseCase
from app.domain.entities.invitation import Invitation, InvitationStatus
from app.domain.exceptions.invitation_exceptions import (
    InvitationAlreadyAcceptedError,
    InvitationExpiredError,
    InvitationRevokedError,
    InvalidInvitationTokenError,
)
from app.domain.value_objects.invite_token import generate_token, hash_token

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_inv(
    *, status: InvitationStatus = InvitationStatus.PENDING, past_expiry: bool = False
) -> tuple[Invitation, str]:
    raw, token_hash = generate_token()
    now = datetime.now(timezone.utc)
    expires_at = now - timedelta(hours=1) if past_expiry else now + timedelta(days=7)
    inv = Invitation(
        id=uuid4(),
        email="user@example.com",
        project_id=uuid4(),
        role_id=uuid4(),
        token_hash=token_hash,
        status=status,
        expires_at=expires_at,
        invited_by=uuid4(),
        created_at=now,
        updated_at=now,
    )
    return inv, raw


def _make_uc(inv_repo, project_repo=None, role_repo=None, user_repo=None) -> VerifyInvitationUseCase:
    project_repo = project_repo or MagicMock()
    role_repo = role_repo or MagicMock()
    user_repo = user_repo or MagicMock()
    # Default mock returns for related entities
    project_repo.find_by_id.return_value = MagicMock(name="Test Project")
    role_repo.find_by_id.return_value = MagicMock(name="member")
    user_repo.find_by_id.return_value = MagicMock(display_or_email="Inviter Name")
    return VerifyInvitationUseCase(
        invitation_repo=inv_repo,
        project_repo=project_repo,
        role_repo=role_repo,
        user_repo=user_repo,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestVerifyInvitation:
    def test_valid_token_returns_dto_without_invitation_id(self):
        inv, raw_token = _make_inv()
        inv_repo = MagicMock()
        inv_repo.find_by_token_hash.return_value = inv

        project = MagicMock()
        project.name = "My Project"
        project_repo = MagicMock()
        project_repo.find_by_id.return_value = project

        role = MagicMock()
        role.name = "member"
        role_repo = MagicMock()
        role_repo.find_by_id.return_value = role

        inviter = MagicMock()
        inviter.display_or_email = "Boss"
        user_repo = MagicMock()
        user_repo.find_by_id.return_value = inviter

        uc = VerifyInvitationUseCase(
            invitation_repo=inv_repo,
            project_repo=project_repo,
            role_repo=role_repo,
            user_repo=user_repo,
        )
        dto = uc.execute(raw_token)

        assert dto.email == "user@example.com"
        assert dto.project_name == "My Project"
        assert dto.role_name == "member"
        assert dto.inviter_name == "Boss"
        # DTO must not expose invitation_id (VerifyInvitationDto has no invitation_id field)
        assert not hasattr(dto, "invitation_id")

    def test_not_found_raises_invalid_token_error(self):
        inv_repo = MagicMock()
        inv_repo.find_by_token_hash.return_value = None
        uc = _make_uc(inv_repo)

        with pytest.raises(InvalidInvitationTokenError):
            uc.execute("totally-unknown-token")

    def test_expired_pending_flips_status_and_raises(self):
        inv, raw_token = _make_inv(past_expiry=True)
        assert inv.status == InvitationStatus.PENDING
        inv_repo = MagicMock()
        inv_repo.find_by_token_hash.return_value = inv
        uc = _make_uc(inv_repo)

        with pytest.raises(InvitationExpiredError):
            uc.execute(raw_token)

        # Status flip should have been persisted
        inv_repo.save.assert_called_once()
        saved = inv_repo.save.call_args[0][0]
        assert saved.status == InvitationStatus.EXPIRED

    def test_already_expired_status_raises(self):
        inv, raw_token = _make_inv(status=InvitationStatus.EXPIRED)
        inv_repo = MagicMock()
        inv_repo.find_by_token_hash.return_value = inv
        uc = _make_uc(inv_repo)

        with pytest.raises(InvitationExpiredError):
            uc.execute(raw_token)

    def test_revoked_raises(self):
        inv, raw_token = _make_inv(status=InvitationStatus.REVOKED)
        inv_repo = MagicMock()
        inv_repo.find_by_token_hash.return_value = inv
        uc = _make_uc(inv_repo)

        with pytest.raises(InvitationRevokedError):
            uc.execute(raw_token)

    def test_already_accepted_raises(self):
        inv, raw_token = _make_inv(status=InvitationStatus.ACCEPTED)
        inv_repo = MagicMock()
        inv_repo.find_by_token_hash.return_value = inv
        uc = _make_uc(inv_repo)

        with pytest.raises(InvitationAlreadyAcceptedError):
            uc.execute(raw_token)

    def test_hash_lookup_uses_correct_hash(self):
        """Ensure the use-case hashes the raw token before querying the repo."""
        inv, raw_token = _make_inv()
        inv_repo = MagicMock()
        inv_repo.find_by_token_hash.return_value = inv
        uc = _make_uc(inv_repo)
        uc.execute(raw_token)

        called_hash = inv_repo.find_by_token_hash.call_args[0][0]
        assert called_hash == hash_token(raw_token)
