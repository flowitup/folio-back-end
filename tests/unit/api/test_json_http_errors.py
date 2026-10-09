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

    @test_app.route("/api/v1/__probe__/form", methods=["POST"])
    def _probe_form():
        return {"fields": dict(request.form), "files": [f.filename for f in request.files.values()]}

    @test_app.route("/api/v1/__probe__/args/<name>")
    def _probe_args(name):
        return {"name": name, "q": request.args.get("q")}

    @test_app.route("/api/v1/__probe__/nul-in-db")
    def _probe_nul_in_db():
        # What psycopg2 raises for a NUL in a text parameter the guard could not see.
        raise ValueError("A string literal cannot contain NUL (0x00) characters.")

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


# ---------------------------------------------------------------------------
# NUL characters: refused with a 400 before any route runs
# ---------------------------------------------------------------------------


def _assert_nul_refused(resp):
    assert resp.status_code == 400, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["error"] == "ValidationError"
    assert "NUL" in body["message"]
    assert "string literal" not in body["message"]


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "QA2-nul\u0000x"},
        {"items": [{"description": "line\u0000"}]},
        {"key\u0000": "value"},
    ],
)
def test_nul_in_a_json_body_is_a_400(app, payload):
    # The JSON text carries it as the escape \u0000, which JSONB refuses as well as text columns.
    _assert_nul_refused(app.test_client().post("/api/v1/__probe__/json", json=payload))


def test_nul_in_a_query_string_or_path_is_a_400(app):
    client = app.test_client()
    _assert_nul_refused(client.get("/api/v1/__probe__/args/ok?q=%00"))
    _assert_nul_refused(client.get("/api/v1/__probe__/args/a%00b"))


def test_nul_in_form_fields_and_file_names_is_a_400(app):
    import io

    client = app.test_client()
    _assert_nul_refused(client.post("/api/v1/__probe__/form", data={"caption": "a\x00b"}))
    _assert_nul_refused(
        client.post(
            "/api/v1/__probe__/form",
            data={"file": (io.BytesIO(b"x"), "photo\x00.jpg")},
            content_type="multipart/form-data",
        )
    )


def test_text_without_nul_still_reaches_the_route(app):
    client = app.test_client()
    resp = client.post("/api/v1/__probe__/json", json={"name": "Thợ chính \u00e9"})
    assert resp.status_code == 200
    assert resp.get_json() == {"body": {"name": "Thợ chính é"}}
    assert client.get("/api/v1/__probe__/args/ok?q=abc").get_json() == {"name": "ok", "q": "abc"}


def test_nul_refused_by_the_database_is_a_400_not_a_500(app):
    app.config["PROPAGATE_EXCEPTIONS"] = False
    try:
        resp = app.test_client().get("/api/v1/__probe__/nul-in-db")
    finally:
        app.config["PROPAGATE_EXCEPTIONS"] = None
    _assert_nul_refused(resp)
