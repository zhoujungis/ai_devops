"""Liveness and readiness probes."""

from __future__ import annotations

import logging
from typing import Any

from django.core.cache import cache
from django.db import connection
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

logger = logging.getLogger(__name__)

_CACHE_SENTINEL = "readiness-probe"


class HealthView(APIView):
    """Liveness probe: the process is running. Never touches dependencies."""

    authentication_classes = ()
    permission_classes = (AllowAny,)

    @extend_schema(exclude=True)
    def get(self, request: Request) -> Response:
        return Response({"status": "ok"})


class ReadinessView(APIView):
    """Readiness probe: PostgreSQL (with pgvector) and Redis are reachable.

    pgvector is part of the contract, not an optional extra: correlation and
    similarity search cannot work without it, so a missing extension makes the
    instance not ready.
    """

    authentication_classes = ()
    permission_classes = (AllowAny,)

    @extend_schema(exclude=True)
    def get(self, request: Request) -> Response:
        checks = {"database": _check_database(), "cache": _check_cache()}
        healthy = all(check["ok"] for check in checks.values())
        return Response(
            {"status": "ok" if healthy else "degraded", "checks": checks},
            status=200 if healthy else 503,
        )


def _check_database() -> dict[str, Any]:
    # A probe reports failure instead of raising, whatever goes wrong. The detail is
    # logged, not returned: this view is unauthenticated, and a connection error
    # names the host, port and user.
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
            cursor.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            row = cursor.fetchone()
    except Exception:
        logger.exception("Readiness check: database is unreachable")
        return {"ok": False, "pgvector": None, "error": "database unreachable"}

    pgvector_version = row[0] if row else None
    return {
        "ok": pgvector_version is not None,
        "pgvector": pgvector_version,
        "error": None if pgvector_version else "pgvector extension is not installed",
    }


def _check_cache() -> dict[str, Any]:
    try:
        # `touch` proves the cache is reachable without rewriting the value on every
        # probe; the sentinel is created once and merely renewed afterwards.
        if not cache.touch(_CACHE_SENTINEL, 60):
            cache.set(_CACHE_SENTINEL, "1", 60)
        value = cache.get(_CACHE_SENTINEL)
    except Exception:
        logger.exception("Readiness check: cache is unreachable")
        return {"ok": False, "error": "cache unreachable"}

    return {"ok": value == "1", "error": None if value == "1" else "cache round-trip failed"}
