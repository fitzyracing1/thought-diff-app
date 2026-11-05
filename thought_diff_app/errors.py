"""User-facing errors for the local app."""


class SessionError(Exception):
    """Configuration, path, or git-ref problem the user can fix."""


class GitRefError(SessionError):
    """The frozen original could not be read from git."""
