from __future__ import annotations

from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from apps.core.pagination import DefaultPagination


def _request(query: str) -> Request:
    return Request(APIRequestFactory().get(f"/?{query}"))


def test_page_size_defaults_to_20() -> None:
    page = DefaultPagination().paginate_queryset(list(range(50)), _request(""))

    assert page is not None
    assert len(page) == 20


def test_client_can_shrink_the_page() -> None:
    page = DefaultPagination().paginate_queryset(list(range(50)), _request("page_size=5"))

    assert page is not None
    assert len(page) == 5


def test_page_size_is_capped() -> None:
    page = DefaultPagination().paginate_queryset(list(range(500)), _request("page_size=1000"))

    assert page is not None
    assert len(page) == 100
