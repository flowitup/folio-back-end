"""HTTP errors under /api/ answer the JSON error envelope, never an HTML page."""

from __future__ import annotations

import pytest
from flask import request


@pytest.fixture(scope="module")
def app():
    from app import create_app
    from config import TestingConfig

    class Config(TestingConfig):
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(Config)

    @test_app.route("/api/v1/__probe__/json", methods=["POST"])
    def _probe_json():
        return {"body": request.get_json()}

    @test_app.route("/api/v1/__probe__/boom")
    def _probe_boom():
        raise RuntimeError("internal detail")

    return test_app


def test_unknown_api_url_answers_json_404(app):
    resp = app.test_client().get("/api/v1/does-not-exist")
    assert resp.status_code == 404
    assert resp.is_json
    assert resp.get_json()["error"] == "NotFound"
    assert resp.get_json()["status_code"] == 404


def test_wrong_method_answers_json_405_with_allow_header(app):
    resp = app.test_client().get("/api/v1/__probe__/json")
    assert resp.status_code == 405
    assert resp.is_json
    assert "POST" in resp.headers["Allow"]


def test_malformed_json_body_answers_json_400(app):
    resp = app.test_client().post("/api/v1/__probe__/json", data="not json", content_type="application/json")
    assert resp.status_code == 400
    assert resp.is_json
    assert resp.get_json()["error"] == "BadRequest"


def test_non_json_content_type_answers_json_415(app):
    resp = app.test_client().post("/api/v1/__probe__/json", data="x=1", content_type="text/plain")
    assert resp.status_code == 415
    assert resp.is_json


def test_unhandled_exception_answers_json_500_without_details(app):
    app.config["PROPAGATE_EXCEPTIONS"] = False
    try:
        resp = app.test_client().get("/api/v1/__probe__/boom")
    finally:
        app.config["PROPAGATE_EXCEPTIONS"] = None
    assert resp.status_code == 500
    assert resp.is_json
    body = resp.get_json()
    assert body["error"] == "InternalServerError"
    assert "internal detail" not in body["message"]


def test_non_api_paths_keep_the_default_page(app):
    resp = app.test_client().get("/not-an-api-path")
    assert resp.status_code == 404
    assert not resp.is_json
