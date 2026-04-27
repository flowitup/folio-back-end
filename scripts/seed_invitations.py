"""Seed script for invitations in all 4 lifecycle states.

Creates a mix of pending / expired / revoked / accepted invitations so test
scenarios cover every state the FE accept-invite flow can encounter:

    pending       — status=pending, expires_at > now      → token still valid
    expiring-soon — status=pending, expires_at = now+1d   → about to expire
    expired       — status=pending, expires_at < now      → 410 Gone (reason: expired)
    revoked       — status=revoked                        → 410 Gone (reason: revoked)
    accepted      — status=accepted, accepted_at set      → 410 Gone (reason: accepted)

Token hashes are SHA-256 of a deterministic seed string (we don't email these
tokens — they're test fixtures only).

Idempotent: re-running skips invitations whose (email, project_id, status) trio
already exists.

Standalone usage:
    EMAIL_PROVIDER=inmemory uv run python -m scripts.seed_invitations

Typically invoked via the main `scripts/seed.py` orchestrator with
`--with-invitations` (requires --with-admin + --with-projects).
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app import db
from app.infrastructure.database.models import (
    InvitationModel,
    ProjectModel,
    RoleModel,
    UserModel,
)


def _fake_token_hash(seed: str) -> str:
    """Deterministic SHA-256 hex from a seed string. Test fixtures only."""
    return hashlib.sha256(f"seed-token::{seed}".encode("utf-8")).hexdigest()


def seed_invitations(
    role_map: dict[str, RoleModel],
    inviter: UserModel,
) -> int:
    """Create invitations across all 4 lifecycle states.

    Args:
        role_map: name → RoleModel from seed_roles()
        inviter: the user to stamp as `invited_by` (typically admin)

    Returns:
        Count of invitations actually created.
    """
    projects = db.session.query(ProjectModel).all()
    if not projects:
        print("  No projects found. Run seed.py with --with-admin --with-projects first.")
        return 0

    if "user" not in role_map or "manager" not in role_map:
        print("  Required roles not found in role_map; skipping invitation seed.")
        return 0

    now = datetime.now(timezone.utc)
    user_role = role_map["user"]
    manager_role = role_map["manager"]

    # Plan: (email, project_index, role, status, expires_offset_days, accepted_offset_days | None)
    # status ∈ {"pending", "revoked", "accepted"}; "expired" is just pending + past expires_at
    PLAN = [
        # PENDING — fresh, lots of time left
        ("new.invitee1@example.com", 0, user_role, "pending", 6, None),
        ("new.invitee2@example.com", 1, manager_role, "pending", 7, None),
        # PENDING — about to expire (1 day left)
        ("expiring.invitee@example.com", 2, user_role, "pending", 1, None),
        # EXPIRED (status=pending but expires_at in the past)
        ("expired.invitee1@example.com", 0, user_role, "pending", -1, None),
        ("expired.invitee2@example.com", 1, user_role, "pending", -7, None),
        # REVOKED
        ("revoked.invitee@example.com", 0, user_role, "revoked", 6, None),
        # ACCEPTED — accepted 3 days ago, original expires_at was 4 days from then (now-3+4=now+1)
        ("accepted.invitee@example.com", 1, user_role, "accepted", 1, -3),
    ]

    created = 0
    for email, proj_idx, role, status, exp_offset_days, acc_offset_days in PLAN:
        if proj_idx >= len(projects):
            print(f"  [warn] Project index {proj_idx} out of range; skipping {email}")
            continue
        project = projects[proj_idx]

        existing = (
            db.session.query(InvitationModel)
            .filter_by(email=email.lower(), project_id=project.id, status=status)
            .first()
        )
        if existing:
            print(f"  [skip] {email} → '{project.name}' ({status}) already exists")
            continue

        expires_at = now + timedelta(days=exp_offset_days)
        accepted_at = now + timedelta(days=acc_offset_days) if acc_offset_days is not None else None

        invitation = InvitationModel(
            id=uuid4(),
            email=email.lower(),
            project_id=project.id,
            role_id=role.id,
            token_hash=_fake_token_hash(f"{email}::{project.id}::{status}"),
            status=status,
            expires_at=expires_at,
            invited_by=inviter.id,
            created_at=now - timedelta(days=2),  # backdate slightly for realism
            accepted_at=accepted_at,
            updated_at=now,
        )
        db.session.add(invitation)
        created += 1

        # Human-readable label for the log
        if status == "pending" and expires_at < now:
            label = "expired"
        elif status == "pending" and (expires_at - now) <= timedelta(days=1):
            label = "expiring-soon"
        else:
            label = status
        print(f"  [add]  {email} → '{project.name}' ({label})")

    db.session.commit()
    print(f"\n  Created {created} invitations.")
    return created


def main() -> None:
    """Standalone entry point — assumes admin + projects + roles already seeded."""
    from app import create_app
    from scripts.seed_auth import seed_permissions, seed_roles

    app = create_app()
    with app.app_context():
        print("Seeding invitations...")
        permission_map = seed_permissions()
        role_map = seed_roles(permission_map)

        admin = db.session.query(UserModel).join(UserModel.roles).filter(RoleModel.name == "admin").first()
        if not admin:
            print("  No admin user found. Run seed.py --with-admin first.")
            return

        seed_invitations(role_map, admin)
        print("\n  Done.")


if __name__ == "__main__":
    main()
