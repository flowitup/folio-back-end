"""Seed script for project data.

Every project belongs to a company (`projects.company_id` is NOT NULL), so the
caller passes the company these demo projects live in.
"""

from uuid import uuid4

from app import db
from app.infrastructure.database.models import CompanyModel, ProjectModel, UserModel

DEFAULT_PROJECTS = [
    {"name": "Downtown Office Tower", "address": "123 Main Street, Suite 100"},
    {"name": "Riverside Apartments", "address": "456 River Road"},
    {"name": "Shopping Mall Renovation", "address": "789 Commerce Blvd"},
]


def seed_projects(owner: UserModel, company: CompanyModel) -> None:
    """Create sample projects for development, owned by `owner` inside `company`."""
    for proj_data in DEFAULT_PROJECTS:
        existing = db.session.query(ProjectModel).filter_by(name=proj_data["name"]).first()
        if existing:
            if existing.company_id is None:
                existing.company_id = company.id
                print(f"  Project '{proj_data['name']}' already exists — attached to the demo company.")
            else:
                print(f"  Project '{proj_data['name']}' already exists, skipping.")
            continue

        project = ProjectModel(
            id=uuid4(),
            name=proj_data["name"],
            address=proj_data["address"],
            owner_id=owner.id,
            company_id=company.id,
        )
        db.session.add(project)
        print(f"  Created project: {proj_data['name']}")

    db.session.commit()
