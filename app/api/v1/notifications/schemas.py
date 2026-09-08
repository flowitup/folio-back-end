"""Schemas for the notification-preference endpoints."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict


class NotificationPreferencesResponse(BaseModel):
    """Current opt-outs. Every field is present; a user with no stored row reads all true."""

    push_enabled: bool
    chat: bool
    attendance: bool
    tasks: bool
    membership: bool
    billing: bool


class UpdateNotificationPreferencesRequest(BaseModel):
    """Partial update — omitted fields keep their current value.

    `extra="forbid"` so a typo'd category is a 422 rather than a silently ignored setting
    the user believes they changed.
    """

    model_config = ConfigDict(extra="forbid")

    push_enabled: Optional[bool] = None
    chat: Optional[bool] = None
    attendance: Optional[bool] = None
    tasks: Optional[bool] = None
    membership: Optional[bool] = None
    billing: Optional[bool] = None
