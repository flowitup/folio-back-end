"""Rotated refresh cookies: a 60 s grace from the first rotation, cut short by a revoke.

Covers both stores: the Redis one (through a minimal fake) and the in-memory fallback.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.infrastructure.adapters import jwt_issuer
from app.infrastructure.adapters.jwt_issuer import ROTATION_GRACE_SECONDS, JWTTokenIssuer


class _FakeRedis:
    """The few Redis calls the issuer makes, with TTLs read against the issuer's clock."""

    def __init__(self):
        self.data: dict[str, tuple[bytes, float | None]] = {}

    def _alive(self, key):
        value, expires = self.data.get(key, (None, None))
        if value is not None and expires is not None and jwt_issuer._now() >= expires:
            del self.data[key]
            return None
        return value

    def set(self, key, value, nx=False, ex=None):
        if nx and self._alive(key) is not None:
            return None
        self.data[key] = (str(value).encode(), jwt_issuer._now() + ex if ex else None)
        return True

    def setex(self, key, ttl, value):
        return self.set(key, value, ex=ttl)

    def get(self, key):
        return self._alive(key)

    def mget(self, *keys):
        return [self._alive(k) for k in keys]


@pytest.fixture(params=["redis", "memory"])
def issuer(request, monkeypatch):
    clock = {"now": 1_000_000.0}
    monkeypatch.setattr(jwt_issuer, "_now", lambda: clock["now"])
    issuer = JWTTokenIssuer()
    if request.param == "redis":
        issuer._redis = _FakeRedis()
    issuer.clock = clock
    return issuer


def test_a_rotated_token_works_until_the_grace_ends(issuer):
    old, new = str(uuid4()), str(uuid4())
    issuer.retire_rotated_token(old, successor_jti=new)
    assert not issuer.is_token_revoked(old)
    issuer.clock["now"] += ROTATION_GRACE_SECONDS - 1
    assert not issuer.is_token_revoked(old)
    issuer.clock["now"] += 1
    assert issuer.is_token_revoked(old)
    assert not issuer.is_token_revoked(new)


def test_reusing_it_in_the_window_does_not_move_the_deadline(issuer):
    old = str(uuid4())
    issuer.retire_rotated_token(old, successor_jti=str(uuid4()))
    issuer.clock["now"] += 50
    issuer.retire_rotated_token(old, successor_jti=str(uuid4()))
    issuer.clock["now"] += 10
    assert issuer.is_token_revoked(old)


def test_revoking_the_successor_revokes_the_rotated_one_at_once(issuer):
    first, second, third = str(uuid4()), str(uuid4()), str(uuid4())
    issuer.retire_rotated_token(first, successor_jti=second)
    issuer.retire_rotated_token(second, successor_jti=third)
    issuer.revoke_token(third, token_type="refresh")
    assert issuer.is_token_revoked(third)
    assert issuer.is_token_revoked(second) and issuer.is_token_revoked(first)


def test_revoking_the_rotated_one_itself_is_immediate(issuer):
    old, new = str(uuid4()), str(uuid4())
    issuer.retire_rotated_token(old, successor_jti=new)
    issuer.revoke_token(old, token_type="refresh")
    assert issuer.is_token_revoked(old)
    assert not issuer.is_token_revoked(new)
