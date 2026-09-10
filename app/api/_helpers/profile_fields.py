"""Apply ``display_name`` / ``phone`` edits to a user the same way everywhere.

Shared by the platform-ops route (``PATCH /admin/users/<id>``) and the self-service route
(``PATCH /auth/me``): the phone is the sign-in identity when ``LOGIN_MODE`` allows phone, so it is
normalised to E.164 and must stay unique across users; the display name is trimmed and cleared
when empty. Returns ``None`` on success or ``(status, error, message)`` for the route to answer.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Tuple

from app.application.ports.user_repository import UserRepositoryPort
from app.domain.entities.user import User
from app.domain.value_objects.phone_number import InvalidPhoneNumberError, normalize_phone

ProfileError = Tuple[int, str, str]


def apply_profile_fields(user: User, provided: Mapping[str, Any], users: UserRepositoryPort) -> Optional[ProfileError]:
    if "display_name" in provided:
        dn = provided["display_name"]
        user.display_name = dn.strip() if isinstance(dn, str) and dn.strip() else None

    if "phone" in provided:
        raw_phone = provided["phone"]
        if isinstance(raw_phone, str) and raw_phone.strip():
            try:
                phone = normalize_phone(raw_phone)
            except InvalidPhoneNumberError:
                return 400, "BadRequest", "Invalid phone number"
            owner = users.find_by_phone(phone)
            if owner is not None and owner.id != user.id:
                return 409, "Conflict", "Phone already in use"
            user.phone = phone
        else:
            user.phone = None
    return None
