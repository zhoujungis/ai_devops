"""Django settings shared by every environment.

Import order matters: this module loads the repository-root ``.env`` before
reading any value from the environment, so management commands, Celery workers
and the WSGI/ASGI entrypoints all see the same configuration.
"""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent  # <repo>/backend
REPO_ROOT = BASE_DIR.parent  # <repo>

load_dotenv(REPO_ROOT / ".env")


def env(name: str, default: str = "") -> str:
    """Return an environment variable as a string."""
    return os.environ.get(name, default)


def env_bool(name: str, default: bool = False) -> bool:
    """Return an environment variable as a bool (``1/true/yes/on`` are truthy)."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    """Return an environment variable as an int, with a clear error when malformed."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"Environment variable {name!r} must be an integer, got {raw!r}") from exc


def env_list(name: str, default: list[str] | None = None) -> list[str]:
    """Return a comma-separated environment variable as a list of strings."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return list(default or [])
    return [item.strip() for item in raw.split(",") if item.strip()]


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------
SECRET_KEY = env("DJANGO_SECRET_KEY", "insecure-dev-key-override-me")
DEBUG = env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", ["localhost", "127.0.0.1"])

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    "django_filters",
    "drf_spectacular",
]

LOCAL_APPS = [
    "apps.core",
    "apps.accounts",
    "apps.integrations",
    "apps.codebase",
    "apps.requirements",
    "apps.testing",
    "apps.bugs",
    "apps.releases",
    "apps.ai",
    "apps.risk",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    # Outermost: every response (including error responses) must carry a request id.
    "apps.core.middleware.RequestIDMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# ---------------------------------------------------------------------------
# Database (PostgreSQL + pgvector)
# ---------------------------------------------------------------------------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("DB_NAME", "ai_devops"),
        "USER": env("DB_USER", "copilot"),
        "PASSWORD": env("DB_PASSWORD", ""),
        "HOST": env("DB_HOST", "127.0.0.1"),
        "PORT": env("DB_PORT", "5432"),
        "CONN_MAX_AGE": env_int("DB_CONN_MAX_AGE", 60),
        "OPTIONS": {"connect_timeout": 10},
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Users are identified by email; everything else is reached through a membership.
AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ---------------------------------------------------------------------------
# Cache / Redis
# ---------------------------------------------------------------------------
REDIS_URL = env("REDIS_URL", "redis://127.0.0.1:6379/0")

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
    }
}

# ---------------------------------------------------------------------------
# Celery
# ---------------------------------------------------------------------------
CELERY_BROKER_URL = env("CELERY_BROKER_URL", "redis://127.0.0.1:6379/1")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", "redis://127.0.0.1:6379/2")
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = "Asia/Shanghai"
CELERY_ENABLE_UTC = True
# AI analysis calls are slow and must not be duplicated when a worker dies.
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 1800
CELERY_TASK_SOFT_TIME_LIMIT = 1740

CELERY_TASK_DEFAULT_QUEUE = "default"
CELERY_TASK_ROUTES = {
    "apps.ai.tasks.*": {"queue": "ai"},
    "apps.integrations.tasks.*": {"queue": "sync"},
}
CELERY_BEAT_SCHEDULE: dict[str, dict[str, object]] = {}

# ---------------------------------------------------------------------------
# REST framework
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.DefaultPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ),
    "EXCEPTION_HANDLER": "apps.core.exceptions.api_exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
    "UNAUTHENTICATED_USER": None,
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env_int("JWT_ACCESS_TOKEN_LIFETIME_MINUTES", 15)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env_int("JWT_REFRESH_TOKEN_LIFETIME_DAYS", 7)),
    "SIGNING_KEY": SECRET_KEY,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_CLAIM": "user_id",
    "UPDATE_LAST_LOGIN": True,
}

SPECTACULAR_SETTINGS = {
    "TITLE": "AI DevOps / QA Copilot API",
    "DESCRIPTION": (
        "Requirement -> Code -> Test -> Bug -> Release correlation, risk analysis "
        "and AI-assisted investigation."
    ),
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SCHEMA_PATH_PREFIX": "/api/v1",
    # Several apps define their own `source` / `kind` choice sets; naming them
    # explicitly keeps generated clients readable instead of `SourceAa3Enum`.
    "ENUM_NAME_OVERRIDES": {
        "LinkSourceEnum": "apps.core.models.LinkSource.choices",
        "RequirementSourceEnum": "apps.requirements.models.RequirementSource.choices",
        "ModuleKindEnum": "apps.codebase.models.ModuleKind.choices",
        "TestSuiteKindEnum": "apps.testing.models.TestSuiteKind.choices",
    },
}

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
CORS_ALLOWED_ORIGINS = env_list(
    "CORS_ALLOWED_ORIGINS", ["http://localhost:5173", "http://127.0.0.1:5173"]
)
CORS_ALLOW_CREDENTIALS = True

# ---------------------------------------------------------------------------
# I18N / TZ / static
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = env("DJANGO_TIME_ZONE", "Asia/Shanghai")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# ---------------------------------------------------------------------------
# Application settings
# ---------------------------------------------------------------------------
# Fernet key used by EncryptedTextField (git tokens, AI provider keys).
FIELD_ENCRYPTION_KEY = env("FIELD_ENCRYPTION_KEY")
# pgvector column dimension: must match the configured embedding model.
EMBEDDING_DIM = env_int("EMBEDDING_DIM", 1536)

# ---------------------------------------------------------------------------
# Git sync
# ---------------------------------------------------------------------------
# Diffs larger than this are stored truncated; the provider omits its own diff for
# binary files, which is recorded as `has_patch=False`.
GIT_PATCH_MAX_BYTES = env_int("GIT_PATCH_MAX_BYTES", 200 * 1024)
# Upper bound on commits ingested per sync run, so a first backfill of a large
# repository cannot monopolise a worker or exhaust the API rate limit.
GIT_SYNC_MAX_COMMITS = env_int("GIT_SYNC_MAX_COMMITS", 200)
# Webhook deliveries are the primary trigger; this reconciles anything missed.
GIT_SYNC_INTERVAL_MINUTES = env_int("GIT_SYNC_INTERVAL_MINUTES", 60)

# ---------------------------------------------------------------------------
# AI
# ---------------------------------------------------------------------------
# Runs store the rendered prompt so any answer can be explained after the fact;
# this caps it so one very large diff cannot bloat the table.
AI_TRACE_TEXT_MAX_BYTES = env_int("AI_TRACE_TEXT_MAX_BYTES", 64 * 1024)
# Upper bound on model<->tool round trips per analysis, so a looping model cannot
# burn budget indefinitely.
AI_ANALYSIS_MAX_TOOL_ITERATIONS = env_int("AI_ANALYSIS_MAX_TOOL_ITERATIONS", 6)

LOGGING: dict[str, Any] = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": env("DJANGO_LOG_LEVEL", "INFO"),
    },
    "loggers": {
        "django.db.backends": {"level": "WARNING", "handlers": ["console"], "propagate": False},
    },
}
