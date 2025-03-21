from django.urls import path, include
from rest_framework.routers import DefaultRouter
from workflows.api.views import StepViewSet, WorkflowViewSet
from workflows.constants import APP_NAME

router = DefaultRouter()
router.register(r'workflows', WorkflowViewSet, basename='workflow')
router.register(r'steps', StepViewSet, basename='workflow-step')

app_name = APP_NAME

urlpatterns = [
    path('', include(router.urls)),
]