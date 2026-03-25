from django.urls import path, include
from rest_framework.routers import DefaultRouter

from sharing.api.views import SharedItemViewSet

router = DefaultRouter()
router.register(r"sharing", SharedItemViewSet, basename="sharing")

app_name = "sharing_api"

urlpatterns = [
    path("", include(router.urls)),
]
