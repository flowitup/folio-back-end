"""Global SMS budget: sliding windows, a Redis adapter that fails open, and the sender that spends it."""

from __future__ import annotations

import logging

import pytest
import redis

from app.domain.exceptions.auth_exceptions import OtpThrottledError
from app.infrastructure.adapters.budgeted_sms_sender import BudgetedSmsSender
from app.infrastructure.adapters.sms_budget import InMemorySmsBudget, RedisSmsBudget


class Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


def _above(low: str) -> int:
    assert low.startswith("(")  # exclusive bound: a send exactly one window old no longer counts
    return int(low[1:])


class FakeRedis:
    """Just the sorted-set commands RedisSmsBudget uses."""

    def __init__(self) -> None:
        self.zset: dict[str, int] = {}

    def pipeline(self):
        return _Pipeline(self)

    def zremrangebyscore(self, _key, low, high):
        self.zset = {m: s for m, s in self.zset.items() if not (low <= s <= high)}

    def zcount(self, _key, low, _high):
        return sum(1 for s in self.zset.values() if s > _above(low))

    def zcard(self, _key):
        return len(self.zset)

    def zadd(self, _key, mapping):
        self.zset.update(mapping)

    def pexpire(self, _key, _ms):
        pass

    def zrangebyscore(self, _key, low, _high, start=0, num=None, withscores=False):
        rows = sorted(((m, s) for m, s in self.zset.items() if s > _above(low)), key=lambda r: r[1])
        return [(m, float(s)) for m, s in rows[start : start + num]]


class _Pipeline:
    def __init__(self, fake: FakeRedis) -> None:
        self._fake, self._calls = fake, []

    def __getattr__(self, name):
        def queue(*args):
            self._calls.append((name, args))
            return self

        return queue

    def execute(self):
        return [getattr(self._fake, name)("k", *args[1:]) for name, args in self._calls]


class DownRedis:
    def pipeline(self):
        raise redis.ConnectionError("connection refused")


def _budgets(clock: Clock, per_10min: int, per_day: int):
    return [
        InMemorySmsBudget(per_10min, per_day, clock=clock),
        RedisSmsBudget("redis://unused", per_10min, per_day, clock=clock, client=FakeRedis()),
    ]


@pytest.fixture(params=["memory", "redis"])
def make_budget(request):
    def make(clock: Clock, per_10min: int, per_day: int):
        return _budgets(clock, per_10min, per_day)[0 if request.param == "memory" else 1]

    return make


def test_sends_past_the_ten_minute_window_wait_for_the_oldest_to_leave(make_budget):
    clock = Clock()
    budget = make_budget(clock, 3, 100)
    waits = []
    for _ in range(3):
        waits.append(budget.acquire())
        clock.now += 100
    assert waits == [0, 0, 0]
    assert budget.acquire() == 300  # the first send leaves the window 600 s after it, 300 s from now
    clock.now += 300
    assert budget.acquire() == 0


def test_refused_sends_are_not_counted(make_budget):
    clock = Clock()
    budget = make_budget(clock, 1, 100)
    assert budget.acquire() == 0
    for _ in range(5):
        assert budget.acquire() == 600
    clock.now += 600
    assert budget.acquire() == 0


def test_the_daily_window_is_enforced_when_the_short_one_has_room(make_budget):
    clock = Clock()
    budget = make_budget(clock, 100, 4)
    for _ in range(4):
        assert budget.acquire() == 0
        clock.now += 1000
    wait = budget.acquire()
    assert wait == 86400 - 4000
    clock.now += wait
    assert budget.acquire() == 0


def test_zero_switches_a_window_off(make_budget):
    clock = Clock()
    budget = make_budget(clock, 0, 0)
    assert all(budget.acquire() == 0 for _ in range(500))


def test_redis_budget_fails_open_and_logs(caplog):
    budget = RedisSmsBudget("redis://unused", 1, 1, client=DownRedis())
    with caplog.at_level(logging.WARNING):
        assert budget.acquire() == 0
        assert budget.acquire() == 0
    assert "sms_budget.unavailable_fail_open" in caplog.text


class RecordingSender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, to: str, text: str) -> None:
        self.sent.append((to, text))


def test_sender_stops_at_the_budget_with_the_throttling_error(caplog):
    clock = Clock()
    inner = RecordingSender()
    sender = BudgetedSmsSender(inner, InMemorySmsBudget(2, 100, clock=clock))
    sender.send("+33611000001", "a")
    sender.send("+33611000002", "b")
    with caplog.at_level(logging.WARNING), pytest.raises(OtpThrottledError) as exc:
        sender.send("+33611000003", "c")
    assert exc.value.retry_after_seconds == 600 and exc.value.hourly_limit is True
    assert [to for to, _ in inner.sent] == ["+33611000001", "+33611000002"]
    assert "sms_budget.exceeded" in caplog.text
