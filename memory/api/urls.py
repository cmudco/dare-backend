"""
Memory API URL Configuration
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from memory.api.views import MemoryViewSet

router = DefaultRouter()
router.register(r"items", MemoryViewSet, basename="memory")

urlpatterns = [
    path("", include(router.urls)),
    # Explicit paths for custom actions that need simpler URLs
    path("search/", MemoryViewSet.as_view({"post": "search"}), name="memory-search"),
    path(
        "import/",
        MemoryViewSet.as_view({"post": "import_memories"}),
        name="memory-import",
    ),
    path("clear/", MemoryViewSet.as_view({"delete": "clear"}), name="memory-clear"),
    path("seed/", MemoryViewSet.as_view({"post": "seed"}), name="memory-seed"),
]
