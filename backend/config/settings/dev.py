"""Local development settings."""

from __future__ import annotations

from config.settings.base import *
from config.settings.base import env_bool

DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = ["*"]
CORS_ALLOW_ALL_ORIGINS = True
