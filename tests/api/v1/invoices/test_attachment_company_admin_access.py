"""API integration tests for company-admin read access on invoice attachment routes.

Covers:
  GET  /api/v1/projects/<project_id>/invoices/<invoice_id>/attachments  (list)
  GET  /api/v1/attachments/<attachment_id>/download                     (download)
  POST /api/v1/projects/<project_id>/invoices/<invoice_id>/attachments  (upload — write, unchanged)
  DELETE /api/v1/attachments/<attachment_id>                            (delete — write, unchanged)
  PATCH  /api/v1/attachments/<attachment_id>/rename                     (rename — write, unchanged)

Scenarios:
  - Company admin (no project membership, non-superadmin global role) → 200 on list and download
  - Company admin of a DIFFERENT company → 403 on list and download (cross-company leak guard)
  - Company admin → 403 on upload, delete, rename (write paths not widened)
  - Project member (no company admin role) → still 200 on list and download (regression)
  - Non-member non-company-admin → still 403 on list and download (regression)
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from io import BytesIO
from uuid import uuid4

import pytest

from app import db
from app.infrastructure.database.models import PermissionModel, ProjectModel, RoleModel, UserModel
from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.invoice import InvoiceModel
from app.infrastructure.database.models.invoice_attachment import InvoiceAttachmentModel


# ---------------------------------------------------------------------------
# Module-scoped Flask app + seeded data
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def att_app():
    """Flask app with in-memory SQLite wired for attachment-access tests.

    Seed layout:
      - company_x: owned by setup_user
      - company_y: separate company (cross-company guard)
      - project_x: belongs to company_x; owner = setup_user
      - project_y: belongs to company_y; owner = setup_user
      - member_user: is a project member of project_x (member_role, has project:read)
      - company_x_admin_user: has admin role in company_x, NO project membership anywhere
        global role = member_role (which has project:read so the permission decorator passes)
      - company_y_admin_user: has admin role in company_y only (cross-company guard)
      - outsider_user: no company admin, no project membership
    """
    from app import create_app
    from app.infrastructure.adapters.argon2_hasher import Argon2PasswordHasher
    from app.infrastructure.adapters.flask_session import FlaskSessionManager
    from app.infrastructure.adapters.jwt_issuer import JWTTokenIssuer
    from app.infrastructure.adapters.sqlalchemy_invoice import SQLAlchemyInvoiceRepository
    from app.infrastructure.adapters.sqlalchemy_invoice_attachment import SQLAlchemyInvoiceAttachmentRepository
    from app.infrastructure.adapters.sqlalchemy_project import SQLAlchemyProjectRepository
    from app.infrastructure.adapters.sqlalchemy_user import SQLAlchemyUserRepository
    from app.infrastructure.adapters.in_memory_document_storage import InMemoryDocumentStorage
    from app.infrastructure.database.repositories.sqlalchemy_company_repository import (
        SqlAlchemyCompanyRepository,
    )
    from app.infrastructure.database.repositories.sqlalchemy_user_company_access_repository import (
        SqlAlchemyUserCompanyAccessRepository,
    )
    from app.infrastructure.database.repositories.sqlalchemy_project_membership import (
        SqlAlchemyProjectMembershipRepository,
    )
    from app.infrastructure.database.repositories.sqlalchemy_role import SqlAlchemyRoleRepository
    from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel
    from app.application.invoice.upload_attachment import UploadAttachmentUseCase  # noqa: F401
    from app.application.invoice.list_materials_expenses_usecase import ListMaterialsExpensesUseCase
    from app.application.invoice.set_refundable_status_usecase import SetInvoiceRefundableStatusUseCase
    from config import TestingConfig
    from wiring import configure_container, get_container

    class AttTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(AttTestConfig)

    with test_app.app_context():
        db.create_all()

        hasher = Argon2PasswordHasher()

        # --- Permissions ---
        # Names must match exactly what @require_permission() checks — they are embedded
        # verbatim in the JWT by AuthorizationService.get_user_permissions().
        star_perm = PermissionModel(name="*:*", resource="*", action="*")
        read_perm = PermissionModel(name="project:read", resource="project", action="read")
        inv_perm = PermissionModel(name="project:manage_invoices", resource="project", action="manage_invoices")

        superadmin_role = RoleModel(name="att_superadmin", description="Superadmin for att tests")
        superadmin_role.permissions.append(star_perm)

        # member_role mirrors production "member" (has project:read) so the
        # @require_permission("project:read") gate passes. The test then verifies that
        # the access decorator is the real gate — not the permission check.
        member_role = RoleModel(name="att_member", description="Member for att tests")
        member_role.permissions.append(read_perm)
        member_role.permissions.append(inv_perm)

        db.session.add_all([star_perm, read_perm, inv_perm, superadmin_role, member_role])
        db.session.flush()

        # --- Users ---
        setup_user = UserModel(
            email="att_setup@test.com",
            password_hash=hasher.hash("Setup1234!"),
            is_active=True,
        )
        setup_user.roles.append(superadmin_role)

        # A member of project_x; has project:read globally and via membership
        member_user = UserModel(
            email="att_member@test.com",
            password_hash=hasher.hash("Member1234!"),
            is_active=True,
        )
        member_user.roles.append(member_role)

        # Company-X admin; NOT a project member anywhere
        company_x_admin_user = UserModel(
            email="att_company_x_admin@test.com",
            password_hash=hasher.hash("CompX1234!"),
            is_active=True,
        )
        company_x_admin_user.roles.append(member_role)

        # Company-Y admin; should be denied access to company-X projects
        company_y_admin_user = UserModel(
            email="att_company_y_admin@test.com",
            password_hash=hasher.hash("CompY1234!"),
            is_active=True,
        )
        company_y_admin_user.roles.append(member_role)

        # Pure outsider: no company admin, no project membership
        outsider_user = UserModel(
            email="att_outsider@test.com",
            password_hash=hasher.hash("Outsider1234!"),
            is_active=True,
        )
        outsider_user.roles.append(member_role)

        db.session.add_all([setup_user, member_user, company_x_admin_user, company_y_admin_user, outsider_user])
        db.session.flush()

        now = datetime.now(timezone.utc)

        # --- Companies ---
        company_x = CompanyModel(
            id=uuid4(),
            legal_name="Company X SARL",
            address="1 rue X",
            created_by=setup_user.id,
            created_at=now,
            updated_at=now,
        )
        company_y = CompanyModel(
            id=uuid4(),
            legal_name="Company Y SAS",
            address="2 rue Y",
            created_by=setup_user.id,
            created_at=now,
            updated_at=now,
        )
        db.session.add_all([company_x, company_y])
        db.session.flush()

        # --- Projects ---
        # project_x belongs to company_x; setup_user is owner
        project_x = ProjectModel(name="Project X", owner_id=setup_user.id, company_id=company_x.id)
        # project_y belongs to company_y
        project_y = ProjectModel(name="Project Y", owner_id=setup_user.id, company_id=company_y.id)
        db.session.add_all([project_x, project_y])
        db.session.flush()

        # member_user is a project member of project_x — use ORM append so the
        # relationship is tracked in the identity map from the start, avoiding
        # stale-cache misses when can_read_project checks project.user_ids.
        project_x.users.append(member_user)
        db.session.commit()

        # --- UserCompanyAccess ---
        access_x = UserCompanyAccessModel(
            user_id=company_x_admin_user.id,
            company_id=company_x.id,
            role="admin",
            is_primary=True,
            attached_at=now,
        )
        access_y = UserCompanyAccessModel(
            user_id=company_y_admin_user.id,
            company_id=company_y.id,
            role="admin",
            is_primary=True,
            attached_at=now,
        )
        db.session.add_all([access_x, access_y])
        db.session.commit()

        # --- Repos ---
        user_repo = SQLAlchemyUserRepository(db.session)
        project_repo = SQLAlchemyProjectRepository(db.session)
        invoice_repo = SQLAlchemyInvoiceRepository(db.session)
        attachment_repo = SQLAlchemyInvoiceAttachmentRepository(db.session)
        company_repo = SqlAlchemyCompanyRepository(db.session)
        access_repo = SqlAlchemyUserCompanyAccessRepository(db.session)
        membership_repo = SqlAlchemyProjectMembershipRepository(db.session)
        role_repo = SqlAlchemyRoleRepository(db.session)
        storage = InMemoryDocumentStorage()

        configure_container(
            user_repository=user_repo,
            project_repository=project_repo,
            password_hasher=hasher,
            token_issuer=JWTTokenIssuer(),
            session_manager=FlaskSessionManager(),
            invoice_repository=invoice_repo,
            invoice_attachment_repository=attachment_repo,
            attachment_storage=storage,
            project_membership_repo=membership_repo,
            role_repo=role_repo,
        )

        _c = get_container()
        _c.company_repo = company_repo
        _c.user_company_access_repo = access_repo

        _c.list_materials_expenses_usecase = ListMaterialsExpensesUseCase(
            invoice_repo=invoice_repo,
            access_repo=access_repo,
        )
        _c.set_refundable_status_usecase = SetInvoiceRefundableStatusUseCase(
            invoice_repo=invoice_repo,
            access_repo=access_repo,
        )

        # --- Seed an invoice + attachment in project_x ---
        inv = InvoiceModel(
            id=uuid4(),
            project_id=project_x.id,
            invoice_number=f"INV-{uuid4().hex[:8]}",
            type="materials_services",
            issue_date=date.today(),
            recipient_name="Supplier",
            items=[{"description": "Parts", "quantity": 1.0, "unit_price": 100.0}],
            created_by=setup_user.id,
            created_at=now,
            updated_at=now,
            refundable_status="refundable",
        )
        db.session.add(inv)
        db.session.flush()

        att = InvoiceAttachmentModel(
            id=uuid4(),
            invoice_id=inv.id,
            filename="receipt.pdf",
            storage_key=f"test/{uuid4().hex}/receipt.pdf",
            mime_type="application/pdf",
            size_bytes=512,
            uploaded_by=setup_user.id,
            uploaded_at=now,
        )
        # Also store the file bytes so download can stream them
        storage.put(att.storage_key, BytesIO(b"%PDF-test"), "application/pdf")
        db.session.add(att)
        db.session.commit()

        test_app._setup_user_id = setup_user.id
        test_app._member_user_id = member_user.id
        test_app._company_x_admin_user_id = company_x_admin_user.id
        test_app._company_y_admin_user_id = company_y_admin_user.id
        test_app._outsider_user_id = outsider_user.id
        test_app._company_x_id = company_x.id
        test_app._company_y_id = company_y.id
        test_app._project_x_id = project_x.id
        test_app._project_y_id = project_y.id
        test_app._invoice_id = inv.id
        test_app._attachment_id = att.id
        test_app._attachment_storage_key = att.storage_key

        # expire_on_commit=True (SQLAlchemy default) already expires all objects after
        # each commit, but expunge_all() makes the identity map fully empty so that
        # module-scoped test functions don't share cached ORM state across requests.
        db.session.expunge_all()

        yield test_app

        db.session.remove()
        db.drop_all()


@pytest.fixture(scope="module")
def att_client(att_app):
    return att_app.test_client()


def _login(client, email: str, password: str) -> str:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["access_token"]


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def setup_tok(att_client):
    return _login(att_client, "att_setup@test.com", "Setup1234!")


@pytest.fixture(scope="module")
def member_tok(att_client):
    return _login(att_client, "att_member@test.com", "Member1234!")


@pytest.fixture(scope="module")
def company_x_admin_tok(att_client):
    return _login(att_client, "att_company_x_admin@test.com", "CompX1234!")


@pytest.fixture(scope="module")
def company_y_admin_tok(att_client):
    return _login(att_client, "att_company_y_admin@test.com", "CompY1234!")


@pytest.fixture(scope="module")
def outsider_tok(att_client):
    return _login(att_client, "att_outsider@test.com", "Outsider1234!")


# ---------------------------------------------------------------------------
# Company admin READ access (list + download)
# ---------------------------------------------------------------------------


class TestCompanyAdminCanReadAttachments:
    """Company admin of the owning company gets 200 on read-only attachment routes."""

    def test_company_admin_can_list_attachments_without_membership(self, att_client, company_x_admin_tok, att_app):
        inv_id = str(att_app._invoice_id)
        proj_id = str(att_app._project_x_id)
        resp = att_client.get(
            f"/api/v1/projects/{proj_id}/invoices/{inv_id}/attachments",
            headers=_auth_header(company_x_admin_tok),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        assert isinstance(data, list)
        assert len(data) >= 1

    def test_company_admin_can_download_attachment_without_membership(self, att_client, company_x_admin_tok, att_app):
        att_id = str(att_app._attachment_id)
        resp = att_client.get(
            f"/api/v1/attachments/{att_id}/download",
            headers=_auth_header(company_x_admin_tok),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)


# ---------------------------------------------------------------------------
# Cross-company isolation guard
# ---------------------------------------------------------------------------


class TestCompanyAdminCrossCompanyDenied:
    """Company admin of a DIFFERENT company must be denied (no cross-company data leak)."""

    def test_company_y_admin_cannot_list_company_x_attachments(self, att_client, company_y_admin_tok, att_app):
        inv_id = str(att_app._invoice_id)
        proj_id = str(att_app._project_x_id)
        resp = att_client.get(
            f"/api/v1/projects/{proj_id}/invoices/{inv_id}/attachments",
            headers=_auth_header(company_y_admin_tok),
        )
        assert resp.status_code == 403

    def test_company_y_admin_cannot_download_company_x_attachment(self, att_client, company_y_admin_tok, att_app):
        att_id = str(att_app._attachment_id)
        resp = att_client.get(
            f"/api/v1/attachments/{att_id}/download",
            headers=_auth_header(company_y_admin_tok),
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Write paths unchanged — company admin cannot upload, delete, or rename
# ---------------------------------------------------------------------------


class TestCompanyAdminWritePathsDenied:
    """Company admin should NOT be able to upload, delete or rename attachments."""

    def test_company_admin_cannot_upload_attachment(self, att_client, company_x_admin_tok, att_app):
        inv_id = str(att_app._invoice_id)
        proj_id = str(att_app._project_x_id)
        data = {"file": (BytesIO(b"%PDF-fake"), "fake.pdf", "application/pdf")}
        resp = att_client.post(
            f"/api/v1/projects/{proj_id}/invoices/{inv_id}/attachments",
            data=data,
            content_type="multipart/form-data",
            headers=_auth_header(company_x_admin_tok),
        )
        assert resp.status_code == 403

    def test_company_admin_cannot_delete_attachment(self, att_client, company_x_admin_tok, att_app):
        att_id = str(att_app._attachment_id)
        resp = att_client.delete(
            f"/api/v1/attachments/{att_id}",
            headers=_auth_header(company_x_admin_tok),
        )
        assert resp.status_code == 403

    def test_company_admin_cannot_rename_attachment(self, att_client, company_x_admin_tok, att_app):
        att_id = str(att_app._attachment_id)
        resp = att_client.patch(
            f"/api/v1/attachments/{att_id}/rename",
            json={"filename": "renamed.pdf"},
            headers=_auth_header(company_x_admin_tok),
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Regression: project member still gets 200
# ---------------------------------------------------------------------------


class TestProjectMemberAccessRegression:
    """A project member should still be able to list and download attachments."""

    def test_project_member_can_list_attachments(self, att_client, member_tok, att_app):
        inv_id = str(att_app._invoice_id)
        proj_id = str(att_app._project_x_id)
        resp = att_client.get(
            f"/api/v1/projects/{proj_id}/invoices/{inv_id}/attachments",
            headers=_auth_header(member_tok),
        )
        assert resp.status_code == 200

    def test_project_member_can_download_attachment(self, att_client, member_tok, att_app):
        att_id = str(att_app._attachment_id)
        resp = att_client.get(
            f"/api/v1/attachments/{att_id}/download",
            headers=_auth_header(member_tok),
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Regression: outsider (non-member, non-company-admin) still gets 403
# ---------------------------------------------------------------------------


class TestOutsiderDeniedRegression:
    """A non-member non-company-admin must still be denied on read routes."""

    def test_outsider_cannot_list_attachments(self, att_client, outsider_tok, att_app):
        inv_id = str(att_app._invoice_id)
        proj_id = str(att_app._project_x_id)
        resp = att_client.get(
            f"/api/v1/projects/{proj_id}/invoices/{inv_id}/attachments",
            headers=_auth_header(outsider_tok),
        )
        assert resp.status_code == 403

    def test_outsider_cannot_download_attachment(self, att_client, outsider_tok, att_app):
        att_id = str(att_app._attachment_id)
        resp = att_client.get(
            f"/api/v1/attachments/{att_id}/download",
            headers=_auth_header(outsider_tok),
        )
        assert resp.status_code == 403
