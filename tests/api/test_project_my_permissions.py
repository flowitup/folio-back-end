"""Project responses expose the caller's EFFECTIVE per-project permissions.

`my_permissions` = global-role perms UNION the caller's membership-role perms on
that project UNION the company-matrix resolver's output for that project
(Phase 1 additive union — see app.api.v1.projects.decorators._effective_perms_for).
The frontend gates per-project UI (e.g. "log labor") on this, so an invited
project admin/manager gets the right UI even though their global role is the
read-only default, and now so does a company admin/manager/member via the matrix.

Fixtures (conftest): inv_client, superadmin_token, invitation_app.

The membership-union math (global ∪ membership-role perms) is covered by
tests/unit/api/test_project_membership_role_permissions.py; here we assert the
project endpoints actually expose it as `my_permissions`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_detail_includes_my_permissions(inv_client, superadmin_token, invitation_app):
    pid = invitation_app._test_project_id
    resp = inv_client.get(f"/api/v1/projects/{pid}", headers=_auth(superadmin_token))
    assert resp.status_code == 200
    body = resp.get_json()
    assert isinstance(body["my_permissions"], list)
    assert "*:*" in body["my_permissions"]


def test_list_includes_my_permissions(inv_client, superadmin_token):
    resp = inv_client.get("/api/v1/projects", headers=_auth(superadmin_token))
    assert resp.status_code == 200
    projects = resp.get_json()["projects"]
    assert projects, "expected at least one project"
    for p in projects:
        assert "my_permissions" in p
        assert "*:*" in p["my_permissions"]


def test_my_permissions_is_legacy_union_resolver(inv_client, admin_token, invitation_app):
    """`my_permissions` additively includes resolver output, not just the legacy union.

    `admin_token`'s global "admin" role only carries {project:invite, project:read}
    (see conftest's invitation_app seed) — none of the company-matrix permissions
    asserted below. Making the seeded project's owner a company "admin" and
    pointing the project at that company must add the full admin matrix on top,
    proving the union is legacy ∪ resolver, not legacy alone.
    """
    from app import db
    from app.infrastructure.database.models.company import CompanyModel
    from app.infrastructure.database.models.project import ProjectModel
    from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel

    pid = invitation_app._test_project_id
    admin_user_id = UUID(invitation_app._test_admin_user_id)

    with invitation_app.app_context():
        now = datetime.now(timezone.utc)
        company = CompanyModel(
            id=uuid4(),
            legal_name="My-Permissions Union Co",
            address="1 rue Union",
            created_by=admin_user_id,
            created_at=now,
            updated_at=now,
        )
        db.session.add(company)
        db.session.flush()
        db.session.add(
            UserCompanyAccessModel(user_id=admin_user_id, company_id=company.id, role="admin", is_primary=True)
        )
        project_row = db.session.get(ProjectModel, UUID(pid))
        project_row.company_id = company.id
        db.session.commit()

    resp = inv_client.get(f"/api/v1/projects/{pid}", headers=_auth(admin_token))
    assert resp.status_code == 200
    perms = set(resp.get_json()["my_permissions"])
    # Legacy global-role permissions still present (union is additive).
    assert {"project:invite", "project:read"} <= perms
    # Resolver-only permissions — NOT in admin_token's legacy set — now present.
    assert {"project:manage_labor", "project:manage_invoices", "bibliotheque:manage", "project:view_pay"} <= perms
