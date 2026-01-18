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


class AuthorizationError(Exception):
    """Base authorization exception."""
    pass


class InsufficientPermissionsError(AuthorizationError):
    """User lacks required permissions."""
    pass


class RoleNotFoundError(AuthorizationError):
    """Role does not exist."""
    pass
