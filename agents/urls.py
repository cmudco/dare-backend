from django.urls import path, include
from agents.api.views import AgentViewSet
from agents.constants import APP_NAME
from .api.urls import router

app_name = APP_NAME

urlpatterns = [
    path('api/', include(router.urls)),
    path('api/agents/<int:pk>/clone/', AgentViewSet.as_view({'post': 'clone_agent'}), name='agent-clone'),
    path('api/agents/<int:pk>/simple-update/', AgentViewSet.as_view({'patch': 'simple_update'}), name='agent-simple-update'),
]
