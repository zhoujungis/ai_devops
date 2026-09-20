"""AI API routes, mounted under ``/api/v1/``."""

from __future__ import annotations

from django.urls import URLPattern, path

from apps.ai import views

analysis_list = views.AnalysisJobViewSet.as_view({"get": "list", "post": "create"})
analysis_detail = views.AnalysisJobViewSet.as_view({"get": "retrieve"})
analysis_agents = views.AnalysisJobViewSet.as_view({"get": "agents"})

finding_list = views.AIFindingViewSet.as_view({"get": "list"})
finding_detail = views.AIFindingViewSet.as_view({"get": "retrieve"})

recommendation_list = views.AIRecommendationViewSet.as_view({"get": "list"})
recommendation_detail = views.AIRecommendationViewSet.as_view({"get": "retrieve"})
recommendation_confirm = views.AIRecommendationViewSet.as_view({"post": "confirm"})
recommendation_reject = views.AIRecommendationViewSet.as_view({"post": "reject"})

provider_list = views.AIProviderConfigViewSet.as_view({"get": "list", "post": "create"})
provider_detail = views.AIProviderConfigViewSet.as_view(
    {"get": "retrieve", "patch": "partial_update", "delete": "destroy"}
)

_BASE = "orgs/<uuid:org_pk>/projects/<uuid:project_pk>"

urlpatterns: list[URLPattern] = [
    # Analyses: POST returns 202 with a job id; the client polls the job.
    path(f"{_BASE}/ai/analyses", analysis_list, name="ai-analysis-list"),
    path(f"{_BASE}/ai/analyses/agents", analysis_agents, name="ai-analysis-agents"),
    path(f"{_BASE}/ai/jobs/<uuid:job_pk>", analysis_detail, name="ai-analysis-detail"),
    path(f"{_BASE}/ai/findings", finding_list, name="ai-finding-list"),
    path(f"{_BASE}/ai/findings/<uuid:finding_pk>", finding_detail, name="ai-finding-detail"),
    path(
        f"{_BASE}/ai/recommendations",
        recommendation_list,
        name="ai-recommendation-list",
    ),
    path(
        f"{_BASE}/ai/recommendations/<uuid:recommendation_pk>",
        recommendation_detail,
        name="ai-recommendation-detail",
    ),
    path(
        f"{_BASE}/ai/recommendations/<uuid:recommendation_pk>/confirm",
        recommendation_confirm,
        name="ai-recommendation-confirm",
    ),
    path(
        f"{_BASE}/ai/recommendations/<uuid:recommendation_pk>/reject",
        recommendation_reject,
        name="ai-recommendation-reject",
    ),
    # Model credentials live at the organization level, like git connections.
    path("orgs/<uuid:org_pk>/ai-providers", provider_list, name="ai-provider-list"),
    path(
        "orgs/<uuid:org_pk>/ai-providers/<uuid:provider_pk>",
        provider_detail,
        name="ai-provider-detail",
    ),
]
