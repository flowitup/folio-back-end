"""Pydantic v2 schemas for the chat API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SendMessageBody(BaseModel):
    """JSON body of POST /chat/channels/<key>/messages (text-only messages).

    Messages with an image use multipart/form-data instead: ``body`` text part + ``file``.
    """

    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=4000)


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
    sender_id: str
    sender_name: str
    body: str | None
    attachment: AttachmentResponse | None
    created_at: str
    mine: bool


class MemberResponse(BaseModel):
    id: str
    name: str


class MessagePageResponse(BaseModel):
    items: list[MessageResponse]
    members: list[MemberResponse]


class ChannelResponse(BaseModel):
    key: str
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
