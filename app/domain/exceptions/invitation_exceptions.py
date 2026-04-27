"""Invitation domain exceptions."""


class InvitationNotUsableError(Exception):
    """Base: invitation cannot be used (expired, revoked, or already accepted)."""

    pass


class InvitationExpiredError(InvitationNotUsableError):
    """Invitation has passed its expiry date."""

    pass


class InvitationRevokedError(InvitationNotUsableError):
    """Invitation was explicitly revoked by the sender."""

    pass


class InvitationAlreadyAcceptedError(InvitationNotUsableError):
    """Invitation was already accepted and cannot be used again."""

    pass


class DuplicatePendingInvitationError(Exception):
    """A pending invitation for this email + project already exists."""

    pass


class InvitationNotFoundError(Exception):
    """No invitation found for the given identifier."""

    pass


class InvalidInvitationTokenError(Exception):
    """Supplied token does not match any invitation or is malformed."""

    pass


class RoleNotAllowedError(Exception):
    """The requested role is not permitted for invitation in this context."""

    pass
