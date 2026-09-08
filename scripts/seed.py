"""Main seed script for the construction backend.

Usage:
    # Seed with admin user (CLI args - dev only)
    uv run python scripts/seed.py --with-admin admin@example.com password

    # Seed with admin user (env vars - recommended)
    ADMIN_EMAIL=admin@example.com ADMIN_PASSWORD=secret \
        uv run python scripts/seed.py --with-admin

    # Seed admin + sample projects
    uv run python scripts/seed.py --with-admin admin@example.com password --with-projects

    # Seed full suite for FE/manual QA + e2e tests
    ADMIN_EMAIL=admin@example.com ADMIN_PASSWORD=password123 \
        uv run python scripts/seed.py --all

    # Granular flags (combine as needed)
    uv run python scripts/seed.py --with-admin admin@example.com password \
        --with-projects --with-users --with-memberships --with-invitations \
        --with-labor --with-invoices --with-notes

    # Reset invoices and reseed (use --reset-invoices alongside --with-invoices)
    uv run python scripts/seed.py --with-admin admin@example.com password \
        --with-projects --with-invoices --reset-invoices

Test password convention:
    All users seeded by --with-users (and the legacy `client@example.com`)
    use the hardcoded password `password123`. DEV/TEST ONLY — never use this
    convention in staging or production.

Security Note:
    Prefer environment variables over CLI args for the admin password to avoid
    exposing it in shell history. CLI args are acceptable for local development.

Flag dependency graph:
    --with-admin            (no deps) — also creates the demo company, since
                            every project needs one (projects.company_id NOT NULL)
    --with-projects         requires --with-admin
    --with-users            requires --with-admin
    --with-memberships      requires --with-projects + --with-users
    --with-invitations      requires --with-admin + --with-projects
    --with-labor            requires --with-projects
    --with-invoices         requires --with-projects
    --with-notes            requires --with-admin + --with-projects (uses --with-users if available)
    --with-companies        requires --with-admin (uses --with-users/--with-persons if available)
    --all                   shorthand for everything above
"""

import sys

from app import create_app
from scripts.seed_auth import (
    create_admin_user,
    create_client_user,
    get_admin_credentials,
)
from scripts.seed_project import seed_projects
from scripts.seed_users import seed_test_users
from scripts.seed_memberships import seed_memberships
from scripts.seed_invitations import seed_invitations
from scripts.seed_labor import seed_labor
from scripts.seed_persons import seed_persons
from scripts.seed_invoices import seed_invoices
from scripts.seed_notes import seed_notes
from scripts.seed_companies import ensure_company, seed_companies


def _flag(name: str, *aliases: str) -> bool:
    """Return True if any of the given flag names is present in argv."""
    return any(n in sys.argv for n in (name, *aliases))


def main() -> None:
    """Main entry point for seeding."""
    app = create_app()

    # Single short-circuit alias: --all turns on every --with-* flag.
    all_flag = _flag("--all", "--full")

    with app.app_context():
        print("Seeding database...")

        admin_user = None
        company = None
        if all_flag or _flag("--with-admin"):
            email, password = get_admin_credentials()

            if email and password:
                print(f"\n1. Creating admin user: {email}...")
                admin_user = create_admin_user(email, password)

                # Also create the legacy client user for backwards-compat
                print("\n2. Creating client user: client@example.com...")
                create_client_user("client@example.com", "password123")

                # The company is the tenant: it exists before any project.
                print("\n3. Creating the demo company...")
                company = ensure_company(admin_user)
            else:
                print("\nError: --with-admin requires credentials")
                print("Options:")
                print("  1. Environment vars: ADMIN_EMAIL=x ADMIN_PASSWORD=y " "python scripts/seed.py --with-admin")
                print("  2. CLI args (dev only): python scripts/seed.py --with-admin email password")
                sys.exit(1)

        # Test user roster (admins, managers, regular users, inactive)
        user_map: dict = {}
        if all_flag or _flag("--with-users"):
            if not admin_user:
                print("\nError: --with-users requires --with-admin")
                sys.exit(1)
            print("\n4. Creating test user roster...")
            user_map = seed_test_users()

        # Sample projects (owned by admin)
        if all_flag or _flag("--with-projects"):
            if not admin_user:
                print("\nError: --with-projects requires --with-admin")
                sys.exit(1)
            print("\n5. Creating sample projects...")
            seed_projects(admin_user, company)

        # Project memberships (user_projects entries beyond owner)
        if all_flag or _flag("--with-memberships"):
            if not admin_user:
                print("\nError: --with-memberships requires --with-admin")
                sys.exit(1)
            if not user_map:
                print("\nError: --with-memberships requires --with-users " "(or --all so the user roster exists)")
                sys.exit(1)
            print("\n6. Creating project assignments...")
            seed_memberships(user_map, admin_user)

        # Invitations across all 4 lifecycle states
        if all_flag or _flag("--with-invitations"):
            if not admin_user:
                print("\nError: --with-invitations requires --with-admin")
                sys.exit(1)
            print("\n7. Creating invitations (pending/expired/revoked/accepted)...")
            seed_invitations(admin_user)

        if all_flag or _flag("--with-labor"):
            print("\n8. Creating sample labor data...")
            seed_labor()

        # Persons (global identity entities) — Phase 1b-ii. Independent of
        # workers; backfill linking workers→persons lands in Phase 1c.
        if all_flag or _flag("--with-persons"):
            print("\n9. Creating sample persons...")
            seed_persons()

        if all_flag or _flag("--with-invoices"):
            print("\n10. Creating sample invoices...")
            seed_invoices(reset=_flag("--reset-invoices"))

        # Notes + per-user dismissals (depends on projects + ideally on users)
        if all_flag or _flag("--with-notes"):
            print("\n11. Creating notes + dismissals...")
            seed_notes()

        # Attach the rest of the roster to the demo company created above and
        # scope labor roles + the person directory. Best run after
        # --with-users and --with-persons so there is something to attach;
        # anything not yet seeded is silently skipped.
        if all_flag or _flag("--with-companies"):
            if not admin_user:
                print("\nError: --with-companies requires --with-admin")
                sys.exit(1)
            print("\n12. Attaching the roster to the demo company...")
            seed_companies(admin_user, user_map)

        print("\nSeeding complete!")


if __name__ == "__main__":
    main()
