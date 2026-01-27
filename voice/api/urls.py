"""
URL configuration for Voice API.
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import VoiceAgentViewSet, VoiceConversationViewSet

router = DefaultRouter()
router.register(r'agents', VoiceAgentViewSet, basename='voice-agent')
router.register(r'conversations', VoiceConversationViewSet, basename='voice-conversation')

urlpatterns = [
    path('', include(router.urls)),
]
