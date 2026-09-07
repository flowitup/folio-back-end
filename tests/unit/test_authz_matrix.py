"""Unit tests for the permission matrix — every cell, plus deny/non-deniable rules.

Pure-domain tests: no Flask app, no DB. `permissions_for` is exercised directly;
deny-wins and `project:read` non-deniability are exercised through the resolver
against a tiny in-memory fake of `AuthzReaderPort`.
"""

from __future__ import annotations

from uuid import uuid4

from app.domain.authz.matrix import (
    CUSTOMISABLE_PERMISSIONS,
    NON_DENIABLE,
    permissions_for,
)
from app.domain.authz.resolver import effective_permissions


# ---------------------------------------------------------------------------
# permissions_for(role, assigned) — every matrix cell.
# ---------------------------------------------------------------------------


class TestAdminMatrix:
    """Admin is implicit on every project of their company — assigned is irrelevant."""

    def test_admin_has_create_and_delete(self):
        perms = permissions_for("admin", assigned=False)
        assert "project:create" in perms
        assert "project:delete" in perms

    def test_admin_has_full_project_surface_unassigned(self):
        """Admin gets project-scoped perms even without an assignment row."""
        perms = permissions_for("admin", assigned=False)
        for perm in (
            "project:read",
            "project:update",
            "project:invite",
            "project:manage_users",
            "project:manage_labor",
            "project:manage_invoices",
            "bibliotheque:manage",
            "project:log_own_attendance",
            "project:view_pay",
        ):
            assert perm in perms, perm

    def test_admin_has_company_management(self):
        perms = permissions_for("admin", assigned=True)
        assert {"company:manage_members", "company:manage_settings", "company:manage_billing"} <= perms

    def test_admin_has_user_read(self):
        assert "user:read" in permissions_for("admin", assigned=False)

    def test_admin_assigned_is_a_superset_equal_to_unassigned(self):
        """Assignment never changes an admin's permission set."""
        assert permissions_for("admin", assigned=True) == permissions_for("admin", assigned=False)


class TestManagerMatrix:
    """Manager holds full project read/write only when assigned to that project."""

    def test_manager_assigned_has_no_create_or_delete(self):
        perms = permissions_for("manager", assigned=True)
        assert "project:create" not in perms
        assert "project:delete" not in perms

    def test_manager_assigned_has_no_company_permissions(self):
        perms = permissions_for("manager", assigned=True)
        assert not any(p.startswith("company:") for p in perms)

    def test_manager_assigned_has_full_write_surface(self):
        perms = permissions_for("manager", assigned=True)
        for perm in (
            "project:read",
            "project:update",
            "project:invite",
            "project:manage_users",
            "project:manage_labor",
            "project:manage_invoices",
            "bibliotheque:manage",
            "project:log_own_attendance",
            "project:view_pay",
        ):
            assert perm in perms, perm

    def test_manager_unassigned_loses_project_scope(self):
        """Not assigned to the project in question → only the always-on perms."""
        perms = permissions_for("manager", assigned=False)
        assert perms == frozenset({"user:read"})


class TestMemberMatrix:
    """Member holds read-only access to the assigned project's surface, never view_pay."""

    def test_member_assigned_is_read_only(self):
        perms = permissions_for("member", assigned=True)
        assert "project:read" in perms
        assert "project:log_own_attendance" in perms
        for perm in (
            "project:update",
            "project:invite",
            "project:manage_users",
            "project:manage_labor",
            "project:manage_invoices",
            "bibliotheque:manage",
        ):
            assert perm not in perms, perm

    def test_member_never_sees_pay(self):
        assert "project:view_pay" not in permissions_for("member", assigned=True)
        assert "project:view_pay" not in permissions_for("member", assigned=False)

    def test_member_unassigned_loses_project_scope(self):
        perms = permissions_for("member", assigned=False)
        assert perms == frozenset({"user:read"})

    def test_member_has_no_create_delete_or_company(self):
        perms = permissions_for("member", assigned=True)
        assert "project:create" not in perms
        assert "project:delete" not in perms
        assert not any(p.startswith("company:") for p in perms)


class TestUnknownRole:
    def test_unknown_role_grants_nothing(self):
        assert permissions_for("superadmin", assigned=True) == frozenset()
        assert permissions_for("", assigned=True) == frozenset()


# ---------------------------------------------------------------------------
# D8 whitelist constants.
# ---------------------------------------------------------------------------


class TestCustomisableWhitelist:
    def test_create_delete_and_company_wildcard_excluded(self):
        assert "project:create" not in CUSTOMISABLE_PERMISSIONS
        assert "project:delete" not in CUSTOMISABLE_PERMISSIONS
        assert not any(p.startswith("company:") for p in CUSTOMISABLE_PERMISSIONS)
        assert "*:*" not in CUSTOMISABLE_PERMISSIONS

    def test_project_read_excluded_from_customisable_and_is_non_deniable(self):
        assert "project:read" not in CUSTOMISABLE_PERMISSIONS
        assert NON_DENIABLE == frozenset({"project:read"})

    def test_whitelist_matches_plan_exactly(self):
        assert CUSTOMISABLE_PERMISSIONS == frozenset(
            {
                "project:update",
                "project:invite",
                "project:manage_users",
                "project:manage_labor",
                "project:manage_invoices",
                "project:log_own_attendance",
                "bibliotheque:manage",
                "project:view_pay",
            }
        )


# ---------------------------------------------------------------------------
# Deny wins; project:read is never denied — exercised through the resolver
# against a minimal in-memory AuthzReaderPort fake.
# ---------------------------------------------------------------------------


class _FakeReader:
    """Minimal AuthzReaderPort fake: one company, one project, configurable grants."""

    def __init__(self, role: "str | None", assigned: bool, grants: "list[tuple[str, str]]"):
        self.role = role
        self.assigned = assigned
        self.grants = grants
        self.company_id = uuid4()
        self.project_id = uuid4()

    def company_role_for(self, user_id, company_id):
        return self.role

    def is_assigned(self, user_id, project_id):
        return self.assigned

    def project_company_id(self, project_id):
        return self.company_id

    def primary_company_id(self, user_id):
        return self.company_id

    def admin_company_ids(self, user_id):
        return [self.company_id] if self.role == "admin" else []

    def grants_for(self, user_id, company_id, project_id):
        return self.grants


def test_deny_removes_a_customisable_permission():
    reader = _FakeReader(role="manager", assigned=True, grants=[("project:manage_invoices", "deny")])
    perms = effective_permissions(reader, uuid4(), project_id=reader.project_id)
    assert "project:manage_invoices" not in perms
    # Everything else the matrix grants stays.
    assert "project:manage_labor" in perms


def test_grant_adds_a_permission_not_in_the_base_matrix():
    reader = _FakeReader(role="member", assigned=True, grants=[("project:manage_invoices", "grant")])
    perms = effective_permissions(reader, uuid4(), project_id=reader.project_id)
    assert "project:manage_invoices" in perms


def test_project_read_deny_is_ignored():
    """project:read is NON_DENIABLE — a deny row for it must have no effect."""
    reader = _FakeReader(role="member", assigned=True, grants=[("project:read", "deny")])
    perms = effective_permissions(reader, uuid4(), project_id=reader.project_id)
    assert "project:read" in perms


def test_deny_wins_over_a_grant_for_the_same_permission():
    reader = _FakeReader(
        role="member",
        assigned=True,
        grants=[("project:manage_labor", "grant"), ("project:manage_labor", "deny")],
    )
    perms = effective_permissions(reader, uuid4(), project_id=reader.project_id)
    assert "project:manage_labor" not in perms
