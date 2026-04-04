"""
Main seed script for the construction backend.

Usage:
    # Seed only permissions and roles
    uv run python scripts/seed.py

    # Seed with admin user (CLI args - dev only)
    uv run python scripts/seed.py --with-admin admin@example.com password

    # Seed with admin user (env vars - recommended)
    ADMIN_EMAIL=admin@example.com ADMIN_PASSWORD=secret uv run python scripts/seed.py --with-admin

    # Seed with admin user and sample projects
    uv run python scripts/seed.py --with-admin admin@example.com password --with-projects

    # Seed with labor data (requires projects to exist)
    uv run python scripts/seed.py --with-admin admin@example.com password --with-projects --with-labor

Security Note:
    Prefer environment variables over CLI args to avoid password exposure
    in shell history. CLI args are acceptable for local development only.
"""

import sys

from app import create_app
from scripts.seed_auth import (
    seed_permissions,
    seed_roles,
    create_admin_user,
    create_client_user,
    get_admin_credentials,
)
from scripts.seed_project import seed_projects
from scripts.seed_labor import seed_labor


def main():
    """Main entry point for seeding."""
    app = create_app()

    with app.app_context():
        print("Seeding database...")

        print("\n1. Creating permissions...")
        permission_map = seed_permissions()

        print("\n2. Creating roles...")
        role_map = seed_roles(permission_map)

        admin_user = None
        if "--with-admin" in sys.argv:
            email, password = get_admin_credentials()

            if email and password:
                print(f"\n3. Creating admin user: {email}...")
                admin_user = create_admin_user(email, password, role_map)

                # Also create a client user for testing
                print("\n4. Creating client user: client@example.com...")
                create_client_user("client@example.com", "password123", role_map)
            else:
                print("\nError: --with-admin requires credentials")
                print("Options:")
                print("  1. Environment vars: ADMIN_EMAIL=x ADMIN_PASSWORD=y python scripts/seed.py --with-admin")
                print("  2. CLI args (dev only): python scripts/seed.py --with-admin email password")
                sys.exit(1)

        if "--with-projects" in sys.argv:
            if admin_user:
                print("\n5. Creating sample projects...")
                seed_projects(admin_user)
            else:
                print("\nError: --with-projects requires --with-admin")
                sys.exit(1)

        if "--with-labor" in sys.argv:
            print("\n6. Creating sample labor data...")
            seed_labor()

        print("\nSeeding complete!")


if __name__ == "__main__":
    main()
