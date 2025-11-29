from django.urls import include, path
from workflows.constants import APP_NAME
from workflows.api.views import WorkflowViewSet
from .api.urls import router, router_v2


app_name = APP_NAME

urlpatterns = [
    # V1 API (legacy)
    path("api/", include((router.urls, app_name), namespace="workflows-api")),
    path('api/workflows/<int:pk>/clone/', WorkflowViewSet.as_view({'post': 'clone_workflow'}), name='workflow-clone'),

    # V2 API (graph-based nodeStates) - router_v2 has 'runs' endpoint
    path("api/workflows/v2/", include((router_v2.urls, app_name), namespace="workflows-api-v2")),
]