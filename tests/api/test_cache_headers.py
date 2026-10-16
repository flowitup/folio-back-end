"""config-deploy-16: authenticated API JSON is never cacheable by a proxy or CDN."""


def test_authenticated_api_answers_are_no_store(inv_client, admin_token):
    resp = inv_client.get("/api/v1/projects", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    assert resp.headers["Cache-Control"] == "no-store"
    assert "Authorization" in resp.headers.get("Vary", "")


def test_anonymous_public_answers_are_left_alone(inv_client):
    inv_client.delete_cookie("access_token_cookie")
    resp = inv_client.get("/api/v1/app-version")
    assert resp.status_code == 200
    assert "no-store" not in resp.headers.get("Cache-Control", "")


def test_s3_cors_lists_every_origin(monkeypatch):
    from app import _cors_origins

    monkeypatch.setenv("CORS_ORIGINS", "https://folio.example, https://www.folio.example,")
    assert _cors_origins() == ["https://folio.example", "https://www.folio.example"]
