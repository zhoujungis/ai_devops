"""Uniform error envelope for the whole API.

Every failing response has the shape::

    {"error": {"code": ..., "message": ..., "details": ..., "request_id": ...}}

``code`` is stable and safe to branch on in clients; ``message`` is meant for
humans; ``details`` carries field-level errors when the failure is a validation
error; ``request_id`` ties the response to server logs and audit records.
"""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response
from rest_framework.views import exception_handler

from apps.core.middleware import get_request_id

_STATUS_CODES: dict[int, str] = {
    status.HTTP_400_BAD_REQUEST: "validation_error",
    status.HTTP_401_UNAUTHORIZED: "not_authenticated",
    status.HTTP_403_FORBIDDEN: "permission_denied",
    status.HTTP_404_NOT_FOUND: "not_found",
    status.HTTP_405_METHOD_NOT_ALLOWED: "method_not_allowed",
    status.HTTP_409_CONFLICT: "conflict",
    status.HTTP_429_TOO_MANY_REQUESTS: "throttled",
    status.HTTP_500_INTERNAL_SERVER_ERROR: "server_error",
}


class ApplicationError(APIException):
    """Base class for domain errors that need a stable, documented error code."""

    status_code: int = status.HTTP_400_BAD_REQUEST
    default_detail: str = "Application error."
    default_code: str = "application_error"


def _resolve_code(exc: Exception, status_code: int) -> str:
    if isinstance(exc, APIException):
        codes = exc.get_codes()
        if isinstance(codes, str):
            return codes
    return _STATUS_CODES.get(status_code, "error")


def _first_message(payload: dict[str, Any]) -> str:
    for value in payload.values():
        text = _as_text(value)
        if text:
            return text
    return ""


def _as_text(value: Any) -> str:
    """Flatten a DRF error value into a plain string.

    DRF nests errors arbitrarily (``{"detail": [ErrorDetail(...)]}``), so a naive
    ``str()`` would surface the repr of a list instead of the message.
    """
    if isinstance(value, list | tuple):
        return _as_text(value[0]) if value else ""
    if isinstance(value, dict):
        return _first_message(value)
    return str(value)


def _resolve_message_and_details(payload: Any) -> tuple[str, Any]:
    fallback = "Request could not be processed."
    if isinstance(payload, dict):
        if set(payload.keys()) == {"detail"}:
            return _as_text(payload["detail"]) or fallback, {}
        return _first_message(payload) or fallback, payload
    if isinstance(payload, list | tuple):
        return (_as_text(payload) or fallback), {"non_field_errors": list(payload)}
    return str(payload), {}


def api_exception_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    """Wrap DRF's default handler so every error carries a code and a request id.

    Returns ``None`` for exceptions DRF does not handle, letting Django produce a
    500 as usual.
    """
    if isinstance(exc, DjangoValidationError):
        exc = ValidationError(
            detail=getattr(exc, "message_dict", None) or list(exc.messages),
        )

    response = exception_handler(exc, context)
    if response is None:
        return None

    message, details = _resolve_message_and_details(response.data)

    response.data = {
        "error": {
            "code": _resolve_code(exc, response.status_code),
            "message": message,
            "details": details,
            "request_id": get_request_id(),
        }
    }
    return response
