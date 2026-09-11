"""Integration tests for auth API endpoints."""

import re

import pytest

from app import create_app, db
from app.application.usecases.otp_login import RequestOtpUseCase, VerifyOtpUseCase
from app.infrastructure.adapters.flask_session import FlaskSessionManager
from app.infrastructure.adapters.jwt_issuer import JWTTokenIssuer
from app.infrastructure.adapters.sqlalchemy_login_otp import SQLAlchemyLoginOtpRepository
from app.infrastructure.adapters.sqlalchemy_user import SQLAlchemyUserRepository
from app.infrastructure.database.models import UserModel
from config import TestingConfig
from tests.auth_login_helper import mint_access_token, mint_tokens
from wiring import configure_container, get_container

# The only user in this module with a phone — phone + SMS code is the only way to
# sign in, so this is the one account TestSessionPolicy can drive through a real
# /auth/otp/request + /auth/otp/verify round-trip.
ACTIVE_PHONE = "+33600000101"


class RecordingSmsSender:
    """Captures outbound SMS text so tests can extract the real 6-digit code."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, to: str, text: str) -> None:
        self.sent.append((to, text))


def _code_from_sms(app) -> str:
    to, text = app._sms.sent[-1]
    match = re.search(r"\b(\d{6})\b", text)
    assert match, text
    return match.group(1)


@pytest.fixture(scope="module")
def app():
    """Create Flask app for testing."""

    # Create custom testing config with proper JWT settings
    class CustomTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False  # Disable rate limiting for tests

    test_app = create_app(CustomTestConfig)

    with test_app.app_context():
        db.create_all()

        # Configure dependency injection container
        user_repo = SQLAlchemyUserRepository(db.session)
        token_issuer = JWTTokenIssuer(access_expires_minutes=30, refresh_expires_days=7)
        session_manager = FlaskSessionManager()

        configure_container(
            user_repository=user_repo,
            token_issuer=token_issuer,
            session_manager=session_manager,
        )

        # Create active and inactive users — no password any more (phone + SMS
        # code only). active_user gets a phone so TestSessionPolicy can sign in
        # for real, through the same route production traffic uses.
        active_user = UserModel(email="active@example.com", is_active=True, phone=ACTIVE_PHONE)
        admin_user = UserModel(email="admin@example.com", is_active=True)
        inactive_user = UserModel(email="inactive@example.com", is_active=False)

        db.session.add_all([active_user, admin_user, inactive_user])
        db.session.commit()

        # Wire phone sign-in (request + verify) so TestSessionPolicy exercises the
        # real REFRESH_TOKEN_POLICY-reading route rather than a minted shortcut.
        otp_repo = SQLAlchemyLoginOtpRepository(db.session)
        sms = RecordingSmsSender()
        container = get_container()
        container.sms_sender = sms
        container.login_otp_repository = otp_repo
        container.request_otp_usecase = RequestOtpUseCase(user_repo, otp_repo, sms)
        container.verify_otp_usecase = VerifyOtpUseCase(
            user_repo, otp_repo, container.authorization_service, token_issuer
        )
        test_app._sms = sms

        yield test_app

        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    """Create test client."""
    return app.test_client()


@pytest.fixture
def container(app):
    """Get dependency injection container."""
    with app.app_context():
        return get_container()


class TestLoginRemoved:
    """POST /auth/login no longer exists: phone + SMS code is the only sign-in."""

    def test_login_endpoint_is_gone(self, client):
        response = client.post("/api/v1/auth/login", json={"email": "active@example.com", "password": "whatever"})
        assert response.status_code == 404


class TestLogoutEndpoint:
    """Test POST /api/v1/auth/logout endpoint."""

    def test_logout_without_token(self, client):
        """Test logout without authentication token."""
        response = client.post("/api/v1/auth/logout")

        # Logout should succeed even without token (optional JWT)
        assert response.status_code == 200
        data = response.get_json()
        assert data["message"] == "Successfully logged out"

    def test_logout_with_token(self, client):
        """Test logout with valid token."""
        token = mint_access_token(client, "active@example.com")

        response = client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 200
        data = response.get_json()
        assert data["message"] == "Successfully logged out"

    def test_logout_clears_cookies(self, client):
        """Test that logout clears JWT cookies."""
        token = mint_access_token(client, "active@example.com")

        response = client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {token}"})

        # Check cookies are cleared (max-age=0 or expires in past)
        cookies = response.headers.getlist("Set-Cookie")
        assert len(cookies) > 0


class TestRefreshEndpoint:
    """Test POST /api/v1/auth/refresh endpoint."""

    def test_refresh_with_valid_refresh_token(self, client):
        """Test refreshing access token with valid refresh token."""
        tokens = mint_tokens(client, "active@example.com")

        # Use refresh token to get new access token
        response = client.post("/api/v1/auth/refresh", headers={"Authorization": f"Bearer {tokens['refresh_token']}"})

        assert response.status_code == 200
        data = response.get_json()
        assert "access_token" in data
        assert data["access_token"] != ""

    def test_refresh_without_token(self, client):
        """Test refresh without token."""
        response = client.post("/api/v1/auth/refresh")

        assert response.status_code == 401
        data = response.get_json()
        assert data["error"] == "Unauthorized"

    def test_refresh_with_access_token_instead_of_refresh(self, client):
        """Test refresh with access token (should fail)."""
        tokens = mint_tokens(client, "active@example.com")

        # Try to use access token for refresh (should fail)
        response = client.post("/api/v1/auth/refresh", headers={"Authorization": f"Bearer {tokens['access_token']}"})

        # Should fail because we need refresh token, not access token
        assert response.status_code in [401, 422]


class TestGetCurrentUserEndpoint:
    """Test GET /api/v1/auth/me endpoint."""

    def test_get_current_user_with_valid_token(self, client):
        """Test getting current user info with valid token."""
        token = mint_access_token(client, "active@example.com")

        # Get current user
        response = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 200
        data = response.get_json()
        assert data["email"] == "active@example.com"
        assert "id" in data
        assert "permissions" in data
        assert "roles" not in data

    def test_get_current_user_without_token(self, client):
        """Test getting current user without token."""
        response = client.get("/api/v1/auth/me")

        assert response.status_code == 401
        data = response.get_json()
        assert data["error"] == "Unauthorized"

    def test_get_current_user_with_invalid_token(self, client):
        """Test getting current user with invalid token."""
        response = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer invalid-token-123"})

        assert response.status_code == 401
        data = response.get_json()
        assert data["error"] in ["InvalidToken", "Unauthorized"]


class TestRateLimiting:
    """Test rate limiting on the phone sign-in endpoint."""

    @pytest.fixture
    def rate_limited_client(self):
        """Create a client with rate limiting enabled.

        configure_container() mutates the process-wide `wiring.container` singleton
        (see wiring.get_container()'s own docstring), so this fixture's own call to
        it would otherwise leak into every test that runs afterward in this module
        — including TestSessionPolicy, which needs the original `app` fixture's
        container (its db.session, its seeded active_user). Save and restore it.
        """
        import wiring as _wiring

        saved_container = _wiring.container

        # Create custom config with rate limiting enabled
        class RateLimitTestConfig(TestingConfig):
            JWT_TOKEN_LOCATION = ["headers", "cookies"]
            RATELIMIT_ENABLED = True
            RATELIMIT_STORAGE_URI = "memory://"
            RATELIMIT_DEFAULT = "100 per minute"

        rate_limited_app = create_app(RateLimitTestConfig)

        with rate_limited_app.app_context():
            db.create_all()

            token_issuer = JWTTokenIssuer()
            user_repo = SQLAlchemyUserRepository(db.session)
            otp_repo = SQLAlchemyLoginOtpRepository(db.session)

            configure_container(user_repository=user_repo, token_issuer=token_issuer)

            container = get_container()
            container.request_otp_usecase = RequestOtpUseCase(user_repo, otp_repo, RecordingSmsSender())

            yield rate_limited_app.test_client()

            # Cleanup
            db.drop_all()

        _wiring.container = saved_container

    def test_otp_request_rate_limiting(self, rate_limited_client):
        """Test that /auth/otp/request is rate limited after 5 attempts (per minute).

        The phone below has no account: RequestOtpUseCase silently ignores unknown
        numbers and always answers 202 without ever touching the throttle counters
        in otp_login._issue_code, so only the route's own `@limiter.limit("5 per
        minute")` can produce the 429 this test is actually about.
        """
        for i in range(6):
            response = rate_limited_client.post("/api/v1/auth/otp/request", json={"phone": "+33698765432"})

            if i < 5:
                assert response.status_code == 202
            else:
                assert response.status_code == 429


class TestHealthCheck:
    """Test health check endpoint."""

    def test_health_check(self, client):
        """Test that health check endpoint works."""
        response = client.get("/health")

        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "ok"


class TestSessionPolicy:
    """REFRESH_TOKEN_POLICY is a deployment switch read at request time."""

    @pytest.fixture(autouse=True)
    def _fresh_rate_limit(self, client):
        from app.infrastructure.rate_limiter import limiter

        limiter.reset()
        saved = client.application.config.get("REFRESH_TOKEN_POLICY")
        yield
        client.application.config.update(REFRESH_TOKEN_POLICY=saved)

    def _refresh_claims(self, client, token):
        from flask_jwt_extended import decode_token

        with client.application.app_context():
            return decode_token(token, allow_expired=True)

    def _login(self, client):
        """Sign active@example.com in for real, via /auth/otp/request + /auth/otp/verify,
        so REFRESH_TOKEN_POLICY is exercised exactly the way a real client triggers it.
        """
        app = client.application
        with app.app_context():
            from app.infrastructure.database.models import LoginOtpOrm

            # This class calls _login() more than once against the same phone; clear
            # earlier codes first so otp_login._issue_code's per-phone resend/hourly
            # throttles never block a later call here.
            db.session.query(LoginOtpOrm).delete()
            db.session.commit()
        resp = client.post("/api/v1/auth/otp/request", json={"phone": ACTIVE_PHONE})
        assert resp.status_code == 202, resp.get_json()
        code = _code_from_sms(app)
        return client.post("/api/v1/auth/otp/verify", json={"phone": ACTIVE_PHONE, "code": code})

    def test_config_endpoint_is_public_and_reflects_settings(self, client):
        client.application.config.update(REFRESH_TOKEN_POLICY="persistent")
        resp = client.get("/api/v1/auth/config")
        assert resp.status_code == 200
        assert resp.get_json() == {"session": "persistent", "signup": True}

    def test_expiring_policy_issues_seven_day_refresh_token(self, client):
        client.application.config.update(REFRESH_TOKEN_POLICY="expiring")
        claims = self._refresh_claims(client, self._login(client).get_json()["refresh_token"])
        assert "exp" in claims and not claims.get("persistent")

    def test_persistent_policy_issues_never_expiring_refresh_token(self, client):
        client.application.config.update(REFRESH_TOKEN_POLICY="persistent")
        login = self._login(client).get_json()
        claims = self._refresh_claims(client, login["refresh_token"])
        assert "exp" not in claims and claims["persistent"] is True
        refreshed = client.post("/api/v1/auth/refresh", headers={"Authorization": f"Bearer {login['refresh_token']}"})
        assert refreshed.status_code == 200

    def test_logout_accepts_refresh_token_in_body(self, client):
        login = self._login(client).get_json()
        out = client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {login['access_token']}"},
            json={"refresh_token": login["refresh_token"]},
        )
        assert out.status_code == 200
        refreshed = client.post("/api/v1/auth/refresh", headers={"Authorization": f"Bearer {login['refresh_token']}"})
        assert refreshed.status_code == 401
