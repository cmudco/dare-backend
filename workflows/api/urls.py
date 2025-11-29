from django.urls import path, include
from rest_framework.routers import DefaultRouter
from workflows.api.views import WorkflowRunViewSet, WorkflowViewSet, WorkflowRunV2ViewSet
from workflows.constants import APP_NAME

# V1 API Router (legacy)
router = DefaultRouter()
router.register(r'workflows', WorkflowViewSet, basename='workflow')
router.register(r'workflow-runs', WorkflowRunViewSet, basename='workflow-run')

# V2 API Router (graph-based nodeStates)
router_v2 = DefaultRouter()
router_v2.register(r'runs', WorkflowRunV2ViewSet, basename='workflow-run-v2')

app_name = APP_NAME

urlpatterns = [
    # V1 API (backward compatible)
    path('', include(router.urls)),
]