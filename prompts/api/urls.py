from django.urls import path, include
from rest_framework.routers import DefaultRouter
from prompts.api.views import PromptViewSet
from prompts.constants import APP_NAME

router = DefaultRouter()
router.register(r'prompts', PromptViewSet, basename='prompt')

app_name = APP_NAME

urlpatterns = [
    path('', include(router.urls)),
]