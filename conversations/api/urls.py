from django.urls import path, include
from rest_framework.routers import DefaultRouter
from conversations.api.views import ConversationViewSet, LLMViewSet, MessageViewSet, ArtifactStatusView, ArtifactContentView
from conversations.constants import APP_NAME


router = DefaultRouter()
router.register(r'conversations', ConversationViewSet, basename='conversation')
router.register(r'messages', MessageViewSet, basename='message')
router.register(r'llms', LLMViewSet, basename='llm')

app_name = APP_NAME

urlpatterns = [
    path('', include(router.urls)),
    path('conversations/<str:conversation_id>/clone/', ConversationViewSet.as_view({'post': 'clone_conversation'}), name='conversation-clone'),
    path('artifacts/<int:artifact_id>/status/', ArtifactStatusView.as_view(), name='artifact-status'),
    path('artifacts/<int:artifact_id>/content/', ArtifactContentView.as_view(), name='artifact-content'),
]