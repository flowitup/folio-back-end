"""Sessions last a week: access tokens must not expire after minutes."""

from datetime import timedelta
from uuid import uuid4

from flask import Flask
from flask_jwt_extended import JWTManager, decode_token

from app.infrastructure.adapters.jwt_issuer import JWTTokenIssuer
from config import Config


def _app() -> Flask:
    app = Flask(__name__)
    app.config["JWT_SECRET_KEY"] = "test-secret-key-with-enough-length-for-hs256"
    JWTManager(app)
    return app


def test_config_access_token_lives_seven_days():
    assert Config.JWT_ACCESS_TOKEN_EXPIRES == timedelta(days=7)


def test_issued_access_token_expires_after_seven_days():
    with _app().app_context():
        token = JWTTokenIssuer().create_access_token(uuid4())
        claims = decode_token(token)
    assert claims["exp"] - claims["iat"] == int(timedelta(days=7).total_seconds())
