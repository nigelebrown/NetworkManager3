class ArubaError(Exception):
    """Base class for Aruba API problems."""


class ArubaConnectionError(ArubaError):
    """Network-level failure: timeout, refused, DNS, TLS verification failed."""


class ArubaAuthError(ArubaError):
    """Login rejected or session invalid and re-login failed."""


class ArubaRestDisabledError(ArubaAuthError):
    """The controller answered on the HTTPS port but not like a REST API: it is probably disabled (the default)."""


class ArubaSshError(ArubaError):
    """SSH problem while enabling the REST API (host key mismatch, rejected command, timeout)."""


class ArubaCommandError(ArubaError):
    """The VC answered but reported a non-success status for the command."""


class ArubaUnsupportedError(ArubaError):
    """Command not allowed by our read-only allowlist, or not supported by this firmware."""
