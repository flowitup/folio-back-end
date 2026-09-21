"""Pydantic v2 schemas for the chat API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SendMessageBody(BaseModel):
    """JSON body of POST /chat/channels/<key>/messages (text-only messages).

    Messages with an image or a voice note use multipart/form-data instead: ``body`` text
    part + ``file`` (+ optional ``lang``/``reply_to_id`` form fields).
    """

    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=4000)
    # Kept when the assistant dispatches so it replies in the right language, whatever
    # the channel kind.
    lang: Literal["vi", "fr", "en"] | None = None
    # Must name a message of this same channel (checked by the use case); a reply to an
    # assistant-authored message dispatches even without an `@folio` mention (D18).
    reply_to_id: UUID | None = None


class ListMessagesQuery(BaseModel):
    """Query parameters of GET /chat/channels/<key>/messages."""

    model_config = ConfigDict(extra="forbid")

    before: datetime | None = None
    limit: int = Field(default=50, ge=1, le=200)


class AttachmentResponse(BaseModel):
    url: str
    filename: str
    content_type: str
    size_bytes: int


class MessageResponse(BaseModel):
    id: str
    channel_key: str
    # None for an assistant-authored message.
    sender_id: str | None
    sender_name: str
    body: str | None
    attachment: AttachmentResponse | None
    created_at: str
    mine: bool
    sender_type: Literal["user", "assistant", "system"]
    content_type: Literal["text", "photo", "card", "choice", "job_status"]
    payload: dict[str, Any] | None = None
    reply_to_id: str | None = None
    mentions_assistant: bool = False


class MemberResponse(BaseModel):
    id: str
    name: str
    # ISO timestamp of the member's read marker (None until they open the channel); drives "seen" avatars.
    last_read_at: str | None = None


class MessagePageResponse(BaseModel):
    items: list[MessageResponse]
    members: list[MemberResponse]


class ChannelResponse(BaseModel):
    key: str
    # "company" (every member), "project" (project members) or "admin" (a company's
    # admins + platform ops — confidential company/payroll data, apps label the kind).
    kind: str
    id: str
    name: str
    member_count: int
    unread_count: int
    last_message_at: str | None


class ChannelListResponse(BaseModel):
    items: list[ChannelResponse]


class FeaturesResponse(BaseModel):
    """Feature flags of this deployment, as seen by the apps."""

    chat: bool
    assistant: bool
