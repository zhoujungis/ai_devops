from __future__ import annotations

from typing import Any

from rest_framework.exceptions import NotFound, ValidationError

from apps.core.exceptions import ApplicationError, api_exception_handler


def _error_payload(exc: Exception) -> dict[str, Any]:
    response = api_exception_handler(exc, {"view": None})

    assert response is not None
    payload: dict[str, Any] = response.data["error"]
    return payload


def test_validation_error_exposes_field_details() -> None:
    payload = _error_payload(ValidationError({"amount": ["This field is required."]}))

    assert payload["code"] == "validation_error"
    assert payload["message"] == "This field is required."
    assert payload["details"] == {"amount": ["This field is required."]}


def test_not_found_has_no_details() -> None:
    payload = _error_payload(NotFound())

    assert payload["code"] == "not_found"
    assert payload["details"] == {}


def test_domain_error_code_is_preserved() -> None:
    payload = _error_payload(ApplicationError(detail="Commit not found.", code="commit_not_found"))

    assert payload["code"] == "commit_not_found"
    assert payload["message"] == "Commit not found."


def test_request_id_defaults_to_empty_without_a_request() -> None:
    assert _error_payload(NotFound())["request_id"] == ""


def test_nested_detail_list_is_flattened_into_a_message() -> None:
    """DRF nests detail errors; the envelope must surface text, not a repr of a list."""
    payload = _error_payload(ValidationError({"detail": "Invalid email or password."}))

    assert payload["message"] == "Invalid email or password."


def test_list_of_errors_uses_the_first_entry() -> None:
    payload = _error_payload(ValidationError(["First problem.", "Second problem."]))

    assert payload["message"] == "First problem."
    assert payload["details"] == {"non_field_errors": ["First problem.", "Second problem."]}
