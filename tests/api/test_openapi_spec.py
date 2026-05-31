"""
Tests locking the OpenAPI spec generator contract and the prod gate.

Cases covered:
  1. Spec shape: 200, openapi 3.0.x, non-empty paths, correct info + security scheme.
  2. Full route coverage (dynamic): every expected (path, method) from url_map appears in spec.
  3. Request enrichment: CreateProject POST has requestBody; CreateProjectRequest in schemas.
  4. Public vs secured: login has no security; projects GET requires bearerAuth.
  5. Prod gate OFF: FLASK_ENV=production blocks /openapi.json and /v1/documentation/.
  6. Prod gate FORCE ON: FLASK_ENV=production + EXPOSE_DOCS=1 → 200.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Helpers shared across cases
# ---------------------------------------------------------------------------

# Skip rules mirror generator.py exactly — keep in sync.
_DOCS_ENDPOINT_PREFIXES = ("openapi.", "swagger_ui.")
_ALLOWED_ROUTE_PREFIXES = ("/api", "/health")
_SKIP_METHODS = {"HEAD", "OPTIONS"}


def _compute_expected_paths(app) -> set[tuple[str, str]]:
    """
    Compute the set of (openapi_path, method) pairs the generator should expose.

    Mirrors the skip logic in generator.build_spec() without reimplementing
    the path-variable conversion — imports the generator helper directly.
    """
    from app.api.openapi.generator import _flask_path_to_openapi

    expected: set[tuple[str, str]] = set()
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        if any(rule.endpoint.startswith(p) for p in _DOCS_ENDPOINT_PREFIXES):
            continue

        rule_str = rule.rule
        if not (rule_str.startswith("/api") or rule_str == "/health" or rule_str.startswith("/health")):
            continue

        methods = rule.methods - _SKIP_METHODS  # type: ignore[operator]
        if not methods:
            continue

        openapi_path, _ = _flask_path_to_openapi(rule_str)
        for method in methods:
            expected.add((openapi_path, method.lower()))

    return expected


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def docs_app():
    """
    Flask app with docs ENABLED (default dev — no FLASK_ENV=production).
    Uses TestingConfig which already disables rate-limiting overhead.
    """
    from app import create_app, db
    from config import TestingConfig

    class DocsTestConfig(TestingConfig):
        RATELIMIT_ENABLED: bool = False
        RATELIMIT_STORAGE_URI: str = "memory://"
        JWT_TOKEN_LOCATION = ["headers", "cookies"]

    app = create_app(DocsTestConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture(scope="module")
def docs_client(docs_app):
    return docs_app.test_client()


@pytest.fixture(scope="module")
def spec(docs_client):
    """Parsed spec dict — fetched once per module."""
    resp = docs_client.get("/openapi.json")
    assert resp.status_code == 200
    return resp.get_json()


# A config that passes the production boot guards (no "dev-" keys, no minioadmin).
class _ProdSafeConfig:
    """Minimal class-level attributes sufficient to pass create_app() prod guards."""

    TESTING: bool = True
    DATABASE_URL: str = "sqlite:///:memory:"
    RATELIMIT_ENABLED: bool = False
    RATELIMIT_STORAGE_URI: str = "memory://"
    JWT_TOKEN_LOCATION = ["headers", "cookies"]
    DEBUG: bool = False
    # Must not contain "dev-" in lower-case for production boot guard.
    JWT_SECRET_KEY: str = "test-secret-jwt-key-safe-for-prod-guard"
    SECRET_KEY: str = "test-secret-flask-key-safe-for-prod-guard"
    # Must not be minioadmin, must not be localhost.
    S3_ACCESS_KEY: str = "test-access-key"
    S3_SECRET_KEY: str = "test-secret-key"
    S3_ENDPOINT_URL: str = "http://s3.test-bucket.example.com"
    S3_BUCKET: str = "test-bucket"
    S3_REGION: str = "us-east-1"
    S3_PUBLIC_ENDPOINT_URL: str = ""
    # Other config attrs accessed by create_app() internals
    EMAIL_PROVIDER: str = "inmemory"
    RESEND_API_KEY: str = "test"
    FROM_EMAIL: str = "test@example.com"
    APP_BASE_URL: str = "http://localhost:3000"
    REDIS_URL: str = "redis://localhost:6379/0"
    SMTP_HOST: str = "localhost"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASS: str = ""
    SMTP_USE_TLS: bool = True
    MAX_CONTENT_LENGTH: int = 151 * 1024 * 1024
    EXPOSE_DOCS: bool = False

    def __post_init__(self):
        pass


# ---------------------------------------------------------------------------
# Case 1: Spec shape
# ---------------------------------------------------------------------------


class TestSpecShape:
    def test_status_200(self, docs_client):
        resp = docs_client.get("/openapi.json")
        assert resp.status_code == 200

    def test_openapi_version_prefix(self, spec):
        assert spec["openapi"].startswith("3.0")

    def test_paths_non_empty(self, spec):
        assert len(spec.get("paths", {})) > 0

    def test_info_title(self, spec):
        assert spec["info"]["title"] == "Folio API"

    def test_bearer_auth_scheme_present(self, spec):
        schemes = spec.get("components", {}).get("securitySchemes", {})
        assert "bearerAuth" in schemes
        scheme = schemes["bearerAuth"]
        assert scheme["type"] == "http"
        assert scheme["scheme"] == "bearer"


# ---------------------------------------------------------------------------
# Case 2: Full route coverage — dynamic, no hardcoded counts
# ---------------------------------------------------------------------------


class TestRouteCoverage:
    def test_all_expected_routes_in_spec(self, docs_app, spec):
        """Every (path, method) the generator should expose is actually in the spec."""
        expected = _compute_expected_paths(docs_app)
        spec_paths = spec.get("paths", {})

        missing: list[str] = []
        for openapi_path, method in expected:
            if openapi_path not in spec_paths or method not in spec_paths[openapi_path]:
                missing.append(f"{method.upper()} {openapi_path}")

        assert not missing, "The following routes were expected in the spec but not found:\n" + "\n".join(
            f"  - {r}" for r in sorted(missing)
        )


# ---------------------------------------------------------------------------
# Case 3: Request enrichment
# ---------------------------------------------------------------------------


class TestRequestEnrichment:
    def test_create_project_has_request_body(self, spec):
        post_op = spec["paths"]["/api/v1/projects"]["post"]
        assert "requestBody" in post_op

    def test_create_project_request_schema_registered(self, spec):
        schemas = spec.get("components", {}).get("schemas", {})
        assert "CreateProjectRequest" in schemas


# ---------------------------------------------------------------------------
# Case 4: Public vs secured endpoints
# ---------------------------------------------------------------------------


class TestSecurityAnnotations:
    def test_login_has_no_bearer_security(self, spec):
        """Login is public — must not carry bearerAuth requirement."""
        post_op = spec["paths"]["/api/v1/auth/login"]["post"]
        # Either security key absent or the list is empty/falsy.
        security = post_op.get("security", None)
        assert not security, f"login POST should be public (no security requirement), got: {security}"

    def test_projects_list_requires_bearer(self, spec):
        """GET /api/v1/projects is secured — must list bearerAuth."""
        get_op = spec["paths"]["/api/v1/projects"]["get"]
        security = get_op.get("security", [])
        bearer_schemes = [list(s.keys()) for s in security]
        flat_schemes = [name for names in bearer_schemes for name in names]
        assert "bearerAuth" in flat_schemes, f"GET /api/v1/projects must require bearerAuth, got security: {security}"


# ---------------------------------------------------------------------------
# Case 5: Prod gate OFF — docs endpoints return 404
# ---------------------------------------------------------------------------


class TestProdGateOff:
    def test_openapi_json_404_in_production(self, monkeypatch):
        monkeypatch.setenv("FLASK_ENV", "production")
        monkeypatch.delenv("EXPOSE_DOCS", raising=False)

        from app import create_app, db

        app = create_app(_ProdSafeConfig)
        with app.app_context():
            db.create_all()
            client = app.test_client()
            resp = client.get("/openapi.json")
            assert (
                resp.status_code == 404
            ), f"Expected 404 for /openapi.json in production (no EXPOSE_DOCS), got {resp.status_code}"
            db.drop_all()

    def test_swagger_ui_404_in_production(self, monkeypatch):
        monkeypatch.setenv("FLASK_ENV", "production")
        monkeypatch.delenv("EXPOSE_DOCS", raising=False)

        from app import create_app, db

        app = create_app(_ProdSafeConfig)
        with app.app_context():
            db.create_all()
            client = app.test_client()
            resp = client.get("/v1/documentation/")
            assert (
                resp.status_code == 404
            ), f"Expected 404 for /v1/documentation/ in production (no EXPOSE_DOCS), got {resp.status_code}"
            db.drop_all()


# ---------------------------------------------------------------------------
# Case 6: Prod gate FORCE ON — EXPOSE_DOCS=1 overrides production gate
# ---------------------------------------------------------------------------


class TestProdGateForceOn:
    def test_openapi_json_200_when_expose_docs_set(self, monkeypatch):
        monkeypatch.setenv("FLASK_ENV", "production")
        monkeypatch.setenv("EXPOSE_DOCS", "1")

        from app import create_app, db

        class _ProdSafeExposedConfig(_ProdSafeConfig):
            EXPOSE_DOCS: bool = True

        app = create_app(_ProdSafeExposedConfig)
        with app.app_context():
            db.create_all()
            client = app.test_client()
            resp = client.get("/openapi.json")
            assert (
                resp.status_code == 200
            ), f"Expected 200 for /openapi.json with EXPOSE_DOCS=1 in production, got {resp.status_code}"
            db.drop_all()
