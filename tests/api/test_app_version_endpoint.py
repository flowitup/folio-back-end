"""GET /api/v1/app-version — public mobile update policy."""

import pytest

from app import create_app
from config import TestingConfig


@pytest.fixture
def client():
    class Cfg(TestingConfig):
        RATELIMIT_ENABLED = False

    app = create_app(Cfg)
    with app.app_context():
        yield app.test_client()


def test_app_version_is_public_and_ungated_by_default(client):
    res = client.get("/api/v1/app-version")
    assert res.status_code == 200
    body = res.get_json()
    assert body["ios"]["min_version"] == ""
    assert body["android"]["min_version"] == ""
    # No default link: the TestFlight / Play-testing pages sent customers to the betas.
    assert body["ios"]["update_url"] == ""
    assert body["android"]["update_url"] == ""


def test_app_version_reflects_configured_minimums(client):
    client.application.config["APP_MIN_VERSION_IOS"] = "0.5.20"
    client.application.config["APP_MIN_VERSION_ANDROID"] = "0.5.19"
    body = client.get("/api/v1/app-version").get_json()
    assert body["ios"]["min_version"] == "0.5.20"
    assert body["android"]["min_version"] == "0.5.19"
