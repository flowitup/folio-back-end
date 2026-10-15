"""Sliding-window budgets of outgoing SMS: Redis in production, in memory for tests.

Both windows (``max_per_10min`` and ``max_per_day``) must have room for a send to be admitted; a
limit of 0 switches that window off. The Redis budget FAILS OPEN: a Redis outage must not stop every
sign-in, so it logs and lets the send through (the per-number and per-address limits still apply).
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Optional
from uuid import uuid4

import redis

logger = logging.getLogger(__name__)

SHORT_WINDOW_SECONDS = 600
DAY_WINDOW_SECONDS = 86400
_KEY = "sms_budget:global"


class InMemorySmsBudget:
    """Single-process budget (tests, and a fake for code that needs a ``SmsBudgetPort``)."""

    def __init__(self, max_per_10min: int, max_per_day: int, clock: Callable[[], float] = time.time) -> None:
        self._max_short = max_per_10min
        self._max_day = max_per_day
        self._clock = clock
        self._sent: Deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> int:
        now = self._clock()
        with self._lock:
            while self._sent and self._sent[0] <= now - DAY_WINDOW_SECONDS:
                self._sent.popleft()
            wait = max(
                self._wait(now, SHORT_WINDOW_SECONDS, self._max_short),
                self._wait(now, DAY_WINDOW_SECONDS, self._max_day),
            )
            if wait == 0:
                self._sent.append(now)
            return wait

    def _wait(self, now: float, window: int, limit: int) -> int:
        inside = [t for t in self._sent if t > now - window]
        if limit <= 0 or len(inside) < limit:
            return 0
        # Room appears when the send that is `limit` places from the newest one leaves the window.
        return max(1, math.ceil(inside[len(inside) - limit] + window - now))


class RedisSmsBudget:
    """Budget shared by every API worker: a sorted set of send times, trimmed to the last day."""

    def __init__(
        self,
        redis_url: str,
        max_per_10min: int,
        max_per_day: int,
        clock: Callable[[], float] = time.time,
        client: Optional["redis.Redis"] = None,
    ) -> None:
        self._max_short = max_per_10min
        self._max_day = max_per_day
        self._clock = clock
        # Short timeouts: a dead Redis must not hold a worker for the length of a send.
        self._redis = client or redis.from_url(redis_url, socket_connect_timeout=1, socket_timeout=1)

    def acquire(self) -> int:
        try:
            return self._acquire()
        except redis.RedisError as exc:
            logger.warning("sms_budget.unavailable_fail_open error=%s", exc)
            return 0

    def _acquire(self) -> int:
        now_ms = int(self._clock() * 1000)
        pipe = self._redis.pipeline()
        pipe.zremrangebyscore(_KEY, 0, now_ms - DAY_WINDOW_SECONDS * 1000)
        pipe.zcount(_KEY, f"({now_ms - SHORT_WINDOW_SECONDS * 1000}", "+inf")
        pipe.zcard(_KEY)
        _, in_short, in_day = pipe.execute()
        wait = max(
            self._wait(now_ms, in_short, SHORT_WINDOW_SECONDS, self._max_short),
            self._wait(now_ms, in_day, DAY_WINDOW_SECONDS, self._max_day),
        )
        if wait == 0:
            self._redis.zadd(_KEY, {f"{now_ms}-{uuid4().hex[:8]}": now_ms})
            self._redis.pexpire(_KEY, DAY_WINDOW_SECONDS * 1000)
        return wait

    def _wait(self, now_ms: int, inside: int, window: int, limit: int) -> int:
        if limit <= 0 or inside < limit:
            return 0
        # Room appears when the send that is `limit` places from the newest one leaves the window.
        leaving: Any = self._redis.zrangebyscore(
            _KEY, f"({now_ms - window * 1000}", "+inf", start=inside - limit, num=1, withscores=True
        )
        if not leaving:
            return 1
        return max(1, math.ceil((float(leaving[0][1]) + window * 1000 - now_ms) / 1000))
