"""Per-user rolling-hour rate limiter (``RateLimiterPort``) — phase 05 hardening.

``AssistantService.handle_message`` refuses a pipeline run once a user has made
``LIMIT_PER_HOUR`` runs across "this hour and the previous one" — a genuine rolling
window, not a fixed clock-hour bucket that would let someone burst 30 requests at
23:59 and another 30 at 00:00. Each hour bucket is its own Redis key
(``assistant:rate:<user_id>:<YYYYMMDDHH>``, Europe/Paris) with a 2h TTL so stale
buckets clean themselves up without a background job.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import cast
from uuid import UUID
from zoneinfo import ZoneInfo

#: Plan's own hardening bar: at most this many `handle_message` runs per user per
#: rolling hour before the pipeline refuses with the "rate_limited" template.
LIMIT_PER_HOUR = 30

_PARIS = ZoneInfo("Europe/Paris")
#: Comfortably covers "current + previous hour bucket" even under clock/replication
#: skew; buckets are keyed by hour so this only affects when Redis reclaims the key.
_TTL_SECONDS = 60 * 60 * 2


def _bucket_key(user_id: UUID, when: datetime) -> str:
    return f"assistant:rate:{user_id}:{when.strftime('%Y%m%d%H')}"


def _current_and_previous_keys(user_id: UUID) -> tuple[str, str]:
    now = datetime.now(_PARIS)
    return _bucket_key(user_id, now), _bucket_key(user_id, now - timedelta(hours=1))


class RedisRateLimiter:
    """Implements RateLimiterPort with two Redis integer counters (current/previous hour)."""

    def __init__(self, redis_url: str, limit_per_hour: int = LIMIT_PER_HOUR) -> None:
        from redis import Redis

        self._redis = Redis.from_url(redis_url)
        self._limit = limit_per_hour

    def allow(self, user_id: UUID) -> bool:
        current_key, previous_key = _current_and_previous_keys(user_id)
        current = int(cast(bytes, self._redis.get(current_key)) or 0)
        previous = int(cast(bytes, self._redis.get(previous_key)) or 0)
        if current + previous >= self._limit:
            return False
        pipe = self._redis.pipeline()
        pipe.incr(current_key)
        pipe.expire(current_key, _TTL_SECONDS)
        pipe.execute()
        return True


class InMemoryRateLimiter:
    """Test/dev RateLimiterPort — no Redis, resets on process restart.

    Mirrors ``RedisRateLimiter``'s rolling-window semantics (current + previous
    Paris-local hour bucket) against a plain in-memory dict instead of Redis keys.
    """

    def __init__(self, limit_per_hour: int = LIMIT_PER_HOUR) -> None:
        self._limit = limit_per_hour
        self._counts: dict[str, int] = {}

    def allow(self, user_id: UUID) -> bool:
        current_key, previous_key = _current_and_previous_keys(user_id)
        current = self._counts.get(current_key, 0)
        previous = self._counts.get(previous_key, 0)
        if current + previous >= self._limit:
            return False
        self._counts[current_key] = current + 1
        return True


__all__ = ["RedisRateLimiter", "InMemoryRateLimiter", "LIMIT_PER_HOUR"]
