"""Authentication and authorization exceptions."""


class AuthenticationError(Exception):
    """Base authentication exception."""

    pass


class InvalidCredentialsError(AuthenticationError):
    """Invalid email or password."""

    pass


class UserNotFoundError(AuthenticationError):
    """User does not exist."""

    pass


class UserInactiveError(AuthenticationError):
    """User account is deactivated."""

    pass


class OtpInvalidError(AuthenticationError):
    """SMS code is wrong, expired, consumed, or locked after too many attempts."""

    pass


#: Who an SMS-code 429 is about: the phone number, the caller's network (a per-address cap
#: shared by everyone on the same Wi-Fi), or the platform's SMS service (its budget ran out).
OTP_SCOPE_NUMBER = "number"
OTP_SCOPE_NETWORK = "network"
OTP_SCOPE_SERVICE = "service"


class OtpThrottledError(AuthenticationError):
    """Too many SMS codes requested for this phone recently.

    ``hourly_limit`` tells the per-hour cap apart from the short gap between two
    codes; ``retry_after_seconds`` is how long the caller has to wait either way.
    ``scope`` says whose limit it is (see ``OTP_SCOPE_*``), so the message does not blame a
    number for a network or service limit.
    """

    def __init__(
        self,
        message: str,
        *,
        retry_after_seconds: int = 60,
        hourly_limit: bool = False,
        scope: str = OTP_SCOPE_NUMBER,
    ) -> None:
        super().__init__(message)
        self.retry_after_seconds = max(1, int(retry_after_seconds))
        self.hourly_limit = hourly_limit
        self.scope = scope


class PhoneAlreadyRegisteredError(AuthenticationError):
    """Sign-up with a phone that already belongs to an account."""

    pass


class PhoneUnchangedError(AuthenticationError):
    """Phone-number change to the number the account already signs in with."""

    pass


class AuthorizationError(Exception):
    """Base authorization exception."""

    pass


class InsufficientPermissionsError(AuthorizationError):
    """User lacks required permissions."""

    pass


class RoleNotFoundError(AuthorizationError):
    """Role does not exist."""

    pass
