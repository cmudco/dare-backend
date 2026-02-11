from django.urls import path, include
from rest_framework.routers import DefaultRouter

from prompts.api.views import PromptViewSet, PublishedPromptViewSet

router = DefaultRouter()
router.register(r'prompts', PromptViewSet, basename='prompt')
router.register(r'library', PublishedPromptViewSet, basename='published-prompt')

urlpatterns = [
    path('', include(router.urls)),
]