"""API tests for GET /billing-documents/<id>/pdf — happy path + rate limit.

Rate limit test requires RATELIMIT_ENABLED=True on the test app.
The invitation_app fixture sets RATELIMIT_ENABLED=False for most tests;
the rate_limit_app fixture below creates a separate app instance with
limits enabled.
"""

from __future__ import annotations

import pytest


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestPdfRoute:
    def test_pdf_returns_binary(self, inv_client, billing_token, seeded_doc):
        resp = inv_client.get(
            f"/api/v1/billing-documents/{seeded_doc['id']}/pdf",
            headers=_auth(billing_token),
        )
        assert resp.status_code == 200
        assert resp.content_type == "application/pdf"
        assert resp.data.startswith(b"%PDF")

    def test_pdf_content_disposition_contains_document_number(self, inv_client, billing_token, seeded_doc):
        resp = inv_client.get(
            f"/api/v1/billing-documents/{seeded_doc['id']}/pdf",
            headers=_auth(billing_token),
        )
        assert resp.status_code == 200
        cd = resp.headers.get("Content-Disposition", "")
        assert "attachment" in cd

    def test_pdf_unauthenticated_returns_401(self, invitation_app):
        """Fresh client with no auth cookies must get 401."""
        import uuid

        fresh_client = invitation_app.test_client()
        resp = fresh_client.get(f"/api/v1/billing-documents/{uuid.uuid4()}/pdf")
        assert resp.status_code == 401

    def test_pdf_wrong_owner_returns_404(self, inv_client, other_token, seeded_doc):
        resp = inv_client.get(
            f"/api/v1/billing-documents/{seeded_doc['id']}/pdf",
            headers=_auth(other_token),
        )
        assert resp.status_code == 404

    def test_pdf_nonexistent_doc_returns_404(self, inv_client, billing_token):
        import uuid

        resp = inv_client.get(
            f"/api/v1/billing-documents/{uuid.uuid4()}/pdf",
            headers=_auth(billing_token),
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Rate limit: 5/min — requires a separate app with limits enabled
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def rate_limit_app():
    """Flask test app with RATELIMIT_ENABLED=True for rate-limit assertions."""
    import os

    os.environ.setdefault("EMAIL_PROVIDER", "inmemory")
    os.environ.setdefault("RESEND_API_KEY", "test")
    os.environ.setdefault("FROM_EMAIL", "test@example.com")
    os.environ.setdefault("APP_BASE_URL", "http://localhost:3000")

    from app import create_app, db
    from app.infrastructure.adapters.argon2_hasher import Argon2PasswordHasher
    from app.infrastructure.adapters.jwt_issuer import JWTTokenIssuer
    from app.infrastructure.adapters.flask_session import FlaskSessionManager
    from app.infrastructure.adapters.sqlalchemy_user import SQLAlchemyUserRepository
    from app.infrastructure.adapters.sqlalchemy_project import SQLAlchemyProjectRepository
    from app.infrastructure.database.repositories.sqlalchemy_invitation import (
        SqlAlchemyInvitationRepository,
    )
    from app.infrastructure.database.repositories.sqlalchemy_project_membership import (
        SqlAlchemyProjectMembershipRepository,
    )
    from app.infrastructure.database.repositories.sqlalchemy_role import SqlAlchemyRoleRepository
    from app.infrastructure.database.models import UserModel, RoleModel, PermissionModel
    from config import TestingConfig
    from wiring import configure_container
    import wiring as _wiring
    from app.infrastructure.email.inmemory_adapter import InMemoryEmailAdapter

    class RateLimitTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = True
        RATELIMIT_STORAGE_URI = "memory://"
        RATELIMIT_DEFAULT = "1000 per hour"  # high default; pdf endpoint has its own 5/min

    test_app = create_app(RateLimitTestConfig)

    with test_app.app_context():
        db.create_all()

        if _wiring._inmemory_email_adapter is None:
            _wiring._inmemory_email_adapter = InMemoryEmailAdapter()

        hasher = Argon2PasswordHasher()
        token_issuer = JWTTokenIssuer()
        user_repo = SQLAlchemyUserRepository(db.session)
        project_repo = SQLAlchemyProjectRepository(db.session)
        inv_repo = SqlAlchemyInvitationRepository(db.session)
        membership_repo = SqlAlchemyProjectMembershipRepository(db.session)
        role_repo = SqlAlchemyRoleRepository(db.session)

        configure_container(
            user_repository=user_repo,
            project_repository=project_repo,
            password_hasher=hasher,
            token_issuer=token_issuer,
            session_manager=FlaskSessionManager(),
            invitation_repo=inv_repo,
            project_membership_repo=membership_repo,
            role_repo=role_repo,
        )

        # Seed roles + admin user
        star_perm = PermissionModel(name="*:*", resource="*", action="*")
        admin_role = RoleModel(name="rl-admin", description="Admin")
        admin_role.permissions.append(star_perm)
        db.session.add_all([star_perm, admin_role])
        db.session.flush()

        admin_user = UserModel(
            email="rl-admin@rate-test.com",
            password_hash=hasher.hash("Admin1234!"),
            is_active=True,
        )
        admin_user.roles.append(admin_role)
        db.session.add(admin_user)
        db.session.commit()

        test_app._rl_admin_email = "rl-admin@rate-test.com"
        test_app._rl_admin_password = "Admin1234!"

        # Wire billing use-cases
        from wiring import get_container as _get_container
        from app.infrastructure.database.repositories.sqlalchemy_billing_document_repository import (
            SqlAlchemyBillingDocumentRepository,
        )
        from app.infrastructure.database.repositories.sqlalchemy_billing_template_repository import (
            SqlAlchemyBillingTemplateRepository,
        )
        from app.infrastructure.database.repositories.sqlalchemy_billing_number_counter_repository import (
            SqlAlchemyBillingNumberCounterRepository,
        )
        from app.infrastructure.pdf.billing_document_pdf_renderer import (
            ReportLabBillingDocumentPdfRenderer,
        )
        from app.application.billing import (
            CreateBillingDocumentUseCase,
            CloneBillingDocumentUseCase,
            ConvertDevisToFactureUseCase,
            UpdateBillingDocumentUseCase,
            UpdateBillingDocumentStatusUseCase,
            ListBillingDocumentsUseCase,
            GetBillingDocumentUseCase,
            DeleteBillingDocumentUseCase,
            RenderBillingDocumentPdfUseCase,
            CreateTemplateUseCase,
            UpdateTemplateUseCase,
            ListTemplatesUseCase,
            GetTemplateUseCase,
            DeleteTemplateUseCase,
            ApplyTemplateToCreateDocumentUseCase,
        )
        from app.infrastructure.database.repositories.sqlalchemy_company_repository import (
            SqlAlchemyCompanyRepository,
        )
        from app.infrastructure.database.repositories.sqlalchemy_user_company_access_repository import (
            SqlAlchemyUserCompanyAccessRepository,
        )

        _c = _get_container()
        _doc_repo = SqlAlchemyBillingDocumentRepository(db.session)
        _tpl_repo = SqlAlchemyBillingTemplateRepository(db.session)
        _counter_repo = SqlAlchemyBillingNumberCounterRepository(db.session)
        _pdf_renderer = ReportLabBillingDocumentPdfRenderer()
        _company_repo = SqlAlchemyCompanyRepository(db.session)
        _access_repo = SqlAlchemyUserCompanyAccessRepository(db.session)

        _c.billing_document_repo = _doc_repo
        _c.billing_template_repo = _tpl_repo
        _c.billing_counter_repo = _counter_repo
        _c.billing_pdf_renderer = _pdf_renderer
        _c.company_repo = _company_repo
        _c.user_company_access_repo = _access_repo
        _c.create_billing_document_usecase = CreateBillingDocumentUseCase(
            doc_repo=_doc_repo, counter_repo=_counter_repo,
            project_repo=None, company_repo=_company_repo, access_repo=_access_repo,
        )
        _c.clone_billing_document_usecase = CloneBillingDocumentUseCase(
            doc_repo=_doc_repo, counter_repo=_counter_repo,
            project_repo=None, company_repo=_company_repo, access_repo=_access_repo,
        )
        _c.convert_devis_to_facture_usecase = ConvertDevisToFactureUseCase(
            doc_repo=_doc_repo, counter_repo=_counter_repo,
            project_repo=None, company_repo=_company_repo, access_repo=_access_repo,
        )
        _c.update_billing_document_usecase = UpdateBillingDocumentUseCase(doc_repo=_doc_repo)
        _c.update_billing_document_status_usecase = UpdateBillingDocumentStatusUseCase(doc_repo=_doc_repo)
        _c.list_billing_documents_usecase = ListBillingDocumentsUseCase(doc_repo=_doc_repo)
        _c.get_billing_document_usecase = GetBillingDocumentUseCase(doc_repo=_doc_repo)
        _c.delete_billing_document_usecase = DeleteBillingDocumentUseCase(doc_repo=_doc_repo)
        _c.render_billing_document_pdf_usecase = RenderBillingDocumentPdfUseCase(
            doc_repo=_doc_repo, pdf_renderer=_pdf_renderer
        )
        _c.create_billing_template_usecase = CreateTemplateUseCase(template_repo=_tpl_repo)
        _c.update_billing_template_usecase = UpdateTemplateUseCase(template_repo=_tpl_repo)
        _c.list_billing_templates_usecase = ListTemplatesUseCase(template_repo=_tpl_repo)
        _c.get_billing_template_usecase = GetTemplateUseCase(template_repo=_tpl_repo)
        _c.delete_billing_template_usecase = DeleteTemplateUseCase(template_repo=_tpl_repo)
        _c.apply_template_usecase = ApplyTemplateToCreateDocumentUseCase(
            doc_repo=_doc_repo,
            template_repo=_tpl_repo,
            counter_repo=_counter_repo,
            project_repo=None,
            company_repo=_company_repo,
            access_repo=_access_repo,
        )

        yield test_app

        db.session.remove()
        db.drop_all()


@pytest.fixture
def rl_client(rate_limit_app):
    return rate_limit_app.test_client()


@pytest.fixture
def rl_token(rl_client, rate_limit_app):
    resp = rl_client.post(
        "/api/v1/auth/login",
        json={
            "email": rate_limit_app._rl_admin_email,
            "password": rate_limit_app._rl_admin_password,
        },
    )
    assert resp.status_code == 200
    return resp.get_json()["access_token"]


@pytest.fixture
def rl_doc(rl_client, rl_token, rate_limit_app):
    """Seed company + billing doc for rate-limit tests (phase-05: company_id required)."""
    import uuid as _uuid
    from datetime import datetime, timezone
    from app import db
    from app.domain.companies.company import Company
    from app.domain.companies.user_company_access import UserCompanyAccess
    from app.infrastructure.database.repositories.sqlalchemy_company_repository import (
        SqlAlchemyCompanyRepository,
    )
    from app.infrastructure.database.repositories.sqlalchemy_user_company_access_repository import (
        SqlAlchemyUserCompanyAccessRepository,
    )
    from app.infrastructure.adapters.sqlalchemy_user import SQLAlchemyUserRepository

    # Resolve rl_admin user_id from DB
    with rate_limit_app.app_context():
        user_repo = SQLAlchemyUserRepository(db.session)
        user = user_repo.find_by_email(rate_limit_app._rl_admin_email)
        admin_user_id = user.id

        # Create company + attach user directly via repos (avoids needing companies use-cases)
        now = datetime.now(timezone.utc)
        company_id = _uuid.uuid4()
        company = Company(
            id=company_id,
            legal_name="RL Co",
            address="1 rue Test",
            siret=None, tva_number=None, iban=None, bic=None,
            logo_url=None, default_payment_terms=None, prefix_override=None,
            created_by=admin_user_id,
            created_at=now, updated_at=now,
        )
        SqlAlchemyCompanyRepository(db.session).save(company)

        access = UserCompanyAccess(
            user_id=admin_user_id,
            company_id=company_id,
            is_primary=True,
            attached_at=now,
        )
        SqlAlchemyUserCompanyAccessRepository(db.session).save(access)
        db.session.commit()

    resp = rl_client.post(
        "/api/v1/billing-documents",
        json={
            "kind": "devis",
            "company_id": str(company_id),
            "recipient_name": "RL Client",
            "items": [{"description": "X", "quantity": "1", "unit_price": "100", "vat_rate": "20"}],
        },
        headers=_auth(rl_token),
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()


def test_pdf_route_rate_limit_5_per_min(rl_client, rl_token, rl_doc):
    """Spec #12: 5th request succeeds, 6th is rate-limited (429)."""
    doc_id = rl_doc["id"]
    url = f"/api/v1/billing-documents/{doc_id}/pdf"
    headers = _auth(rl_token)

    # First 5 should succeed
    for i in range(5):
        resp = rl_client.get(url, headers=headers)
        assert resp.status_code == 200, f"Request {i+1} failed with {resp.status_code}"

    # 6th should be rate-limited
    resp = rl_client.get(url, headers=headers)
    assert resp.status_code == 429
