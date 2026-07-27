from django.urls import include, path
from rest_framework.routers import DefaultRouter

from libraries.api.views import SharedLibraryViewSet

router = DefaultRouter()
router.register(r"libraries", SharedLibraryViewSet, basename="libraries")

app_name = "libraries_api"

urlpatterns = [
    path("", include(router.urls)),
]
