"""In-memory `AuthzReaderPort` for unit tests — one company, one project.

Use it wherever a use-case resolves permissions itself (invitations) instead of
standing up a database: pick the caller's company role, whether they are
assigned, their D8 rows and the platform-ops flag, and the real resolver does
the rest.
"""

from __future__ import annotations


class FakeAuthzReader:
    """AuthzReaderPort double covering one `(company, project)` pair."""

    def __init__(self, *, role, company_id, project_id, assigned=True, grants=(), ops=False):
        self._role = role
        self._company_id = company_id
        self._project_id = project_id
        self._assigned = assigned
        self._grants = list(grants)
        self._ops = ops

    def company_role_for(self, user_id, company_id):
        return self._role if company_id == self._company_id else None

    def is_assigned(self, user_id, project_id):
        return self._assigned and project_id == self._project_id

    def project_company_id(self, project_id):
        return self._company_id if project_id == self._project_id else None

    def project_exists(self, project_id):
        return project_id == self._project_id

    def grants_for(self, user_id, company_id, project_id):
        return list(self._grants)

    def primary_company_id(self, user_id):
        return self._company_id

    def admin_company_ids(self, user_id):
        return [self._company_id] if self._role == "admin" else []

    def company_roles_for(self, user_id):
        return [(self._company_id, self._role)]

    def is_platform_ops(self, user_id):
        return self._ops

    def has_project_assignment_in_company(self, user_id, company_id):
        return self._assigned

    def project_ids_for_company(self, company_id):
        return [self._project_id]

    def assigned_project_ids(self, user_id, project_ids):
        return list(project_ids) if self._assigned else []

    def assigned_project_ids_for_users(self, company_id, user_ids):
        return {}
