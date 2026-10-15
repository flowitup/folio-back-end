"""Per-route request body caps: small by default, each upload route keeps its documented size."""

from __future__ import annotations

import ast
import io
from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask, request

from app.api._helpers import request_limits
from app.api._helpers.request_limits import (
    DEFAULT_MAX_BODY_BYTES,
    MULTIPART_FILE_LIMITS,
    MULTIPART_OVERHEAD_BYTES,
    max_body_bytes,
    register_request_limits,
)

API_DIR = Path(__file__).resolve().parents[3] / "app" / "api"

# Where each upload route's file-size constant lives, and how the route reads the file.
CAPS = {
    "project_documents.upload_project_document": (
        "app.application.project_documents.upload_project_document",
        ["MAX_SIZE_BYTES"],
    ),
    "project_photos.upload_project_photo": (
        "app.application.project_photos.upload_project_photo",
        ["MAX_SIZE_BYTES", "MAX_VIDEO_SIZE_BYTES"],
    ),
    "invoices.upload_attachment": ("app.application.invoice.upload_attachment", ["MAX_FILE_SIZE_BYTES"]),
    "chat.send_message": ("app.application.chat.usecases", ["MAX_ATTACHMENT_BYTES"]),
    "bibliotheque.upload_product_image": (
        "app.application.bibliotheque.upload_product_image_usecase",
        ["IMAGE_MAX_SIZE_BYTES"],
    ),
    "chiffrage.upload_article_image": ("app.application.chiffrage.article_image_usecases", ["IMAGE_MAX_SIZE_BYTES"]),
    "project_analyses.create_analysis": (
        "app.application.project_analyses.create_project_analysis_usecase",
        ["MAX_SIZE_BYTES"],
    ),
}


@pytest.fixture(scope="module")
def real_app():
    from app import create_app
    from config import TestingConfig

    class Config(TestingConfig):
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    return create_app(Config)


@pytest.fixture
def probe_app():
    """A bare app with only the limit hook and one probe route per upload endpoint."""
    app = Flask(__name__)
    register_request_limits(app)

    def make_view(name):
        def view():
            upload = request.files.get("file")
            return {"endpoint": name, "size": len(upload.read()) if upload else None}

        view.__name__ = name.replace(".", "_")
        return view

    for endpoint in MULTIPART_FILE_LIMITS:
        app.add_url_rule(f"/probe/{endpoint}", endpoint=endpoint, view_func=make_view(endpoint), methods=["POST"])

    @app.post("/probe/json")
    def probe_json():
        return {"keys": list(request.get_json())}

    return app


# --- inventory: every upload route is in the table --------------------------------------------


def _functions_reading_request_files() -> set[str]:
    names = set()
    for path in API_DIR.rglob("*.py"):
        if "_helpers" in path.parts:  # the body guards read the files, they are not routes
            continue
        tree = ast.parse(path.read_text())
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.Attribute)
                    and inner.attr == "files"
                    and getattr(inner.value, "id", "") == "request"
                ):
                    names.add(node.name)
    return names


def test_every_route_reading_request_files_has_a_limit(real_app):
    reading = _functions_reading_request_files()
    assert reading, "scan found no upload route: the test is broken"
    by_function = {real_app.view_functions[e].__name__: e for e in real_app.view_functions}
    missing = sorted(name for name in reading if by_function.get(name) not in MULTIPART_FILE_LIMITS)
    assert missing == []


def test_table_endpoints_exist_and_cover_every_documented_cap(real_app):
    assert set(MULTIPART_FILE_LIMITS) <= set(real_app.view_functions)
    assert set(MULTIPART_FILE_LIMITS) == set(CAPS)


def test_limits_follow_the_documented_constants():
    from app.application.invoice.upload_attachment import MAX_FILE_SIZE_BYTES
    from app.application.project_documents.upload_project_document import MAX_SIZE_BYTES

    assert max_body_bytes("project_documents.upload_project_document", "multipart/form-data") == (
        MAX_SIZE_BYTES + MULTIPART_OVERHEAD_BYTES
    )
    assert max_body_bytes("invoices.upload_attachment", "multipart/form-data") == (
        MAX_FILE_SIZE_BYTES + MULTIPART_OVERHEAD_BYTES
    )
    # the 150 MiB project document keeps the 151 MiB the app always allowed
    assert max_body_bytes("project_documents.upload_project_document", "multipart/form-data") == 151 * 1024 * 1024


def test_json_urlencoded_and_unknown_routes_get_the_small_default():
    assert max_body_bytes(None, "application/json") == DEFAULT_MAX_BODY_BYTES
    assert max_body_bytes("auth.request_otp", "application/json") == DEFAULT_MAX_BODY_BYTES
    assert max_body_bytes("project_documents.upload_project_document", "application/json") == DEFAULT_MAX_BODY_BYTES
    assert max_body_bytes("project_photos.upload_project_photo", "application/x-www-form-urlencoded") == (
        DEFAULT_MAX_BODY_BYTES
    )
    assert max_body_bytes("chat.send_message", "application/json") == DEFAULT_MAX_BODY_BYTES


def test_library_import_keeps_room_for_a_full_1000_record_payload():
    from app.api.v1.bibliotheque.schemas import ImportRecordSchema

    worst_record = sum(
        f.metadata[0].max_length if f.metadata and hasattr(f.metadata[0], "max_length") else 0
        for f in ImportRecordSchema.model_fields.values()
    )
    assert max_body_bytes("bibliotheque.import_purchases", "application/json") >= 1000 * worst_record


# --- every upload route still accepts a body at its documented size --------------------------------


@pytest.mark.parametrize("endpoint", sorted(CAPS))
def test_upload_route_accepts_its_documented_size_and_refuses_more(endpoint, probe_app, monkeypatch):
    import importlib

    module_name, constants = CAPS[endpoint]
    module = importlib.import_module(module_name)
    for constant in constants:
        monkeypatch.setattr(module, constant, 4096)
    monkeypatch.setattr(request_limits, "MULTIPART_OVERHEAD_BYTES", 1024)
    monkeypatch.setattr(request_limits, "DEFAULT_MAX_BODY_BYTES", 512)
    client = probe_app.test_client()
    url = f"/probe/{endpoint}"

    at_cap = client.post(url, data={"file": (io.BytesIO(b"x" * 4096), "f.bin")}, content_type="multipart/form-data")
    assert at_cap.status_code == 200, at_cap.get_data(as_text=True)
    assert at_cap.get_json()["size"] == 4096

    over = client.post(url, data={"file": (io.BytesIO(b"x" * 8192), "f.bin")}, content_type="multipart/form-data")
    assert over.status_code == 413


def test_the_same_route_refuses_a_json_body_over_the_default(probe_app, monkeypatch):
    monkeypatch.setattr(request_limits, "DEFAULT_MAX_BODY_BYTES", 512)
    client = probe_app.test_client()
    assert client.post("/probe/json", json={"a": 1}).status_code == 200
    assert client.post("/probe/json", json={"a": "x" * 1024}).status_code == 413


# --- oversized bodies are refused before any parsing -----------------------------------------------------


def _big_json() -> bytes:
    return b'{"x": "' + b"a" * (DEFAULT_MAX_BODY_BYTES + 10) + b'"}'


@pytest.mark.parametrize("path", ["/api/v1/auth/otp/request", "/api/v1/projects", "/api/v1/no-such-route"])
def test_oversized_json_is_a_413_without_parsing(real_app, path):
    with (
        patch("flask.wrappers.Request.get_json") as get_json,
        patch("app.api._helpers.nul_guard._request_has_nul") as has_nul,
        patch("json.loads") as loads,
    ):
        resp = real_app.test_client().post(path, data=_big_json(), content_type="application/json")
    assert resp.status_code == 413
    assert resp.is_json and resp.get_json()["status_code"] == 413
    get_json.assert_not_called()
    has_nul.assert_not_called()
    loads.assert_not_called()


def test_oversized_urlencoded_and_multipart_to_a_non_upload_route_are_refused(real_app):
    client = real_app.test_client()
    big = "a" * (DEFAULT_MAX_BODY_BYTES + 10)
    assert client.post("/api/v1/auth/otp/request", data={"x": big}).status_code == 413
    resp = client.post(
        "/api/v1/auth/otp/request",
        data={"file": (io.BytesIO(big.encode()), "f.bin")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 413


def test_nul_guard_refuses_an_oversized_body_even_without_the_limit_hook(real_app):
    from app.api._helpers.nul_guard import reject_nul_characters
    from werkzeug.exceptions import RequestEntityTooLarge

    with real_app.test_request_context(
        "/api/v1/x", method="POST", data=_big_json(), content_type="application/json"
    ) as ctx:
        ctx.request.max_content_length = DEFAULT_MAX_BODY_BYTES
        with patch("app.api._helpers.nul_guard._request_has_nul") as has_nul:
            with pytest.raises(RequestEntityTooLarge):
                reject_nul_characters()
        has_nul.assert_not_called()


def test_a_body_within_the_cap_still_reaches_the_nul_guard(real_app):
    resp = real_app.test_client().post("/api/v1/auth/otp/request", json={"phone": "a\u0000b"})
    assert resp.status_code == 400
    assert "NUL" in resp.get_json()["message"]


def test_nul_guard_does_not_parse_multipart_on_an_upload_route(real_app):
    from app.api._helpers.nul_guard import reject_nul_characters

    url = "/api/v1/projects/00000000-0000-0000-0000-000000000000/documents"
    data = {"file": (io.BytesIO(b"x" * 64), "f.bin")}
    with real_app.test_request_context(url, method="POST", data=data, content_type="multipart/form-data") as ctx:
        ctx.request.max_content_length = max_body_bytes(ctx.request.endpoint, "multipart/form-data")
        assert ctx.request.max_content_length > DEFAULT_MAX_BODY_BYTES
        with patch("werkzeug.formparser.FormDataParser.parse") as parse:
            assert reject_nul_characters() is None
        parse.assert_not_called()
