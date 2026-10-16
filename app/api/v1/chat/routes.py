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
    BlockedUsersResponse,
    ListReportsQuery,
    MessageResponse,
    ReportListResponse,
    ResolveReportBody,
    ReactionsResponse,
    ReportMessageBody,
    SendMessageBody,
    SetReactionBody,
)
from app.application.chat.dtos import ChannelDto, MemberDto, MessageDto, ReactionDto, ReportDto
from app.application.chat.exceptions import (
    AttachmentContentMismatchError,
    AttachmentTooLargeError,
    BlockTargetNotFoundError,
    CannotBlockSelfError,
    CannotReportOwnMessageError,
    ChatChannelNotFoundError,
    ChatMessageNotFoundError,
    ChatReportNotFoundError,
    NotCompanyAdminError,
    EmptyMessageError,
    InvalidReactionError,
    NotChannelMemberError,
    ReplyTargetNotInChannelError,
    UnsupportedAttachmentTypeError,
)
from app.application.chat.usecases import MAX_ATTACHMENT_BYTES
from app.domain.value_objects.display_filename import strip_control_chars
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


def _serialize_reactions(reactions: tuple[ReactionDto, ...]) -> list[dict[str, Any]]:
    return [
        {"user_id": str(r.user_id), "emoji": r.emoji, "created_at": _iso(r.created_at), "user_name": r.user_name}
        for r in reactions
    ]


def _serialize_member(m: MemberDto) -> dict[str, Any]:
    return {
        "id": str(m.id),
        "name": m.name,
        "last_read_at": _iso(m.last_read_at) if m.last_read_at else None,
        "state": m.state,
    }


def _serialize_report(dto: ReportDto, actor_id: UUID) -> dict[str, Any]:
    return {
        "id": str(dto.id),
        "message": _serialize_message(dto.message, actor_id),
        "channel_name": dto.channel_name,
        "reporter_id": str(dto.reporter_id),
        "reporter_name": dto.reporter_name,
        "reason": dto.reason,
        "created_at": _iso(dto.created_at),
    }


def _serialize_message(dto: MessageDto, actor_id: UUID) -> dict[str, Any]:
    return {
        "id": str(dto.id),
        "channel_key": dto.channel_key,
        "sender_id": str(dto.sender_id) if dto.sender_id is not None else None,
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
        "sender_type": dto.sender_type,
        "content_type": dto.content_type,
        "payload": dto.payload,
        "reply_to_id": str(dto.reply_to_id) if dto.reply_to_id is not None else None,
        "reactions": _serialize_reactions(dto.reactions),
        "sender_state": dto.sender_state,
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
                "members": [_serialize_member(m) for m in page.members],
            }
        ),
        200,
    )


# ---------------------------------------------------------------------------
# POST /api/v1/chat/channels/<key>/messages   (JSON {body} or multipart body+file)
# ---------------------------------------------------------------------------


@chat_bp.post("/chat/channels/<string:channel_key>/messages")
@openapi_doc(
    summary="Send a message (JSON text, or multipart/form-data with an image or voice note `file`)",
    request=SendMessageBody,
    responses={201: MessageResponse},
    tags=["chat"],
)
@jwt_required()  # type: ignore[untyped-decorator]
@require_chat_feature
@limiter.limit("60 per minute", key_func=jwt_user_key)
def send_message(channel_key: str) -> Any:
    attachment: tuple[str, str, bytes] | None = None
    lang: str | None = None
    reply_to_id: UUID | None = None
    if request.files:
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            return _err(400, "BadRequest", "Expected an image or voice note in the 'file' part")
        upload.stream.seek(0, 2)
        size = upload.stream.tell()
        upload.stream.seek(0)
        if size > MAX_ATTACHMENT_BYTES:
            return _err(413, "AttachmentTooLarge", f"Attachment exceeds {MAX_ATTACHMENT_BYTES} bytes")
        attachment = (upload.filename, upload.mimetype or "application/octet-stream", upload.stream.read())
        body: str | None = (request.form.get("body") or "").strip() or None
        raw_lang = (request.form.get("lang") or "").strip() or None
        if raw_lang is not None:
            if raw_lang not in ("vi", "fr", "en"):
                return _err(422, "ValidationError", "Invalid input: lang must be one of vi, fr, en")
            lang = raw_lang
        raw_reply_to = (request.form.get("reply_to_id") or "").strip() or None
        if raw_reply_to is not None:
            try:
                reply_to_id = UUID(raw_reply_to)
            except ValueError:
                return _err(422, "ValidationError", "Invalid input: reply_to_id must be a UUID")
    else:
        try:
            parsed = SendMessageBody.model_validate(request.get_json(silent=True) or {})
        except ValidationError as exc:
            fields = safe_validation_fields(exc)
            return _err(422, "ValidationError", f"Invalid input: {', '.join(str(f) for f in fields)}")
        body = parsed.body
        lang = parsed.lang
        reply_to_id = parsed.reply_to_id

    actor_id = UUID(get_jwt_identity())
    container = get_container()
    if container.send_chat_message_usecase is None:
        raise RuntimeError("send_chat_message_usecase not wired in container")
    try:
        dto = container.send_chat_message_usecase.execute(
            actor_id=actor_id,
            channel_key=channel_key,
            body=body,
            attachment=attachment,
            lang=lang,
            reply_to_id=reply_to_id,
        )
    except ChatChannelNotFoundError:
        return _err(404, "NotFound", "Channel not found")
    except NotChannelMemberError:
        return _err(403, "Forbidden", "Not a member of this channel")
    except ReplyTargetNotInChannelError as exc:
        return _err(400, "BadRequest", str(exc))
    except EmptyMessageError as exc:
        return _err(400, "BadRequest", str(exc))
    except AttachmentContentMismatchError as exc:
        return _err(415, "AttachmentContentMismatch", str(exc))
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
    # A name stored with a line break (before uploads were sanitised) must not make send_file raise.
    download_name = strip_control_chars(dto.filename) or "attachment"
    response = send_file(dto.stream, mimetype=dto.content_type, download_name=download_name, as_attachment=False)
    response.headers["Content-Length"] = str(dto.content_length)
    response.headers["Cache-Control"] = "private, max-age=3600"
    # User-supplied bytes: never let a browser sniff them into something executable.
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return response


# ---------------------------------------------------------------------------
# POST /api/v1/chat/messages/<id>/report
# ---------------------------------------------------------------------------


@chat_bp.post("/chat/messages/<uuid:message_id>/report")
@openapi_doc(summary="Report a message (channel members only)", request=ReportMessageBody, tags=["chat"])
@jwt_required()  # type: ignore[untyped-decorator]
@require_chat_feature
@limiter.limit("30 per minute", key_func=jwt_user_key)
def report_message(message_id: UUID) -> Any:
    try:
        parsed = ReportMessageBody.model_validate(request.get_json(silent=True) or {})
    except ValidationError as exc:
        fields = safe_validation_fields(exc)
        return _err(422, "ValidationError", f"Invalid input: {', '.join(str(f) for f in fields)}")
    actor_id = UUID(get_jwt_identity())
    container = get_container()
    if container.report_chat_message_usecase is None:
        raise RuntimeError("report_chat_message_usecase not wired in container")
    try:
        container.report_chat_message_usecase.execute(actor_id=actor_id, message_id=message_id, reason=parsed.reason)
    except (ChatMessageNotFoundError, ChatChannelNotFoundError):
        return _err(404, "NotFound", "Message not found")
    except NotChannelMemberError:
        return _err(403, "Forbidden", "Not a member of this channel")
    except CannotReportOwnMessageError:
        return _err(400, "CannotReportOwnMessage", "You cannot report your own message")
    except Exception:
        logger.exception("report_message unexpected error message=%s", message_id)
        return _err(500, "InternalError", "An unexpected error occurred.")
    return "", 204


# ---------------------------------------------------------------------------
# GET /api/v1/chat/reports?company_id=…, POST /api/v1/chat/reports/<id>/resolve
# ---------------------------------------------------------------------------


@chat_bp.get("/chat/reports")
@openapi_doc(
    summary="Open reports on the company's chat messages (company admins only)",
    query=ListReportsQuery,
    responses={200: ReportListResponse},
    tags=["chat"],
)
@jwt_required()  # type: ignore[untyped-decorator]
@require_chat_feature
@limiter.limit("120 per minute", key_func=jwt_user_key)
def list_reports() -> Any:
    try:
        query = ListReportsQuery.model_validate(request.args.to_dict())
    except ValidationError as exc:
        fields = safe_validation_fields(exc)
        return _err(422, "ValidationError", f"Invalid query: {', '.join(str(f) for f in fields)}")
    actor_id = UUID(get_jwt_identity())
    container = get_container()
    if container.list_chat_reports_usecase is None:
        raise RuntimeError("list_chat_reports_usecase not wired in container")
    try:
        reports = container.list_chat_reports_usecase.execute(actor_id=actor_id, company_id=query.company_id)
    except NotCompanyAdminError:
        return _err(403, "Forbidden", "Only the company's admins review chat reports")
    except Exception:
        logger.exception("list_reports unexpected error company=%s", query.company_id)
        return _err(500, "InternalError", "An unexpected error occurred.")
    return jsonify({"items": [_serialize_report(r, actor_id) for r in reports]}), 200


@chat_bp.post("/chat/reports/<uuid:report_id>/resolve")
@openapi_doc(
    summary="Close a report: dismiss it, or delete the reported message (company admins only)",
    request=ResolveReportBody,
    tags=["chat"],
)
@jwt_required()  # type: ignore[untyped-decorator]
@require_chat_feature
@limiter.limit("60 per minute", key_func=jwt_user_key)
def resolve_report(report_id: UUID) -> Any:
    try:
        parsed = ResolveReportBody.model_validate(request.get_json(silent=True) or {})
    except ValidationError as exc:
        fields = safe_validation_fields(exc)
        return _err(422, "ValidationError", f"Invalid input: {', '.join(str(f) for f in fields)}")
    actor_id = UUID(get_jwt_identity())
    container = get_container()
    if container.resolve_chat_report_usecase is None:
        raise RuntimeError("resolve_chat_report_usecase not wired in container")
    try:
        container.resolve_chat_report_usecase.execute(actor_id=actor_id, report_id=report_id, action=parsed.action)
    except ChatReportNotFoundError:
        return _err(404, "NotFound", "Report not found")
    except Exception:
        logger.exception("resolve_report unexpected error report=%s", report_id)
        return _err(500, "InternalError", "An unexpected error occurred.")
    return "", 204


# ---------------------------------------------------------------------------
# PUT|DELETE /api/v1/chat/messages/<id>/reaction
# ---------------------------------------------------------------------------


@chat_bp.put("/chat/messages/<uuid:message_id>/reaction")
@openapi_doc(
    summary="Leave (or change) the caller's emoji on a message",
    request=SetReactionBody,
    responses={200: ReactionsResponse},
    tags=["chat"],
)
@jwt_required()  # type: ignore[untyped-decorator]
@require_chat_feature
@limiter.limit("120 per minute", key_func=jwt_user_key)
def set_reaction(message_id: UUID) -> Any:
    try:
        parsed = SetReactionBody.model_validate(request.get_json(silent=True) or {})
    except ValidationError as exc:
        fields = safe_validation_fields(exc)
        return _err(422, "ValidationError", f"Invalid input: {', '.join(str(f) for f in fields)}")
    actor_id = UUID(get_jwt_identity())
    container = get_container()
    if container.set_chat_reaction_usecase is None:
        raise RuntimeError("set_chat_reaction_usecase not wired in container")
    try:
        reactions = container.set_chat_reaction_usecase.execute(
            actor_id=actor_id, message_id=message_id, emoji=parsed.emoji
        )
    except InvalidReactionError:
        return _err(422, "ValidationError", "Invalid input: emoji")
    except (ChatMessageNotFoundError, ChatChannelNotFoundError):
        return _err(404, "NotFound", "Message not found")
    except NotChannelMemberError:
        return _err(403, "Forbidden", "Not a member of this channel")
    except Exception:
        logger.exception("set_reaction unexpected error message=%s", message_id)
        return _err(500, "InternalError", "An unexpected error occurred.")
    return jsonify({"reactions": _serialize_reactions(reactions)}), 200


@chat_bp.delete("/chat/messages/<uuid:message_id>/reaction")
@openapi_doc(
    summary="Take the caller's emoji off a message",
    responses={200: ReactionsResponse},
    tags=["chat"],
)
@jwt_required()  # type: ignore[untyped-decorator]
@require_chat_feature
@limiter.limit("120 per minute", key_func=jwt_user_key)
def remove_reaction(message_id: UUID) -> Any:
    actor_id = UUID(get_jwt_identity())
    container = get_container()
    if container.remove_chat_reaction_usecase is None:
        raise RuntimeError("remove_chat_reaction_usecase not wired in container")
    try:
        reactions = container.remove_chat_reaction_usecase.execute(actor_id=actor_id, message_id=message_id)
    except (ChatMessageNotFoundError, ChatChannelNotFoundError):
        return _err(404, "NotFound", "Message not found")
    except NotChannelMemberError:
        return _err(403, "Forbidden", "Not a member of this channel")
    except Exception:
        logger.exception("remove_reaction unexpected error message=%s", message_id)
        return _err(500, "InternalError", "An unexpected error occurred.")
    return jsonify({"reactions": _serialize_reactions(reactions)}), 200


# ---------------------------------------------------------------------------
# GET /api/v1/chat/blocks, PUT|DELETE /api/v1/chat/blocks/<user_id>
# ---------------------------------------------------------------------------


@chat_bp.get("/chat/blocks")
@openapi_doc(summary="People the caller has blocked in chat", responses={200: BlockedUsersResponse}, tags=["chat"])
@jwt_required()  # type: ignore[untyped-decorator]
@require_chat_feature
@limiter.limit("120 per minute", key_func=jwt_user_key)
def list_blocks() -> Any:
    actor_id = UUID(get_jwt_identity())
    container = get_container()
    if container.list_blocked_chat_users_usecase is None:
        raise RuntimeError("list_blocked_chat_users_usecase not wired in container")
    members = container.list_blocked_chat_users_usecase.execute(actor_id=actor_id)
    return jsonify({"items": [_serialize_member(m) for m in members]})


@chat_bp.put("/chat/blocks/<uuid:user_id>")
@openapi_doc(summary="Block a person in chat (hides their messages and pushes)", tags=["chat"])
@jwt_required()  # type: ignore[untyped-decorator]
@require_chat_feature
@limiter.limit("30 per minute", key_func=jwt_user_key)
def block_user(user_id: UUID) -> Any:
    actor_id = UUID(get_jwt_identity())
    container = get_container()
    if container.block_chat_user_usecase is None:
        raise RuntimeError("block_chat_user_usecase not wired in container")
    try:
        container.block_chat_user_usecase.execute(actor_id=actor_id, target_id=user_id)
    except CannotBlockSelfError:
        return _err(400, "BadRequest", "You cannot block yourself")
    except BlockTargetNotFoundError:
        return _err(404, "NotFound", "User not found")
    except Exception:
        logger.exception("block_user unexpected error target=%s", user_id)
        return _err(500, "InternalError", "An unexpected error occurred.")
    return "", 204


@chat_bp.delete("/chat/blocks/<uuid:user_id>")
@openapi_doc(summary="Unblock a person in chat", tags=["chat"])
@jwt_required()  # type: ignore[untyped-decorator]
@require_chat_feature
@limiter.limit("30 per minute", key_func=jwt_user_key)
def unblock_user(user_id: UUID) -> Any:
    actor_id = UUID(get_jwt_identity())
    container = get_container()
    if container.unblock_chat_user_usecase is None:
        raise RuntimeError("unblock_chat_user_usecase not wired in container")
    container.unblock_chat_user_usecase.execute(actor_id=actor_id, target_id=user_id)
    return "", 204
