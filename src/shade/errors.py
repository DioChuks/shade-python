"""
Shade SDK exceptions.
"""
from __future__ import annotations

import json
from typing import Any, Optional

INVALID_REQUEST_STATUS_CODES = (400, 422)

# Sentinel distinguishing "field_errors not supplied" (parse it from the body)
# from an explicit ``field_errors=None`` passed by the response funnel.
_UNSET = object()


class ShadeError(Exception):
    """Base exception for all Shade SDK errors."""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        response_body: Optional[str] = None,
    ) -> None:
        self.message = message
        self.status_code = status_code
        self.response_body = response_body
        super().__init__(message)

    def __str__(self) -> str:
        if self.status_code is None:
            return self.message
        return f"{self.message} (status code: {self.status_code})"


class HTTPError(ShadeError):
    """Raised for non-2xx responses that are not handled by a more specific error."""

    def __init__(
        self,
        message: str,
        status_code: int,
        response_body: Optional[str] = None,
    ) -> None:
        super().__init__(message, status_code=status_code, response_body=response_body)


class RateLimitError(HTTPError):
    """
    Raised when the API returns HTTP 429 Too Many Requests and either:
    - auto-retry is disabled, or
    - ``max_retries`` has been exhausted.

    Attributes
    ----------
    retry_after : int | None
        Seconds to wait before the next attempt, parsed from the
        ``Retry-After`` response header.  ``None`` if the header was absent.
    """

    def __init__(
        self,
        message: str,
        retry_after: Optional[int] = None,
        status_code: int = 429,
        response_body: Optional[str] = None,
    ) -> None:
        super().__init__(message, status_code=status_code, response_body=response_body)
        self.retry_after = retry_after

    def __str__(self) -> str:  # pragma: no cover
        base = super().__str__()
        if self.retry_after is not None:
            return f"{base} (retry after {self.retry_after}s)"
        return base


class AuthenticationError(ShadeError):
    """Raised when authentication fails or credentials are invalid."""


class InvalidRequestError(ShadeError):
    """Raised when a request is malformed or rejected by validation (HTTP 400/422).

    Attributes:
        param: The offending parameter named by the API, if any.
        field_errors: Field-level validation errors. When supplied explicitly by
            the SDK's response funnel it reflects exactly what the body provided
            (a dict, a list, or ``None`` when absent). When the error is built
            directly from a response body, it is parsed into a dict (``{}`` when
            absent).
    """

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        response_body: Optional[str] = None,
        param: Optional[str] = None,
        field_errors: Any = _UNSET,
    ) -> None:
        super().__init__(message, status_code, response_body)
        parsed = _parse_error_response(response_body)
        self.param: Optional[str] = param if param is not None else parsed.get("param")
        self.field_errors: Any = (
            parsed.get("field_errors", {}) if field_errors is _UNSET else field_errors
        )

    def __str__(self) -> str:
        message = self.message
        if self.param:
            message = f"{message} (param: {self.param})"
        if self.status_code is None:
            return message
        return f"{message} (status code: {self.status_code})"

    @classmethod
    def from_response(
        cls,
        status_code: int,
        response_body: Optional[str] = None,
    ) -> "InvalidRequestError":
        """Construct from a raw 400/422 API response body."""
        parsed = _parse_error_response(response_body)
        message = parsed.get("message") or "Invalid request"
        return cls(
            message,
            status_code=status_code,
            response_body=response_body,
            param=parsed.get("param"),
            field_errors=parsed.get("field_errors", {}),
        )


class NotFoundError(ShadeError):
    """Raised on HTTP 404 responses.

    Attributes:
        resource_type: Kind of resource that was not found (e.g. "payment", "invoice").
        resource_id:   ID of the missing resource.
    """

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        response_body: Optional[str] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
    ) -> None:
        super().__init__(message, status_code, response_body)
        parsed = _parse_body(response_body)
        self.resource_type: Optional[str] = resource_type or parsed.get("resource_type")
        self.resource_id: Optional[str] = resource_id or parsed.get("resource_id")

    @classmethod
    def from_response(
        cls,
        message: str,
        response_body: Optional[str] = None,
    ) -> "NotFoundError":
        """Construct from a raw 404 response body."""
        return cls(message, status_code=404, response_body=response_body)


class NetworkError(ShadeError):
    """Raised when the SDK cannot complete a network request."""


def raise_for_invalid_request(
    status_code: int,
    response_body: Optional[str] = None,
) -> None:
    """Raise InvalidRequestError when the API returns 400 or 422."""
    if status_code in INVALID_REQUEST_STATUS_CODES:
        raise InvalidRequestError.from_response(status_code, response_body)


def _parse_body(response_body: Optional[str]) -> dict[str, Any]:
    if not response_body:
        return {}
    try:
        data = json.loads(response_body)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, ValueError):
        return {}


def _parse_error_response(response_body: Optional[str]) -> dict[str, Any]:
    data = _parse_body(response_body)
    error = data.get("error", {})
    if not isinstance(error, dict):
        error = {}

    field_errors = error.get("field_errors")
    if not isinstance(field_errors, dict):
        field_errors = data.get("field_errors")
    if not isinstance(field_errors, dict):
        field_errors = {}

    message = error.get("message")
    if not message:
        message = data.get("message")

    return {
        "message": message,
        "param": error.get("param"),
        "field_errors": field_errors,
    }
