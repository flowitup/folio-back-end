"""Create a project of a company the test's caller has nothing to do with.

Used to separate the two answers a project id can get: an id that does not
exist is 404, an existing project the caller may not read is 403. Both are
route-level contracts, so every project-scoped suite can assert them.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4


def create_foreign_project(app, *, legal_name: str = "Foreign Tenant Co") -> str:
    """Insert `<company, owner, project>` nobody in the fixture belongs to. Returns the project id."""
    from app import db
    from app.infrastructure.database.models import ProjectModel, UserModel
    from app.infrastructure.database.models.company import CompanyModel

    now = datetime.now(timezone.utc)
    with app.app_context():
        owner = UserModel(id=uuid4(), email=f"foreign_{uuid4().hex[:8]}@test.com", is_active=True)
        db.session.add(owner)
        db.session.flush()

        company = CompanyModel(
            id=uuid4(),
            legal_name=f"{legal_name} {uuid4().hex[:6]}",
            address="1 rue Ailleurs",
            created_by=owner.id,
            created_at=now,
            updated_at=now,
        )
        db.session.add(company)
        db.session.flush()

        project = ProjectModel(id=uuid4(), name="Foreign Project", owner_id=owner.id, company_id=company.id)
        db.session.add(project)
        db.session.commit()
        return str(project.id)
