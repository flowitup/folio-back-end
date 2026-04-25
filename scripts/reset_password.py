"""One-shot helper to reset a user's password.

Usage (from construction-back-end/):
    uv run python scripts/reset_password.py admin@test.com password123
"""
import sys

from app import create_app, db
from app.infrastructure.database.models import UserModel
from argon2 import PasswordHasher


def main():
    if len(sys.argv) != 3:
        print("Usage: python scripts/reset_password.py <email> <new_password>")
        sys.exit(1)

    email, new_password = sys.argv[1], sys.argv[2]
    app = create_app()
    with app.app_context():
        user = db.session.query(UserModel).filter_by(email=email.lower()).first()
        if not user:
            print(f"User not found: {email}")
            sys.exit(1)
        user.password_hash = PasswordHasher().hash(new_password)
        db.session.commit()
        print(f"Password reset for {email}")


if __name__ == "__main__":
    main()
