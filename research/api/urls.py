from django.urls import include, path
from rest_framework.routers import DefaultRouter

# isort: off
from research.api.views import (
    ResearchAgentRunViewSet,
    ResearchHermesRunCallbackView,
    ResearchKnowledgeItemViewSet,
    ResearchMetadataView,
    ResearchProjectViewSet,
    ResearchSoulFileVersionViewSet,
    ResearchSoulFileViewSet,
    ResearchSourceViewSet,
    ResearchStagingItemViewSet,
)

# isort: on

router = DefaultRouter()
router.register("projects", ResearchProjectViewSet, basename="research-project")
router.register("soul-files", ResearchSoulFileViewSet, basename="research-soul-file")
router.register(
    "soul-file-versions",
    ResearchSoulFileVersionViewSet,
    basename="research-soul-file-version",
)
router.register("sources", ResearchSourceViewSet, basename="research-source")
router.register("agent-runs", ResearchAgentRunViewSet, basename="research-agent-run")
router.register(
    "staging-items", ResearchStagingItemViewSet, basename="research-staging-item"
)
router.register(
    "knowledge-items", ResearchKnowledgeItemViewSet, basename="research-knowledge-item"
)

urlpatterns = [
    path("metadata/", ResearchMetadataView.as_view(), name="research-metadata"),
    path(
        "internal/hermes/runs/<uuid:run_id>/callback/",
        ResearchHermesRunCallbackView.as_view(),
        name="research-hermes-run-callback",
    ),
    path("", include(router.urls)),
]
