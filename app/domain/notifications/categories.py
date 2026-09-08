"""Notification categories a user can mute, framework-free.

One category per family of events rather than one per event: users think in terms of
"stop telling me about chat", not "stop telling me about chat_message". Adding an event
to an existing family therefore needs no migration and no UI change.
"""

from __future__ import annotations

from enum import Enum


class NotificationCategory(str, Enum):
    CHAT = "chat"
    ATTENDANCE = "attendance"
    TASKS = "tasks"
    MEMBERSHIP = "membership"
    BILLING = "billing"


ALL_CATEGORIES: tuple[str, ...] = tuple(c.value for c in NotificationCategory)


def is_valid_category(value: str) -> bool:
    return value in ALL_CATEGORIES
