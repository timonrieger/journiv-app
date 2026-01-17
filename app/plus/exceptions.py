"""
Exception classes for Journiv Plus integration.

Provides structured exceptions for Plus server communication,
license operations, and authentication failures.
"""


class PlusServerError(Exception):
    """Base exception for all Plus server related errors."""
    pass


class PlusIdentityRevokedError(PlusServerError):
    """
    Raised when instance identity has been revoked by Plus server.

    This occurs when:
    - Instance has been blacklisted/blocked on Plus server
    - install_id has been invalidated
    - Re-registration fails after 401

    User action required: Contact support or create new installation.
    """
    pass


class PlusRegistrationError(PlusServerError):
    """
    Raised when instance registration with Plus server fails.

    This occurs when:
    - Network timeout during registration
    - Rate limit exceeded
    - Server validation error
    - Server unavailable

    For version checks: Will retry on next scheduled run
    For license operations: Blocks the operation
    """
    pass


class PlusRateLimitError(PlusServerError):
    """
    Raised when Plus server rate limit is exceeded.

    Includes retry_after information for backoff.
    """

    def __init__(self, message: str, retry_after: int):
        """
        Initialize rate limit error.

        Args:
            message: Error message
            retry_after: Seconds until rate limit resets
        """
        super().__init__(message)
        self.retry_after = retry_after


class PlusAuthenticationError(PlusServerError):
    """
    Raised when authentication with Plus server fails.

    This occurs when:
    - Signature verification fails
    - Timestamp out of range
    - Headers malformed or missing

    Triggers automatic re-registration flow.
    """
    pass


class PlusNetworkError(PlusServerError):
    """
    Raised when network communication with Plus server fails.

    This occurs when:
    - Connection timeout
    - DNS resolution failure
    - TLS/SSL errors
    - Server unreachable

    For version checks: Best effort retry
    For license operations: Fail with error message
    """
    pass


class PlusHTTPError(PlusServerError):
    """
    Raised when Plus server returns a non-OK HTTP response.

    Includes status_code for callers that need to branch on specific errors.
    """

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
