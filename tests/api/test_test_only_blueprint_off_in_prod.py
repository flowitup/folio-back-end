"""Security test: test-only blueprint must NOT be registered when TESTING=False."""

from __future__ import annotations


def test_test_only_endpoint_returns_404_when_testing_false():
    """Instantiate app with TESTING=False and assert the __test__ route does not exist."""
    from app import create_app, db
    from config import Config

    class ProdLikeConfig(Config):
        TESTING: bool = False
        DATABASE_URL: str = "sqlite:///:memory:"
        RATELIMIT_ENABLED: bool = False
        RATELIMIT_STORAGE_URI: str = "memory://"
        # Override production check guards
        JWT_TOKEN_LOCATION = ["headers", "cookies"]

    app = create_app(ProdLikeConfig)
    with app.app_context():
        db.create_all()
        client = app.test_client()
        resp = client.get("/api/v1/__test__/last-email")
        assert resp.status_code == 404, (
            f"Expected 404 for test-only endpoint in non-TESTING mode, got {resp.status_code}. "
            "The __test__ blueprint must not be registered outside TESTING mode."
        )
        db.drop_all()
