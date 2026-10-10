"""POST/DELETE /push/devices — the app registers its Expo push token for the signed-in user."""

from uuid import UUID

from flask import jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required
from pydantic import ValidationError

from app.api._helpers.pydantic_errors import validation_message
from app.api._helpers.rate_limit_keys import jwt_user_key
from app.api.openapi import openapi_doc
from app.api.v1.push import push_bp
from app.api.v1.push.schemas import RegisterPushDeviceRequest, UnregisterPushDeviceRequest
from app.infrastructure.rate_limiter import limiter
from wiring import get_container


def _bad_request(message: str):
    return jsonify({"error": "ValidationError", "message": message, "status_code": 400}), 400


@push_bp.route("/push/devices", methods=["POST"])
@openapi_doc(
    summary="Register this device's push token for the signed-in user", request=RegisterPushDeviceRequest, tags=["push"]
)
@jwt_required()
@limiter.limit("30 per minute", key_func=jwt_user_key)
def register_push_device():
    try:
        data = RegisterPushDeviceRequest.model_validate(request.get_json(silent=True) or {})
    except ValidationError as exc:
        return _bad_request(validation_message(exc))
    repo = get_container().push_device_repository
    if repo is None:
        raise RuntimeError("push_device_repository not wired in container")
    repo.upsert(UUID(str(get_jwt_identity())), data.token, data.platform, data.locale)
    return "", 204


@push_bp.route("/push/devices", methods=["DELETE"])
@openapi_doc(summary="Forget this device's push token (sign-out)", request=UnregisterPushDeviceRequest, tags=["push"])
@jwt_required()
@limiter.limit("30 per minute", key_func=jwt_user_key)
def unregister_push_device():
    try:
        data = UnregisterPushDeviceRequest.model_validate(request.get_json(silent=True) or {})
    except ValidationError as exc:
        return _bad_request(validation_message(exc))
    repo = get_container().push_device_repository
    if repo is None:
        raise RuntimeError("push_device_repository not wired in container")
    # Scoped to the caller; 204 either way, so the answer never says whose a token is.
    repo.delete_token_for_user(UUID(get_jwt_identity()), data.token)
    return "", 204
