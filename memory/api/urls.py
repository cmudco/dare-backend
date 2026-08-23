"""Memory API URL configuration.

The compat surface (items / search / clear) keeps the round-1 frontend
working unchanged; the v2 surface exposes the layered store for round 2.
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from memory.api.views import MemoryViewSet

router = DefaultRouter()
router.register(r"items", MemoryViewSet, basename="memory")

urlpatterns = [
    path("", include(router.urls)),
    # Explicit paths for custom actions that need simpler URLs
    path("search/", MemoryViewSet.as_view({"post": "search"}), name="memory-search"),
    path("clear/", MemoryViewSet.as_view({"delete": "clear"}), name="memory-clear"),
    # v2 — the layered surface
    path(
        "v2/document/",
        MemoryViewSet.as_view({"get": "document", "put": "document"}),
        name="memory-v2-document",
    ),
    path(
        "v2/ledger/",
        MemoryViewSet.as_view({"get": "ledger"}),
        name="memory-v2-ledger",
    ),
    path(
        "v2/hold/",
        MemoryViewSet.as_view({"post": "hold"}),
        name="memory-v2-hold",
    ),
    path(
        "v2/consolidate/",
        MemoryViewSet.as_view({"get": "consolidate", "post": "consolidate"}),
        name="memory-v2-consolidate",
    ),
    path(
        "v2/recall/",
        MemoryViewSet.as_view({"get": "recall"}),
        name="memory-v2-recall",
    ),
    path(
        "v2/export/",
        MemoryViewSet.as_view({"get": "export"}),
        name="memory-v2-export",
    ),
    path(
        "v2/import/",
        MemoryViewSet.as_view({"post": "import_bundle"}),
        name="memory-v2-import",
    ),
    path(
        "v2/import/foreign/",
        MemoryViewSet.as_view({"post": "import_foreign"}),
        name="memory-v2-import-foreign",
    ),
    path(
        "v2/sessions/",
        MemoryViewSet.as_view({"get": "sessions"}),
        name="memory-v2-sessions",
    ),
]
