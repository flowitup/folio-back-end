"""Unit tests for PushDispatcher — the cross-cutting rules every notifier inherits."""

from __future__ import annotations

from uuid import uuid4

from app.application.push.dispatcher import PushDispatcher

CATEGORY = "chat"


class RecordingSender:
    def __init__(self) -> None:
        self.sent: list = []
        self.raise_on_send = False

    def send(self, messages, on_invalid_token=None):
        if self.raise_on_send:
            raise RuntimeError("provider down")
        self.sent.extend(messages)


class StubDevices:
    def __init__(self, tokens=None) -> None:
        self._tokens = tokens or {}
        self.forgotten: list = []

    def tokens_for_users(self, user_ids):
        return {u: self._tokens[u] for u in user_ids if u in self._tokens}

    def delete_token(self, token):
        self.forgotten.append(token)


class StubPreferences:
    def __init__(self, muted=None, explode=False) -> None:
        self._muted = muted or set()
        self._explode = explode

    def muted_user_ids(self, user_ids, category):
        if self._explode:
            raise RuntimeError("preferences unavailable")
        return {u for u in user_ids if u in self._muted}


def _dispatcher(devices, sender, preferences=None):
    return PushDispatcher(devices=devices, sender=sender, preferences=preferences, locale="en", run_async=False)


def _dispatch(d, recipients, exclude=None):
    d.dispatch(
        category=CATEGORY,
        recipients=recipients,
        title="t",
        body="b",
        data={"kind": "chat_message"},
        exclude=exclude,
    )


def test_sends_one_message_per_token():
    alice, bob = uuid4(), uuid4()
    devices = StubDevices({alice: ["tok-a1", "tok-a2"], bob: ["tok-b"]})
    sender = RecordingSender()
    _dispatch(_dispatcher(devices, sender), [alice, bob])
    assert sorted(m.token for m in sender.sent) == ["tok-a1", "tok-a2", "tok-b"]


def test_actor_is_never_notified_about_their_own_action():
    actor, other = uuid4(), uuid4()
    devices = StubDevices({actor: ["tok-actor"], other: ["tok-other"]})
    sender = RecordingSender()
    _dispatch(_dispatcher(devices, sender), [actor, other], exclude=actor)
    assert [m.token for m in sender.sent] == ["tok-other"]


def test_muted_recipients_are_dropped_before_token_lookup():
    muted, allowed = uuid4(), uuid4()
    devices = StubDevices({muted: ["tok-muted"], allowed: ["tok-allowed"]})
    sender = RecordingSender()
    d = _dispatcher(devices, sender, StubPreferences(muted={muted}))
    _dispatch(d, [muted, allowed])
    assert [m.token for m in sender.sent] == ["tok-allowed"]


def test_everyone_muted_sends_nothing():
    muted = uuid4()
    devices = StubDevices({muted: ["tok"]})
    sender = RecordingSender()
    _dispatch(_dispatcher(devices, sender, StubPreferences(muted={muted})), [muted])
    assert sender.sent == []


def test_preference_lookup_failure_does_not_silence_the_notification():
    user = uuid4()
    devices = StubDevices({user: ["tok"]})
    sender = RecordingSender()
    _dispatch(_dispatcher(devices, sender, StubPreferences(explode=True)), [user])
    assert [m.token for m in sender.sent] == ["tok"]


def test_provider_failure_never_surfaces():
    user = uuid4()
    sender = RecordingSender()
    sender.raise_on_send = True
    _dispatch(_dispatcher(StubDevices({user: ["tok"]}), sender), [user])  # must not raise


def test_no_recipients_and_no_tokens_are_no_ops():
    sender = RecordingSender()
    d = _dispatcher(StubDevices({}), sender)
    _dispatch(d, [])
    _dispatch(d, [uuid4()])  # recipient exists but has no device
    assert sender.sent == []


def test_invalid_token_is_forgotten():
    user = uuid4()
    devices = StubDevices({user: ["dead-token"]})

    class ReportingSender:
        def send(self, messages, on_invalid_token=None):
            for m in messages:
                on_invalid_token(m.token)

    _dispatch(_dispatcher(devices, ReportingSender()), [user])
    assert devices.forgotten == ["dead-token"]


def test_unsupported_locale_falls_back_to_vi():
    assert PushDispatcher(StubDevices(), RecordingSender(), locale="de").locale == "vi"
    assert PushDispatcher(StubDevices(), RecordingSender(), locale="fr").locale == "fr"


class LocaleDevices(StubDevices):
    def __init__(self, tokens, locales) -> None:
        super().__init__(tokens)
        self._locales = locales

    def locales_for_tokens(self, tokens):
        return {t: self._locales[t] for t in tokens if t in self._locales}


def test_render_writes_each_device_in_its_own_language():
    alice, bob = uuid4(), uuid4()
    devices = LocaleDevices({alice: ["tok-fr"], bob: ["tok-old"]}, {"tok-fr": "fr"})
    sender = RecordingSender()
    texts = {"fr": ("Bonjour", "corps"), "en": ("Hello", "body")}
    _dispatcher(devices, sender).dispatch(
        category=CATEGORY, recipients=[alice, bob], data={}, render=lambda loc: texts[loc]
    )
    by_token = {m.token: m.title for m in sender.sent}
    # A device that never registered a language gets the dispatcher's default ("en" here).
    assert by_token == {"tok-fr": "Bonjour", "tok-old": "Hello"}


class RecordingEvents:
    def __init__(self, explode=False) -> None:
        self.rows: list = []
        self._explode = explode

    def record(self, user_ids, *, category, kind, texts, data):
        if self._explode:
            raise RuntimeError("db down")
        self.rows.append((sorted(user_ids, key=str), category, kind, texts))


def test_bell_copy_is_kept_for_muted_users_but_never_for_the_actor():
    actor, muted, other = uuid4(), uuid4(), uuid4()
    devices = StubDevices({muted: ["tok-m"], other: ["tok-o"]})
    sender, events = RecordingSender(), RecordingEvents()
    d = PushDispatcher(
        devices=devices,
        sender=sender,
        preferences=StubPreferences(muted={muted}),
        locale="en",
        run_async=False,
        events=events,
    )
    texts = {"vi": ("a", "b"), "fr": ("c", "d"), "en": ("e", "f")}
    d.dispatch(
        category=CATEGORY,
        recipients=[actor, muted, other],
        data={"kind": "chat_message"},
        exclude=actor,
        render=lambda loc: texts[loc],
    )
    assert [m.token for m in sender.sent] == ["tok-o"]  # muted: no push
    users, category, kind, stored = events.rows[0]
    assert users == sorted([muted, other], key=str) and (category, kind) == (CATEGORY, "chat_message")
    assert stored["fr"] == ["c", "d"] and set(stored) == {"vi", "fr", "en"}


def test_a_failing_bell_write_never_costs_the_push():
    user = uuid4()
    sender = RecordingSender()
    d = PushDispatcher(
        devices=StubDevices({user: ["tok"]}),
        sender=sender,
        locale="en",
        run_async=False,
        events=RecordingEvents(explode=True),
    )
    _dispatch(d, [user])
    assert [m.token for m in sender.sent] == ["tok"]
