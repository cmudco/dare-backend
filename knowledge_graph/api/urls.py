from rest_framework.routers import DefaultRouter

from knowledge_graph.api.views import KnowledgeGraphViewSet

router = DefaultRouter()
router.register("knowledge-graphs", KnowledgeGraphViewSet, basename="knowledge-graph")

urlpatterns = router.urls
