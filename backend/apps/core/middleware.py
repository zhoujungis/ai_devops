"""Request correlation middleware."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from contextvars import ContextVar

from django.http import HttpRequest, HttpResponse

REQUEST_ID_HEADER = "X-Request-ID"

_request_id: ContextVar[str] = ContextVar("request_id", default="")


def get_request_id() -> str:
    """Return the correlation id of the work currently being executed."""
    return _request_id.get()


class RequestIDMiddleware:
    """Attach a correlation id to every request and echo it back on the response.

    An inbound ``X-Request-ID`` is reused when present, so a single id can follow a
    request across the gateway, the API, the error envelope and server logs.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        incoming = request.headers.get(REQUEST_ID_HEADER, "").strip()
        request_id = incoming or uuid.uuid4().hex
        token = _request_id.set(request_id)
        try:
            response = self.get_response(request)
        finally:
            _request_id.reset(token)
        response[REQUEST_ID_HEADER] = request_id
        return response
