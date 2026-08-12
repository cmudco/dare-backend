"""
DARE Tools URL configuration.
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from dare_tools.api.views import DareToolExecutionViewSet, DareToolViewSet

app_name = "dare_tools"

router = DefaultRouter()
router.register(r"tools", DareToolViewSet, basename="dare-tools")
router.register(
    r"executions", DareToolExecutionViewSet, basename="dare-tool-executions"
)

urlpatterns = [
    path("api/", include(router.urls)),
]
