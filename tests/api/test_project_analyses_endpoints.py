"""Integration tests for project-scoped analysis report endpoints (8 routes).

Authorization model — TWO TIERS (read carefully):
  * Read (list, tags, get, content) and create: project membership sufficient.
  * Mutate (PATCH, DELETE): membership AND (uploader OR project owner OR admin).

Security notes:
  - Content route must return specific headers: X-Content-Type-Options,
    CSP string, Content-Disposition: inline, Cache-Control: private, no-store.
  - PATCH field-drop regression: patching summary must not null title/tags.
  - Cross-project guard: get/content on another project's analysis must 404.

SQLite UUID caveat: see test_project_documents_api.py module docstring.
"""

from __future__ import annotations

import io
import pytest
from uuid import uuid4


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _auth(token: str) -> dict:
    """Build Authorization header."""
    return {"Authorization": f"Bearer {token}"}


def _analyses_url(project_id: str) -> str:
    """Base URL for project analyses list."""
    return f"/api/v1/projects/{project_id}/analyses"


def _tags_url(project_id: str) -> str:
    """URL for project analysis tags vocabulary."""
    return f"/api/v1/projects/{project_id}/analyses/tags"


def _analysis_url(project_id: str, analysis_id: str) -> str:
    """URL for analysis metadata."""
    return f"/api/v1/projects/{project_id}/analyses/{analysis_id}"


def _content_url(project_id: str, analysis_id: str) -> str:
    """URL for analysis content."""
    return f"/api/v1/projects/{project_id}/analyses/{analysis_id}/content"


def _upload(
    client,
    project_id: str,
    token: str,
    content: str = "<html><body>Test</body></html>",
    filename: str = "test.html",
    title: str = "Test Analysis",
    summary: str | None = None,
    source_url: str | None = None,
    tags: list[str] | None = None,  # IGNORED - use MultiDict manually for tags
) -> object:
    """Execute a multipart upload and return response.

    NOTE: tags parameter is ignored here. For tags, construct the request
    manually using werkzeug.datastructures.MultiDict. See test cases above.
    """
    data = {
        "file": (io.BytesIO(content.encode("utf-8")), filename, "text/html"),
        "title": title,
    }
    if summary is not None:
        data["summary"] = summary
    if source_url is not None:
        data["source_url"] = source_url

    return client.post(
        _analyses_url(project_id),
        data=data,
        content_type="multipart/form-data",
        headers=_auth(token),
    )


def _upload_analysis(
    client,
    project_id: str,
    token: str,
    title: str = "Test Analysis",
    summary: str | None = None,
    source_url: str | None = None,
) -> str:
    """Helper: upload an analysis and return its UUID."""
    resp = _upload(
        client,
        project_id,
        token,
        title=title,
        summary=summary,
        source_url=source_url,
    )
    assert resp.status_code == 201, f"Upload failed: {resp.get_data(as_text=True)}"
    return resp.get_json()["id"]


def _login(client, email: str, password: str) -> str:
    """Authenticate and return access token."""
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, f"Login failed: {resp.get_data(as_text=True)}"
    return resp.get_json()["access_token"]


@pytest.fixture
def analyses_app(invitation_app):
    """Use the existing invitation_app which has analyses use-cases wired.

    invitation_app has:
    - admin_user (project owner, but NOT in project membership)
    - member_user (project member)
    - target_user (project member)
    - outsider_user (not a member)
    - superadmin_user (has *:*)

    For our tests:
    - analyses_owner: target_user (member but different from main uploader)
    - analyses_member: member_user (main test uploader)
    - analyses_another: admin_user (project owner, not in membership, tests owner rights)
    - outsider: outsider_user (not in project)
    - superadmin: superadmin_user
    """
    # Add admin_user to project as a member so it can upload
    from sqlalchemy import text
    from datetime import datetime, timezone
    from app import db

    with invitation_app.app_context():
        # Add admin_user as member of project (owner by right, but for tests needs to be in membership)
        db.session.execute(
            text(
                "INSERT INTO user_projects "
                "(user_id, project_id, role_id, invited_by_user_id, assigned_at) "
                "VALUES (:uid, :pid, :rid, NULL, :at) "
                "ON CONFLICT (user_id, project_id) DO NOTHING"
            ),
            {
                "uid": invitation_app._test_admin_user_id,
                "pid": invitation_app._test_project_id,
                "rid": invitation_app._test_member_role_id,  # Use member role, not admin
                "at": datetime.now(timezone.utc),
            },
        )
        db.session.commit()

    invitation_app._analyses_owner_email = invitation_app._test_target_user_email
    invitation_app._analyses_owner_password = "Target1234!"  # From conftest
    invitation_app._analyses_member_email = invitation_app._test_member_email
    invitation_app._analyses_member_password = invitation_app._test_member_password
    invitation_app._analyses_another_email = invitation_app._test_admin_email  # Project owner/admin
    invitation_app._analyses_another_password = invitation_app._test_admin_password
    invitation_app._analyses_outsider_email = invitation_app._test_outsider_email
    invitation_app._analyses_outsider_password = invitation_app._test_outsider_password
    invitation_app._analyses_superadmin_email = invitation_app._test_superadmin_email
    invitation_app._analyses_superadmin_password = invitation_app._test_superadmin_password
    invitation_app._analyses_project_id = invitation_app._test_project_id
    invitation_app._analyses_other_project_id = invitation_app._test_project_2_id
    invitation_app._analyses_owner_user_id = invitation_app._test_target_user_id
    invitation_app._analyses_member_user_id = invitation_app._test_member_user_id

    return invitation_app


@pytest.fixture
def inv_client(analyses_app):
    """Test client for the analyses app."""
    return analyses_app.test_client()


@pytest.fixture
def owner_token(inv_client, analyses_app):
    """JWT token for owner_user (target_user, project member)."""
    return _login(inv_client, analyses_app._analyses_owner_email, analyses_app._analyses_owner_password)


@pytest.fixture
def member_token(inv_client, analyses_app):
    """JWT token for member_user (main uploader in tests)."""
    return _login(inv_client, analyses_app._analyses_member_email, analyses_app._analyses_member_password)


@pytest.fixture
def another_token(inv_client, analyses_app):
    """JWT token for another_member (admin_user, project owner)."""
    return _login(inv_client, analyses_app._analyses_another_email, analyses_app._analyses_another_password)


@pytest.fixture
def outsider_token(inv_client, analyses_app):
    """JWT token for outsider_user (not in project)."""
    return _login(inv_client, analyses_app._analyses_outsider_email, analyses_app._analyses_outsider_password)


@pytest.fixture
def superadmin_token(inv_client, analyses_app):
    """JWT token for superadmin_user (has *:* permission)."""
    return _login(inv_client, analyses_app._analyses_superadmin_email, analyses_app._analyses_superadmin_password)


# ===========================================================================
# POST /api/v1/projects/<project_id>/analyses — upload report
# ===========================================================================


class TestCreateAnalysisEndpoint:
    """Upload happy path and validation error cases."""

    def test_201_member_uploads_analysis(self, inv_client, member_token, analyses_app):
        """Happy path: member uploads analysis → 201 with metadata."""
        from werkzeug.datastructures import MultiDict

        data_dict = {
            "file": (io.BytesIO(b"<html><body>Test</body></html>"), "test.html", "text/html"),
            "title": "Marketing Analysis",
            "summary": "Q3 market trends",
            "source_url": "https://example.com/report",
        }
        md = MultiDict(data_dict)
        md.add("tags", "market")
        md.add("tags", "q3")

        resp = inv_client.post(
            _analyses_url(analyses_app._analyses_project_id),
            data=md,
            content_type="multipart/form-data",
            headers=_auth(member_token),
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["title"] == "Marketing Analysis"
        assert data["summary"] == "Q3 market trends"
        assert data["source_url"] == "https://example.com/report"
        assert set(data["tags"]) == {"market", "q3"}
        assert "id" in data
        assert "project_id" in data
        assert "uploader_id" in data
        assert data["project_id"] == analyses_app._analyses_project_id
        assert "content_url" in data
        assert data["size_bytes"] > 0

    def test_201_response_shape_complete(self, inv_client, member_token, analyses_app):
        """Response shape includes all required fields."""
        resp = _upload(inv_client, analyses_app._analyses_project_id, member_token)
        assert resp.status_code == 201
        data = resp.get_json()
        required_keys = {
            "id",
            "project_id",
            "uploader_id",
            "title",
            "summary",
            "source_url",
            "size_bytes",
            "tags",
            "created_at",
            "updated_at",
            "content_url",
        }
        assert required_keys.issubset(data.keys())
        assert isinstance(data["tags"], list)

    def test_201_tags_normalized_lowercase_dedupe(self, inv_client, member_token, analyses_app):
        """Tags are normalized: lowercased, deduplicated."""
        # Create request with multiple tags
        from werkzeug.datastructures import MultiDict

        data_dict = {
            "file": (io.BytesIO(b"<html><body>Test</body></html>"), "test.html", "text/html"),
            "title": "Test Analysis",
        }
        md = MultiDict(data_dict)
        for tag in ["Market", "MARKET", "q3"]:
            md.add("tags", tag)

        resp = inv_client.post(
            _analyses_url(analyses_app._analyses_project_id),
            data=md,
            content_type="multipart/form-data",
            headers=_auth(member_token),
        )
        assert resp.status_code == 201
        tags = set(resp.get_json()["tags"])
        assert tags == {"market", "q3"}

    def test_400_missing_title(self, inv_client, member_token, analyses_app):
        """Title required → 400."""
        resp = inv_client.post(
            _analyses_url(analyses_app._analyses_project_id),
            data={
                "file": (io.BytesIO(b"<html></html>"), "test.html", "text/html"),
            },
            content_type="multipart/form-data",
            headers=_auth(member_token),
        )
        assert resp.status_code == 400
        assert "MissingTitle" in resp.get_json().get("error", "")

    def test_400_missing_file(self, inv_client, member_token, analyses_app):
        """No file → 400."""
        resp = inv_client.post(
            _analyses_url(analyses_app._analyses_project_id),
            data={"title": "Test"},
            content_type="multipart/form-data",
            headers=_auth(member_token),
        )
        assert resp.status_code == 400
        assert "MissingFile" in resp.get_json().get("error", "")

    def test_400_bad_extension(self, inv_client, member_token, analyses_app):
        """Wrong extension (not .html/.htm) → 400."""
        resp = _upload(
            inv_client,
            analyses_app._analyses_project_id,
            member_token,
            filename="test.pdf",
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "InvalidFile"

    def test_400_bad_mimetype(self, inv_client, member_token, analyses_app):
        """Wrong mimetype (not text/html/application/xhtml+xml) → 400."""
        resp = inv_client.post(
            _analyses_url(analyses_app._analyses_project_id),
            data={
                "file": (io.BytesIO(b"<html></html>"), "test.html", "application/pdf"),
                "title": (None, "Test"),
            },
            content_type="multipart/form-data",
            headers=_auth(member_token),
        )
        assert resp.status_code == 400
        assert "InvalidFile" in resp.get_json().get("error", "")

    def test_400_non_utf8_body(self, inv_client, member_token, analyses_app):
        """File body not valid UTF-8 → 400."""
        resp = inv_client.post(
            _analyses_url(analyses_app._analyses_project_id),
            data={
                "file": (io.BytesIO(b"\xff\xfe"), "test.html", "text/html"),
                "title": (None, "Test"),
            },
            content_type="multipart/form-data",
            headers=_auth(member_token),
        )
        assert resp.status_code == 400
        assert "InvalidFile" in resp.get_json().get("error", "")

    def test_413_file_too_large(self, inv_client, member_token, analyses_app):
        """File >2MB → 413."""
        # 2.1 MB of HTML
        large_content = "<html><body>" + ("x" * (2 * 1024 * 1024 + 100)) + "</body></html>"
        resp = _upload(
            inv_client,
            analyses_app._analyses_project_id,
            member_token,
            content=large_content,
        )
        assert resp.status_code == 413
        assert "FileTooLarge" in resp.get_json().get("error", "")

    def test_403_non_member_cannot_upload(self, inv_client, outsider_token, analyses_app):
        """Non-member → 403."""
        resp = _upload(inv_client, analyses_app._analyses_project_id, outsider_token)
        assert resp.status_code == 403

    def test_401_unauthenticated(self, inv_client, analyses_app):
        """No token → 401."""
        resp = inv_client.post(
            _analyses_url(analyses_app._analyses_project_id),
            data={
                "file": (io.BytesIO(b"<html></html>"), "test.html", "text/html"),
                "title": (None, "Test"),
            },
            content_type="multipart/form-data",
        )
        assert resp.status_code == 401


# ===========================================================================
# GET /api/v1/projects/<project_id>/analyses — list with pagination/filters
# ===========================================================================


class TestListAnalysesEndpoint:
    """List: pagination, search, tag AND-filter, sort/order, soft-deleted excluded."""

    def test_200_list_returns_paginated_items(self, inv_client, member_token, analyses_app):
        """List returns items + pagination metadata."""
        _upload_analysis(inv_client, analyses_app._analyses_project_id, member_token, title="A1")
        _upload_analysis(inv_client, analyses_app._analyses_project_id, member_token, title="A2")

        resp = inv_client.get(
            _analyses_url(analyses_app._analyses_project_id),
            headers=_auth(member_token),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "items" in data
        assert "total" in data
        assert "page" in data
        assert "per_page" in data
        assert len(data["items"]) == 2
        assert data["total"] == 2

    def test_200_search_matches_title_and_summary(self, inv_client, member_token, analyses_app):
        """Search `q` matches title and summary."""
        _upload_analysis(
            inv_client,
            analyses_app._analyses_project_id,
            member_token,
            title="Quarterly Report",
        )
        _upload_analysis(
            inv_client,
            analyses_app._analyses_project_id,
            member_token,
            title="Monthly Report",
        )

        # Search for "Quarterly" in title
        resp = inv_client.get(
            _analyses_url(analyses_app._analyses_project_id),
            query_string={"q": "Quarterly"},
            headers=_auth(member_token),
        )
        assert resp.status_code == 200
        items = resp.get_json()["items"]
        assert len(items) == 1
        assert items[0]["title"] == "Quarterly Report"

    def test_200_tag_and_filter(self, inv_client, member_token, analyses_app):
        """Multiple tags AND together (all must match)."""
        _upload_analysis(
            inv_client,
            analyses_app._analyses_project_id,
            member_token,
            title="Report1",
            tags=["market", "q3"],
        )
        _upload_analysis(
            inv_client,
            analyses_app._analyses_project_id,
            member_token,
            title="Report2",
            tags=["market", "q4"],
        )
        _upload_analysis(
            inv_client,
            analyses_app._analyses_project_id,
            member_token,
            title="Report3",
            tags=["q3"],
        )

        # Filter for both market AND q3
        resp = inv_client.get(
            _analyses_url(analyses_app._analyses_project_id),
            query_string={"tag": ["market", "q3"]},
            headers=_auth(member_token),
        )
        assert resp.status_code == 200
        items = resp.get_json()["items"]
        assert len(items) == 1
        assert items[0]["title"] == "Report1"

    def test_200_pagination_page_2(self, inv_client, member_token, analyses_app):
        """Pagination: page 2."""
        for i in range(3):
            _upload_analysis(inv_client, analyses_app._analyses_project_id, member_token, title=f"Report{i}")

        resp = inv_client.get(
            _analyses_url(analyses_app._analyses_project_id),
            query_string={"page": 2, "per_page": 2},
            headers=_auth(member_token),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["page"] == 2
        assert len(data["items"]) == 1
        assert data["total"] == 3

    def test_200_sort_by_title_asc(self, inv_client, member_token, analyses_app):
        """Sort by title ascending."""
        _upload_analysis(inv_client, analyses_app._analyses_project_id, member_token, title="Zulu")
        _upload_analysis(inv_client, analyses_app._analyses_project_id, member_token, title="Alpha")
        _upload_analysis(inv_client, analyses_app._analyses_project_id, member_token, title="Bravo")

        resp = inv_client.get(
            _analyses_url(analyses_app._analyses_project_id),
            query_string={"sort": "title", "order": "asc"},
            headers=_auth(member_token),
        )
        assert resp.status_code == 200
        items = resp.get_json()["items"]
        titles = [item["title"] for item in items]
        assert titles == ["Alpha", "Bravo", "Zulu"]

    def test_200_soft_deleted_excluded(self, inv_client, member_token, analyses_app):
        """Soft-deleted analyses excluded from list."""
        analysis_id = _upload_analysis(inv_client, analyses_app._analyses_project_id, member_token)

        # Delete it
        del_resp = inv_client.delete(
            _analysis_url(analyses_app._analyses_project_id, analysis_id),
            headers=_auth(member_token),
        )
        assert del_resp.status_code == 204

        # List should not include it
        resp = inv_client.get(
            _analyses_url(analyses_app._analyses_project_id),
            headers=_auth(member_token),
        )
        assert resp.status_code == 200
        ids = [item["id"] for item in resp.get_json()["items"]]
        assert analysis_id not in ids

    def test_403_non_member_cannot_list(self, inv_client, outsider_token, analyses_app):
        """Non-member → 403."""
        resp = inv_client.get(
            _analyses_url(analyses_app._analyses_project_id),
            headers=_auth(outsider_token),
        )
        assert resp.status_code == 403

    def test_401_unauthenticated(self, inv_client, analyses_app):
        """No token → 401."""
        resp = inv_client.get(_analyses_url(analyses_app._analyses_project_id))
        assert resp.status_code == 401


# ===========================================================================
# GET /api/v1/projects/<project_id>/analyses/tags — tag vocabulary
# ===========================================================================


class TestListAnalysisTagsEndpoint:
    """Tags: distinct vocabulary, excludes soft-deleted, member-only access."""

    def test_200_returns_distinct_tags(self, inv_client, member_token, analyses_app):
        """Returns all distinct tags."""
        _upload_analysis(inv_client, analyses_app._analyses_project_id, member_token, tags=["market", "q3"])
        _upload_analysis(inv_client, analyses_app._analyses_project_id, member_token, tags=["market", "q4"])

        resp = inv_client.get(
            _tags_url(analyses_app._analyses_project_id),
            headers=_auth(member_token),
        )
        assert resp.status_code == 200
        tags = resp.get_json()["tags"]
        assert set(tags) == {"market", "q3", "q4"}

    def test_200_empty_tags(self, inv_client, member_token, analyses_app):
        """No analyses → empty tags list."""
        resp = inv_client.get(
            _tags_url(analyses_app._analyses_project_id),
            headers=_auth(member_token),
        )
        assert resp.status_code == 200
        assert resp.get_json()["tags"] == []

    def test_200_excludes_tags_from_soft_deleted(self, inv_client, member_token, analyses_app):
        """Soft-deleted analysis tags excluded from vocabulary."""
        analysis_id = _upload_analysis(
            inv_client,
            analyses_app._analyses_project_id,
            member_token,
            tags=["unique-tag"],
        )
        _upload_analysis(inv_client, analyses_app._analyses_project_id, member_token, tags=["other-tag"])

        # Delete the first one
        inv_client.delete(
            _analysis_url(analyses_app._analyses_project_id, analysis_id),
            headers=_auth(member_token),
        )

        # Tags should exclude unique-tag
        resp = inv_client.get(
            _tags_url(analyses_app._analyses_project_id),
            headers=_auth(member_token),
        )
        assert resp.status_code == 200
        tags = resp.get_json()["tags"]
        assert "unique-tag" not in tags
        assert "other-tag" in tags

    def test_403_non_member_cannot_list_tags(self, inv_client, outsider_token, analyses_app):
        """Non-member → 403."""
        resp = inv_client.get(
            _tags_url(analyses_app._analyses_project_id),
            headers=_auth(outsider_token),
        )
        assert resp.status_code == 403

    def test_401_unauthenticated(self, inv_client, analyses_app):
        """No token → 401."""
        resp = inv_client.get(_tags_url(analyses_app._analyses_project_id))
        assert resp.status_code == 401


# ===========================================================================
# GET /api/v1/projects/<project_id>/analyses/<analysis_id> — metadata
# ===========================================================================


class TestGetAnalysisEndpoint:
    """Get metadata: happy path, 404 for missing/deleted/cross-project."""

    def test_200_returns_metadata(self, inv_client, member_token, analyses_app):
        """Happy path: returns metadata."""
        analysis_id = _upload_analysis(inv_client, analyses_app._analyses_project_id, member_token)

        resp = inv_client.get(
            _analysis_url(analyses_app._analyses_project_id, analysis_id),
            headers=_auth(member_token),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["id"] == analysis_id
        assert data["project_id"] == analyses_app._analyses_project_id

    def test_404_missing_analysis(self, inv_client, member_token, analyses_app):
        """Non-existent analysis → 404."""
        fake_id = str(uuid4())
        resp = inv_client.get(
            _analysis_url(analyses_app._analyses_project_id, fake_id),
            headers=_auth(member_token),
        )
        assert resp.status_code == 404

    def test_404_soft_deleted_analysis(self, inv_client, member_token, analyses_app):
        """Soft-deleted analysis → 404."""
        analysis_id = _upload_analysis(inv_client, analyses_app._analyses_project_id, member_token)

        # Delete it
        inv_client.delete(
            _analysis_url(analyses_app._analyses_project_id, analysis_id),
            headers=_auth(member_token),
        )

        # Get should 404
        resp = inv_client.get(
            _analysis_url(analyses_app._analyses_project_id, analysis_id),
            headers=_auth(member_token),
        )
        assert resp.status_code == 404

    def test_404_cross_project_guard(self, inv_client, member_token, analyses_app):
        """Analysis from other project → 404 (existence not leaked)."""
        analysis_id = _upload_analysis(inv_client, analyses_app._analyses_project_id, member_token)

        # Try to get it from other project
        resp = inv_client.get(
            _analysis_url(analyses_app._analyses_other_project_id, analysis_id),
            headers=_auth(member_token),
        )
        assert resp.status_code == 404

    def test_403_non_member_cannot_get(self, inv_client, outsider_token, analyses_app):
        """Non-member → 403."""
        analysis_id = _upload_analysis(
            inv_client,
            analyses_app._analyses_project_id,
            _login(inv_client, analyses_app._analyses_owner_email, analyses_app._analyses_owner_password),
        )

        resp = inv_client.get(
            _analysis_url(analyses_app._analyses_project_id, analysis_id),
            headers=_auth(outsider_token),
        )
        assert resp.status_code == 403

    def test_401_unauthenticated(self, inv_client, analyses_app):
        """No token → 401."""
        resp = inv_client.get(_analysis_url(analyses_app._analyses_project_id, str(uuid4())))
        assert resp.status_code == 401


# ===========================================================================
# GET /api/v1/projects/<project_id>/analyses/<analysis_id>/content — body
# ===========================================================================


class TestGetAnalysisContentEndpoint:
    """Content: exact bytes returned, all security headers asserted."""

    def test_200_returns_exact_content(self, inv_client, member_token, analyses_app):
        """Exact HTML body returned."""
        test_html = "<html><body>Test Content</body></html>"
        analysis_id = _upload_analysis(
            inv_client,
            analyses_app._analyses_project_id,
            member_token,
            content=test_html,
        )

        resp = inv_client.get(
            _content_url(analyses_app._analyses_project_id, analysis_id),
            headers=_auth(member_token),
        )
        assert resp.status_code == 200
        assert resp.get_data(as_text=True) == test_html

    def test_200_content_type_header(self, inv_client, member_token, analyses_app):
        """Content-Type: text/html; charset=utf-8."""
        analysis_id = _upload_analysis(inv_client, analyses_app._analyses_project_id, member_token)

        resp = inv_client.get(
            _content_url(analyses_app._analyses_project_id, analysis_id),
            headers=_auth(member_token),
        )
        assert resp.status_code == 200
        assert resp.headers["Content-Type"] == "text/html; charset=utf-8"

    def test_200_x_content_type_options_header(self, inv_client, member_token, analyses_app):
        """X-Content-Type-Options: nosniff."""
        analysis_id = _upload_analysis(inv_client, analyses_app._analyses_project_id, member_token)

        resp = inv_client.get(
            _content_url(analyses_app._analyses_project_id, analysis_id),
            headers=_auth(member_token),
        )
        assert resp.status_code == 200
        assert resp.headers["X-Content-Type-Options"] == "nosniff"

    def test_200_content_disposition_header(self, inv_client, member_token, analyses_app):
        """Content-Disposition: inline; filename=..."""
        analysis_id = _upload_analysis(
            inv_client,
            analyses_app._analyses_project_id,
            member_token,
            title="My Test Report",
        )

        resp = inv_client.get(
            _content_url(analyses_app._analyses_project_id, analysis_id),
            headers=_auth(member_token),
        )
        assert resp.status_code == 200
        disposition = resp.headers["Content-Disposition"]
        assert disposition.startswith("inline; filename=")

    def test_200_cache_control_header(self, inv_client, member_token, analyses_app):
        """Cache-Control: private, no-store."""
        analysis_id = _upload_analysis(inv_client, analyses_app._analyses_project_id, member_token)

        resp = inv_client.get(
            _content_url(analyses_app._analyses_project_id, analysis_id),
            headers=_auth(member_token),
        )
        assert resp.status_code == 200
        assert resp.headers["Cache-Control"] == "private, no-store"

    def test_200_csp_header_contains_required_directives(self, inv_client, member_token, analyses_app):
        """CSP header contains unsafe-inline for style-src and script-src."""
        analysis_id = _upload_analysis(inv_client, analyses_app._analyses_project_id, member_token)

        resp = inv_client.get(
            _content_url(analyses_app._analyses_project_id, analysis_id),
            headers=_auth(member_token),
        )
        assert resp.status_code == 200
        csp = resp.headers.get("Content-Security-Policy", "")
        assert "style-src 'unsafe-inline'" in csp
        assert "script-src 'unsafe-inline'" in csp
        assert "frame-ancestors 'self'" in csp

    def test_404_missing_analysis(self, inv_client, member_token, analyses_app):
        """Non-existent analysis → 404."""
        resp = inv_client.get(
            _content_url(analyses_app._analyses_project_id, str(uuid4())),
            headers=_auth(member_token),
        )
        assert resp.status_code == 404

    def test_404_soft_deleted_analysis(self, inv_client, member_token, analyses_app):
        """Soft-deleted analysis → 404."""
        analysis_id = _upload_analysis(inv_client, analyses_app._analyses_project_id, member_token)

        inv_client.delete(
            _analysis_url(analyses_app._analyses_project_id, analysis_id),
            headers=_auth(member_token),
        )

        resp = inv_client.get(
            _content_url(analyses_app._analyses_project_id, analysis_id),
            headers=_auth(member_token),
        )
        assert resp.status_code == 404

    def test_403_non_member_cannot_get_content(self, inv_client, outsider_token, analyses_app, owner_token):
        """Non-member → 403."""
        analysis_id = _upload_analysis(inv_client, analyses_app._analyses_project_id, owner_token)

        resp = inv_client.get(
            _content_url(analyses_app._analyses_project_id, analysis_id),
            headers=_auth(outsider_token),
        )
        assert resp.status_code == 403

    def test_401_unauthenticated(self, inv_client, analyses_app):
        """No token → 401."""
        resp = inv_client.get(_content_url(analyses_app._analyses_project_id, str(uuid4())))
        assert resp.status_code == 401


# ===========================================================================
# PATCH /api/v1/projects/<project_id>/analyses/<analysis_id> — update
# ===========================================================================


class TestUpdateAnalysisEndpoint:
    """PATCH: field-drop regression (single-field patches), authz matrix."""

    def test_200_patch_title_only(self, inv_client, member_token, analyses_app):
        """Patch ONLY title leaves summary/source_url/tags untouched."""
        analysis_id = _upload_analysis(
            inv_client,
            analyses_app._analyses_project_id,
            member_token,
            title="Old Title",
            summary="Summary",
            source_url="https://example.com",
            tags=["tag1"],
        )

        resp = inv_client.patch(
            _analysis_url(analyses_app._analyses_project_id, analysis_id),
            json={"title": "New Title"},
            headers=_auth(member_token),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["title"] == "New Title"
        assert data["summary"] == "Summary"
        assert data["source_url"] == "https://example.com"
        assert data["tags"] == ["tag1"]

    def test_200_patch_summary_only_field_drop_regression(self, inv_client, member_token, analyses_app):
        """Patch ONLY summary must not null title (regression test)."""
        analysis_id = _upload_analysis(
            inv_client,
            analyses_app._analyses_project_id,
            member_token,
            title="Keep This",
            summary="Old Summary",
            tags=["keep"],
        )

        resp = inv_client.patch(
            _analysis_url(analyses_app._analyses_project_id, analysis_id),
            json={"summary": "New Summary"},
            headers=_auth(member_token),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["title"] == "Keep This", "PATCH summary must NOT null title"
        assert data["summary"] == "New Summary"
        assert data["tags"] == ["keep"], "PATCH summary must NOT clear tags"

    def test_200_patch_source_url_only(self, inv_client, member_token, analyses_app):
        """Patch ONLY source_url leaves others untouched."""
        analysis_id = _upload_analysis(
            inv_client,
            analyses_app._analyses_project_id,
            member_token,
            title="Title",
            source_url="https://old.com",
            tags=["tag1"],
        )

        resp = inv_client.patch(
            _analysis_url(analyses_app._analyses_project_id, analysis_id),
            json={"source_url": "https://new.com"},
            headers=_auth(member_token),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["title"] == "Title"
        assert data["source_url"] == "https://new.com"
        assert data["tags"] == ["tag1"]

    def test_200_patch_tags_only(self, inv_client, member_token, analyses_app):
        """Patch ONLY tags leaves others untouched."""
        analysis_id = _upload_analysis(
            inv_client,
            analyses_app._analyses_project_id,
            member_token,
            title="Title",
            summary="Summary",
            tags=["old"],
        )

        resp = inv_client.patch(
            _analysis_url(analyses_app._analyses_project_id, analysis_id),
            json={"tags": ["new", "tags"]},
            headers=_auth(member_token),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["title"] == "Title"
        assert data["summary"] == "Summary"
        assert set(data["tags"]) == {"new", "tags"}

    def test_200_patch_multiple_fields(self, inv_client, member_token, analyses_app):
        """Patch multiple fields together."""
        analysis_id = _upload_analysis(inv_client, analyses_app._analyses_project_id, member_token)

        resp = inv_client.patch(
            _analysis_url(analyses_app._analyses_project_id, analysis_id),
            json={"title": "New", "summary": "Updated", "tags": ["new"]},
            headers=_auth(member_token),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["title"] == "New"
        assert data["summary"] == "Updated"
        assert data["tags"] == ["new"]

    def test_200_patch_clear_summary_explicitly(self, inv_client, member_token, analyses_app):
        """Explicitly clear summary by passing null."""
        analysis_id = _upload_analysis(
            inv_client,
            analyses_app._analyses_project_id,
            member_token,
            summary="To Clear",
        )

        resp = inv_client.patch(
            _analysis_url(analyses_app._analyses_project_id, analysis_id),
            json={"summary": None},
            headers=_auth(member_token),
        )
        assert resp.status_code == 200
        assert resp.get_json()["summary"] is None

    def test_200_patch_clear_tags_explicitly(self, inv_client, member_token, analyses_app):
        """Explicitly clear tags by passing empty list."""
        analysis_id = _upload_analysis(
            inv_client,
            analyses_app._analyses_project_id,
            member_token,
            tags=["tag1"],
        )

        resp = inv_client.patch(
            _analysis_url(analyses_app._analyses_project_id, analysis_id),
            json={"tags": []},
            headers=_auth(member_token),
        )
        assert resp.status_code == 200
        assert resp.get_json()["tags"] == []

    def test_403_non_uploader_member_cannot_patch(self, inv_client, member_token, another_token, analyses_app):
        """Member who didn't upload cannot PATCH (regression test for two-tier authz)."""
        analysis_id = _upload_analysis(inv_client, analyses_app._analyses_project_id, member_token)

        # another_token is a project member but didn't upload this analysis
        resp = inv_client.patch(
            _analysis_url(analyses_app._analyses_project_id, analysis_id),
            json={"title": "Hijacked"},
            headers=_auth(another_token),
        )
        assert resp.status_code == 403

    def test_200_project_owner_can_patch_others_analysis(self, inv_client, member_token, owner_token, analyses_app):
        """Project owner can PATCH any analysis (uploader or not)."""
        analysis_id = _upload_analysis(inv_client, analyses_app._analyses_project_id, member_token)

        resp = inv_client.patch(
            _analysis_url(analyses_app._analyses_project_id, analysis_id),
            json={"title": "Owner Edit"},
            headers=_auth(owner_token),
        )
        assert resp.status_code == 200
        assert resp.get_json()["title"] == "Owner Edit"

    def test_200_admin_can_patch_any_analysis(self, inv_client, member_token, superadmin_token, analyses_app):
        """Admin (*:*) can PATCH any analysis."""
        analysis_id = _upload_analysis(inv_client, analyses_app._analyses_project_id, member_token)

        resp = inv_client.patch(
            _analysis_url(analyses_app._analyses_project_id, analysis_id),
            json={"title": "Admin Edit"},
            headers=_auth(superadmin_token),
        )
        assert resp.status_code == 200
        assert resp.get_json()["title"] == "Admin Edit"

    def test_403_non_member_cannot_patch(self, inv_client, outsider_token, analyses_app, owner_token):
        """Non-member → 403."""
        analysis_id = _upload_analysis(inv_client, analyses_app._analyses_project_id, owner_token)

        resp = inv_client.patch(
            _analysis_url(analyses_app._analyses_project_id, analysis_id),
            json={"title": "Hijacked"},
            headers=_auth(outsider_token),
        )
        assert resp.status_code == 403

    def test_404_patch_missing_analysis(self, inv_client, member_token, analyses_app):
        """Patch non-existent → 404."""
        resp = inv_client.patch(
            _analysis_url(analyses_app._analyses_project_id, str(uuid4())),
            json={"title": "New"},
            headers=_auth(member_token),
        )
        assert resp.status_code == 404

    def test_404_patch_soft_deleted(self, inv_client, member_token, analyses_app):
        """Patch soft-deleted → 404."""
        analysis_id = _upload_analysis(inv_client, analyses_app._analyses_project_id, member_token)

        inv_client.delete(
            _analysis_url(analyses_app._analyses_project_id, analysis_id),
            headers=_auth(member_token),
        )

        resp = inv_client.patch(
            _analysis_url(analyses_app._analyses_project_id, analysis_id),
            json={"title": "New"},
            headers=_auth(member_token),
        )
        assert resp.status_code == 404

    def test_401_unauthenticated(self, inv_client, analyses_app):
        """No token → 401."""
        resp = inv_client.patch(
            _analysis_url(analyses_app._analyses_project_id, str(uuid4())),
            json={"title": "New"},
        )
        assert resp.status_code == 401


# ===========================================================================
# DELETE /api/v1/projects/<project_id>/analyses/<analysis_id> — soft-delete
# ===========================================================================


class TestDeleteAnalysisEndpoint:
    """DELETE: 204 response, soft-deleted behavior, authz matrix."""

    def test_204_uploader_can_delete(self, inv_client, member_token, analyses_app):
        """Uploader can DELETE → 204."""
        analysis_id = _upload_analysis(inv_client, analyses_app._analyses_project_id, member_token)

        resp = inv_client.delete(
            _analysis_url(analyses_app._analyses_project_id, analysis_id),
            headers=_auth(member_token),
        )
        assert resp.status_code == 204

    def test_204_then_get_returns_404(self, inv_client, member_token, analyses_app):
        """After DELETE, GET returns 404 (soft-deleted)."""
        analysis_id = _upload_analysis(inv_client, analyses_app._analyses_project_id, member_token)

        inv_client.delete(
            _analysis_url(analyses_app._analyses_project_id, analysis_id),
            headers=_auth(member_token),
        )

        resp = inv_client.get(
            _analysis_url(analyses_app._analyses_project_id, analysis_id),
            headers=_auth(member_token),
        )
        assert resp.status_code == 404

    def test_204_then_list_excludes_deleted(self, inv_client, member_token, analyses_app):
        """After DELETE, list excludes the analysis."""
        analysis_id = _upload_analysis(inv_client, analyses_app._analyses_project_id, member_token)

        inv_client.delete(
            _analysis_url(analyses_app._analyses_project_id, analysis_id),
            headers=_auth(member_token),
        )

        resp = inv_client.get(
            _analyses_url(analyses_app._analyses_project_id),
            headers=_auth(member_token),
        )
        assert resp.status_code == 200
        ids = [item["id"] for item in resp.get_json()["items"]]
        assert analysis_id not in ids

    def test_200_project_owner_can_delete_others_analysis(self, inv_client, member_token, owner_token, analyses_app):
        """Project owner can DELETE any analysis."""
        analysis_id = _upload_analysis(inv_client, analyses_app._analyses_project_id, member_token)

        resp = inv_client.delete(
            _analysis_url(analyses_app._analyses_project_id, analysis_id),
            headers=_auth(owner_token),
        )
        assert resp.status_code == 204

    def test_200_admin_can_delete_any_analysis(self, inv_client, member_token, superadmin_token, analyses_app):
        """Admin (*:*) can DELETE any analysis."""
        analysis_id = _upload_analysis(inv_client, analyses_app._analyses_project_id, member_token)

        resp = inv_client.delete(
            _analysis_url(analyses_app._analyses_project_id, analysis_id),
            headers=_auth(superadmin_token),
        )
        assert resp.status_code == 204

    def test_403_non_uploader_member_cannot_delete(self, inv_client, member_token, another_token, analyses_app):
        """Member who didn't upload cannot DELETE (regression test)."""
        analysis_id = _upload_analysis(inv_client, analyses_app._analyses_project_id, member_token)

        resp = inv_client.delete(
            _analysis_url(analyses_app._analyses_project_id, analysis_id),
            headers=_auth(another_token),
        )
        assert resp.status_code == 403

    def test_403_non_member_cannot_delete(self, inv_client, outsider_token, analyses_app, owner_token):
        """Non-member → 403."""
        analysis_id = _upload_analysis(inv_client, analyses_app._analyses_project_id, owner_token)

        resp = inv_client.delete(
            _analysis_url(analyses_app._analyses_project_id, analysis_id),
            headers=_auth(outsider_token),
        )
        assert resp.status_code == 403

    def test_404_delete_missing_analysis(self, inv_client, member_token, analyses_app):
        """Delete non-existent → 404."""
        resp = inv_client.delete(
            _analysis_url(analyses_app._analyses_project_id, str(uuid4())),
            headers=_auth(member_token),
        )
        assert resp.status_code == 404

    def test_404_delete_already_deleted(self, inv_client, member_token, analyses_app):
        """Delete already-deleted → 404."""
        analysis_id = _upload_analysis(inv_client, analyses_app._analyses_project_id, member_token)

        inv_client.delete(
            _analysis_url(analyses_app._analyses_project_id, analysis_id),
            headers=_auth(member_token),
        )

        resp = inv_client.delete(
            _analysis_url(analyses_app._analyses_project_id, analysis_id),
            headers=_auth(member_token),
        )
        assert resp.status_code == 404

    def test_401_unauthenticated(self, inv_client, analyses_app):
        """No token → 401."""
        resp = inv_client.delete(_analysis_url(analyses_app._analyses_project_id, str(uuid4())))
        assert resp.status_code == 401
