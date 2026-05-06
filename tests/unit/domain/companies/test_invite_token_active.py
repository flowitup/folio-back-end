"""Unit tests for CompanyInviteToken.is_active matrix."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.domain.companies.invite_token import CompanyInviteToken


def _make_token(**overrides) -> CompanyInviteToken:
    now = datetime.now(timezone.utc)
    defaults = dict(
        id=uuid4(),
        company_id=uuid4(),
        token_hash="argon2_abc",
        created_by=uuid4(),
        created_at=now,
        expires_at=now + timedelta(days=7),
        redeemed_at=None,
        redeemed_by=None,
    )
    defaults.update(overrides)
    return CompanyInviteToken(**defaults)


class TestIsActive:
    def test_active_when_not_expired_not_redeemed(self):
        now = datetime.now(timezone.utc)
        t = _make_token(expires_at=now + timedelta(hours=1))
        assert t.is_active(now) is True

    def test_inactive_when_expired(self):
        now = datetime.now(timezone.utc)
        t = _make_token(expires_at=now - timedelta(seconds=1))
        assert t.is_active(now) is False

    def test_inactive_when_redeemed(self):
        now = datetime.now(timezone.utc)
        t = _make_token(redeemed_at=now - timedelta(hours=1), redeemed_by=uuid4())
        assert t.is_active(now) is False

    def test_inactive_when_expired_and_redeemed(self):
        now = datetime.now(timezone.utc)
        t = _make_token(
            expires_at=now - timedelta(days=1),
            redeemed_at=now - timedelta(hours=1),
            redeemed_by=uuid4(),
        )
        assert t.is_active(now) is False

    def test_expires_at_boundary_is_expired(self):
        """expires_at == now means expired (not active)."""
        now = datetime.now(timezone.utc)
        t = _make_token(expires_at=now)
        assert t.is_expired(now) is True
        assert t.is_active(now) is False

    def test_one_second_before_expiry_is_active(self):
        now = datetime.now(timezone.utc)
        t = _make_token(expires_at=now + timedelta(seconds=1))
        assert t.is_active(now) is True


class TestIsRedeemed:
    def test_not_redeemed_when_redeemed_at_none(self):
        t = _make_token(redeemed_at=None, redeemed_by=None)
        assert t.is_redeemed is False

    def test_redeemed_when_redeemed_at_set(self):
        t = _make_token(
            redeemed_at=datetime.now(timezone.utc),
            redeemed_by=uuid4(),
        )
        assert t.is_redeemed is True


class TestWithUpdates:
    def test_mark_redeemed(self):
        now = datetime.now(timezone.utc)
        redeemer = uuid4()
        t = _make_token()
        updated = t.with_updates(redeemed_at=now, redeemed_by=redeemer)
        assert updated.is_redeemed is True
        assert updated.redeemed_by == redeemer
        assert t.is_redeemed is False  # original unchanged

    def test_equality_by_id(self):
        t_id = uuid4()
        t1 = _make_token(id=t_id)
        t2 = _make_token(id=t_id, redeemed_at=datetime.now(timezone.utc), redeemed_by=uuid4())
        assert t1 == t2

    def test_not_equal_to_non_token(self):
        t = _make_token()
        assert t.__eq__("not a token") is NotImplemented
