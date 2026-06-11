"""
URL routing for the Research API.
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from research.api.views import (
    ResearchAgentMemoryView,
    ResearchAgentRunView,
    ResearchArtifactGenerateView,
    ResearchChatView,
    ResearchProjectGraphView,
    ResearchProjectViewSet,
    ResearchScoutView,
    ResearchSoulFileView,
    ResearchStagingItemCriticView,
    ResearchStagingItemReviewView,
)

router = DefaultRouter()
router.register("projects", ResearchProjectViewSet, basename="research-project")

urlpatterns = [
    path(
        "projects/<int:project_id>/chat/",
        ResearchChatView.as_view(),
        name="research-chat",
    ),
    path(
        "projects/<int:project_id>/scout/",
        ResearchScoutView.as_view(),
        name="research-scout",
    ),
    path(
        "projects/<int:project_id>/soul/",
        ResearchSoulFileView.as_view(),
        name="research-soul",
    ),
    path(
        "projects/<int:project_id>/artifact/",
        ResearchArtifactGenerateView.as_view(),
        name="research-artifact-generate",
    ),
    path(
        "projects/<int:project_id>/graph/",
        ResearchProjectGraphView.as_view(),
        name="research-project-graph",
    ),
    path(
        "staging-items/<int:item_id>/review/",
        ResearchStagingItemReviewView.as_view(),
        name="research-staging-review",
    ),
    path(
        "staging-items/<int:item_id>/critic/",
        ResearchStagingItemCriticView.as_view(),
        name="research-staging-critic",
    ),
    path(
        "agent-runs/<int:run_id>/",
        ResearchAgentRunView.as_view(),
        name="research-agent-run",
    ),
    path(
        "agent-memory/",
        ResearchAgentMemoryView.as_view(),
        name="research-agent-memory",
    ),
    path("", include(router.urls)),
]
