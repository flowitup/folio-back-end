"""Reading a chat channel also reads its entries in the bell, so one message is not counted twice."""

from __future__ import annotations

from uuid import uuid4

from app.application.chat.usecases import MarkChannelReadUseCase


class Directory:
    def channel_exists(self, channel):
        return True

    def is_member(self, actor_id, channel):
        return True


class Reads:
    def __init__(self) -> None:
        self.marked: list = []

    def mark_read(self, user_id, channel, at):
        self.marked.append((user_id, channel.key, at))


class Session:
    def commit(self):
        pass


class Bell:
    def __init__(self) -> None:
        self.calls: list = []

    def channel_read(self, *, channel_key, user_id, read_at):
        self.calls.append((channel_key, user_id, read_at))


def test_marking_a_channel_read_clears_its_bell_entries():
    reads, bell = Reads(), Bell()
    usecase = MarkChannelReadUseCase(Directory(), reads, Session(), bell=bell)
    user, key = uuid4(), f"project:{uuid4()}"
    usecase.execute(actor_id=user, channel_key=key)
    assert bell.calls == [(key, user, reads.marked[0][2])]


def test_without_a_bell_it_only_moves_the_marker():
    reads = Reads()
    MarkChannelReadUseCase(Directory(), reads, Session()).execute(actor_id=uuid4(), channel_key=f"company:{uuid4()}")
    assert len(reads.marked) == 1
