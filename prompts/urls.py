from django.urls import path, include

from prompts.api.views import PromptViewSet
from prompts.constants import APP_NAME
from .api.urls import router

app_name = APP_NAME

urlpatterns = [
    path('api/', include(router.urls)),
    path('api/prompts/<int:pk>/clone/', PromptViewSet.as_view({'post': 'clone_prompt'}), name='prompt-clone'),
    path('api/prompts/<int:pk>/simple-update/', PromptViewSet.as_view({'patch': 'simple_update'}), name='prompt-simple-update'),
    path('api/prompts/<int:pk>/publish/', PromptViewSet.as_view({'post': 'publish_prompt'}), name='prompt-publish'),
    path('api/prompts/<int:pk>/unpublish/', PromptViewSet.as_view({'post': 'unpublish_prompt'}), name='prompt-unpublish'),
]