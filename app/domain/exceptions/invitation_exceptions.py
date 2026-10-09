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


class InvitationNotFoundError(Exception):
    """No invitation found for the given identifier."""

    pass


class InvalidInvitationTokenError(Exception):
    """Supplied token does not match any invitation or is malformed."""

    pass


class InvitationAccountExistsError(Exception):
    """The invited address already has an account, and the phone offered is not that account's.

    Only the existing account can take the invitation up: with a code sent to its own
    phone, or from a session signed in to it.
    """

    pass


class InvitationWrongAccountError(Exception):
    """A signed-in user tried to accept an invitation sent to another email address."""

    pass
