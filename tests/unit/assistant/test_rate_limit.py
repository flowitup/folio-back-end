"""Unit tests for `app.infrastructure.ai.rate_limit` — the per-user rolling-hour pipeline
run limiter (phase 05 hardening). `RedisRateLimiter` needs a real Redis; its rolling-
window logic is identical to `InMemoryRateLimiter`'s (both build on the same
`_current_and_previous_keys` helper), so the behavioural contract is exercised here
against the in-memory implementation only — the same one `AssistantService` uses in tests.
"""

from __future__ import annotations

from uuid import uuid4

from app.infrastructure.ai.rate_limit import InMemoryRateLimiter


def test_allows_up_to_the_limit_then_refuses() -> None:
    limiter = InMemoryRateLimiter(limit_per_hour=3)
    user_id = uuid4()

    assert limiter.allow(user_id) is True
    assert limiter.allow(user_id) is True
    assert limiter.allow(user_id) is True
    assert limiter.allow(user_id) is False


def test_is_scoped_per_user() -> None:
    limiter = InMemoryRateLimiter(limit_per_hour=1)
    alice = uuid4()
    bob = uuid4()

    assert limiter.allow(alice) is True
    assert limiter.allow(alice) is False
    assert limiter.allow(bob) is True


def test_default_limit_is_thirty_per_hour() -> None:
    limiter = InMemoryRateLimiter()
    user_id = uuid4()

    for _ in range(30):
        assert limiter.allow(user_id) is True
    assert limiter.allow(user_id) is False


def test_refused_calls_stay_refused_and_do_not_error() -> None:
    """A refusal must be idempotent — hammering the endpoint after the limit is hit
    keeps refusing (never flips back to allowed, never raises)."""
    limiter = InMemoryRateLimiter(limit_per_hour=1)
    user_id = uuid4()

    assert limiter.allow(user_id) is True
    for _ in range(10):
        assert limiter.allow(user_id) is False
