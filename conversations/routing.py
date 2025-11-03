from django.urls import re_path
from .consumers import ChatConsumer, PublicBotConsumer

websocket_urlpatterns = [
    # Authenticated WebSocket
    re_path(r'ws/conversations/(?P<conversation_id>\w+)/$', ChatConsumer.as_asgi()),

    # Public bot WebSocket (no auth required)
    re_path(r'ws/public/conversations/(?P<conversation_id>\w+)/$', PublicBotConsumer.as_asgi()),
]
