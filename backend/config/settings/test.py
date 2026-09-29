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

# Functional tests are not about rate limits, and the shared locmem cache would let
# counters leak between cases. Throttling itself is exercised explicitly, with a
# deliberately tiny rate, in apps/accounts/tests/test_throttling.py.
REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    "DEFAULT_THROTTLE_RATES": {
        "login": "100000/min",
        "register": "100000/min",
        "refresh": "100000/min",
        "webhook": "100000/min",
    },
}

# Fixed key so EncryptedTextField is exercisable in tests without a real .env.
FIELD_ENCRYPTION_KEY = env("FIELD_ENCRYPTION_KEY", "hSn56swUzys_6F0fDXBo2VRCbywjt2Zg4Jocx2RaJSc=")

LOGGING["root"]["level"] = "WARNING"
