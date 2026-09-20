"""Root URL configuration."""

from __future__ import annotations

from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from apps.core.health import HealthView, ReadinessView

urlpatterns = [
    # Probes are intentionally outside /api/v1 so they never move.
    path("healthz", HealthView.as_view(), name="healthz"),
    path("readyz", ReadinessView.as_view(), name="readyz"),
    path("admin/", admin.site.urls),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    path("api/v1/", include("apps.core.urls")),
    path("api/v1/", include("apps.accounts.urls")),
    path("api/v1/", include("apps.integrations.urls")),
    path("api/v1/", include("apps.codebase.urls")),
    path("api/v1/", include("apps.requirements.urls")),
    path("api/v1/", include("apps.testing.urls")),
    path("api/v1/", include("apps.bugs.urls")),
    path("api/v1/", include("apps.releases.urls")),
    path("api/v1/", include("apps.ai.urls")),
    path("api/v1/", include("apps.risk.urls")),
]
