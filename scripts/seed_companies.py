"""Seed script for the company-as-tenant model.

The company is the tenant, so it comes first: `ensure_company()` creates the
demo company ("Folio Demo SARL") and makes the seed admin its `admin`, and
every project seeded afterwards is created inside it (`projects.company_id` is
NOT NULL). `seed_companies()` then attaches the rest of the roster at their
company role and scopes persons and labor roles.

Idempotent: re-running finds the existing company by legal_name and skips
attachments/labor roles/company_persons that already exist.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app import db
from app.domain.value_objects.phone_number import InvalidPhoneNumberError, normalize_phone
from app.infrastructure.database.models import (
    CompanyModel,
    CompanyPersonModel,
    LaborRoleModel,
    PersonModel,
    UserCompanyAccessModel,
    UserModel,
)

COMPANY_NAME = "Folio Demo SARL"
COMPANY_ADDRESS = "12 rue de la Construction, 75011 Paris"

# Maps the seed_users.py TEST_USERS email → company role. Any seeded user
# not listed here (e.g. the inactive account) is skipped.
_COMPANY_ROLE_FOR_EMAIL: dict[str, str] = {
    "superadmin@example.com": "admin",
    "admin2@example.com": "admin",
    "manager.alice@example.com": "manager",
    "manager.bob@example.com": "manager",
    "manager.carol@example.com": "manager",
    "user.dave@example.com": "member",
    "user.eve@example.com": "member",
    "user.frank@example.com": "member",
    "user.grace@example.com": "member",
    "user.henry@example.com": "member",
}

# Mirrors migrations/versions/f2a3b4c5d6e7_add_labor_roles.py (name, color, slug).
_DEFAULT_LABOR_ROLES = (
    ("Thợ chính", "#3B82F6", "tho_chinh"),
    ("Thợ phụ", "#10B981", "tho_phu"),
)


def _find_or_create_company(admin_user: UserModel) -> CompanyModel:
    company = db.session.query(CompanyModel).filter_by(legal_name=COMPANY_NAME).first()
    if company is not None:
        print(f"  Company '{COMPANY_NAME}' already exists, skipping create.")
        return company

    now = datetime.now(timezone.utc)
    company = CompanyModel(
        id=uuid4(),
        legal_name=COMPANY_NAME,
        address=COMPANY_ADDRESS,
        created_by=admin_user.id,
        created_at=now,
        updated_at=now,
        default_phone_region="FR",
    )
    db.session.add(company)
    db.session.flush()
    print(f"  Created company: {COMPANY_NAME} ({company.id})")
    return company


def _attach_member(company: CompanyModel, user: UserModel, role: str, is_primary: bool) -> None:
    existing = db.session.get(UserCompanyAccessModel, (user.id, company.id))
    if existing is not None:
        print(f"    [skip] {user.email} already attached to {COMPANY_NAME}")
        return
    db.session.add(
        UserCompanyAccessModel(
            user_id=user.id,
            company_id=company.id,
            role=role,
            is_primary=is_primary,
            attached_at=datetime.now(timezone.utc),
        )
    )
    print(f"    [add]  {user.email} → {role}")


def ensure_company(admin_user: UserModel) -> CompanyModel:
    """Create (or find) the demo company with `admin_user` as its admin.

    Runs before anything that needs a company — projects above all, whose
    `company_id` is NOT NULL. Commits so later steps can reference the id.
    """
    company = _find_or_create_company(admin_user)
    _attach_member(company, admin_user, role="admin", is_primary=True)
    _scope_labor_roles(company)
    db.session.commit()
    return company


def _attach_memberships(company: CompanyModel, user_map: dict[str, UserModel]) -> None:
    for email, role in _COMPANY_ROLE_FOR_EMAIL.items():
        user = user_map.get(email)
        if user is None:
            continue
        _attach_member(company, user, role=role, is_primary=False)


def _scope_labor_roles(company: CompanyModel) -> None:
    for name, color, slug in _DEFAULT_LABOR_ROLES:
        existing = db.session.query(LaborRoleModel).filter_by(company_id=company.id, name=name).first()
        if existing is not None:
            print(f"    [skip] labor role '{name}' already scoped to {COMPANY_NAME}")
            continue
        db.session.add(
            LaborRoleModel(
                id=uuid4(),
                company_id=company.id,
                name=name,
                slug=slug,
                color=color,
                created_at=datetime.now(timezone.utc),
            )
        )
        print(f"    [add]  labor role '{name}' ({slug}) for {COMPANY_NAME}")


def _scope_persons(company: CompanyModel) -> None:
    persons = db.session.query(PersonModel).all()
    for person in persons:
        existing = db.session.query(CompanyPersonModel).filter_by(company_id=company.id, person_id=person.id).first()
        if existing is not None:
            continue
        phone_normalized = None
        if person.phone:
            try:
                phone_normalized = normalize_phone(person.phone, default_region=company.default_phone_region)
            except InvalidPhoneNumberError:
                phone_normalized = None
        db.session.add(
            CompanyPersonModel(
                id=uuid4(),
                company_id=company.id,
                person_id=person.id,
                is_active=True,
                phone_normalized=phone_normalized,
                created_at=datetime.now(timezone.utc),
            )
        )
        print(f"    [add]  company_persons for '{person.name}' in {COMPANY_NAME}")


def seed_companies(admin_user: UserModel, user_map: dict[str, UserModel]) -> CompanyModel:
    """Attach the roster to the demo company and scope labor roles + persons.

    `user_map` is the dict returned by `scripts.seed_users.seed_test_users`
    (may be empty if `--with-users` was not run — attachments for those users
    are then simply skipped).
    """
    company = ensure_company(admin_user)
    _attach_memberships(company, user_map)
    _scope_labor_roles(company)
    _scope_persons(company)
    db.session.commit()
    print(f"  Seeded company '{COMPANY_NAME}' with members, labor roles and persons.")
    return company
