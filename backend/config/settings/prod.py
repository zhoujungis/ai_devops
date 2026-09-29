"""Production settings.

Every security-relevant value comes from the environment; nothing here may
silently fall back to a development default.
"""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured

from config.settings.base import *
from config.settings.base import env, env_list

DEBUG = False

if SECRET_KEY == "insecure-dev-key-override-me":
    raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set in production.")

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS")
if not ALLOWED_HOSTS:
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS must be set in production.")

if not env("FIELD_ENCRYPTION_KEY"):
    raise ImproperlyConfigured(
        "FIELD_ENCRYPTION_KEY must be set in production: git tokens and AI provider "
        "keys are encrypted at rest with it."
    )

SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# The schema and the Swagger UI describe every endpoint, parameter and enum, and they
# are anonymous by default. Development keeps them open — `docs/local-setup-windows.md`
# points at /api/docs/ — but a production instance does not hand its whole API surface
# to anyone who guesses the path.
SPECTACULAR_SETTINGS = {
    **SPECTACULAR_SETTINGS,
    "SERVE_PERMISSIONS": ["rest_framework.permissions.IsAdminUser"],
}
