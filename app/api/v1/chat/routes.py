"""Chat API routes.

Every chat route answers 404 ``FeatureDisabled`` unless ``FEATURE_CHAT`` is on for this
deployment; ``GET /features`` is always available so the apps know whether to show chat.
"""

from __future__ import annotations

import logging
from datetime import timezone
from typing import Any, Callable, TypeVar, cast
from functools import wraps
from uuid import UUID

from flask import Response, current_app, jsonify, request, send_file
from flask_jwt_extended import get_jwt_identity, jwt_required
from pydantic import ValidationError

from app.api._helpers.rate_limit_keys import jwt_user_key
from app.api._helpers.validation_error import safe_validation_fields
from app.api.openapi import openapi_doc
from app.api.v1.chat import chat_bp
from app.api.v1.chat.schemas import (
    ChannelListResponse,
    FeaturesResponse,
    ListMessagesQuery,
    MessagePageResponse,
    MessageResponse,
    SendMessageBody,
)
from app.application.chat.dtos import ChannelDto, MessageDto
from app.application.chat.exceptions import (
    AttachmentTooLargeError,
    ChatChannelNotFoundError,
    ChatMessageNotFoundError,
    EmptyMessageError,
    NotChannelMemberError,
    UnsupportedAttachmentTypeError,
)
from app.application.chat.usecases import MAX_ATTACHMENT_BYTES
from app.infrastructure.rate_limiter import limiter
from wiring import get_container

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def _err(code: int, error: str, message: str) -> tuple[Response, int]:
    return jsonify({"error": error, "message": message}), code


def chat_enabled() -> bool:
    return bool(current_app.config.get("FEATURE_CHAT"))


def require_chat_feature(func: F) -> F:
    """404 unless the deployment enables chat — the feature does not exist for the others."""

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if not chat_enabled():
            return _err(404, "FeatureDisabled", "Chat is not enabled on this server.")
        return func(*args, **kwargs)

    return cast(F, wrapper)


def _iso(value: Any) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return str(value.isoformat())


def _serialize_channel(dto: ChannelDto) -> dict[str, Any]:
    return {
        "key": dto.key,
        "kind": dto.kind,
        "id": str(dto.id),
        "name": dto.name,
        "member_count": dto.member_count,
        "unread_count": dto.unread_count,
        "last_message_at": _iso(dto.last_message_at) if dto.last_message_at else None,
    }


def _serialize_message(dto: MessageDto, actor_id: UUID) -> dict[str, Any]:
    return {
        "id": str(dto.id),
        "channel_key": dto.channel_key,
        "sender_id": str(dto.sender_id),
        "sender_name": dto.sender_name,
        "body": dto.body,
        "attachment": (
            {
                "url": f"/api/v1/chat/messages/{dto.id}/attachment",
                "filename": dto.attachment.filename,
                "content_type": dto.attachment.content_type,
                "size_bytes": dto.attachment.size_bytes,
            }
            if dto.attachment
            else None
        ),
        "created_at": _iso(dto.created_at),
        "mine": dto.sender_id == actor_id,
    }


# ---------------------------------------------------------------------------
# GET /api/v1/features
# ---------------------------------------------------------------------------


@chat_bp.get("/features")
@openapi_doc(summary="Feature flags of this deployment", responses={200: FeaturesResponse}, tags=["features"])
@jwt_required()  # type: ignore[untyped-decorator]
def get_features() -> Any:
    return jsonify({"chat": chat_enabled()}), 200


# ---------------------------------------------------------------------------
# GET /api/v1/chat/channels
# ---------------------------------------------------------------------------


@chat_bp.get("/chat/channels")
@openapi_doc(summary="List the chat channels of the current user", responses={200: ChannelListResponse}, tags=["chat"])
@jwt_required()  # type: ignore[untyped-decorator]
@require_chat_feature
@limiter.limit("120 per minute", key_func=jwt_user_key)
def list_channels() -> Any:
    actor_id = UUID(get_jwt_identity())
    container = get_container()
    if container.list_chat_channels_usecase is None:
        raise RuntimeError("list_chat_channels_usecase not wired in container")
    try:
        channels = container.list_chat_channels_usecase.execute(actor_id=actor_id)
    except Exception:
        logger.exception("list_channels unexpected error")
        return _err(500, "InternalError", "An unexpected error occurred.")
    return jsonify({"items": [_serialize_channel(c) for c in channels]}), 200


# ---------------------------------------------------------------------------
# GET /api/v1/chat/channels/<key>/messages
# ---------------------------------------------------------------------------


@chat_bp.get("/chat/channels/<string:channel_key>/messages")
@openapi_doc(
    summary="List messages of a channel (oldest first) with its members",
    query=ListMessagesQuery,
    responses={200: MessagePageResponse},
    tags=["chat"],
)
@jwt_required()  # type: ignore[untyped-decorator]
@require_chat_feature
@limiter.limit("240 per minute", key_func=jwt_user_key)
def list_messages(channel_key: str) -> Any:
    try:
        raw_args = request.args.to_dict()
        if "before" in raw_args:
            # A '+' timezone sign arrives as a space when the client did not percent-encode it.
            raw_args["before"] = raw_args["before"].replace(" ", "+")
        query = ListMessagesQuery.model_validate(raw_args)
    except ValidationError as exc:
        fields = safe_validation_fields(exc)
        return _err(422, "ValidationError", f"Invalid query: {', '.join(str(f) for f in fields)}")
    actor_id = UUID(get_jwt_identity())
    container = get_container()
    if container.list_chat_messages_usecase is None:
        raise RuntimeError("list_chat_messages_usecase not wired in container")
    try:
        page = container.list_chat_messages_usecase.execute(
            actor_id=actor_id, channel_key=channel_key, before=query.before, limit=query.limit
        )
    except ChatChannelNotFoundError:
        return _err(404, "NotFound", "Channel not found")
    except NotChannelMemberError:
        return _err(403, "Forbidden", "Not a member of this channel")
    except Exception:
        logger.exception("list_messages unexpected error channel=%s", channel_key)
        return _err(500, "InternalError", "An unexpected error occurred.")
    return (
        jsonify(
            {
                "items": [_serialize_message(m, actor_id) for m in page.items],
                "members": [
                    {
                        "id": str(m.id),
                        "name": m.name,
                        "last_read_at": _iso(m.last_read_at) if m.last_read_at else None,
                    }
                    for m in page.members
                ],
            }
        ),
        200,
    )


# ---------------------------------------------------------------------------
# POST /api/v1/chat/channels/<key>/messages   (JSON {body} or multipart body+file)
# ---------------------------------------------------------------------------


@chat_bp.post("/chat/channels/<string:channel_key>/messages")
@openapi_doc(
    summary="Send a message (JSON text, or multipart/form-data with an image `file`)",
    request=SendMessageBody,
    responses={201: MessageResponse},
    tags=["chat"],
)
@jwt_required()  # type: ignore[untyped-decorator]
@require_chat_feature
@limiter.limit("60 per minute", key_func=jwt_user_key)
def send_message(channel_key: str) -> Any:
    attachment: tuple[str, str, bytes] | None = None
    if request.files:
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            return _err(400, "BadRequest", "Expected an image in the 'file' part")
        upload.stream.seek(0, 2)
        size = upload.stream.tell()
        upload.stream.seek(0)
        if size > MAX_ATTACHMENT_BYTES:
            return _err(413, "AttachmentTooLarge", f"Attachment exceeds {MAX_ATTACHMENT_BYTES} bytes")
        attachment = (upload.filename, upload.mimetype or "application/octet-stream", upload.stream.read())
        body: str | None = (request.form.get("body") or "").strip() or None
    else:
        try:
            parsed = SendMessageBody.model_validate(request.get_json(silent=True) or {})
        except ValidationError as exc:
            fields = safe_validation_fields(exc)
            return _err(422, "ValidationError", f"Invalid input: {', '.join(str(f) for f in fields)}")
        body = parsed.body

    actor_id = UUID(get_jwt_identity())
    container = get_container()
    if container.send_chat_message_usecase is None:
        raise RuntimeError("send_chat_message_usecase not wired in container")
    try:
        dto = container.send_chat_message_usecase.execute(
            actor_id=actor_id, channel_key=channel_key, body=body, attachment=attachment
        )
    except ChatChannelNotFoundError:
        return _err(404, "NotFound", "Channel not found")
    except NotChannelMemberError:
        return _err(403, "Forbidden", "Not a member of this channel")
    except EmptyMessageError as exc:
        return _err(400, "BadRequest", str(exc))
    except UnsupportedAttachmentTypeError as exc:
        return _err(415, "UnsupportedMediaType", str(exc))
    except AttachmentTooLargeError as exc:
        return _err(413, "AttachmentTooLarge", str(exc))
    except Exception:
        logger.exception("send_message unexpected error channel=%s", channel_key)
        return _err(500, "InternalError", "An unexpected error occurred.")
    return jsonify(_serialize_message(dto, actor_id)), 201


# ---------------------------------------------------------------------------
# POST /api/v1/chat/channels/<key>/read
# ---------------------------------------------------------------------------


@chat_bp.post("/chat/channels/<string:channel_key>/read")
@openapi_doc(summary="Mark a channel as read up to now", tags=["chat"])
@jwt_required()  # type: ignore[untyped-decorator]
@require_chat_feature
@limiter.limit("120 per minute", key_func=jwt_user_key)
def mark_read(channel_key: str) -> Any:
    actor_id = UUID(get_jwt_identity())
    container = get_container()
    if container.mark_chat_channel_read_usecase is None:
        raise RuntimeError("mark_chat_channel_read_usecase not wired in container")
    try:
        container.mark_chat_channel_read_usecase.execute(actor_id=actor_id, channel_key=channel_key)
    except ChatChannelNotFoundError:
        return _err(404, "NotFound", "Channel not found")
    except NotChannelMemberError:
        return _err(403, "Forbidden", "Not a member of this channel")
    except Exception:
        logger.exception("mark_read unexpected error channel=%s", channel_key)
        return _err(500, "InternalError", "An unexpected error occurred.")
    return "", 204


# ---------------------------------------------------------------------------
# GET /api/v1/chat/messages/<id>/attachment
# ---------------------------------------------------------------------------


@chat_bp.get("/chat/messages/<uuid:message_id>/attachment")
@openapi_doc(summary="Download a message attachment (channel members only)", tags=["chat"])
@jwt_required()  # type: ignore[untyped-decorator]
@require_chat_feature
@limiter.limit("240 per minute", key_func=jwt_user_key)
def get_attachment(message_id: UUID) -> Any:
    actor_id = UUID(get_jwt_identity())
    container = get_container()
    if container.get_chat_attachment_usecase is None:
        raise RuntimeError("get_chat_attachment_usecase not wired in container")
    try:
        dto = container.get_chat_attachment_usecase.execute(actor_id=actor_id, message_id=message_id)
    except ChatMessageNotFoundError:
        return _err(404, "NotFound", "Attachment not found")
    except ChatChannelNotFoundError:
        return _err(404, "NotFound", "Channel not found")
    except NotChannelMemberError:
        return _err(403, "Forbidden", "Not a member of this channel")
    except Exception:
        logger.exception("get_attachment unexpected error message=%s", message_id)
        return _err(500, "InternalError", "An unexpected error occurred.")
    response = send_file(dto.stream, mimetype=dto.content_type, download_name=dto.filename, as_attachment=False)
    response.headers["Content-Length"] = str(dto.content_length)
    response.headers["Cache-Control"] = "private, max-age=3600"
    return response
