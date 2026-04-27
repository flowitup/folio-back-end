"""Domain entity invariant tests for Invitation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.domain.entities.invitation import (
    Invitation,
    InvitationStatus,
    InvalidInvitationEmailError,
    _normalize_email,
)
from app.domain.exceptions.invitation_exceptions import (
    InvitationAlreadyAcceptedError,
    InvitationExpiredError,
    InvitationRevokedError,
)
from app.domain.value_objects.invite_token import hash_token


def _make_pending(*, ttl_days: int = 7) -> tuple[Invitation, str]:
    """Helper: create a PENDING invitation and return (entity, raw_token)."""
    return Invitation.create(
        email="test@example.com",
        project_id=uuid4(),
        role_id=uuid4(),
        invited_by=uuid4(),
        ttl_days=ttl_days,
    )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

class TestInvitationCreate:
    def test_returns_pending_status(self):
        inv, _ = _make_pending()
        assert inv.status == InvitationStatus.PENDING

    def test_token_hash_matches_helper(self):
        inv, raw_token = _make_pending()
        assert inv.token_hash == hash_token(raw_token)

    def test_raw_token_not_stored_on_entity(self):
        inv, raw_token = _make_pending()
        # Ensure raw token is NOT accessible as an attribute
        assert not hasattr(inv, "raw_token")
        assert raw_token not in str(inv)

    def test_expires_at_approximately_7_days_from_now(self):
        inv, _ = _make_pending(ttl_days=7)
        now = datetime.now(timezone.utc)
        delta = inv.expires_at - now
        assert timedelta(days=6, hours=23) < delta <= timedelta(days=7, minutes=1)


# ---------------------------------------------------------------------------
# is_usable
# ---------------------------------------------------------------------------

class TestIsUsable:
    def test_pending_within_ttl_is_usable(self):
        inv, _ = _make_pending()
        assert inv.is_usable() is True

    def test_pending_past_expiry_is_not_usable(self):
        inv, _ = _make_pending()
        from dataclasses import replace
        expired = replace(inv, expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        assert expired.is_usable() is False

    def test_accepted_is_not_usable(self):
        inv, _ = _make_pending()
        accepted = inv.accept()
        assert accepted.is_usable() is False

    def test_revoked_is_not_usable(self):
        inv, _ = _make_pending()
        revoked = inv.revoke()
        assert revoked.is_usable() is False


# ---------------------------------------------------------------------------
# accept() state transitions
# ---------------------------------------------------------------------------

class TestAccept:
    def test_accept_pending_returns_accepted_invitation(self):
        inv, _ = _make_pending()
        accepted = inv.accept()
        assert accepted.status == InvitationStatus.ACCEPTED
        assert accepted.accepted_at is not None

    def test_accept_revoked_raises(self):
        inv, _ = _make_pending()
        revoked = inv.revoke()
        with pytest.raises(InvitationRevokedError):
            revoked.accept()

    def test_accept_already_accepted_raises(self):
        inv, _ = _make_pending()
        accepted = inv.accept()
        with pytest.raises(InvitationAlreadyAcceptedError):
            accepted.accept()

    def test_accept_expired_raises(self):
        inv, _ = _make_pending()
        from dataclasses import replace
        expired = replace(inv, expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        with pytest.raises(InvitationExpiredError):
            expired.accept()


# ---------------------------------------------------------------------------
# revoke() — idempotent
# ---------------------------------------------------------------------------

class TestRevoke:
    def test_revoke_pending_returns_revoked(self):
        inv, _ = _make_pending()
        revoked = inv.revoke()
        assert revoked.status == InvitationStatus.REVOKED

    def test_revoke_already_revoked_is_idempotent(self):
        inv, _ = _make_pending()
        revoked = inv.revoke()
        # Should not raise
        double_revoked = revoked.revoke()
        assert double_revoked.status == InvitationStatus.REVOKED

    def test_revoke_accepted_idempotent(self):
        inv, _ = _make_pending()
        accepted = inv.accept()
        # revoke() is purely immutable — it should NOT raise
        result = accepted.revoke()
        assert result.status == InvitationStatus.REVOKED


# ---------------------------------------------------------------------------
# _normalize_email — malformed inputs
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "bad_email",
    [
        "not-an-email",
        "missing@",
        "@nodomain.com",
        "two@@signs.com",
        "",
        "spaces in@email.com",
        "no-dot-domain@host",
    ],
)
def test_normalize_email_rejects_malformed(bad_email: str):
    with pytest.raises(InvalidInvitationEmailError):
        _normalize_email(bad_email)


def test_normalize_email_normalizes_case():
    result = _normalize_email("  User@Example.COM  ")
    assert result == "user@example.com"
