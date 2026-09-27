"""Apply ``display_name`` / ``phone`` edits to a user the same way everywhere.

Shared by the platform-ops route (``PATCH /admin/users/<id>``) and the self-service route
(``PATCH /auth/me``): the phone is the sign-in identity (phone + SMS code is the only way in),
so it is normalised to E.164 and must stay unique across users; the display name is trimmed and
cleared when empty. Returns ``None`` on success or ``(status, error, message)`` for the route to
answer.

Self-service callers pass ``allow_phone_change=False``: nothing proves the caller holds a new
number, and clearing or mistyping it locks the account out for good, so the profile may only
re-send the number it already has. A user moves to a new number through the verified flow
(``POST /auth/me/phone/request-code`` then ``/auth/me/phone/confirm``, SMS code to the new number).
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Tuple

from app.application.ports.user_repository import UserRepositoryPort
from app.domain.entities.user import User
from app.domain.value_objects.phone_number import InvalidPhoneNumberError, normalize_phone

ProfileError = Tuple[int, str, str]

PHONE_CHANGE_REFUSED: ProfileError = (
    400,
    "PhoneChangeNotAllowed",
    "Your phone number is how you sign in, so it cannot be removed or edited here. "
    'Use "Change number" to move your account to a new number with a code sent to it.',
)


def apply_profile_fields(
    user: User,
    provided: Mapping[str, Any],
    users: UserRepositoryPort,
    *,
    allow_phone_change: bool = True,
) -> Optional[ProfileError]:
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
            if not allow_phone_change:
                return None if phone == user.phone else PHONE_CHANGE_REFUSED
            owner = users.find_by_phone(phone)
            if owner is not None and owner.id != user.id:
                return 409, "Conflict", "Phone already in use"
            user.phone = phone
        elif not allow_phone_change:
            if user.phone is not None:
                return PHONE_CHANGE_REFUSED
        else:
            user.phone = None
    return None
