"""Test settings: fast hashing, in-memory cache, eager Celery."""

from __future__ import annotations

from config.settings.base import *
from config.settings.base import env

DEBUG = False
ALLOWED_HOSTS = ["*"]

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "test-cache",
    }
}

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# Run tasks inline so tests never need a live worker.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_RESULT_BACKEND = "cache+memory://"
CELERY_BROKER_URL = "memory://"

# Fixed key so EncryptedTextField is exercisable in tests without a real .env.
FIELD_ENCRYPTION_KEY = env("FIELD_ENCRYPTION_KEY", "hSn56swUzys_6F0fDXBo2VRCbywjt2Zg4Jocx2RaJSc=")

LOGGING["root"]["level"] = "WARNING"
