from django.urls import include, path
from workflows.constants import APP_NAME
from workflows.api.views import WorkflowViewSet
from .api.urls import router


app_name = APP_NAME

urlpatterns = [
    path("api/", include((router.urls, app_name), namespace="workflows-api")),
    path('api/workflows/<int:pk>/clone/', WorkflowViewSet.as_view({'post': 'clone_workflow'}), name='workflow-clone'),
]
