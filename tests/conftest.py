"""Pytest configuration and fixtures for database tests."""

from __future__ import annotations

import os
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Ensure EMAIL_PROVIDER=inmemory and required vars are set before any app import
os.environ.setdefault("EMAIL_PROVIDER", "inmemory")
os.environ.setdefault("RESEND_API_KEY", "test")
os.environ.setdefault("FROM_EMAIL", "test@example.com")
os.environ.setdefault("APP_BASE_URL", "http://localhost:3000")

# Add project root to Python path for wiring module
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.infrastructure.database.models import Base

# ---------------------------------------------------------------------------
# Low-level SQLAlchemy session fixtures (kept for unit/repository tests)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def test_db_url():
    """Get test database URL from environment or use default SQLite."""
    return os.getenv("TEST_DATABASE_URL", "sqlite:///:memory:")


@pytest.fixture(scope="session")
def engine(test_db_url):
    """Create SQLAlchemy engine for tests."""
    return create_engine(test_db_url, echo=False)


@pytest.fixture(scope="session")
def tables(engine):
    """Create all tables for testing."""
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture(scope="function")
def session(engine, tables):
    """Create a new database session for a test."""
    connection = engine.connect()
    transaction = connection.begin()
    Session = sessionmaker(bind=connection)
    session = Session()

    yield session

    session.close()
    transaction.rollback()
    connection.close()


# ---------------------------------------------------------------------------
# InMemoryEmailAdapter fixture — for assertions in integration tests
# ---------------------------------------------------------------------------


@pytest.fixture
def inmemory_email_adapter():
    """Return the global InMemoryEmailAdapter singleton and clear it before each test."""
    import wiring

    # Ensure the singleton is initialised (may be None if configure_container never ran)
    if wiring._inmemory_email_adapter is None:
        from app.infrastructure.email.inmemory_adapter import InMemoryEmailAdapter

        wiring._inmemory_email_adapter = InMemoryEmailAdapter()
    adapter = wiring._inmemory_email_adapter
    adapter.clear()
    yield adapter
    adapter.clear()


# ---------------------------------------------------------------------------
# Flask app + test client fixtures for API-level tests
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def invitation_app():
    """Flask app wired with in-memory DB + InMemoryEmailAdapter for invitation tests."""
    from app import create_app, db
    from app.infrastructure.adapters.argon2_hasher import Argon2PasswordHasher
    from app.infrastructure.adapters.jwt_issuer import JWTTokenIssuer
    from app.infrastructure.adapters.flask_session import FlaskSessionManager
    from app.infrastructure.adapters.sqlalchemy_user import SQLAlchemyUserRepository
    from app.infrastructure.adapters.sqlalchemy_project import SQLAlchemyProjectRepository
    from app.infrastructure.database.repositories.sqlalchemy_invitation import SqlAlchemyInvitationRepository
    from app.infrastructure.database.repositories.sqlalchemy_project_membership import (
        SqlAlchemyProjectMembershipRepository,
    )
    from app.infrastructure.database.repositories.sqlalchemy_role import SqlAlchemyRoleRepository
    from app.infrastructure.database.models import UserModel, RoleModel, PermissionModel, ProjectModel
    from config import TestingConfig
    from wiring import configure_container
    import wiring as _wiring
    from app.infrastructure.email.inmemory_adapter import InMemoryEmailAdapter

    class InviteTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(InviteTestConfig)

    with test_app.app_context():
        db.create_all()

        # Ensure InMemoryEmailAdapter singleton is ready
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

        # ------------------------------------------------------------------
        # Seed roles + permissions (SQLite in-memory: no migration fixtures)
        # ------------------------------------------------------------------
        invite_perm = PermissionModel(name="project:invite", resource="project", action="invite")
        read_perm = PermissionModel(name="project:read", resource="project", action="read")
        star_perm = PermissionModel(name="*:*", resource="*", action="*")

        admin_role = RoleModel(name="admin", description="Admin")
        member_role = RoleModel(name="member", description="Member")
        superadmin_role = RoleModel(name="superadmin", description="Superadmin")

        admin_role.permissions.append(invite_perm)
        admin_role.permissions.append(read_perm)
        member_role.permissions.append(read_perm)
        superadmin_role.permissions.append(star_perm)

        # Seed roles + permissions first so they get IDs before users reference them
        db.session.add_all(
            [
                invite_perm,
                read_perm,
                star_perm,
                admin_role,
                member_role,
                superadmin_role,
            ]
        )
        db.session.flush()  # assign DB-generated UUIDs

        # Seed users
        admin_user = UserModel(
            email="admin@invite-test.com",
            password_hash=hasher.hash("Admin1234!"),
            is_active=True,
        )
        admin_user.roles.append(admin_role)

        member_user = UserModel(
            email="member@invite-test.com",
            password_hash=hasher.hash("Member1234!"),
            is_active=True,
        )
        member_user.roles.append(member_role)

        outsider_user = UserModel(
            email="outsider@invite-test.com",
            password_hash=hasher.hash("Outsider1234!"),
            is_active=True,
        )

        # Seed superadmin user (needed for admin bulk-add + search endpoint tests)
        superadmin_user = UserModel(
            email="superadmin@invite-test.com",
            password_hash=hasher.hash("Superadmin1234!"),
            is_active=True,
        )
        superadmin_user.roles.append(superadmin_role)

        # Seed target user — the user being bulk-added in admin tests
        target_user = UserModel(
            email="target@invite-test.com",
            password_hash=hasher.hash("Target1234!"),
            is_active=True,
            display_name="Target User",
        )

        db.session.add_all([admin_user, member_user, outsider_user, superadmin_user, target_user])
        db.session.flush()  # assign user IDs before project references admin_user.id

        # Seed a project owned by admin
        project = ProjectModel(
            name="Invite Test Project",
            owner_id=admin_user.id,
        )
        # Two extra projects for bulk-add multi-project tests
        project2 = ProjectModel(
            name="Bulk Add Test Project 2",
            owner_id=admin_user.id,
        )
        project3 = ProjectModel(
            name="Bulk Add Test Project 3",
            owner_id=admin_user.id,
        )
        db.session.add_all([project, project2, project3])
        db.session.commit()

        # Store IDs on app for use in fixtures
        test_app._test_admin_email = "admin@invite-test.com"
        test_app._test_admin_password = "Admin1234!"
        test_app._test_member_email = "member@invite-test.com"
        test_app._test_member_password = "Member1234!"
        test_app._test_outsider_email = "outsider@invite-test.com"
        test_app._test_outsider_password = "Outsider1234!"
        test_app._test_superadmin_email = "superadmin@invite-test.com"
        test_app._test_superadmin_password = "Superadmin1234!"
        test_app._test_target_user_email = "target@invite-test.com"
        test_app._test_project_id = str(project.id)
        test_app._test_project_2_id = str(project2.id)
        test_app._test_project_3_id = str(project3.id)
        test_app._test_member_role_id = str(member_role.id)
        test_app._test_superadmin_role_id = str(superadmin_role.id)
        test_app._test_admin_user_id = str(admin_user.id)
        test_app._test_member_user_id = str(member_user.id)
        test_app._test_superadmin_user_id = str(superadmin_user.id)
        test_app._test_target_user_id = str(target_user.id)

        # Add member_user as a project member so they can list invitations
        # (user_projects is an association table — no ORM model; use raw SQL)
        from sqlalchemy import text
        from datetime import datetime, timezone

        db.session.execute(
            text(
                "INSERT INTO user_projects "
                "(user_id, project_id, role_id, invited_by_user_id, assigned_at) "
                "VALUES (:uid, :pid, :rid, NULL, :at) "
                "ON CONFLICT (user_id, project_id) DO NOTHING"
            ),
            {
                "uid": str(member_user.id),
                "pid": str(project.id),
                "rid": str(member_role.id),
                "at": datetime.now(timezone.utc),
            },
        )

        # Add target_user as a member of project (P1 only) with member_role
        # so bulk-add tests can exercise ALREADY_MEMBER_SAME_ROLE against project.id
        db.session.execute(
            text(
                "INSERT INTO user_projects "
                "(user_id, project_id, role_id, invited_by_user_id, assigned_at) "
                "VALUES (:uid, :pid, :rid, NULL, :at) "
                "ON CONFLICT (user_id, project_id) DO NOTHING"
            ),
            {
                "uid": str(target_user.id),
                "pid": str(project.id),
                "rid": str(member_role.id),
                "at": datetime.now(timezone.utc),
            },
        )
        db.session.commit()

        # ------------------------------------------------------------------
        # Wire notes use-cases (phase 03) — same pattern as app/__init__.py
        # ------------------------------------------------------------------
        from app.infrastructure.database.repositories.sqlalchemy_note_repository import (
            SqlAlchemyNoteRepository,
        )
        from app.infrastructure.database.repositories.sqlalchemy_note_dismissal_repository import (
            SqlAlchemyNoteDismissalRepository,
        )
        from app.infrastructure.database.repositories.sqlalchemy_project_membership_reader import (
            SqlAlchemyProjectMembershipReader,
        )
        from app.application.notes.create_note_usecase import CreateNoteUseCase
        from app.application.notes.list_project_notes_usecase import ListProjectNotesUseCase
        from app.application.notes.update_note_usecase import UpdateNoteUseCase
        from app.application.notes.delete_note_usecase import DeleteNoteUseCase
        from app.application.notes.mark_note_done_usecase import MarkNoteDoneUseCase
        from app.application.notes.mark_note_open_usecase import MarkNoteOpenUseCase
        from app.application.notes.list_due_notifications_usecase import ListDueNotificationsUseCase
        from app.application.notes.dismiss_notification_usecase import DismissNotificationUseCase
        from wiring import get_container as _get_container

        _c = _get_container()
        _note_repo = SqlAlchemyNoteRepository(db.session)
        _dismissal_repo = SqlAlchemyNoteDismissalRepository(db.session)
        _membership_reader = SqlAlchemyProjectMembershipReader(db.session)

        _c.note_repo = _note_repo
        _c.note_dismissal_repo = _dismissal_repo
        _c.note_membership_reader = _membership_reader
        _c.create_note_usecase = CreateNoteUseCase(
            note_repo=_note_repo,
            membership_reader=_membership_reader,
            db_session=db.session,
        )
        _c.list_project_notes_usecase = ListProjectNotesUseCase(
            note_repo=_note_repo,
            membership_reader=_membership_reader,
        )
        _c.update_note_usecase = UpdateNoteUseCase(
            note_repo=_note_repo,
            dismissal_repo=_dismissal_repo,
            membership_reader=_membership_reader,
            db_session=db.session,
        )
        _c.delete_note_usecase = DeleteNoteUseCase(
            note_repo=_note_repo,
            membership_reader=_membership_reader,
            db_session=db.session,
        )
        _c.mark_note_done_usecase = MarkNoteDoneUseCase(
            note_repo=_note_repo,
            membership_reader=_membership_reader,
            db_session=db.session,
        )
        _c.mark_note_open_usecase = MarkNoteOpenUseCase(
            note_repo=_note_repo,
            membership_reader=_membership_reader,
            db_session=db.session,
        )
        _c.list_due_notifications_usecase = ListDueNotificationsUseCase(
            note_query=_note_repo,
        )
        _c.dismiss_notification_usecase = DismissNotificationUseCase(
            note_repo=_note_repo,
            dismissal_repo=_dismissal_repo,
            membership_reader=_membership_reader,
            db_session=db.session,
        )

        # ------------------------------------------------------------------
        # Wire companies use-cases (phase 03) — mirrors app/__init__.py wiring.
        # These were already wired by _configure_di_container() during create_app,
        # but we must also wire the company/access repos into the billing use-cases
        # below so they satisfy the phase-05 company_id requirement.
        # ------------------------------------------------------------------
        from app.infrastructure.database.repositories.sqlalchemy_company_repository import (
            SqlAlchemyCompanyRepository,
        )
        from app.infrastructure.database.repositories.sqlalchemy_user_company_access_repository import (
            SqlAlchemyUserCompanyAccessRepository,
        )
        from app.infrastructure.database.repositories.sqlalchemy_company_invite_token_repository import (
            SqlAlchemyCompanyInviteTokenRepository,
        )
        from app.infrastructure.security.argon2_hasher import Argon2Hasher
        from app.infrastructure.security.secure_token_generator import SecureTokenGenerator
        from app.application.companies import (
            CreateCompanyUseCase as _CreateCompanyUseCase,
            UpdateCompanyUseCase as _UpdateCompanyUseCase,
            DeleteCompanyUseCase as _DeleteCompanyUseCase,
            ListAllCompaniesUseCase as _ListAllCompaniesUseCase,
            GenerateInviteTokenUseCase as _GenerateInviteTokenUseCase,
            RevokeInviteTokenUseCase as _RevokeInviteTokenUseCase,
            ListAttachedUsersUseCase as _ListAttachedUsersUseCase,
            BootAttachedUserUseCase as _BootAttachedUserUseCase,
            ListMyCompaniesUseCase as _ListMyCompaniesUseCase,
            GetCompanyUseCase as _GetCompanyUseCase,
            RedeemInviteTokenUseCase as _RedeemInviteTokenUseCase,
            SetPrimaryCompanyUseCase as _SetPrimaryCompanyUseCase,
            DetachCompanyUseCase as _DetachCompanyUseCase,
        )
        import datetime as _dt

        class _UtcClock:
            def now(self) -> _dt.datetime:
                return _dt.datetime.now(_dt.timezone.utc)

        _company_repo = SqlAlchemyCompanyRepository(db.session)
        _access_repo = SqlAlchemyUserCompanyAccessRepository(db.session)
        _token_repo = SqlAlchemyCompanyInviteTokenRepository(db.session)
        _argon2_hasher = Argon2Hasher()
        _token_generator = SecureTokenGenerator()
        _clock = _UtcClock()
        _role_checker = _c.authorization_service

        _c.company_repo = _company_repo
        _c.user_company_access_repo = _access_repo
        _c.company_invite_token_repo = _token_repo

        _c.create_company_usecase = _CreateCompanyUseCase(
            company_repo=_company_repo,
            role_checker=_role_checker,
        )
        _c.update_company_usecase = _UpdateCompanyUseCase(
            company_repo=_company_repo,
            role_checker=_role_checker,
        )
        _c.delete_company_usecase = _DeleteCompanyUseCase(
            company_repo=_company_repo,
            role_checker=_role_checker,
        )
        _c.list_all_companies_usecase = _ListAllCompaniesUseCase(
            company_repo=_company_repo,
            role_checker=_role_checker,
        )
        _c.generate_invite_token_usecase = _GenerateInviteTokenUseCase(
            company_repo=_company_repo,
            token_repo=_token_repo,
            hasher=_argon2_hasher,
            token_generator=_token_generator,
            clock=_clock,
            role_checker=_role_checker,
        )
        _c.revoke_invite_token_usecase = _RevokeInviteTokenUseCase(
            company_repo=_company_repo,
            token_repo=_token_repo,
            role_checker=_role_checker,
        )
        _c.list_attached_users_usecase = _ListAttachedUsersUseCase(
            company_repo=_company_repo,
            access_repo=_access_repo,
            role_checker=_role_checker,
        )
        _c.boot_attached_user_usecase = _BootAttachedUserUseCase(
            company_repo=_company_repo,
            access_repo=_access_repo,
            role_checker=_role_checker,
        )
        _c.list_my_companies_usecase = _ListMyCompaniesUseCase(
            company_repo=_company_repo,
            role_checker=_role_checker,
        )
        _c.get_company_usecase = _GetCompanyUseCase(
            company_repo=_company_repo,
            access_repo=_access_repo,
            role_checker=_role_checker,
        )
        _c.redeem_invite_token_usecase = _RedeemInviteTokenUseCase(
            token_repo=_token_repo,
            access_repo=_access_repo,
            hasher=_argon2_hasher,
            clock=_clock,
        )
        _c.set_primary_company_usecase = _SetPrimaryCompanyUseCase(access_repo=_access_repo)
        _c.detach_company_usecase = _DetachCompanyUseCase(access_repo=_access_repo)

        # ------------------------------------------------------------------
        # Wire billing use-cases (phase 05) — mirrors app/__init__.py wiring.
        # CRITICAL: any use-case added to _configure_di_container() MUST also
        # appear here or the test fixture will drift from production wiring.
        # Phase 05: billing use-cases now receive company_repo + access_repo
        # so company_id validation works end-to-end in tests.
        # ------------------------------------------------------------------
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
        from app.infrastructure.xlsx.billing_document_xlsx_renderer import (
            OpenpyxlBillingDocumentXlsxRenderer,
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
            RenderBillingDocumentXlsxUseCase,
            CreateTemplateUseCase,
            UpdateTemplateUseCase,
            ListTemplatesUseCase,
            GetTemplateUseCase,
            DeleteTemplateUseCase,
            ApplyTemplateToCreateDocumentUseCase,
            ImportBillingDocumentUseCase,
            ListActivitySuggestionsUseCase,
        )

        _billing_doc_repo = SqlAlchemyBillingDocumentRepository(db.session)
        _billing_tpl_repo = SqlAlchemyBillingTemplateRepository(db.session)
        _billing_counter_repo = SqlAlchemyBillingNumberCounterRepository(db.session)
        _billing_pdf_renderer = ReportLabBillingDocumentPdfRenderer()
        _billing_xlsx_renderer = OpenpyxlBillingDocumentXlsxRenderer()

        _c.billing_document_repo = _billing_doc_repo
        _c.billing_template_repo = _billing_tpl_repo
        _c.billing_counter_repo = _billing_counter_repo
        _c.billing_pdf_renderer = _billing_pdf_renderer
        _c.billing_xlsx_renderer = _billing_xlsx_renderer

        _c.create_billing_document_usecase = CreateBillingDocumentUseCase(
            doc_repo=_billing_doc_repo,
            counter_repo=_billing_counter_repo,
            project_repo=None,
            company_repo=_company_repo,
            access_repo=_access_repo,
        )
        _c.clone_billing_document_usecase = CloneBillingDocumentUseCase(
            doc_repo=_billing_doc_repo,
            counter_repo=_billing_counter_repo,
            project_repo=None,
            company_repo=_company_repo,
            access_repo=_access_repo,
        )
        _c.convert_devis_to_facture_usecase = ConvertDevisToFactureUseCase(
            doc_repo=_billing_doc_repo,
            counter_repo=_billing_counter_repo,
            project_repo=None,
            company_repo=_company_repo,
            access_repo=_access_repo,
        )
        _c.update_billing_document_usecase = UpdateBillingDocumentUseCase(
            doc_repo=_billing_doc_repo,
        )
        _c.update_billing_document_status_usecase = UpdateBillingDocumentStatusUseCase(
            doc_repo=_billing_doc_repo,
        )
        _c.list_billing_documents_usecase = ListBillingDocumentsUseCase(
            doc_repo=_billing_doc_repo,
        )
        _c.get_billing_document_usecase = GetBillingDocumentUseCase(
            doc_repo=_billing_doc_repo,
        )
        _c.delete_billing_document_usecase = DeleteBillingDocumentUseCase(
            doc_repo=_billing_doc_repo,
        )
        _c.render_billing_document_pdf_usecase = RenderBillingDocumentPdfUseCase(
            doc_repo=_billing_doc_repo,
            pdf_renderer=_billing_pdf_renderer,
        )
        _c.render_billing_document_xlsx_usecase = RenderBillingDocumentXlsxUseCase(
            doc_repo=_billing_doc_repo,
            xlsx_renderer=_billing_xlsx_renderer,
        )
        _c.create_billing_template_usecase = CreateTemplateUseCase(
            template_repo=_billing_tpl_repo,
        )
        _c.update_billing_template_usecase = UpdateTemplateUseCase(
            template_repo=_billing_tpl_repo,
        )
        _c.list_billing_templates_usecase = ListTemplatesUseCase(
            template_repo=_billing_tpl_repo,
        )
        _c.get_billing_template_usecase = GetTemplateUseCase(
            template_repo=_billing_tpl_repo,
        )
        _c.delete_billing_template_usecase = DeleteTemplateUseCase(
            template_repo=_billing_tpl_repo,
        )
        _c.apply_template_usecase = ApplyTemplateToCreateDocumentUseCase(
            doc_repo=_billing_doc_repo,
            template_repo=_billing_tpl_repo,
            counter_repo=_billing_counter_repo,
            project_repo=None,
            company_repo=_company_repo,
            access_repo=_access_repo,
        )

        # Wire import + activity-suggestions use-cases (phase 08)
        _c.import_billing_document_usecase = ImportBillingDocumentUseCase(
            doc_repo=_billing_doc_repo,
            counter_repo=_billing_counter_repo,
            company_repo=_company_repo,
            access_repo=_access_repo,
        )
        _c.list_activity_suggestions_usecase = ListActivitySuggestionsUseCase(
            doc_repo=_billing_doc_repo,
        )

        # ------------------------------------------------------------------
        # Wire payment_methods use-cases (invoice-payment-method feature)
        # CRITICAL: any use-case added to _configure_di_container() MUST also
        # appear here or the invitation_app test fixture will drift from prod.
        # ------------------------------------------------------------------
        from app.infrastructure.database.repositories.sqlalchemy_payment_method_repository import (
            SqlAlchemyPaymentMethodRepository,
        )
        from app.application.payment_methods.list_payment_methods_usecase import (
            ListPaymentMethodsUseCase as _ListPMUseCase,
        )
        from app.application.payment_methods.create_payment_method_usecase import (
            CreatePaymentMethodUseCase as _CreatePMUseCase,
        )
        from app.application.payment_methods.update_payment_method_usecase import (
            UpdatePaymentMethodUseCase as _UpdatePMUseCase,
        )
        from app.application.payment_methods.delete_payment_method_usecase import (
            DeletePaymentMethodUseCase as _DeletePMUseCase,
        )
        from app.application.payment_methods.seed_payment_methods_for_company_usecase import (
            SeedPaymentMethodsForCompanyUseCase as _SeedPMUseCase,
        )

        _pm_repo = SqlAlchemyPaymentMethodRepository(db.session)
        _c.payment_method_repo = _pm_repo

        _c.list_payment_methods_usecase = _ListPMUseCase(
            payment_method_repo=_pm_repo,
            role_checker=_role_checker,
            access_repo=_access_repo,
            company_repo=_company_repo,
        )
        _c.create_payment_method_usecase = _CreatePMUseCase(
            payment_method_repo=_pm_repo,
            role_checker=_role_checker,
        )
        _c.update_payment_method_usecase = _UpdatePMUseCase(
            payment_method_repo=_pm_repo,
            role_checker=_role_checker,
        )
        _c.delete_payment_method_usecase = _DeletePMUseCase(
            payment_method_repo=_pm_repo,
            role_checker=_role_checker,
        )
        _c.seed_payment_methods_usecase = _SeedPMUseCase(
            payment_method_repo=_pm_repo,
        )

        # Re-wire invoice use-cases with payment_method_repo injected
        from app.application.invoice.create_invoice import CreateInvoiceUseCase as _CreateInvoiceUC
        from app.application.invoice.update_invoice import UpdateInvoiceUseCase as _UpdateInvoiceUC

        if _c.invoice_repository is None:
            from app.infrastructure.adapters.sqlalchemy_invoice import SQLAlchemyInvoiceRepository as _InvRepo

            _inv_repo = _InvRepo(db.session)
            _c.invoice_repository = _inv_repo
        _c.create_invoice_usecase = _CreateInvoiceUC(
            invoice_repo=_c.invoice_repository,
            payment_method_repo=_pm_repo,
        )
        _c.update_invoice_usecase = _UpdateInvoiceUC(
            invoice_repo=_c.invoice_repository,
            payment_method_repo=_pm_repo,
        )

        # Re-wire create_company_usecase with seeder
        from app.application.companies.create_company_usecase import (
            CreateCompanyUseCase as _CreateCompanyUCv2,
        )

        _c.create_company_usecase = _CreateCompanyUCv2(
            company_repo=_company_repo,
            role_checker=_role_checker,
            seed_payment_methods=_c.seed_payment_methods_usecase,
        )

        yield test_app

        db.session.remove()
        db.drop_all()


@pytest.fixture
def inv_client(invitation_app):
    """Test client for invitation tests."""
    return invitation_app.test_client()


def _login(client, email: str, password: str) -> str:
    """Helper: login and return access token."""
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, f"Login failed: {resp.get_data(as_text=True)}"
    return resp.get_json()["access_token"]


@pytest.fixture
def admin_token(inv_client, invitation_app):
    return _login(inv_client, invitation_app._test_admin_email, invitation_app._test_admin_password)


@pytest.fixture
def member_token(inv_client, invitation_app):
    return _login(inv_client, invitation_app._test_member_email, invitation_app._test_member_password)


@pytest.fixture
def outsider_token(inv_client, invitation_app):
    return _login(inv_client, invitation_app._test_outsider_email, invitation_app._test_outsider_password)


@pytest.fixture
def superadmin_token(inv_client, invitation_app):
    return _login(
        inv_client,
        invitation_app._test_superadmin_email,
        invitation_app._test_superadmin_password,
    )


# ---------------------------------------------------------------------------
# Note fixtures — used by test_notes_endpoints.py and test_notifications_endpoints.py
# ---------------------------------------------------------------------------


@pytest.fixture
def note_open(invitation_app):
    """An open note with due_date=today, created_by member_user in project P1."""
    from datetime import date, datetime, timezone
    from uuid import UUID, uuid4

    from app import db
    from app.infrastructure.database.models.note_orm import NoteOrm

    with invitation_app.app_context():
        now = datetime.now(timezone.utc)
        # FK columns must be UUID objects (not strings) for SQLite PG_UUID compat
        note = NoteOrm(
            id=uuid4(),
            project_id=UUID(invitation_app._test_project_id),
            created_by=UUID(invitation_app._test_member_user_id),
            title="Open test note",
            description=None,
            due_date=date.today(),
            lead_time_minutes=0,
            status="open",
            created_at=now,
            updated_at=now,
        )
        db.session.add(note)
        db.session.commit()
        note_id = str(note.id)

    yield note_id

    with invitation_app.app_context():
        from sqlalchemy import text

        db.session.execute(text("DELETE FROM notes WHERE id = :id"), {"id": note_id})
        db.session.commit()


@pytest.fixture
def note_done(invitation_app):
    """A done note for 'Done' bucket assertions."""
    from datetime import date, datetime, timezone
    from uuid import UUID, uuid4

    from app import db
    from app.infrastructure.database.models.note_orm import NoteOrm

    with invitation_app.app_context():
        now = datetime.now(timezone.utc)
        note = NoteOrm(
            id=uuid4(),
            project_id=UUID(invitation_app._test_project_id),
            created_by=UUID(invitation_app._test_member_user_id),
            title="Done test note",
            description=None,
            due_date=date.today(),
            lead_time_minutes=0,
            status="done",
            created_at=now,
            updated_at=now,
        )
        db.session.add(note)
        db.session.commit()
        note_id = str(note.id)

    yield note_id

    with invitation_app.app_context():
        from sqlalchemy import text

        db.session.execute(text("DELETE FROM notes WHERE id = :id"), {"id": note_id})
        db.session.commit()


@pytest.fixture
def note_other_project(invitation_app):
    """A note in a project where member_user is NOT a member (for 403 tests)."""
    from datetime import date, datetime, timezone
    from uuid import UUID, uuid4

    from app import db
    from app.infrastructure.database.models import ProjectModel
    from app.infrastructure.database.models.note_orm import NoteOrm

    with invitation_app.app_context():
        # Create a fresh project owned by superadmin — member_user is not in it
        other_project = ProjectModel(
            name="Other Project (no member access)",
            owner_id=UUID(invitation_app._test_superadmin_user_id),
        )
        db.session.add(other_project)
        db.session.flush()

        now = datetime.now(timezone.utc)
        note = NoteOrm(
            id=uuid4(),
            project_id=other_project.id,
            created_by=UUID(invitation_app._test_superadmin_user_id),
            title="Note in other project",
            description=None,
            due_date=date.today(),
            lead_time_minutes=0,
            status="open",
            created_at=now,
            updated_at=now,
        )
        db.session.add(note)
        db.session.commit()
        note_id = str(note.id)
        project_id = str(other_project.id)

    yield note_id, project_id

    with invitation_app.app_context():
        from sqlalchemy import text

        db.session.execute(text("DELETE FROM notes WHERE id = :id"), {"id": note_id})
        db.session.execute(text("DELETE FROM projects WHERE id = :id"), {"id": project_id})
        db.session.commit()


@pytest.fixture
def note_dismissed_by_member(invitation_app):
    """An open note that member_user has already dismissed."""
    from datetime import date, datetime, timezone
    from uuid import UUID, uuid4

    from app import db
    from app.infrastructure.database.models.note_orm import NoteDismissalOrm, NoteOrm

    with invitation_app.app_context():
        now = datetime.now(timezone.utc)
        note = NoteOrm(
            id=uuid4(),
            project_id=UUID(invitation_app._test_project_id),
            created_by=UUID(invitation_app._test_member_user_id),
            title="Already dismissed note",
            description=None,
            due_date=date.today(),
            lead_time_minutes=0,
            status="open",
            created_at=now,
            updated_at=now,
        )
        db.session.add(note)
        db.session.flush()

        dismissal = NoteDismissalOrm(
            user_id=UUID(invitation_app._test_member_user_id),
            note_id=note.id,
            dismissed_at=now,
        )
        db.session.add(dismissal)
        db.session.commit()
        note_id = str(note.id)

    yield note_id

    with invitation_app.app_context():
        from sqlalchemy import text

        db.session.execute(text("DELETE FROM notes WHERE id = :id"), {"id": note_id})
        db.session.commit()


@pytest.fixture
def non_member_user(invitation_app):
    """Alias for outsider_user — a user with no project memberships."""
    # The existing 'outsider@invite-test.com' has no memberships in P1.
    return invitation_app._test_outsider_email


@pytest.fixture
def non_member_token(inv_client, invitation_app):
    """JWT token for the outsider user (no project memberships)."""
    return _login(inv_client, invitation_app._test_outsider_email, "Outsider1234!")
