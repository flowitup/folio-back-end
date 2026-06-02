"""Integration tests for project photos API endpoints."""

from __future__ import annotations

import io
from uuid import uuid4

from PIL import Image


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _photos_url(project_id: str) -> str:
    return f"/api/v1/projects/{project_id}/photos"


def _photo_url(project_id: str, photo_id: str) -> str:
    return f"/api/v1/projects/{project_id}/photos/{photo_id}"


def _thumbnail_url(project_id: str, photo_id: str) -> str:
    return f"/api/v1/projects/{project_id}/photos/{photo_id}/thumbnail"


def _original_url(project_id: str, photo_id: str) -> str:
    return f"/api/v1/projects/{project_id}/photos/{photo_id}/original"


def _make_jpeg_bytes(width: int = 50, height: int = 50) -> bytes:
    img = Image.new("RGB", (width, height), color=(200, 100, 50))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _upload_jpeg(client, token: str, project_id: str, **extra_form) -> dict:
    """Helper: POST a valid JPEG to the photos endpoint and return parsed JSON."""
    data = _make_jpeg_bytes()
    form = {
        "file": (io.BytesIO(data), "photo.jpg", "image/jpeg"),
        **extra_form,
    }
    resp = client.post(
        _photos_url(project_id),
        data=form,
        content_type="multipart/form-data",
        headers=_auth(token),
    )
    return resp


# ===========================================================================
# POST /api/v1/projects/<project_id>/photos — upload
# ===========================================================================


class TestUploadProjectPhoto:
    def test_201_valid_jpeg_response_shape(self, inv_client, admin_token, invitation_app):
        resp = _upload_jpeg(inv_client, admin_token, invitation_app._test_project_id)
        assert resp.status_code == 201, resp.get_data(as_text=True)
        data = resp.get_json()
        for key in (
            "id",
            "project_id",
            "filename",
            "content_type",
            "size_bytes",
            "caption",
            "captured_at",
            "uploaded_at",
            "uploader_id",
            "thumbnail_url",
            "original_url",
        ):
            assert key in data, f"Missing key: {key}"
        assert data["thumbnail_url"].endswith("/thumbnail")
        assert data["original_url"].endswith("/original")

    def test_201_with_caption_and_captured_at_persisted(self, inv_client, admin_token, invitation_app):
        jpeg = _make_jpeg_bytes()
        resp = inv_client.post(
            _photos_url(invitation_app._test_project_id),
            data={
                "file": (io.BytesIO(jpeg), "site.jpg", "image/jpeg"),
                "caption": "Day 5 progress",
                "captured_at": "2025-03-15",
            },
            content_type="multipart/form-data",
            headers=_auth(admin_token),
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["caption"] == "Day 5 progress"
        assert "2025-03-15" in data["captured_at"]

    def test_201_captured_at_iso_datetime_string(self, inv_client, admin_token, invitation_app):
        jpeg = _make_jpeg_bytes()
        resp = inv_client.post(
            _photos_url(invitation_app._test_project_id),
            data={
                "file": (io.BytesIO(jpeg), "wall.jpg", "image/jpeg"),
                "captured_at": "2025-06-01T10:30:00Z",
            },
            content_type="multipart/form-data",
            headers=_auth(admin_token),
        )
        assert resp.status_code == 201
        assert "2025-06-01" in resp.get_json()["captured_at"]

    def test_400_empty_file(self, inv_client, admin_token, invitation_app):
        resp = inv_client.post(
            _photos_url(invitation_app._test_project_id),
            data={"file": (io.BytesIO(b""), "empty.jpg", "image/jpeg")},
            content_type="multipart/form-data",
            headers=_auth(admin_token),
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "EMPTY_FILE"

    def test_400_missing_file_part(self, inv_client, admin_token, invitation_app):
        resp = inv_client.post(
            _photos_url(invitation_app._test_project_id),
            data={"caption": "no file"},
            content_type="multipart/form-data",
            headers=_auth(admin_token),
        )
        assert resp.status_code == 400

    def test_413_oversize_image(self, inv_client, admin_token, invitation_app, monkeypatch):
        import app.application.project_photos.upload_project_photo as _uc_mod

        monkeypatch.setattr(_uc_mod, "MAX_SIZE_BYTES", 10)
        jpeg = _make_jpeg_bytes()  # larger than 10 bytes
        resp = inv_client.post(
            _photos_url(invitation_app._test_project_id),
            data={"file": (io.BytesIO(jpeg), "big.jpg", "image/jpeg")},
            content_type="multipart/form-data",
            headers=_auth(admin_token),
        )
        assert resp.status_code == 413
        assert resp.get_json()["error"] == "FILE_TOO_LARGE"

    def test_415_unsupported_mime_type(self, inv_client, admin_token, invitation_app):
        resp = inv_client.post(
            _photos_url(invitation_app._test_project_id),
            data={"file": (io.BytesIO(b"hello world"), "doc.txt", "text/plain")},
            content_type="multipart/form-data",
            headers=_auth(admin_token),
        )
        assert resp.status_code == 415
        assert resp.get_json()["error"] == "UNSUPPORTED_TYPE"

    def test_422_corrupt_image_bytes(self, inv_client, admin_token, invitation_app):
        """Corrupt image bytes that pass MIME/extension check but fail thumbnail generation."""
        resp = inv_client.post(
            _photos_url(invitation_app._test_project_id),
            data={"file": (io.BytesIO(b"not_a_real_jpeg_ffff"), "corrupt.jpg", "image/jpeg")},
            content_type="multipart/form-data",
            headers=_auth(admin_token),
        )
        assert resp.status_code == 422
        assert resp.get_json()["error"] == "INVALID_IMAGE"

    def test_401_unauthenticated(self, inv_client, invitation_app):
        jpeg = _make_jpeg_bytes()
        resp = inv_client.post(
            _photos_url(invitation_app._test_project_id),
            data={"file": (io.BytesIO(jpeg), "photo.jpg", "image/jpeg")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 401


# ===========================================================================
# GET thumbnail / original — streaming
# ===========================================================================


class TestGetPhotoStream:
    def _upload_and_get_id(self, client, token, project_id):
        resp = _upload_jpeg(client, token, project_id)
        assert resp.status_code == 201
        return resp.get_json()["id"]

    def test_200_thumbnail_content_type_jpeg(self, inv_client, admin_token, invitation_app):
        photo_id = self._upload_and_get_id(inv_client, admin_token, invitation_app._test_project_id)
        resp = inv_client.get(
            _thumbnail_url(invitation_app._test_project_id, photo_id),
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200
        assert "image/jpeg" in resp.content_type

    def test_200_thumbnail_nosniff_header(self, inv_client, admin_token, invitation_app):
        photo_id = self._upload_and_get_id(inv_client, admin_token, invitation_app._test_project_id)
        resp = inv_client.get(
            _thumbnail_url(invitation_app._test_project_id, photo_id),
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"

    def test_200_original_content_type_matches_upload(self, inv_client, admin_token, invitation_app):
        photo_id = self._upload_and_get_id(inv_client, admin_token, invitation_app._test_project_id)
        resp = inv_client.get(
            _original_url(invitation_app._test_project_id, photo_id),
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200
        assert "image/jpeg" in resp.content_type

    def test_200_original_nosniff_header(self, inv_client, admin_token, invitation_app):
        photo_id = self._upload_and_get_id(inv_client, admin_token, invitation_app._test_project_id)
        resp = inv_client.get(
            _original_url(invitation_app._test_project_id, photo_id),
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"

    def test_404_nonexistent_photo(self, inv_client, admin_token, invitation_app):
        fake_id = str(uuid4())
        resp = inv_client.get(
            _thumbnail_url(invitation_app._test_project_id, fake_id),
            headers=_auth(admin_token),
        )
        assert resp.status_code == 404

    def test_401_unauthenticated_thumbnail(self, admin_token, invitation_app):
        """Fresh client (no auth cookie) must get 401."""
        with invitation_app.test_client() as fresh_client:
            photo_id = self._upload_and_get_id(fresh_client, admin_token, invitation_app._test_project_id)
            resp = fresh_client.get(_thumbnail_url(invitation_app._test_project_id, photo_id))
        assert resp.status_code == 401


# ===========================================================================
# GET list — pagination + ordering
# ===========================================================================


class TestListProjectPhotos:
    def test_200_list_response_shape(self, inv_client, admin_token, invitation_app):
        resp = inv_client.get(
            _photos_url(invitation_app._test_project_id),
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "items" in data
        assert "total" in data
        assert "page" in data
        assert "per_page" in data

    def test_200_list_ordered_captured_at_desc(self, inv_client, admin_token, invitation_app):
        """Photos uploaded with different captured_at must appear newest-first."""
        pid = invitation_app._test_project_id
        jpeg = _make_jpeg_bytes()
        # Upload three photos with distinct captured_at dates
        for date_str in ("2024-01-01", "2024-06-15", "2025-03-10"):
            r = inv_client.post(
                _photos_url(pid),
                data={
                    "file": (io.BytesIO(jpeg), "p.jpg", "image/jpeg"),
                    "captured_at": date_str,
                },
                content_type="multipart/form-data",
                headers=_auth(admin_token),
            )
            assert r.status_code == 201

        resp = inv_client.get(_photos_url(pid), headers=_auth(admin_token))
        assert resp.status_code == 200
        items = resp.get_json()["items"]
        captured_dates = [item["captured_at"] for item in items if item.get("captured_at")]
        assert captured_dates == sorted(captured_dates, reverse=True), "Photos must be ordered captured_at DESC"

    def test_200_pagination_per_page(self, inv_client, admin_token, invitation_app):
        pid = invitation_app._test_project_id
        resp = inv_client.get(
            f"{_photos_url(pid)}?page=1&per_page=2",
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["items"]) <= 2
        assert data["per_page"] == 2
        assert data["page"] == 1

    def test_401_unauthenticated_list(self, inv_client, invitation_app):
        resp = inv_client.get(_photos_url(invitation_app._test_project_id))
        assert resp.status_code == 401


# ===========================================================================
# PATCH /api/v1/projects/<project_id>/photos/<photo_id>
# ===========================================================================


class TestUpdateProjectPhoto:
    def _upload(self, client, token, project_id, **form):
        resp = _upload_jpeg(client, token, project_id, **form)
        assert resp.status_code == 201
        return resp.get_json()

    def test_200_patch_caption_only_leaves_captured_at_unchanged(self, inv_client, admin_token, invitation_app):
        pid = invitation_app._test_project_id
        jpeg = _make_jpeg_bytes()
        upload_resp = inv_client.post(
            _photos_url(pid),
            data={
                "file": (io.BytesIO(jpeg), "photo.jpg", "image/jpeg"),
                "captured_at": "2025-01-20",
                "caption": "original caption",
            },
            content_type="multipart/form-data",
            headers=_auth(admin_token),
        )
        assert upload_resp.status_code == 201
        photo_id = upload_resp.get_json()["id"]

        patch_resp = inv_client.patch(
            _photo_url(pid, photo_id),
            json={"caption": "updated caption"},
            headers=_auth(admin_token),
        )
        assert patch_resp.status_code == 200
        patched = patch_resp.get_json()
        assert patched["caption"] == "updated caption"
        # captured_at must NOT change — compare date portion (timezone repr may vary)
        assert patched["captured_at"].startswith("2025-01-20")

    def test_200_patch_captured_at_only_leaves_caption_unchanged(self, inv_client, admin_token, invitation_app):
        pid = invitation_app._test_project_id
        jpeg = _make_jpeg_bytes()
        upload_resp = inv_client.post(
            _photos_url(pid),
            data={
                "file": (io.BytesIO(jpeg), "photo2.jpg", "image/jpeg"),
                "caption": "keep this caption",
                "captured_at": "2025-01-01",
            },
            content_type="multipart/form-data",
            headers=_auth(admin_token),
        )
        assert upload_resp.status_code == 201
        photo_id = upload_resp.get_json()["id"]

        patch_resp = inv_client.patch(
            _photo_url(pid, photo_id),
            json={"captured_at": "2025-12-25T00:00:00Z"},
            headers=_auth(admin_token),
        )
        assert patch_resp.status_code == 200
        patched = patch_resp.get_json()
        assert patched["caption"] == "keep this caption"
        assert "2025-12-25" in patched["captured_at"]

    def test_404_patch_nonexistent_photo(self, inv_client, admin_token, invitation_app):
        resp = inv_client.patch(
            _photo_url(invitation_app._test_project_id, str(uuid4())),
            json={"caption": "nope"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 404

    def test_4xx_patch_missing_all_fields(self, inv_client, admin_token, invitation_app):
        """PATCH with neither caption nor captured_at must be rejected (400 empty body or 422 missing fields)."""
        # Upload a photo first so we have a real ID
        resp = _upload_jpeg(inv_client, admin_token, invitation_app._test_project_id)
        photo_id = resp.get_json()["id"]
        # Empty JSON object {} — route validates body is non-null JSON first (→400),
        # then checks that at least one field is present (→422).
        # An empty dict {} IS valid JSON, so we get 422 from the field presence check.
        patch_resp = inv_client.patch(
            _photo_url(invitation_app._test_project_id, photo_id),
            json={},
            headers=_auth(admin_token),
        )
        assert patch_resp.status_code in (400, 422)

    def test_401_unauthenticated_patch(self, inv_client, invitation_app, admin_token):
        resp = _upload_jpeg(inv_client, admin_token, invitation_app._test_project_id)
        photo_id = resp.get_json()["id"]
        patch_resp = inv_client.patch(
            _photo_url(invitation_app._test_project_id, photo_id),
            json={"caption": "x"},
        )
        assert patch_resp.status_code == 401


# ===========================================================================
# DELETE /api/v1/projects/<project_id>/photos/<photo_id>
# ===========================================================================


class TestDeleteProjectPhoto:
    def test_204_delete_then_list_omits_photo(self, inv_client, admin_token, invitation_app):
        pid = invitation_app._test_project_id
        resp = _upload_jpeg(inv_client, admin_token, pid)
        assert resp.status_code == 201
        photo_id = resp.get_json()["id"]

        del_resp = inv_client.delete(
            _photo_url(pid, photo_id),
            headers=_auth(admin_token),
        )
        assert del_resp.status_code == 204

        list_resp = inv_client.get(_photos_url(pid), headers=_auth(admin_token))
        ids_in_list = [item["id"] for item in list_resp.get_json()["items"]]
        assert photo_id not in ids_in_list

    def test_404_get_thumbnail_after_delete(self, inv_client, admin_token, invitation_app):
        pid = invitation_app._test_project_id
        resp = _upload_jpeg(inv_client, admin_token, pid)
        photo_id = resp.get_json()["id"]

        inv_client.delete(_photo_url(pid, photo_id), headers=_auth(admin_token))

        get_resp = inv_client.get(
            _thumbnail_url(pid, photo_id),
            headers=_auth(admin_token),
        )
        assert get_resp.status_code == 404

    def test_404_delete_nonexistent_photo(self, inv_client, admin_token, invitation_app):
        resp = inv_client.delete(
            _photo_url(invitation_app._test_project_id, str(uuid4())),
            headers=_auth(admin_token),
        )
        assert resp.status_code == 404

    def test_401_unauthenticated_delete(self, inv_client, admin_token, invitation_app):
        resp = _upload_jpeg(inv_client, admin_token, invitation_app._test_project_id)
        photo_id = resp.get_json()["id"]
        del_resp = inv_client.delete(
            _photo_url(invitation_app._test_project_id, photo_id),
        )
        assert del_resp.status_code == 401


# ===========================================================================
# Cross-project isolation
# ===========================================================================


class TestCrossProjectIsolation:
    def test_404_photo_from_project_a_requested_under_project_b(self, inv_client, admin_token, invitation_app):
        pid_a = invitation_app._test_project_id
        pid_b = invitation_app._test_project_2_id

        resp = _upload_jpeg(inv_client, admin_token, pid_a)
        assert resp.status_code == 201
        photo_id = resp.get_json()["id"]

        # Request the same photo ID under a different project
        bad_resp = inv_client.get(
            _thumbnail_url(pid_b, photo_id),
            headers=_auth(admin_token),
        )
        assert bad_resp.status_code == 404


# ===========================================================================
# Permission tests
# ===========================================================================


class TestPhotoPermissions:
    """PATCH and DELETE by non-uploader/non-owner/non-admin → 403.
    Admin (*:*) → always allowed.

    The upload route uses require_project_access(write=False) which checks
    project.user_ids (ORM relationship) or project:create permission.  The
    admin_user is the project owner, so admin_token can upload.  The
    superadmin_user has *:* which satisfies can_read_project via has_permission.
    Outsider has no project:read and is not the owner → 403 at decorator level.
    """

    def _upload_as_admin(self, inv_client, admin_token, invitation_app) -> str:
        """Upload a photo as admin (project owner) and return the photo id."""
        resp = _upload_jpeg(inv_client, admin_token, invitation_app._test_project_id)
        assert resp.status_code == 201, resp.get_data(as_text=True)
        return resp.get_json()["id"]

    def test_403_outsider_cannot_patch(self, inv_client, admin_token, outsider_token, invitation_app):
        """Outsider has neither project:read nor project membership → 403."""
        photo_id = self._upload_as_admin(inv_client, admin_token, invitation_app)
        resp = inv_client.patch(
            _photo_url(invitation_app._test_project_id, photo_id),
            json={"caption": "hacker"},
            headers=_auth(outsider_token),
        )
        # Outsider has project:read role but is not a project member/owner;
        # the use-case rejects them because they are not the uploader, not the
        # project owner, and not an admin.
        assert resp.status_code == 403

    def test_403_outsider_cannot_delete(self, inv_client, admin_token, outsider_token, invitation_app):
        photo_id = self._upload_as_admin(inv_client, admin_token, invitation_app)
        resp = inv_client.delete(
            _photo_url(invitation_app._test_project_id, photo_id),
            headers=_auth(outsider_token),
        )
        assert resp.status_code == 403

    def test_admin_star_perm_can_patch_any_photo(self, inv_client, admin_token, superadmin_token, invitation_app):
        """Superadmin (*:*) can update any photo regardless of uploader."""
        photo_id = self._upload_as_admin(inv_client, admin_token, invitation_app)
        resp = inv_client.patch(
            _photo_url(invitation_app._test_project_id, photo_id),
            json={"caption": "admin override"},
            headers=_auth(superadmin_token),
        )
        assert resp.status_code == 200
        assert resp.get_json()["caption"] == "admin override"

    def test_admin_star_perm_can_delete_any_photo(self, inv_client, admin_token, superadmin_token, invitation_app):
        """Superadmin (*:*) can delete any photo."""
        photo_id = self._upload_as_admin(inv_client, admin_token, invitation_app)
        resp = inv_client.delete(
            _photo_url(invitation_app._test_project_id, photo_id),
            headers=_auth(superadmin_token),
        )
        assert resp.status_code == 204

    def test_uploader_can_patch_own_photo(self, inv_client, admin_token, invitation_app):
        """Project owner (uploader) can always update their own photo."""
        photo_id = self._upload_as_admin(inv_client, admin_token, invitation_app)
        resp = inv_client.patch(
            _photo_url(invitation_app._test_project_id, photo_id),
            json={"caption": "owner update"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200

    def test_uploader_can_delete_own_photo(self, inv_client, admin_token, invitation_app):
        """Project owner (uploader) can delete their own photo."""
        photo_id = self._upload_as_admin(inv_client, admin_token, invitation_app)
        resp = inv_client.delete(
            _photo_url(invitation_app._test_project_id, photo_id),
            headers=_auth(admin_token),
        )
        assert resp.status_code == 204
